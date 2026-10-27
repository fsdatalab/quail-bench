"""Map drafts: plans, generated text, validation, persistence, and tokens."""

import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from test_classify import (
    IMDB_TABLES,
    SENTIMENT,
    _classify_predicate,
    _collection,
)

import quail_b
from quail_b import minimum, predicates, prompts, rendering
from quail_b.queries import queries
from quail_b.run import _query_hash, _read_output, _save_output, _validate_output
from quail_b.substrait import Map
from tools.make_substrait_plans import Map as MapNode
from tools.make_substrait_plans import Scan, Struct, build_plan

MAP_QUERIES = (*(f"IMDB-MAP-{i}" for i in range(1, 6)),
               *(f"CUAD-MAP-{i}" for i in range(1, 4)))
REVIEWS = IMDB_TABLES["reviews"]


def _spec(tree, select):
    return quail_b.QuerySpec.from_plan("T", "test", build_plan(tree, select))


def _map_spec(prompt=None, max_tokens=64, output="answer"):
    return _spec(MapNode(Scan("reviews", "r", "body"),
                         prompt or prompts.IMDB_MAP_NEGATIVES,
                         max_tokens, output), ("r", f"r.{output}"))


def test_map_queries_are_drafts_over_their_corpora():
    published = queries()
    drafts = queries(include_draft=True)
    assert len(published) == 85
    assert all(query_id not in published for query_id in MAP_QUERIES)
    assert all(query_id in drafts for query_id in MAP_QUERIES)
    expected = {
        "IMDB-MAP-1": [(prompts.IMDB_MAP_NEGATIVES, 64)],
        "IMDB-MAP-2": [(prompts.IMDB_MAP_SUMMARY, 256)],
        "IMDB-MAP-3": [(prompts.IMDB_MAP_NEGATIVES, 64),
                       (prompts.IMDB_MAP_SUMMARY, 256),
                       (prompts.IMDB_MAP_TOPIC, 32)],
        "IMDB-MAP-4": [(prompts.IMDB_MAP_SUMMARY, 256)],
        "IMDB-MAP-5": [(prompts.IMDB_MAP_SUMMARY, 256)],
        "CUAD-MAP-1": [(prompts.CUAD_MAP_PARTIES, 256)],
        "CUAD-MAP-2": [(prompts.CUAD_MAP_PARTIES, 128),
                       (prompts.CUAD_MAP_TERM, 128),
                       (prompts.CUAD_MAP_GOVERNING_LAW, 128)],
        "CUAD-MAP-3": [(prompts.CUAD_MAP_NON_COMPETE, 256)],
    }
    for query_id, calls in expected.items():
        info = drafts[query_id].info
        assert [(op.prompt, op.max_tokens) for op in info.maps] == calls
        table, alias = (("reviews", "r") if query_id.startswith("IMDB")
                        else ("contracts", "c"))
        assert [(rel.table, rel.alias) for rel in info.relations] == [(table, alias)]
        assert [op.id for op in info.maps] == [
            f"map-{i}" for i in range(1, len(calls) + 1)]
        assert not info.structs
        assert all(f"{alias}.{op.output}" in info.select for op in info.maps)


def test_map_plan_has_literal_limit_and_nonnull_string_output():
    spec = _map_spec()
    (operator,) = spec.info.maps
    assert operator == Map("map-1", "r", prompts.IMDB_MAP_NEGATIVES, 64, "answer")
    assert spec.info.select == ("r.id", "r.answer")
    assert spec.info.columns == ("r", "answer")
    assert spec.info.ai_operators == (operator,)
    plan = spec.plan
    project = plan.relations[0].root.input.project.input.project
    assert project.common.hint.alias == "map-1"
    function = project.expressions[0].scalar_function
    declarations = {item.extension_function.function_anchor:
                    item.extension_function.name for item in plan.extensions}
    assert declarations[function.function_reference] == "ai_map:str_str_i32"
    assert function.arguments[0].value.literal.string == operator.prompt
    assert function.arguments[1].value.selection.direct_reference \
        .struct_field.field == 1
    limit = function.arguments[2].value.literal
    assert limit.WhichOneof("literal_type") == "i32" and limit.i32 == 64
    assert function.output_type.WhichOneof("kind") == "string"
    assert function.output_type.string.nullability == 2


