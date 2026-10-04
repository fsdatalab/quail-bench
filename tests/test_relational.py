"""CPU checks for the relational queries: plans, reference results, and scoring."""

import pyarrow as pa
import pytest

import quail_b
from quail_b import prompts, substrait
from quail_b.labels import GroundTruthCollection, PredicateLabels
from quail_b.queries import get_query
from quail_b.relational import (
    apply_tail,
    expected_result,
    reference_table,
    relational_accuracy,
    score_fields,
)
from quail_b.run import _query_hash, _validate_output
from quail_b.scoring import evaluate, output_columns
from tools.make_substrait_plans import Fetch, Scan, Score, Sort, build_plan

TRACES = pa.table({
    "id": [f"s{i}" for i in range(8)],
    "trace": [f"trace {i}" for i in range(8)],
    "trajectory_id": ["A", "A", "A", "B", "B", "C", "C", "C"],
    "turn_index": [5, 10, 15, 10, 20, 5, 10, 15],
    "token_count": [2000, 4000, 9000, 5000, 7000, 1000, 3000, 8000],
})
RECOVERED = [True, False, True, True, True, False, True, False]
FIX = [True, True, False, True, True, True, True, True]
PROGRESS = ["located the relevant code", "changed the code, check passes",
            "changed the code, check fails", "changed the code, not checked",
            "changed the code, check passes", "has not located the relevant code",
            "changed the code, check passes", "changed the code, check fails"]
TEST_RESULT = ["bug still occurs", "bug fixed, tests pass", "bug still occurs",
               "run errored", "bug fixed, tests pass", "bug still occurs",
               "bug fixed, tests pass", "bug still occurs"]


def _labels(key, template, values, kind="filter", labels=()):
    predicate = {"template": template, "kind": kind}
    if kind == "classify":
        predicate["labels"] = list(labels)
    return PredicateLabels(key, f"ls_{key}", predicate, {
        (row_id, None): value for row_id, value in zip(TRACES["id"].to_pylist(),
                                                       values)
    }, {"test": len(values)})


@pytest.fixture()
def ground_truth():
    return GroundTruthCollection("gt_test", "c_test", 0.1, None, {
        "recovered": _labels("recovered", prompts.AGENT_RECOVERED, RECOVERED),
        "fix": _labels("fix", prompts.AGENT_IMPLEMENTED_FIX, FIX),
        "progress": _labels("progress", prompts.AGENT_PROGRESS, PROGRESS,
                            "classify", prompts.AGENT_PROGRESS_LABELS),
        "test_result": _labels("test_result", prompts.AGENT_TEST_RESULT,
                               TEST_RESULT, "classify",
                               prompts.AGENT_TEST_RESULT_LABELS),
    })


CORPUS = {"agent_traces": TRACES}


def test_relational_plans_read_back_their_steps():
    spec = get_query("REL-AGENT-6")
    info = spec.info
    assert [type(step) for step in info.tail] == [
        substrait.Aggregate, substrait.Having, substrait.Sort, substrait.Fetch]
    assert info.aggregate.keys == ("t.trajectory_id",)
    assert info.aggregate.measures == (
        ("fixes", "count", None), ("first_fix", "min", "t.turn_index"),
        ("longest", "max", "t.token_count"))
    assert info.having.tests == (("fixes", ">=", 2),)
    assert info.sort.keys == (("first_fix", False), ("t.trajectory_id", False))
    assert (info.fetch.offset, info.fetch.count) == (0, 50)
    assert output_columns(spec) == ["trajectory_id", "fixes", "first_fix",
                                    "longest"]
    tests = get_query("REL-AGENT-1").info.column_tests
    assert [(t.column, t.comparison, t.value) for t in tests] == [
        ("turn_index", ">=", 10), ("token_count", "<=", 6000)]
    score = get_query("REL-AGENT-4").info.scores[0]
    assert (score.relation, score.output, score.prompt) == (
        "t", "recovered_score", prompts.AGENT_RECOVERED)
    assert score_fields(get_query("REL-AGENT-7")) == {
        "t.fix_score", "mean_fix_score"}
    assert get_query("REL-AGENT-5").info.aggregate.measures[1] == (
        "trajectories", "count_distinct", "t.trajectory_id")
    # the published hashes of the other queries do not change
    assert _query_hash(get_query("IMDB-2")) == (
        "f3b93b898b0d631fb451046b072920cb81f12d5aabb8dc1b853f913030b4f45e")
    assert _query_hash(get_query("REL-AGENT-6")) != _query_hash(
        get_query("REL-AGENT-3"))


