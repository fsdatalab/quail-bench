"""Extraction queries: plans, prompts, labels, and span scoring."""

import json

import pyarrow as pa
import pytest
from test_classify import _collection

import quail_b
from quail_b import predicates, prompts, rendering
from quail_b.queries import pending_query_ids, queries
from quail_b.scoring import normalize_answer, span_scores
from quail_b.substrait import AI_EXTRACT_NAME, Extract
from tools.make_substrait_plans import Extract as ExtractNode
from tools.make_substrait_plans import (
    Fetch,
    Scan,
    Sort,
    build_plan,
)

CUAD_QUERIES = tuple(f"CUAD-{i}" for i in range(1, 16))
GOVERNING_LAW = prompts.CUAD_QUESTIONS["Governing Law"]
AUDIT_RIGHTS = prompts.CUAD_QUESTIONS["Audit Rights"]


def test_cuad_queries_are_published_and_use_every_operator_kind():
    assert not set(CUAD_QUERIES) & set(pending_query_ids())
    assert all(query_id in queries() for query_id in CUAD_QUERIES)
    infos = {query_id: quail_b.get_query(query_id).info
             for query_id in CUAD_QUERIES}
    assert [query_id for query_id, info in infos.items() if info.extracts] == [
        "CUAD-1", "CUAD-2", "CUAD-3", "CUAD-4", "CUAD-7", "CUAD-8", "CUAD-9",
        "CUAD-10", "CUAD-15"]
    assert [query_id for query_id, info in infos.items() if info.joins] == [
        "CUAD-13", "CUAD-14"]
    assert [query_id for query_id, info in infos.items() if info.classifies] == [
        "CUAD-11", "CUAD-12"]
    assert [query_id for query_id, info in infos.items() if info.relational] == [
        "CUAD-11", "CUAD-12", "CUAD-15"]
    assert sum(len(info.filters) for info in infos.values()) == 11
    assert {relation.table for info in infos.values()
            for relation in info.relations} == {"contracts", "clause_types"}


def test_extract_plans_name_the_question_and_the_answer_column():
    info = quail_b.get_query("CUAD-7").info
    assert [operator.id for operator in info.operators] == [
        "filter-1", "extract-1"]
    (extract,) = info.extracts
    assert extract == Extract("extract-1", "c", AUDIT_RIGHTS, "audit_rights")
    assert info.select == ("c.id", "c.audit_rights")
    assert info.filters[0].prompt == prompts.cuad_filter("Audit Rights")
    plan = quail_b.get_query("CUAD-7").plan
    assert list(plan.relations[0].root.names) == ["c", "audit_rights"]
    project = plan.relations[0].root.input.project.input.project
    assert project.common.hint.alias == "extract-1"
    assert project.input.filter.common.hint.alias == "filter-1"
    (declaration,) = [d for d in plan.extensions
                      if d.extension_function.name == AI_EXTRACT_NAME]
    assert declaration.extension_function.function_anchor == 19
    arguments = project.expressions[0].scalar_function.arguments
    assert arguments[0].value.literal.string == AUDIT_RIGHTS
    # the answer column is nullable: a contract may have no such clause
    assert project.expressions[0].scalar_function.output_type.string.nullability \
        == 1
    chained = quail_b.get_query("CUAD-10").info
    assert [operator.output for operator in chained.extracts] == [
        "agreement_date", "expiration_date", "governing_law"]
    assert chained.select == ("c.id", "c.agreement_date", "c.expiration_date",
                              "c.governing_law")
    relational = quail_b.get_query("CUAD-15").info
    assert relational.relational and relational.fetch.count == 20
    assert relational.extracts[0].output == "expiration_date"


def _spec(tree, select):
    return quail_b.QuerySpec("T", "test", build_plan(tree, select)
                             .SerializeToString())


def test_invalid_extract_uses_are_rejected():
    contracts = Scan("contracts", "c", "text")
    # an answer column must be selected
    with pytest.raises(ValueError, match="never used"):
        _spec(ExtractNode(contracts, GOVERNING_LAW, "law"), ("c",))
    # two answer columns cannot share a name
    with pytest.raises(ValueError, match="already a field"):
        _spec(ExtractNode(
            ExtractNode(contracts, GOVERNING_LAW, "law"), AUDIT_RIGHTS, "law"),
            ("c", "c.law"))
    # a relational query must select its answer column too
    with pytest.raises(ValueError, match="never used"):
        _spec(Fetch(Sort(ExtractNode(contracts, GOVERNING_LAW, "law"),
                         (("c.id", False),)), 5), ("c",))


