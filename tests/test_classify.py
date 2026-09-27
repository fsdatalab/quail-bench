"""Classification queries: plans, prompts, labels, and scoring."""

import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import quail_b
from quail_b import predicates, prompts, rendering
from quail_b.data import (
    DATA_SEED,
    GROUND_TRUTH_ROOT,
    PUBLISHED_CORPORA,
    SOURCE_REVISIONS,
    corpus_identity,
)
from quail_b.queries import QuerySpec, pending_query_ids, queries
from quail_b.run import _write_json
from tools.make_substrait_plans import (
    Classify,
    LabelFilter,
    Scan,
    build_plan,
)

PENDING = ("IMDB-11", "IMDB-12", "IMDB-13", "IMDB-14", "BIO-5", "BIO-6",
           "FEV-11", "LEP-6", "AGENT-3", "AGENT-4")


def test_classification_queries_wait_for_published_labels():
    assert pending_query_ids() == PENDING
    assert not set(PENDING) & set(queries())
    assert set(PENDING) <= set(queries(include_pending=True))
    assert quail_b.get_query("IMDB-13").id == "IMDB-13"


def test_every_classification_has_one_predicate():
    by_template = {
        spec.template: spec for spec in predicates.CLASSIFY_PREDICATES}
    assert len(by_template) == len(predicates.CLASSIFY_PREDICATES)
    used = set()
    for query_id in PENDING:
        for operator in quail_b.get_query(query_id)._info.classifies:
            spec = by_template[operator.prompt]
            assert spec.labels == operator.labels
            assert predicates._descriptions(spec) == operator.descriptions
            used.add(spec.key)
    assert used == {spec.key for spec in predicates.CLASSIFY_PREDICATES}


def test_plans_carry_label_columns_and_filters():
    info = quail_b.get_query("IMDB-13")._info
    assert [operator.id for operator in info.operators] == [
        "classify-1", "label-filter-1", "join-1"]
    assert info.select == ("r.id", "r.sentiment", "a.id")
    assert list(quail_b.get_query("IMDB-13").plan.relations[0].root.names) \
        == ["r", "sentiment", "a"]
    (label_filter,) = info.label_filters
    assert label_filter.accepted == ("negative", "mixed")
    agent = quail_b.get_query("AGENT-4")._info
    assert [operator.output for operator in agent.classifies] == [
        "outcome", "failure"]
    assert agent.classifies[0].descriptions == prompts.AGENT_OUTCOME_DESCRIPTIONS
    assert agent.classifies[1].descriptions == prompts.AGENT_FAILURE_DESCRIPTIONS
    assert quail_b.get_query("IMDB-12")._info.classifies[0].descriptions == (
        ("",) * len(prompts.IMDB_GENRE_LABELS))


def _spec(tree, select):
    return QuerySpec.from_plan("TEST", "test", build_plan(tree, select))


@pytest.mark.parametrize(("labels", "message"), [
    (("fixed the bug", "fixed the bug quickly"), "extends label"),
    (("same", "Same "), "distinct"),
    (("only",), "at least two"),
])
def test_invalid_label_lists_are_rejected(labels, message):
    tree = Classify(Scan("reviews", "r", "body"), "{0}", labels, "x")
    with pytest.raises(ValueError, match=message):
        _spec(tree, ("r", "r.x"))


def test_invalid_label_uses_are_rejected():
    scan = Scan("reviews", "r", "body")
    tree = Classify(scan, "{0}", ("yes", "no"), "x")
    with pytest.raises(ValueError, match="not in its call"):
        _spec(LabelFilter(tree, "x", ("maybe",)), None)
    with pytest.raises(ValueError, match="never used"):
        _spec(tree, None)
    with pytest.raises(ValueError, match="relation's id"):
        _spec(tree, ("r.x",))


