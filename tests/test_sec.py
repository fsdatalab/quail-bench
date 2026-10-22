"""SEC plans, deterministic sampling, input validation, and aggregation."""

import json
import shutil
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import quail_b
from quail_b import prompts, sec
from quail_b._files import GROUND_TRUTH_ROOT, download_cache
from quail_b.labels import GroundTruthCollection, PredicateLabels
from quail_b.predicates import SEC_PREDICATES, label_set_identity, predicate_payload
from quail_b.queries import queries, query_family_name
from quail_b.relational import expected_result
from quail_b.rendering import render_classify_prompt, render_filter_prompt
from quail_b.run import _validate_output
from quail_b.scoring import RunOutput
from quail_b.substrait import AnswerStruct

SEC_IDS = tuple(f"SEC-{i}" for i in range(1, 9))
LONG = "x" * sec.SEC_LONG_MIN_CHARS


def test_sec_plans_are_explicit_and_preserve_the_published_suite():
    assert len(queries()) == 85
    assert set(SEC_IDS) <= set(queries(include_draft=True)) - set(queries())
    assert query_family_name(SEC_IDS) == "sec"
    specs = quail_b.select_queries(SEC_IDS, scale_factor=1.0)
    assert [tuple(op.id for op in spec.info.ai_operators) for spec in specs] == [
        ("filter-1",), ("extract-1",), ("extract-1",),
        ("filter-1", "extract-1", "extract-2", "extract-3"),
        ("filter-1", "classify-1"), ("filter-1", "classify-1"), ("extract-1",),
        ("extract-1",),
    ]
    for spec in specs:
        assert {relation.table for relation in spec.info.relations} == {
            "sec_filings"}
    assert specs[3].info.select == (
        "f.id", "f.ceo_name", "f.departure_date", "f.departure_reason")
    assert specs[3].info.structs == (AnswerStruct("departure", (
        "f.ceo_name", "f.departure_date", "f.departure_reason")),)
    for spec in [specs[i] for i in (0, 3, 4, 5)]:
        assert spec.info.column_tests == ()
    # The event queries read only the long, recent filings.
    for spec, output in zip([specs[i] for i in (1, 2, 6, 7)], (
            "event_party", "event_amount", "event_sentence", "event_change")):
        assert [(test.column, test.comparison, test.value)
                for test in spec.info.column_tests] == [
            ("text_chars", ">=", 9_600), ("text_chars", "<=", 90_000),
            ("filing_month", ">=", "2023-01")]
        assert spec.info.structs == (AnswerStruct("report", (f"f.{output}",)),)
        assert spec.info.columns == ("f", "report")
    assert specs[5].info.aggregate.keys == (
        "f.filing_month", "f.successor_status")
    assert specs[5].info.aggregate.measures == (("filings", "count", None),)
    for ids, sf in [(SEC_IDS, 0.1), (["SEC-1", "IMDB-1"], 1.0)]:
        with pytest.raises(ValueError, match="separately"):
            quail_b.select_queries(ids, scale_factor=sf)


def test_sec_prompts_share_the_departure_and_label_definitions():
    by_prompt = {predicate.template: predicate for predicate in SEC_PREDICATES}
    assert len(by_prompt) == 9
    used = set()
    for query_id in SEC_IDS:
        for operator in quail_b.get_query(query_id).info.ai_operators:
            predicate = by_prompt[operator.prompt]
            assert predicate.workload == "sec"
            assert predicate.left_column == "text"
            used.add(predicate.key)
    assert used == {predicate.key for predicate in SEC_PREDICATES}
    for question in (prompts.SEC_CEO_NAME, prompts.SEC_CEO_DATE,
                     prompts.SEC_CEO_REASON, prompts.SEC_CEO_SUCCESSOR):
        assert prompts.SEC_DEPARTURE_TARGET in question
    for question in (prompts.SEC_EVENT_PARTY, prompts.SEC_EVENT_AMOUNT,
                     prompts.SEC_EVENT_SENTENCE, prompts.SEC_EVENT_CHANGE):
        assert prompts.SEC_DEPARTURE_SCOPE not in question
    filter_prompt = render_filter_prompt(prompts.SEC_CEO_DEPARTURE, "filing")
    classify_prompt = render_classify_prompt(
        prompts.SEC_CEO_SUCCESSOR, "filing", prompts.SEC_CEO_SUCCESSOR_LABELS,
        prompts.SEC_CEO_SUCCESSOR_DESCRIPTIONS)
    assert filter_prompt.startswith("DOCUMENT:\nfiling\n\n")
    assert classify_prompt.startswith("DOCUMENT:\nfiling\n\n")
    assert "interim" in classify_prompt and "unspecified" in classify_prompt
    with pytest.raises(ValueError, match="reference source is not defined"):
        label_set_identity(SEC_PREDICATES[1], "corpus", "hash")