def test_extract_prompt_text():
    text = rendering.render_extract_prompt(
        "When does the lease end?", "The lease ends on March 3.")
    expected = "".join([
        "DOCUMENT:\nThe lease ends on March 3.\n\n",
        "Question: When does the lease end?\n\n",
        "Copy the words that answer the question from the document. Copy them ",
        "exactly. Use the fewest words that answer it. If the document does not ",
        "answer the question, write none.\n",
        "Answer in this format:\n",
        'Phrase: "<copied words>"\n',
        'Phrase: "'])
    assert text == expected
    with pytest.raises(ValueError, match="placeholder"):
        rendering.render_extract_prompt("{0}?", "x")
    spec = predicates.PREDICATE_BY_KEY["quailb.cuad.contract.governing_law_span"]
    assert predicates.render_extract_prompt(spec, "x").startswith(
        "DOCUMENT:\nx\n\nQuestion: " + GOVERNING_LAW)
    with pytest.raises(ValueError, match="not an extraction"):
        predicates.render_extract_prompt(predicates.PREDICATES[0], "x")


def test_every_cuad_operator_has_one_predicate_with_its_own_identity():
    by_template = {}
    for spec in (*predicates.PREDICATES, *predicates.CLASSIFY_PREDICATES,
                 *predicates.EXTRACT_PREDICATES):
        assert spec.template not in by_template
        by_template[spec.template] = spec
    used = set()
    for query_id in CUAD_QUERIES:
        for operator in quail_b.get_query(query_id).info.ai_operators:
            spec = by_template[operator.prompt]
            assert spec.workload == "cuad"
            assert spec.source_policy == "cuad_annotation"
            if isinstance(operator, Extract):
                assert spec.kind == "extract"
                assert operator.prompt in prompts.CUAD_QUESTIONS.values()
            used.add(spec.key)
    cuad = {spec.key for spec in by_template.values() if spec.workload == "cuad"}
    assert used == cuad and len(cuad) == 18
    extract = predicates.PREDICATE_BY_KEY[
        "quailb.cuad.contract.liability_cap_span"]
    payload = predicates.predicate_payload(extract)
    assert payload["render"] == "extract_document_then_question_v1"
    assert payload["answer_cue"] == rendering.EXTRACT_CUE
    assert "task_instruction" not in payload
    # the lawyers' spans are the source, not a judge
    (source,) = predicates.label_sources(extract)
    assert source["spec"]["dataset"] == "theatticusproject/cuad"
    assert source["id"].startswith("s_")
    filter_spec = predicates.PREDICATE_BY_KEY[
        "quailb.cuad.contract.caps_liability"]
    assert (predicates.predicate_version(filter_spec)
            != predicates.predicate_version(extract))


@pytest.mark.parametrize("answer, references, expected", [
    ("New York", ["New York"], (1.0, 1.0)),
    ("the State of New York", ["New York"], (0.0, 2 / 3)),
    ("New York", ["Delaware", "the laws of New York"], (0.0, 2 / 3)),
    (None, [], (1.0, 1.0)),
    (None, ["New York"], (0.0, 0.0)),
    ("New York", [], (0.0, 0.0)),
    ("Texas", ["New York"], (0.0, 0.0)),
])
def test_span_scores_follow_the_squad_convention(answer, references, expected):
    assert span_scores(answer, references) == pytest.approx(expected)
    assert normalize_answer("The State of New York.") == ["state", "of", "new",
                                                          "york"]


CONTRACTS = pa.table({
    "id": ["c0", "c1", "c2"],
    "title": ["A", "B", "C"],
    "text": ["Audit yearly. New York law.", "No audit. Texas law.",
             "Audit at will."],
    "token_count": pa.array([6, 5, 4], pa.int32()),
})
TYPES = pa.table({"id": ["k0"], "name": ["Audit Rights"],
                  "question": ["Audit Rights: " + AUDIT_RIGHTS]})
AUDIT_FILTER = predicates.PREDICATE_BY_KEY["quailb.cuad.contract.audit_rights"]
AUDIT_SPAN = predicates.PREDICATE_BY_KEY[
    "quailb.cuad.contract.audit_rights_span"]
LABEL_SETS = [
    ({"kind": "filter", "template": AUDIT_FILTER.template,
      "left_table": "contracts", "left_column": "text"},
     [("c0", True), ("c1", False), ("c2", True)]),
    ({"kind": "extract", "template": AUDIT_SPAN.template,
      "left_table": "contracts", "left_column": "text"},
     [("c0", ["Audit yearly."]), ("c1", []), ("c2", ["Audit at will."])]),
]