def test_reference_results_apply_tests_labels_and_the_tail(ground_truth):
    rows = lambda query_id: expected_result(  # noqa: E731
        get_query(query_id), ground_truth, CORPUS).to_pydict()
    # turn >= 10 and tokens <= 6000 keep s1, s3, and s6; s3 and s6 recovered
    assert rows("REL-AGENT-1") == {"t": ["s3", "s6"]}
    # recovered, shortest first: s6 3000, s3 5000, s4 7000, s2 9000, s0 2000
    table = reference_table(get_query("REL-AGENT-2"), ground_truth, CORPUS)
    assert sorted(table["t.id"]) == ["s0", "s2", "s3", "s4", "s6"]
    ordered = apply_tail(table, get_query("REL-AGENT-2"), fetch=False)
    assert list(ordered["t.id"]) == ["s0", "s6", "s3", "s4", "s2"]
    assert rows("REL-AGENT-2") == {"t": [], "token_count": []}
    assert rows("REL-AGENT-3") == {"trajectory_id": ["A", "B", "C"]}
    assert rows("REL-AGENT-5") == {"test_result": [], "n": [], "trajectories": []}
    # fixes per trajectory: A 2 (first 5, longest 4000), B 2 (10, 7000),
    # C 3 (5, 8000); earliest first then by trajectory
    assert rows("REL-AGENT-6") == {
        "trajectory_id": ["A", "C", "B"], "fixes": [2, 3, 2],
        "first_fix": [5, 5, 10], "longest": [4000, 8000, 7000]}


def test_accuracy_compares_rows_and_ranks_scored_rows_by_eligibility(
        ground_truth):
    spec = get_query("REL-AGENT-6")
    expected = expected_result(spec, ground_truth, CORPUS)
    exact = relational_accuracy(spec, expected, ground_truth, CORPUS)
    assert exact["exact_match"] and exact["ordered_match"]
    shuffled = expected.take([2, 0, 1])
    result = relational_accuracy(spec, shuffled, ground_truth, CORPUS)
    assert result["exact_match"] and not result["ordered_match"]
    wrong = pa.table({"trajectory_id": ["A", "C"], "fixes": [2, 3],
                      "first_fix": [5, 6], "longest": [4000, 8000]})
    result = relational_accuracy(spec, wrong, ground_truth, CORPUS)
    assert (result["matching_rows"], result["predicted_rows"],
            result["expected_rows"]) == (1, 2, 3)

    # the 20 highest recovered scores over eight snapshots: every
    # snapshot ranks at least as well as the twentieth, so all are eligible
    spec = get_query("REL-AGENT-4")
    returned = pa.table({"t": ["s3", "s4", "s1"],
                         "recovered_score": [0.9, 0.8, 0.7]})
    result = relational_accuracy(spec, returned, ground_truth, CORPUS)
    assert (result["eligible_rows"], result["precision_at_k"]) == (8, 1.0)
    # with a fetch of two, the recovered snapshots are eligible, since
    # the reference scores tie at 1.0, and the others are not
    traces = Scan("agent_traces", "t", "trace",
                  ("trajectory_id", "turn_index", "token_count"),
                  integers=("turn_index", "token_count"))
    top2 = quail_b.QuerySpec.from_plan("TEST-1", "top two", build_plan(
        Fetch(Sort(Score(traces, prompts.AGENT_RECOVERED, "s"),
                   (("t.s", True),)), 2),
        select=("t", "t.s")))
    result = relational_accuracy(top2, returned.rename_columns(["t", "s"]),
                                 ground_truth, CORPUS)
    assert result == {"predicted_rows": 3, "k": 2, "eligible_rows": 5,
                      "matching_rows": 2, "precision_at_k": round(2 / 3, 6),
                      "exact_match": False}

    # mean fix score per trajectory with at least five snapshots: none
    # here, so nothing is eligible and a returned row is wrong
    spec = get_query("REL-AGENT-7")
    returned = pa.table({"trajectory_id": ["A"], "mean_fix_score": [0.5],
                         "n": [3]})
    result = relational_accuracy(spec, returned, ground_truth, CORPUS)
    assert (result["eligible_rows"], result["precision_at_k"]) == (0, 0.0)