def test_classify_prompt_text():
    text = rendering.render_classify_prompt(
        prompts.FEV_TOPIC, "Paris is in France.", ("geography", "politics"),
        ("places and borders", ""))
    expected_text = (
        "DOCUMENT:\nParis is in France.\n\n"
        "Answer with exactly one of the categories below for the following "
        "question: Judge strictly from the claim above which topic it is "
        "about.\n\n"
        "Categories:\n- geography: places and borders\n- politics\nANSWER:")
    assert text == expected_text
    spec = predicates.PREDICATE_BY_KEY["quailb.fever.claim.topic"]
    assert predicates.render_classify_prompt(spec, "x").endswith(
        "- religion\n- other\nANSWER:")


def test_classify_label_sets_have_their_own_identity():
    spec = predicates.PREDICATE_BY_KEY["quailb.imdb.review.sentiment"]
    payload = predicates.predicate_payload(spec)
    assert payload["render"] == "classify_document_then_categories_v1"
    assert payload["labels"] == list(prompts.IMDB_SENTIMENT_LABELS)
    assert "task_instruction" not in payload
    identity = predicates.label_set_identity(spec, "c_test", "hash")
    assert identity["sources"][0]["spec"] == predicates.CLASSIFY_JUDGE_SPEC


def _collection(root, tables, label_sets):
    """Write a sf=0.1 corpus and one collection of the given label sets.

    Args:
        root: Local mirror root.
        tables: Table name to rows.
        label_sets: (predicate, rows) pairs. A classify predicate's rows
            are (left_id, label); a filter's (left_id, answer); a join's
            (left_id, right_id, answer).
    """
    corpus_id = PUBLISHED_CORPORA[0.1]
    corpus = root / GROUND_TRUTH_ROOT / "corpora" / corpus_id
    corpus.mkdir(parents=True)
    manifest = corpus_identity(tables, 0.1, DATA_SEED, SOURCE_REVISIONS)
    manifest["corpus_id"] = corpus_id
    _write_json(corpus / "manifest.json", manifest)
    for name, table in tables.items():
        pq.write_table(table, corpus / f"{name}.parquet")
    ids = {}
    for index, (predicate, rows) in enumerate(label_sets):
        key = f"predicate_{index}"
        label_id = f"ls_test_{index}"
        ids[key] = label_id
        directory = root / GROUND_TRUTH_ROOT / "label_sets" / key / label_id
        directory.mkdir(parents=True)
        _write_json(directory / "manifest.json", {
            "status": "complete", "rows": len(rows),
            "source_rows": {"test": len(rows)},
            "predicate": {"key": key, **predicate},
        })
        columns = {
            "predicate_key": [key] * len(rows),
            "label_set_id": [label_id] * len(rows),
            "left_id": [row[0] for row in rows],
        }
        if predicate["kind"] == "classify":
            columns["right_id"] = pa.array([None] * len(rows), pa.string())
            columns["label"] = [row[1] for row in rows]
        elif predicate["kind"] == "join":
            columns["right_id"] = [row[1] for row in rows]
            columns["answer"] = [row[2] for row in rows]
        else:
            columns["right_id"] = pa.array([None] * len(rows), pa.string())
            columns["answer"] = [row[1] for row in rows]
        pq.write_table(pa.table(columns), directory / "labels.parquet")
    collection_id = "gt_test"
    collection = root / GROUND_TRUTH_ROOT / "collections" / collection_id
    collection.mkdir(parents=True)
    _write_json(collection / "manifest.json", {
        "status": "complete", "collection_id": collection_id,
        "corpus_id": corpus_id, "scale_factor": 0.1, "label_sets": ids,
        "summary": {"model": "test"},
    })
    _write_json(corpus / "active_collection.json",
                {"collection_id": collection_id})


def _classify_predicate(spec):
    return {"kind": "classify", "template": spec.template,
            "left_table": spec.left_table, "left_column": spec.left_column,
            "labels": list(spec.labels)}


SENTIMENT = predicates.PREDICATE_BY_KEY["quailb.imdb.review.sentiment"]