def test_map_filter_classification_and_relational_compositions():
    filtered = quail_b.get_query("IMDB-MAP-4").info
    assert [op.id for op in filtered.ai_operators] == ["filter-1", "map-1"]
    assert filtered.filters[0].prompt == "Is this review negative?\n\n{0}"
    assert filtered.filters[0].prompt == prompts.IMDB_NEGATIVE
    classified = quail_b.get_query("IMDB-MAP-5").info
    original = quail_b.get_query("IMDB-11").info.classifies[0]
    (operator,) = classified.classifies
    assert operator.output == "tone"
    assert (operator.prompt, operator.labels, operator.descriptions) == (
        original.prompt, original.labels, original.descriptions)
    assert operator.labels == ("positive", "negative", "neutral", "mixed")
    assert [op.id for op in classified.ai_operators] == ["classify-1", "map-1"]
    assert "r.tone" in classified.select
    limited = quail_b.get_query("CUAD-MAP-3").info
    assert limited.filters[0].prompt == prompts.cuad_filter("Non-Compete")
    assert limited.relational
    assert limited.sort.keys == (("c.id", False),)
    assert limited.fetch.count == 10


@pytest.mark.parametrize("kind,value", [
    ("i32", 0), ("i32", -1), ("i64", 64), ("string", "64"),
    ("fp64", 64.0), ("boolean", True), ("selection", None),
])
def test_invalid_map_limit_literals_and_types_are_rejected(kind, value):
    original = _map_spec()
    plan = original.plan
    function = plan.relations[0].root.input.project.input.project \
        .expressions[0].scalar_function
    argument = function.arguments[2].value
    argument.Clear()
    if kind == "selection":
        argument.CopyFrom(function.arguments[1].value)
    else:
        setattr(argument.literal, kind, value)
    with pytest.raises(ValueError):
        quail_b.QuerySpec.from_plan("T", "test", plan)


@pytest.mark.parametrize("kind", ["nullable", "integer"])
def test_map_output_type_is_required_string(kind):
    plan = _map_spec().plan
    output = plan.relations[0].root.input.project.input.project \
        .expressions[0].scalar_function.output_type
    if kind == "nullable":
        output.string.nullability = 1
    else:
        output.Clear()
        output.i32.nullability = 2
    with pytest.raises(ValueError):
        quail_b.QuerySpec.from_plan("T", "test", plan)


def test_invalid_map_column_uses_are_rejected():
    scan = Scan("reviews", "r", "body")
    mapped = MapNode(scan, prompts.IMDB_MAP_SUMMARY, 256, "summary")
    with pytest.raises(ValueError, match="never used"):
        _spec(mapped, ("r",))
    with pytest.raises(ValueError, match="already a field"):
        _spec(MapNode(mapped, prompts.IMDB_MAP_TOPIC, 32, "summary"),
              ("r", "r.summary"))
    with pytest.raises(ValueError, match="holds only answer columns"):
        _spec(mapped, ("r", Struct("review", ("r.summary",))))