def _source(index, text=None, date="20260115"):
    return {"metadata_accession-number": f"0000000001-26-{index:06d}",
            "metadata_filing-date": date,
            "text": f"Filing {index}" if text is None else text}


def test_sample_uses_all_shards_and_unique_eligible_accessions(monkeypatch, tmp_path):
    monkeypatch.setattr(sec, "SEC_DOCUMENTS", 7)
    rows = [_source(index) for index in range(100)]
    # Keep the first valid duplicate in sorted file order, unchanged.
    rows[0]["text"] = "  original text\n"
    first, second = tmp_path / "a.parquet", tmp_path / "b.parquet"
    pq.write_table(pa.Table.from_pylist(rows[:50]), first)
    pq.write_table(pa.Table.from_pylist([
        *rows[50:], _source(0, "duplicate"), _source(101, ""),
        _source(102, date="20260230"), _source(103, date="bad"),
        {**_source(104), "metadata_accession-number": "bad"},
    ]), second)
    table, counts = sec._sample_filings([second, first])
    expected = sorted(rows, key=lambda row: sec._priority(
        row["metadata_accession-number"]))[:7]
    assert table["id"].to_pylist() == sorted(
        row["metadata_accession-number"] for row in expected)
    assert counts == {"source_rows": 105, "invalid_rows": 4,
                      "duplicate_rows": 1, "eligible_filings": 100}
    assert table.schema == sec.SEC_SCHEMA
    assert table["filing_month"].to_pylist() == ["2026-01"] * 7
    again, _ = sec._sample_filings([first, second])
    assert again.equals(table)
    monkeypatch.setattr(sec, "SEC_DOCUMENTS", 100)
    all_rows, _ = sec._sample_filings([second, first])
    assert all_rows["text"][0].as_py() == "  original text\n"
    monkeypatch.setattr(sec, "SEC_DOCUMENTS", 101)
    with pytest.raises(ValueError, match="only 100 eligible"):
        sec._sample_filings([first, second])


@pytest.fixture()
def sec_corpus(monkeypatch, tmp_path):
    monkeypatch.setattr(sec, "SEC_DOCUMENTS", 4)
    source = tmp_path / "source.parquet"
    # filing 0 holds U+037E and a combining accent, which NFC changes;
    # filing 3 is from 2022
    texts = ["Roge\u0301t\u037e", LONG, "x" * (sec.SEC_LONG_MAX_CHARS + 1),
             LONG + "y"]
    dates = ["20260115"] * 3 + ["20221231"]
    pq.write_table(pa.Table.from_pylist(
        [_source(i, text, date)
         for i, (text, date) in enumerate(zip(texts, dates))]), source)
    monkeypatch.setattr(sec, "_download_sec_source", lambda _: [source])
    directory = sec.build_sec_corpus(tmp_path / "corpus")
    return directory


def test_sec_loader_validates_identity_and_requires_explicit_labels(sec_corpus):
    loaded = quail_b.load_benchmark(
        SEC_IDS, scale_factor=1.0, data_dir=sec_corpus, accuracy=False)
    assert loaded.tables[sec.SEC_TABLE].num_rows == 4
    assert loaded.ground_truth is None
    assert loaded.corpus_id == sec.sec_identity(
        sec.load_sec_table(sec_corpus))["corpus_id"]
    engine_table = loaded.tables[sec.SEC_TABLE]
    assert engine_table["text"][0].as_py() == "Rog\u00e9t;"
    assert sec.load_sec_table(sec_corpus)["text"][0].as_py() == "Roge\u0301t\u037e"
    assert engine_table["text_chars"].to_pylist() == [6, 9_600, 90_001, 9_601]
    assert sec.build_sec_corpus(sec_corpus) == sec_corpus
    with pytest.raises(ValueError, match="not published"):
        quail_b.load_benchmark("SEC-1", scale_factor=1.0, data_dir=sec_corpus)
    table = sec.load_sec_table(sec_corpus)
    pq.write_table(table.set_column(1, "text", pa.array(["changed"] * 4)),
                   sec_corpus / "sec_filings.parquet")
    with pytest.raises(ValueError, match="corpus manifest"):
        sec.load_sec_table(sec_corpus)


def test_sec_loader_rejects_a_different_sample_revision(sec_corpus):
    path = sec_corpus / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["sample"]["revision"] = "other"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="fixed 100,000 sample"):
        sec.load_sec_table(sec_corpus)