def _imdb_13(root):
    _collection(root, {
        "reviews": pa.table({"id": ["r0", "r1", "r2"],
                             "body": ["a", "b", "c"]}),
        "aspects": pa.table({"id": ["a0", "a1"], "aspect": ["x", "y"]}),
    }, [
        (_classify_predicate(SENTIMENT), [
            ("r0", "negative"), ("r1", "positive"), ("r2", "mixed")]),
        ({"kind": "join", "template": prompts.DISCUSS_ASPECT,
          "left_table": "reviews", "right_table": "aspects"}, [
            ("r0", "a0", True), ("r0", "a1", True), ("r1", "a0", True),
            ("r1", "a1", False), ("r2", "a0", False), ("r2", "a1", True)]),
    ])


def _imdb_13_output(rows=True, wrong_row=False):
    engine_rows = pa.table({
        "r": ["r0", "r0", "r1"],
        "sentiment": ["negative"] * 2
        + ["mixed" if wrong_row else "negative"],
        "a": ["a0", "a1", "a0"],
    })
    return quail_b.RunOutput(
        {},
        {"join-1": pa.table({
            "r": ["r0", "r0", "r1", "r1"], "a": ["a0", "a1", "a0", "a1"],
            "answer": [True, True, True, False]})},
        engine_rows if rows else None,
        runtime_s=1.0,
        classify_answers={"classify-1": pa.table({
            "r": ["r0", "r1", "r2"],
            "label": ["negative", "negative", "positive"]})})


def test_classification_run_scores_labels_and_rows(tmp_path):
    _imdb_13(tmp_path)
    benchmark = quail_b.load_benchmark(["IMDB-13"], root=tmp_path)
    expected = quail_b.scoring.expected_rows(
        benchmark.queries[0], benchmark.ground_truth, benchmark.tables)
    assert sorted(expected.to_pylist(), key=str) == sorted([
        {"a": "a0", "r": "r0", "sentiment": "negative"},
        {"a": "a1", "r": "r0", "sentiment": "negative"},
        {"a": "a1", "r": "r2", "sentiment": "mixed"},
    ], key=str)

    record = quail_b.run(
        lambda spec, tables: _imdb_13_output(), queries=["IMDB-13"],
        output_dir=tmp_path / "run", root=tmp_path)
    accuracy = record["queries"][0]["metrics"]["accuracy"]
    assert accuracy["label_accuracy"] == {
        "correct": 1, "evaluated": 3, "accuracy": 0.333333}
    assert accuracy["answer_accuracy"]["correct"] == 4
    assert accuracy["output_accuracy"]["predicted_rows"] == 3
    assert accuracy["output_accuracy"]["expected_rows"] == 3
    assert accuracy["output_accuracy"]["matching_rows"] == 2
    classify_item = accuracy["per_predicate"][-1]
    assert classify_item["op"] == "classify"
    assert classify_item["alias"] == "r"
    report = (tmp_path / "run" / "report.md").read_text()
    assert "| Label accuracy | Evaluated labels |" in report

    saved = json.loads((tmp_path / "run" / "run.json").read_text())
    quail_b.report(tmp_path / "run", root=tmp_path)
    rescored = json.loads((tmp_path / "run" / "run.json").read_text())
    assert rescored["queries"][0]["metrics"] == saved["queries"][0]["metrics"]

    untraced = quail_b.RunOutput(
        None, None, _imdb_13_output().rows, runtime_s=1.0)
    record = quail_b.run(
        lambda spec, tables: untraced, queries=["IMDB-13"],
        output_dir=tmp_path / "untraced", root=tmp_path)
    output = record["queries"][0]["metrics"]["accuracy"]["output_accuracy"]
    assert (output["predicted_rows"], output["matching_rows"]) == (3, 2)
    assert record["queries"][0]["metrics"]["accuracy"]["label_accuracy"] is None


def test_rows_must_follow_the_classification_answers(tmp_path):
    _imdb_13(tmp_path)
    with pytest.raises(ValueError, match="not implied"):
        quail_b.run(
            lambda spec, tables: _imdb_13_output(wrong_row=True),
            queries=["IMDB-13"], output_dir=tmp_path / "run", root=tmp_path)

    def unknown_label(spec, tables):
        output = _imdb_13_output()
        output.classify_answers["classify-1"] = pa.table({
            "r": ["r0", "r1", "r2"],
            "label": ["negative", "negative", "loves it"]})
        return output

    with pytest.raises(ValueError, match="not one of the query's labels"):
        quail_b.run(unknown_label, queries=["IMDB-13"],
                    output_dir=tmp_path / "unknown", root=tmp_path)