def _cuad_7_output(wrong_answer=False):
    answers = pa.table({
        "c": ["c0", "c2"],
        "answer": pa.array(["Audit yearly" if not wrong_answer else "yearly",
                            None], pa.string()),
    })
    rows = pa.table({
        "c": ["c0", "c2"],
        "audit_rights": pa.array(
            ["Audit yearly" if not wrong_answer else "yearly", None],
            pa.string()),
    })
    return quail_b.RunOutput(
        {"filter-1": pa.table({"c": ["c0", "c1", "c2"],
                               "answer": [True, False, True]})},
        {}, rows, runtime_s=1.0, extract_answers={"extract-1": answers})


def test_extraction_run_scores_spans_and_rows(tmp_path):
    _collection(tmp_path, {"contracts": CONTRACTS, "clause_types": TYPES},
                LABEL_SETS)
    record = quail_b.run(
        lambda spec, tables: _cuad_7_output(), queries=["CUAD-7"],
        output_dir=tmp_path / "run", root=tmp_path)
    accuracy = record["queries"][0]["metrics"]["accuracy"]
    # "Audit yearly" matches its span up to punctuation; the null answer
    # of c2, which has a span, scores zero
    assert accuracy["span_accuracy"] == {
        "evaluated": 2, "exact": 1, "exact_match": 0.5, "token_f1": 0.5}
    assert accuracy["answer_accuracy"]["correct"] == 3
    # the rows are compared on ids alone; the answer column is scored
    # from the answer table
    assert accuracy["output_accuracy"]["predicted_rows"] == 2
    assert accuracy["output_accuracy"]["expected_rows"] == 2
    assert accuracy["output_accuracy"]["matching_rows"] == 2
    (span_item,) = [item for item in accuracy["per_predicate"]
                    if item["op"] == "extract"]
    assert span_item["predicate_key"] == "predicate_1"
    report = (tmp_path / "run" / "report.md").read_text()
    assert "| Span token F1 | Evaluated spans |" in report
    saved = json.loads((tmp_path / "run" / "run.json").read_text())
    assert saved["queries"][0]["files"]["extracts"][0]["key"] == "extract-1"
    quail_b.report(tmp_path / "run", root=tmp_path)
    rescored = json.loads((tmp_path / "run" / "run.json").read_text())
    assert rescored["queries"][0]["metrics"] == saved["queries"][0]["metrics"]
    # a returned answer must be the answer table's
    with pytest.raises(ValueError, match="differs from the engine"):
        output = _cuad_7_output()
        output.rows = output.rows.set_column(
            1, "audit_rights", pa.array(["x", None], pa.string()))
        quail_b.run(lambda spec, tables: output, queries=["CUAD-7"],
                    output_dir=tmp_path / "bad", root=tmp_path)


def test_extraction_token_metrics_count_the_question_as_a_suffix(tmp_path):
    _collection(tmp_path, {"contracts": CONTRACTS, "clause_types": TYPES},
                LABEL_SETS)
    output = _cuad_7_output()
    output.measurements = {"fresh_tokens": 40}
    output.prompt_pieces = {
        "tokenizer": "test", "preamble": [1],
        "filters": [{"id": "filter-1", "tail": [2, 3]}],
        "extracts": [{"id": "extract-1", "tail": [4, 5, 6]}]}
    from quail_b import minimum

    def load_tokenizer(name):
        # one id per word, distinct across documents, so no document
        # shares a prefix with another
        return lambda texts: [
            [1000 * (1 + sum(map(ord, text)) % 97) + position
             for position in range(len(text.split()))]
            for text in texts]

    original = minimum.load_tokenizer
    minimum.load_tokenizer = load_tokenizer
    try:
        record = quail_b.run(
            lambda spec, tables: output, queries=["CUAD-7"],
            output_dir=tmp_path / "run", root=tmp_path)
    finally:
        minimum.load_tokenizer = original
    metrics = record["queries"][0]["metrics"]
    # the shared preamble once, three documents of 5, 4, and 3 words,
    # the filter tail after every document, the extract tail after two
    assert metrics["minimum_tokens"] == 1 + (5 + 4 + 3) + 3 * 2 + 2 * 3
    assert metrics["input_tokens"] == (
        3 * (1 + 2) + (5 + 4 + 3) + 2 * (1 + 3) + (5 + 3))
    assert metrics["regret_tokens"] == 40 - metrics["minimum_tokens"]
    with pytest.raises(ValueError, match="missing extract operators"):
        minimum.validate_prompt_pieces(
            quail_b.get_query("CUAD-7"),
            {"tokenizer": "t", "filters": [{"id": "filter-1", "tail": []}]})