def test_map_prompt_is_document_then_instruction_and_answer_cue():
    template = "{0}\n\nSummarize the negative arguments in one sentence."
    document = "Weak plot. {0} is literal document text."
    expected = ("DOCUMENT:\n" + document + "\n\n"
                "Summarize the negative arguments in one sentence.\nANSWER:")
    assert rendering.render_map_prompt(template, document) == expected
    preamble, tail = rendering.map_frame(template)
    assert preamble == "DOCUMENT:\n"
    assert preamble + document + tail == expected
    assert "TRUE or FALSE" not in tail
    assert "Categories:" not in tail
    for prompt in {op.prompt for query_id in MAP_QUERIES
                   for op in quail_b.get_query(query_id).info.maps}:
        assert prompt.startswith("{0}\n\n")
        assert rendering.render_map_prompt(prompt, "doc") == (
            "DOCUMENT:\ndoc" + prompt[len("{0}"):] + "\nANSWER:")
    with pytest.raises(ValueError, match="placeholder"):
        rendering.render_map_prompt("no placeholder", "document")
    with pytest.raises(ValueError, match="placeholder"):
        rendering.render_map_prompt("{0}\n\nCompare with {1}", "document")


@pytest.mark.parametrize("template", [
    "Summarize.\n\n{0}", "{0}", "{0}\n\n", "{0}\n\nRepeat {0}",
])
def test_map_prompt_requires_one_document_before_a_nonempty_instruction(template):
    with pytest.raises(ValueError):
        rendering.render_map_prompt(template, "document")
    with pytest.raises(ValueError):
        _map_spec(prompt=template)


def test_map_predicates_include_limit_in_identity_and_have_no_labels():
    by_call = {(spec.template, spec.max_tokens): spec
               for spec in predicates.MAP_PREDICATES}
    assert len(by_call) == len(predicates.MAP_PREDICATES)
    used = set()
    for query_id in MAP_QUERIES:
        info = quail_b.get_query(query_id).info
        for operator in info.maps:
            spec = by_call[(operator.prompt, operator.max_tokens)]
            assert spec.kind == "map"
            assert spec.source_policy == "unpublished"
            assert spec.left_table == info.relations[0].table
            with pytest.raises(ValueError, match="not defined"):
                predicates.label_sources(spec)
            used.add(spec.key)
    assert used == {spec.key for spec in predicates.MAP_PREDICATES}
    short, long = (by_call[(prompts.CUAD_MAP_PARTIES, limit)]
                   for limit in (128, 256))
    assert short.key != long.key
    assert predicates.predicate_version(short) != predicates.predicate_version(long)


def _output(rows=None, answers=None):
    if answers is None:
        answers = pa.table({"r": ["r0", "r1"], "answer": ["Weak plot.", ""]})
    if rows is None:
        output = quail_b.get_query("IMDB-MAP-1").info.maps[0].output
        rows = pa.table({"r": ["r0", "r1"], output: ["Weak plot.", ""]})
    return quail_b.RunOutput({}, {}, rows, runtime_s=1.0,
                             map_answers={"map-1": answers})


def test_map_validation_accepts_empty_strings_and_partial_evaluation():
    _validate_output(quail_b.get_query("IMDB-MAP-1"), _output(),
                     {"reviews": REVIEWS})


@pytest.mark.parametrize("filter_answers", [None, {}])
@pytest.mark.parametrize("values,type_", [
    ([None, "ok"], pa.string()), ([1, 2], pa.int64()),
    ([b"x", b"y"], pa.binary()),
])
@pytest.mark.parametrize("location", ["answers", "rows"])
def test_map_validation_rejects_null_and_nonstring_text(
        values, type_, location, filter_answers):
    output = _output()
    output.filter_answers = filter_answers
    if location == "answers":
        output.map_answers["map-1"] = pa.table({
            "r": ["r0", "r1"], "answer": pa.array(values, type_)})
    else:
        name = output.rows.column_names[1]
        output.rows = output.rows.set_column(1, name, pa.array(values, type_))
    with pytest.raises(ValueError):
        _validate_output(quail_b.get_query("IMDB-MAP-1"), output,
                         {"reviews": REVIEWS})