@pytest.fixture()
def published_sec(sec_corpus, monkeypatch, tmp_path):
    manifest = json.loads((sec_corpus / "manifest.json").read_text())
    monkeypatch.setattr(sec, "SEC_CORPUS_ID", manifest["corpus_id"])
    root = tmp_path / "published"
    directory = root / GROUND_TRUTH_ROOT / "corpora" / sec.SEC_CORPUS_ID
    shutil.copytree(sec_corpus, directory)
    return root, directory


def test_sec_download_uses_cache_and_works_without_source_files(
        published_sec, monkeypatch, tmp_path):
    from quail_b import _files

    root, directory = published_sec
    local_location = _files._location

    def location(requested_root, path):
        assert requested_root is None
        return local_location(root, path)

    monkeypatch.setattr(_files, "_location", location)
    monkeypatch.setattr(sec, "_location", location)
    with download_cache(tmp_path / "cache"):
        first = quail_b.load_benchmark(SEC_IDS, scale_factor=1.0, accuracy=False)
        shutil.rmtree(directory)
        second = quail_b.load_benchmark(SEC_IDS, scale_factor=1.0, accuracy=False)
    assert first.corpus_id == sec.SEC_CORPUS_ID
    assert first.ground_truth is None
    assert first.tables[sec.SEC_TABLE].num_rows == 4
    assert second.tables[sec.SEC_TABLE].equals(first.tables[sec.SEC_TABLE])


def test_sec_loads_from_published_mirror(published_sec):
    root, _ = published_sec
    suite = quail_b.load_benchmark(
        SEC_IDS, scale_factor=1.0, accuracy=False, root=root)
    assert suite.corpus_id == sec.SEC_CORPUS_ID
    assert suite.tables[sec.SEC_TABLE].num_rows == 4


def test_sec_rejects_a_different_published_corpus(published_sec):
    root, directory = published_sec
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["corpus_id"] = "c_other"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="wrong corpus ID"):
        sec.load_sec_table(root=root)


def test_sec_rejects_modified_published_data(published_sec):
    root, directory = published_sec
    path = directory / "sec_filings.parquet"
    table = pq.read_table(path)
    pq.write_table(table.set_column(1, "text", pa.array(["changed"] * 4)), path)
    with pytest.raises(ValueError, match="corpus manifest"):
        sec.load_sec_table(root=root)


def test_sec_requires_labels_before_downloading(monkeypatch):
    def unexpected_download(*args, **kwargs):
        pytest.fail("SEC data should not be downloaded without required labels")

    monkeypatch.setattr(sec, "load_sec_table", unexpected_download)
    with pytest.raises(ValueError, match="not published"):
        quail_b.load_benchmark("SEC-1", scale_factor=1.0)


def test_source_download_pins_revision_and_caches_only_input_columns(
        monkeypatch, tmp_path):
    import huggingface_hub

    source = tmp_path / "upstream.parquet"
    pq.write_table(pa.Table.from_pylist([
        {**_source(i), "content": "unused raw document"} for i in range(2)]),
        source)
    opened = []

    class Api:
        def __init__(self, **kwargs):
            assert kwargs == {"token": False}

        def dataset_info(self, repo, revision):
            assert (repo, revision) == (sec.SEC_REPO, sec.SEC_REVISION)
            return SimpleNamespace(siblings=[
                SimpleNamespace(rfilename=name) for name in
                ["8-K/b.parquet", "10-K/other.parquet", "8-K/a.parquet"]])

    class Filesystem:
        def __init__(self, **kwargs):
            assert kwargs == {"token": False}

        def open(self, path, mode, cache_type):
            assert path.startswith(f"datasets/{sec.SEC_REPO}@{sec.SEC_REVISION}/")
            assert mode == "rb" and cache_type == "none"
            opened.append(path)
            return source.open("rb")

    monkeypatch.setattr(huggingface_hub, "HfApi", Api)
    monkeypatch.setattr(huggingface_hub, "HfFileSystem", Filesystem)
    monkeypatch.setattr(sec, "SEC_SOURCE_FILES", 2)
    paths = sec._download_sec_source(tmp_path / "cache")
    assert [path.name for path in paths] == ["a.parquet", "b.parquet"]
    for path in paths:
        assert pq.read_schema(path).names == list(sec.SEC_SOURCE_COLUMNS)
        assert path.parent.name == sec.SEC_REVISION
    assert sec._download_sec_source(tmp_path / "cache") == paths
    assert len(opened) == 2