def test_relational_runs_are_validated_and_scored(ground_truth):
    spec = get_query("REL-AGENT-1")
    output = quail_b.RunOutput(
        {"filter-1": pa.table({"t": ["s1", "s3", "s6"],
                               "answer": [False, True, True]})},
        {}, pa.table({"t": ["s3", "s6"]}), runtime_s=1.0)
    _validate_output(spec, output, CORPUS)
    scores = evaluate(spec, output, ground_truth, CORPUS)
    assert scores["output_accuracy"]["exact_match"]
    assert scores["answer_accuracy"]["accuracy"] == 1.0
    with pytest.raises(ValueError, match="selected fields"):
        _validate_output(spec, quail_b.RunOutput(
            {}, {}, pa.table({"t": ["s3"], "extra": [1]}), runtime_s=1.0),
            CORPUS)
    with pytest.raises(ValueError, match="fetch of 10"):
        _validate_output(get_query("REL-AGENT-2"), quail_b.RunOutput(
            {}, {}, pa.table({"t": [f"s{i}" for i in range(11)] * 1,
                              "token_count": list(range(11))}),
            runtime_s=1.0), CORPUS)
    with pytest.raises(ValueError, match="include its rows"):
        _validate_output(spec, quail_b.RunOutput({}, {}, None, runtime_s=1.0),
                         CORPUS)


def _published_inputs(root):
    """Write the trace corpus and recovered labels in the published layout."""
    import pyarrow.parquet as pq

    from quail_b.data import (
        DATA_SEED,
        GROUND_TRUTH_ROOT,
        PUBLISHED_CORPORA,
        SOURCE_REVISIONS,
        corpus_identity,
    )
    from quail_b.run import _write_json

    corpus_id = PUBLISHED_CORPORA[0.1]
    corpus = root / GROUND_TRUTH_ROOT / "corpora" / corpus_id
    corpus.mkdir(parents=True)
    manifest = corpus_identity(CORPUS, 0.1, DATA_SEED, SOURCE_REVISIONS)
    manifest["corpus_id"] = corpus_id
    _write_json(corpus / "manifest.json", manifest)
    pq.write_table(TRACES, corpus / "agent_traces.parquet")
    key, label_id = "recovered", "ls_recovered"
    directory = root / GROUND_TRUTH_ROOT / "label_sets" / key / label_id
    directory.mkdir(parents=True)
    _write_json(directory / "manifest.json", {
        "status": "complete", "rows": len(RECOVERED),
        "source_rows": {"test": len(RECOVERED)},
        "predicate": {
            "key": key, "template": prompts.AGENT_RECOVERED, "kind": "filter",
            "left_table": "agent_traces", "left_column": "trace",
            "right_table": None, "right_column": None,
        },
    })
    pq.write_table(pa.table({
        "predicate_key": [key] * len(RECOVERED),
        "label_set_id": [label_id] * len(RECOVERED),
        "left_id": TRACES["id"], "right_id": [None] * len(RECOVERED),
        "answer": RECOVERED,
    }), directory / "labels.parquet")
    collection = root / GROUND_TRUTH_ROOT / "collections" / "gt_test"
    collection.mkdir(parents=True)
    _write_json(collection / "manifest.json", {
        "status": "complete", "collection_id": "gt_test",
        "corpus_id": corpus_id, "scale_factor": 0.1,
        "label_sets": {key: label_id}, "summary": {"model": "test"},
    })
    _write_json(corpus / "active_collection.json", {"collection_id": "gt_test"})


def test_relational_runs_rescore_from_their_saved_rows(tmp_path):
    import json

    def execute(spec, tables):
        return quail_b.RunOutput(
            {"filter-1": pa.table({"t": ["s1", "s3", "s6"],
                                   "answer": [False, True, True]})},
            {}, pa.table({"t": ["s3", "s6"]}), runtime_s=1.0)

    _published_inputs(tmp_path)
    destination = tmp_path / "run"
    record = quail_b.run(execute, queries=["REL-AGENT-1"],
                         output_dir=destination, root=tmp_path)
    metrics = record["queries"][0]["metrics"]
    assert metrics["accuracy"]["output_accuracy"]["exact_match"]
    quail_b.report(destination, rescore=True, root=tmp_path)
    rescored = json.loads((destination / "run.json").read_text())
    assert rescored["queries"][0]["status"] == "complete"
    assert rescored["queries"][0]["metrics"] == metrics