def test_two_label_columns_and_chained_calls(tmp_path):
    complaint = predicates.PREDICATE_BY_KEY["quailb.imdb.review.main_complaint"]
    outcome = predicates.PREDICATE_BY_KEY["quailb.agent.trace.outcome"]
    failure = predicates.PREDICATE_BY_KEY["quailb.agent.trace.failure_mode"]
    topic = predicates.PREDICATE_BY_KEY["quailb.fever.claim.topic"]
    _collection(tmp_path, {
        "reviews": pa.table({"id": ["r0", "r1", "r2"],
                             "body": ["a", "b", "c"]}),
        "agent_traces": pa.table({
            "id": ["t0", "t1"], "trace": ["p", "q"],
            "trajectory_id": ["j0", "j1"], "turn_index": [3, 5],
            "token_count": [10, 20]}),
        "claims": pa.table({
            "id": ["c0", "c1", "c2"], "claim": ["u", "v", "w"],
            "label": ["SUPPORTS"] * 3, "evidence_wiki_url": ["p0", "p1", "p2"]}),
    }, [
        (_classify_predicate(SENTIMENT), [
            ("r0", "negative"), ("r1", "positive"), ("r2", "mixed")]),
        (_classify_predicate(complaint), [
            ("r0", "pacing"), ("r1", "no specific complaint"),
            ("r2", "acting")]),
        (_classify_predicate(outcome), [("t0", "resolved"),
                                        ("t1", "gave up")]),
        (_classify_predicate(failure), [("t0", "incorrect fix"),
                                        ("t1", "ran out of steps")]),
        (_classify_predicate(topic), [("c0", "politics"), ("c1", "sports"),
                                      ("c2", "history")]),
    ])

    def labels(alias, ids, values):
        return pa.table({alias: ids, "label": values})

    def execute(spec, tables):
        if spec.id == "IMDB-14":
            return quail_b.RunOutput(
                {}, {}, pa.table({
                    "r": ["r0", "r2"], "sentiment": ["negative", "mixed"],
                    "complaint": ["pacing", "plot"]}),
                runtime_s=1.0, classify_answers={
                    "classify-1": labels("r", ["r0", "r1", "r2"],
                                         ["negative", "positive", "mixed"]),
                    "classify-2": labels("r", ["r0", "r2"],
                                         ["pacing", "plot"])})
        if spec.id == "AGENT-4":
            return quail_b.RunOutput(
                {}, {}, pa.table({"t": ["t1"], "failure": ["ran out of steps"]}),
                runtime_s=1.0, classify_answers={
                    "classify-1": labels("t", ["t0", "t1"],
                                         ["resolved", "gave up"]),
                    "classify-2": labels("t", ["t1"], ["ran out of steps"])})
        return quail_b.RunOutput(
            {}, {}, pa.table({"c": ["c0", "c2"],
                              "topic": ["politics", "history"]}),
            runtime_s=1.0, classify_answers={"classify-1": labels(
                "c", ["c0", "c1", "c2"], ["politics", "science", "history"])})

    record = quail_b.run(execute, queries=["IMDB-14", "AGENT-4", "FEV-11"],
                         output_dir=tmp_path / "run", root=tmp_path)
    imdb, agent, fever = (
        item["metrics"]["accuracy"] for item in record["queries"])
    assert imdb["label_accuracy"] == {
        "correct": 4, "evaluated": 5, "accuracy": 0.8}
    assert imdb["output_accuracy"]["matching_rows"] == 1
    assert agent["label_accuracy"]["correct"] == 3
    assert agent["output_accuracy"]["exact_match"]
    assert fever["label_accuracy"]["correct"] == 2
    assert fever["output_accuracy"]["exact_match"]
    for item in record["queries"]:
        assert item["metrics"]["minimum_tokens"] is None