def test_sec_aggregation_counts_filtered_filings_by_filing_month():
    table = pa.Table.from_pylist([
        {"id": f"f{i}", "text": f"filing {i}", "filing_date": date,
         "filing_month": date[:7]}
        for i, date in enumerate([
            "2026-01-01", "2026-01-15", "2026-01-31", "2026-02-01", "2026-02-02"])
    ], schema=sec.SEC_SCHEMA)
    values = {
        "ceo_departure": [True, True, False, True, True],
        "successor_status": ["interim", "interim", "permanent",
                             "permanent", "unspecified"],
    }
    labels = {}
    for predicate in SEC_PREDICATES:
        if predicate.slug not in values:
            continue
        payload = predicate_payload(predicate)
        labels[predicate.key] = PredicateLabels(
            predicate.key, f"ls_{predicate.slug}", payload,
            {(f"f{i}", None): value
             for i, value in enumerate(values[predicate.slug])}, {},
            predicate_payload=payload)
    truth = GroundTruthCollection("test", "test", 1.0, None, labels)
    spec = quail_b.get_query("SEC-6")
    tables = {sec.SEC_TABLE: table}
    result = expected_result(spec, truth, tables)
    assert result.to_pydict() == {
        "filing_month": ["2026-01", "2026-02", "2026-02"],
        "successor_status": ["interim", "permanent", "unspecified"],
        "filings": [2, 1, 1],
    }
    _validate_output(spec, RunOutput(None, None, result, runtime_s=1.0), tables)


def test_sec_queries_run_with_an_explicit_matching_reference_collection(
        monkeypatch, sec_corpus, tmp_path):
    from quail_b import benchmark as loading

    table = sec.load_sec_table(sec_corpus)
    ids = table["id"].to_pylist()
    kept = [0, 2, 3]
    long = [1]
    values = {
        "ceo_departure": [True, False, True, True],
        "ceo_name": [["Alex"], [], ["Casey"], ["Dana"]],
        "departure_date": [["January 1"], [], [], ["February 2"]],
        "departure_reason": [[], [], ["retirement"], []],
        "successor_status": ["interim", "unspecified", "permanent", "unspecified"],
        "event_party": [[], ["Acme Bank"], ["Beta Corp."], []],
        "event_amount": [[], ["$10.5 million"], [], ["$3,200,000"]],
        "event_sentence": [[], ["Alex resigned."], ["Casey retired."], []],
        "event_change": [[], [], [], ["The company will close a plant."]],
    }
    labels = {}
    for predicate in SEC_PREDICATES:
        payload = predicate_payload(predicate)
        labels[predicate.key] = PredicateLabels(
            predicate.key, f"ls_{predicate.slug}", payload,
            {(row_id, None): value
             for row_id, value in zip(ids, values[predicate.slug])}, {},
            predicate_payload=payload)
    truth = GroundTruthCollection(
        "gt_sec_test", sec.sec_identity(table)["corpus_id"], 1.0, None, labels)
    monkeypatch.setattr(loading, "load_ground_truth", lambda *a, **kw: truth)

    def adapter(spec, tables):
        indices = (kept if spec.info.filters else
                   long if spec.info.column_tests else list(range(4)))
        selected = [ids[i] for i in indices]
        columns = {"f": selected}
        extracts = {}
        for operator in spec.info.extracts:
            answers = [next(iter(values[operator.output][i]), None)
                       for i in indices]
            columns[operator.output] = pa.array(answers, pa.string())
            extracts[operator.id] = pa.table({"f": selected,
                                              "answer": columns[operator.output]})
        if spec.info.extracts:
            answers = [columns.pop(op.output) for op in spec.info.extracts]
            columns[spec.info.structs[0].name] = pa.StructArray.from_arrays(
                answers, names=[op.output for op in spec.info.extracts])
        if spec.info.classifies:
            columns["successor_status"] = [values["successor_status"][i]
                                             for i in indices]
        if spec.id == "SEC-6":
            rows = pa.table({
                "filing_month": ["2026-01"] * 3,
                "successor_status": ["interim", "permanent", "unspecified"],
                "filings": [1, 1, 1]})
        else:
            rows = pa.table(columns)
        return RunOutput(None, None, rows, runtime_s=1.0,
                         extract_answers=extracts or None)

    record = quail_b.run(
        adapter, queries=SEC_IDS, scale_factor=1.0, data_dir=sec_corpus,
        collection_id=truth.collection_id, output_dir=tmp_path / "run")
    assert record["status"] == "complete"
    assert all(query["status"] == "complete" for query in record["queries"])
    for query in record["queries"]:
        if quail_b.get_query(query["id"]).info.extracts:
            assert query["metrics"]["accuracy"]["span_accuracy"]["token_f1"] == 1.0
    with pytest.raises(ValueError, match="wrong collection ID"):
        quail_b.load_benchmark("SEC-1", scale_factor=1.0, data_dir=sec_corpus,
                               collection_id="gt_other")