@pytest.mark.parametrize("filter_answers", [None, {}])
@pytest.mark.parametrize("case", [
    "operator", "unknown_id", "duplicate_id", "mismatch", "missing_answer",
])
def test_map_validation_checks_operator_ids_document_ids_and_returned_text(
        case, filter_answers):
    output = _output()
    output.filter_answers = filter_answers
    if case == "operator":
        output.map_answers = {"map-99": output.map_answers["map-1"]}
    elif case == "unknown_id":
        output.map_answers["map-1"] = pa.table({
            "r": ["r0", "absent"], "answer": ["Weak plot.", ""]})
    elif case == "duplicate_id":
        output.map_answers["map-1"] = pa.table({
            "r": ["r0", "r0"], "answer": ["Weak plot.", "Weak plot."]})
    elif case == "mismatch":
        output.rows = output.rows.set_column(
            1, output.rows.column_names[1], pa.array(["Changed.", ""]))
    else:
        output.map_answers["map-1"] = output.map_answers["map-1"].slice(0, 1)
    with pytest.raises(ValueError):
        _validate_output(quail_b.get_query("IMDB-MAP-1"), output,
                         {"reviews": REVIEWS})


def test_map_answers_persist_and_round_trip(tmp_path):
    spec = quail_b.get_query("IMDB-MAP-1")
    output = _output()
    directory = tmp_path / "query"
    paths = _save_output(directory, output, spec)
    assert paths["maps"] == [{"key": "map-1", "path": "maps-0.parquet"}]
    assert pq.read_table(directory / "maps-0.parquet").equals(
        output.map_answers["map-1"])
    restored = _read_output(directory, {"files": paths, "runtime_s": 1.0})
    assert restored.rows.equals(output.rows)
    assert restored.map_answers["map-1"].equals(output.map_answers["map-1"])
    _validate_output(spec, restored, {"reviews": REVIEWS})
    no_rows = _read_output(directory, {"files": paths}, rows=False)
    assert no_rows.rows is None
    assert no_rows.map_answers["map-1"].equals(output.map_answers["map-1"])


def test_map_hash_includes_prompt_limit_and_output_but_not_wire_metadata():
    original = _map_spec()
    changed = original.plan
    changed.version.producer = "another-producer"
    assert _query_hash(original) == _query_hash(
        quail_b.QuerySpec.from_plan("T", "other description", changed))
    assert _query_hash(original) != _query_hash(_map_spec(max_tokens=65))
    assert _query_hash(original) != _query_hash(_map_spec(output="other"))
    assert _query_hash(original) != _query_hash(
        _map_spec(prompt="{0}\n\nSay something else."))


def _token_tables(monkeypatch):
    tables = {"reviews": pa.table({
        "id": ["r0", "r1"], "body": ["alpha beta", "gamma delta epsilon"]})}
    ids = {"alpha beta": [11, 12], "gamma delta epsilon": [21, 22, 23],
           "short": [31], "long " * 5: [32] * 5, "": [],
           "generated": [41, 42, 43]}

    def tokenizer(name):
        assert name == "test"
        return lambda texts: [ids[text] for text in texts]

    monkeypatch.setattr(minimum, "load_tokenizer", tokenizer)
    return tables


def test_multiple_maps_share_prompts_but_count_each_answer_decode(monkeypatch):
    spec = quail_b.get_query("IMDB-MAP-3")
    tables = _token_tables(monkeypatch)
    answers = pa.table({"r": ["r0", "r1"], "answer": ["short", "long " * 5]})
    output = quail_b.RunOutput(
        {}, {}, None, runtime_s=1.0, measurements={"fresh_tokens": 40},
        prompt_pieces={
            "tokenizer": "test", "preamble": [1],
            "maps": [{"id": "map-1", "tail": [4, 5]},
                     {"id": "map-2", "tail": [6]},
                     {"id": "map-3", "tail": [7, 8, 9]}]},
        map_answers={f"map-{i}": answers for i in (1, 2, 3)})
    metrics = minimum.token_metrics(spec, output, tables)
    prompt_minimum = 1 + (2 + 3) + 2 * (2 + 1 + 3)
    decode = 3 * (5 - 1)
    assert metrics["minimum_tokens"] == prompt_minimum + decode
    assert metrics["input_tokens"] == (
        2 * (1 + 2) + 5) + (2 * (1 + 1) + 5) + (2 * (1 + 3) + 5)
    assert metrics["regret_tokens"] == 40 - metrics["minimum_tokens"]
    short = pa.table({"r": ["r0", "r1"], "answer": ["", ""]})
    output.map_answers = {f"map-{i}": short for i in (1, 2, 3)}
    assert minimum.token_metrics(spec, output, tables) == {
        **metrics, "minimum_tokens": prompt_minimum,
        "regret_tokens": metrics["regret_tokens"] + decode}
    with pytest.raises(ValueError, match="missing map operators"):
        minimum.validate_prompt_pieces(spec, {"tokenizer": "test", "maps": [
            {"id": "map-1", "tail": []}]})
    output.map_answers = None
    with pytest.raises(ValueError, match="need map answers"):
        minimum.token_metrics(spec, output, tables)


@pytest.mark.parametrize("answer,decode", [("", 0), ("short", 0), ("generated", 2)])
@pytest.mark.parametrize("extra", [0, 7])
@pytest.mark.parametrize("reported_input", [None, 4, 8])
def test_map_regret_excludes_required_decode_inputs(
        monkeypatch, answer, decode, extra, reported_input):
    spec = quail_b.get_query("IMDB-MAP-2")
    tables = _token_tables(monkeypatch)
    # One prompt has no shared KV: preamble + document + tail = 5 tokens.
    prompt = 5 if reported_input is None else reported_input
    output = quail_b.RunOutput(
        {}, {}, None, runtime_s=1.0,
        measurements={"fresh_tokens": prompt + decode + extra},
        prompt_pieces={"tokenizer": "test", "preamble": [1],
                       "maps": [{"id": "map-1", "tail": [4, 5]}]},
        map_answers={"map-1": pa.table({"r": ["r0"], "answer": [answer]})})
    if reported_input is not None:
        output.measurements["input_tokens"] = reported_input
    assert minimum.token_metrics(spec, output, tables) == {
        "input_tokens": prompt, "fresh_tokens": prompt + decode + extra,
        "minimum_tokens": prompt + decode, "regret_tokens": extra,
        "regret_approximate": reported_input is not None}
    pieces = minimum.validate_prompt_pieces(spec, output.prompt_pieces)
    documents = minimum.DocumentTokens(tables, minimum.load_tokenizer("test"))
    assert minimum.minimum_input_tokens(
        spec, pieces, {}, {}, documents, map_answers=output.map_answers) == 5 + decode


def test_empty_map_answer_table_needs_no_tokens(monkeypatch):
    spec = quail_b.get_query("IMDB-MAP-2")
    tables = _token_tables(monkeypatch)
    output = quail_b.RunOutput(
        {}, {}, None, runtime_s=1.0, measurements={"fresh_tokens": 0},
        prompt_pieces={"tokenizer": "test", "preamble": [1],
                       "maps": [{"id": "map-1", "tail": [4, 5]}]},
        map_answers={"map-1": pa.table({"r": [], "answer": []}, schema=pa.schema([
            ("r", pa.string()), ("answer", pa.string())]))})
    assert minimum.token_metrics(spec, output, tables) == {
        "input_tokens": 0, "fresh_tokens": 0, "minimum_tokens": 0,
        "regret_tokens": 0, "regret_approximate": False}


@pytest.mark.parametrize("ids", [("map-99",), ("map-1", "map-1")])
def test_map_prompt_pieces_reject_unknown_and_duplicate_operators(ids):
    spec = quail_b.get_query("IMDB-MAP-1")
    with pytest.raises(ValueError, match="unknown map operator"):
        minimum.validate_prompt_pieces(spec, {
            "tokenizer": "test", "maps": [
                {"id": operator_id, "tail": []} for operator_id in ids]})


def test_filtered_map_counts_only_documents_actually_mapped(monkeypatch):
    spec = quail_b.get_query("IMDB-MAP-4")
    tables = _token_tables(monkeypatch)
    output = quail_b.RunOutput(
        {"filter-1": pa.table({"r": ["r0", "r1"], "answer": [False, True]})},
        {}, None, runtime_s=1.0, measurements={"fresh_tokens": 20},
        prompt_pieces={"tokenizer": "test", "preamble": [1],
                       "filters": [{"id": "filter-1", "tail": [4, 5]}],
                       "maps": [{"id": "map-1", "tail": [6, 7, 8]}]},
        map_answers={"map-1": pa.table({"r": ["r1"], "answer": ["generated"]})})
    metrics = minimum.token_metrics(spec, output, tables)
    assert metrics["minimum_tokens"] == 1 + (2 + 3) + 2 * 2 + 3 + (3 - 1)
    assert metrics["input_tokens"] == 2 * (1 + 2) + 5 + (1 + 3 + 3)


def test_map_only_run_scores_ids_and_rescores_without_reference_predicates(tmp_path):
    _collection(tmp_path, IMDB_TABLES, [])
    spec = quail_b.get_query("IMDB-MAP-1")
    name = spec.info.maps[0].output
    ids = ["r0", "r1", "r2"]
    text = ["Invented text.", "", "No reference is needed."]
    output = quail_b.RunOutput(
        {}, {}, pa.table({"r": ids, name: text}), runtime_s=1.0,
        map_answers={"map-1": pa.table({"r": ids, "answer": text})})
    directory = tmp_path / "run"
    record = quail_b.run(lambda query, tables: output, queries=[spec.id],
                         output_dir=directory, root=tmp_path)
    metrics = record["queries"][0]["metrics"]
    accuracy = metrics["accuracy"]
    assert accuracy["answer_accuracy"] is None
    assert accuracy["label_accuracy"] is None
    assert accuracy["span_accuracy"] is None
    assert accuracy["per_predicate"] == []
    assert accuracy["output_accuracy"]["matching_rows"] == 3
    assert accuracy["output_accuracy"]["exact_match"]
    assert record["queries"][0]["files"]["maps"] == [
        {"key": "map-1", "path": "maps-0.parquet"}]
    quail_b.report(directory, root=tmp_path)
    rescored = json.loads((directory / "run.json").read_text())
    assert rescored["queries"][0]["metrics"] == metrics


def test_map_text_is_not_scored_but_accompanying_classification_is(tmp_path):
    labels = ["positive", "negative", "neutral"]
    _collection(tmp_path, IMDB_TABLES, [
        (_classify_predicate(SENTIMENT), list(zip(["r0", "r1", "r2"], labels)))])
    spec = quail_b.get_query("IMDB-MAP-5")
    name = spec.info.maps[0].output
    answers = ["Arbitrary generated text.", "", "Not a reference answer."]
    output = quail_b.RunOutput(
        {}, {}, pa.table({"r": ["r0", "r1", "r2"], "tone": labels, name: answers}),
        runtime_s=1.0,
        classify_answers={"classify-1": pa.table({
            "r": ["r0", "r1", "r2"], "label": labels})},
        map_answers={"map-1": pa.table({"r": ["r0", "r1", "r2"],
                                       "answer": answers})})
    record = quail_b.run(lambda query, tables: output, queries=[spec.id],
                         output_dir=tmp_path / "run", root=tmp_path)
    accuracy = record["queries"][0]["metrics"]["accuracy"]
    assert accuracy["label_accuracy"]["correct"] == 3
    assert accuracy["span_accuracy"] is None
    assert [item["op"] for item in accuracy["per_predicate"]] == ["classify"]
    assert accuracy["output_accuracy"]["matching_rows"] == 3
    assert record["queries"][0]["files"]["maps"][0]["key"] == "map-1"
    quail_b.report(tmp_path / "run", root=tmp_path)


def test_map_text_is_not_scored_but_accompanying_filter_and_sorted_ids_are(tmp_path):
    tables = {"contracts": pa.table({
        "id": ["c2", "c1", "c0"], "title": ["A", "B", "C"],
        "text": ["Non-compete.", "No clause.", "Non-compete."],
        "token_count": pa.array([2, 2, 2], pa.int32())})}
    template = prompts.cuad_filter("Non-Compete")
    _collection(tmp_path, tables, [
        ({"kind": "filter", "template": template,
          "left_table": "contracts", "left_column": "text"},
         [("c2", True), ("c1", False), ("c0", True)])])
    spec = quail_b.get_query("CUAD-MAP-3")
    name = spec.info.maps[0].output
    output = quail_b.RunOutput(
        {"filter-1": pa.table({"c": ["c2", "c1", "c0"],
                                "answer": [True, False, True]})}, {},
        pa.table({"c": ["c0", "c2"], name: ["One summary.", "Another."]}),
        runtime_s=1.0, map_answers={"map-1": pa.table({
            "c": ["c2", "c0"], "answer": ["Another.", "One summary."]})})
    record = quail_b.run(lambda query, tables: output, queries=[spec.id],
                         output_dir=tmp_path / "run", root=tmp_path)
    accuracy = record["queries"][0]["metrics"]["accuracy"]
    assert accuracy["answer_accuracy"]["correct"] == 3
    assert accuracy["span_accuracy"] is None
    assert [item["op"] for item in accuracy["per_predicate"]] == ["filter"]
    assert accuracy["output_accuracy"]["matching_rows"] == 2
    assert accuracy["output_accuracy"]["ordered_match"]


def test_a_draft_with_an_unlabeled_filter_runs_and_scores_no_rows(tmp_path):
    _collection(tmp_path, IMDB_TABLES, [])
    spec = quail_b.get_query("IMDB-MAP-4")
    name = spec.info.maps[0].output
    output = quail_b.RunOutput(
        {"filter-1": pa.table({"r": ["r0", "r1", "r2"],
                                "answer": [True, False, True]})}, {},
        pa.table({"r": ["r0", "r2"], name: ["One summary.", "Another."]}),
        runtime_s=1.0, map_answers={"map-1": pa.table({
            "r": ["r0", "r2"], "answer": ["One summary.", "Another."]})})
    directory = tmp_path / "run"
    record = quail_b.run(lambda query, tables: output, queries=[spec.id],
                         output_dir=directory, root=tmp_path)
    accuracy = record["queries"][0]["metrics"]["accuracy"]
    assert accuracy["unlabeled_operators"] == ["filter-1"]
    assert accuracy["answer_accuracy"] is None
    assert accuracy["per_predicate"] == []
    # the reference rows need the filter's labels
    assert accuracy["output_accuracy"] is None
    quail_b.report(directory, root=tmp_path)


def test_a_published_query_still_needs_its_labels(tmp_path):
    _collection(tmp_path, IMDB_TABLES, [])
    with pytest.raises(ValueError, match="no reference labels"):
        quail_b.load_benchmark(["IMDB-1"], root=tmp_path)


def test_map_drafts_load_inputs_without_reference_labels(tmp_path):
    _collection(tmp_path, IMDB_TABLES, [])
    suite = quail_b.load_benchmark(
        ["IMDB-MAP-1", "IMDB-MAP-3"], accuracy=False, root=tmp_path)
    assert [spec.id for spec in suite.queries] == ["IMDB-MAP-1", "IMDB-MAP-3"]
    assert suite.tables["reviews"].equals(REVIEWS)
