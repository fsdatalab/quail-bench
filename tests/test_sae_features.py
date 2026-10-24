"""SAE feature queries: plans, prompts, predicates, validation, and tokens."""

import pyarrow as pa
import pytest

import quail_b
from quail_b import minimum, predicates, prompts, rendering
from quail_b.predicates import SAE_FEATURES_PREDICATES, predicate_version
from quail_b.queries import queries
from quail_b.run import _validate_output
from quail_b.substrait import SaeFeatures
from tools.make_substrait_plans import SaeFeatures as SaeFeaturesNode
from tools.make_substrait_plans import Scan, Struct, build_plan

FEATURE_QUERIES = ("IMDB-18", "IMDB-19", "BIO-7", "SUPPORT-7", "WRENCH-9")

FEATURE_LIST = pa.list_(
    pa.struct([("feature", pa.int32()), ("strength", pa.float32())]))


def _spec(tree, select):
    return quail_b.QuerySpec("T", "test", build_plan(tree, select)
                             .SerializeToString())


def test_sae_feature_queries_are_drafts_over_their_corpora():
    published = queries()
    drafts = queries(include_draft=True)
    assert all(query_id not in published for query_id in FEATURE_QUERIES)
    assert all(query_id in drafts for query_id in FEATURE_QUERIES)
    poolings = {query_id: [operator.pooling for operator in
                           drafts[query_id].info.sae_features]
                for query_id in FEATURE_QUERIES}
    assert poolings == {
        "IMDB-18": ["last"], "IMDB-19": ["max"], "BIO-7": ["max"],
        "SUPPORT-7": ["max", "max", "max"], "WRENCH-9": ["last"]}
    tables = {query_id: drafts[query_id].info.relations[0].table
              for query_id in FEATURE_QUERIES}
    assert tables == {
        "IMDB-18": "reviews", "IMDB-19": "reviews", "BIO-7": "reports",
        "SUPPORT-7": "support_traces", "WRENCH-9": "wrench_steps"}


def test_sae_features_plan_names_the_prompt_pooling_and_output():
    info = quail_b.get_query("IMDB-18").info
    (operator,) = info.sae_features
    assert operator == SaeFeatures("sae-features-1", "r",
                                   prompts.IMDB_TOPIC_FEATURES, "last",
                                   "features")
    assert info.select == ("r.id", "r.features")
    assert info.columns == ("r", "features")
    assert info.ai_operators == (operator,)
    # the two IMDB feature queries ask the same question with another pooling
    other = quail_b.get_query("IMDB-19").info.sae_features[0]
    assert other.prompt == operator.prompt
    assert other.pooling == "max"
    # three feature calls over the same conversations
    support = quail_b.get_query("SUPPORT-7").info
    assert [operator.output for operator in support.sae_features] == [
        "request_features", "policy_features", "tone_features"]
    assert support.select == ("t.id", "t.request_features",
                              "t.policy_features", "t.tone_features")


def test_invalid_sae_features_uses_are_rejected():
    reviews = Scan("reviews", "r", "body")
    question = prompts.IMDB_TOPIC_FEATURES
    with pytest.raises(ValueError, match="pooling must be one of"):
        _spec(SaeFeaturesNode(reviews, question, "mean", "features"),
              ("r", "r.features"))
    with pytest.raises(ValueError, match="never used"):
        _spec(SaeFeaturesNode(reviews, question, "last", "features"), ("r",))
    with pytest.raises(ValueError, match="already a field"):
        _spec(SaeFeaturesNode(
            SaeFeaturesNode(reviews, question, "last", "features"),
            question, "max", "features"), ("r", "r.features"))
    # a struct holds extraction answers, not feature lists
    with pytest.raises(ValueError, match="holds only answer columns"):
        _spec(SaeFeaturesNode(reviews, question, "last", "features"),
              ("r", Struct("review", ("r.features",))))


def test_sae_features_prompt_text():
    text = rendering.render_sae_features_prompt(
        prompts.IMDB_TOPIC_FEATURES, "Great soundtrack.")
    # the document comes first, so its prefix is query-independent; the
    # question follows with no instruction and no answer cue
    assert text == ("DOCUMENT:\nGreat soundtrack.\n\n"
                    "What is this review about?")
    with pytest.raises(ValueError, match="placeholders"):
        rendering.render_sae_features_prompt("no placeholder", "document")


def test_each_feature_call_has_one_predicate_with_the_pooling_in_identity():
    by_question = {(spec.template, spec.pooling): spec
                   for spec in SAE_FEATURES_PREDICATES}
    drafts = queries(include_draft=True)
    for query_id in FEATURE_QUERIES:
        for operator in drafts[query_id].info.sae_features:
            spec = by_question[(operator.prompt, operator.pooling)]
            assert spec.kind == "sae_features"
            assert spec.source_policy == "unpublished"
            assert spec.left_table == drafts[
                query_id].info.relations[0].table
    # the same question with another pooling is another predicate
    last, max_pooling = (by_question[(prompts.IMDB_TOPIC_FEATURES, pooling)]
                         for pooling in ("last", "max"))
    assert predicate_version(last) != predicate_version(max_pooling)
    with pytest.raises(ValueError, match="not defined"):
        predicates.label_sources(last)


def _imdb_18_output(rows=None, answers=None):
    values = [[{"feature": 7, "strength": 0.5}],
              [{"feature": 1, "strength": 2.0}, {"feature": 9, "strength": 1.0}]]
    if answers is None:
        answers = pa.table({"r": ["r0", "r1"],
                            "features": pa.array(values, FEATURE_LIST)})
    if rows is None:
        rows = pa.table({"r": ["r0", "r1"],
                         "features": pa.array(values, FEATURE_LIST)})
    return quail_b.RunOutput({}, {}, rows, runtime_s=1.0,
                             features_answers={"sae-features-1": answers})


def test_run_output_validation_checks_feature_lists():
    spec = quail_b.get_query("IMDB-18")
    tables = {"reviews": pa.table({"id": ["r0", "r1", "r2"],
                                   "body": ["a", "b", "c"]})}
    _validate_output(spec, _imdb_18_output(), tables)
    with pytest.raises(ValueError, match="unknown features operator"):
        output = _imdb_18_output()
        output.features_answers = {
            "sae-features-9": output.features_answers["sae-features-1"]}
        _validate_output(spec, output, tables)
    with pytest.raises(ValueError, match="struct<feature: int32"):
        wrong = pa.table({"r": ["r0", "r1"], "features": pa.array(
            [[{"feature": 7, "strength": 0.5}], []],
            pa.list_(pa.struct([("feature", pa.int32()),
                                ("strength", pa.float64())])))})
        _validate_output(spec, _imdb_18_output(rows=wrong, answers=wrong),
                         tables)
    with pytest.raises(ValueError, match="non-null lists"):
        null_list = pa.table({"r": ["r0", "r1"], "features": pa.array(
            [[{"feature": 7, "strength": 0.5}], None], FEATURE_LIST)})
        _validate_output(spec, _imdb_18_output(rows=null_list,
                                               answers=null_list), tables)
    with pytest.raises(ValueError, match="differs from the engine"):
        changed = pa.table({"r": ["r0", "r1"], "features": pa.array(
            [[{"feature": 7, "strength": 0.5}], []], FEATURE_LIST)})
        _validate_output(spec, _imdb_18_output(rows=changed), tables)


def test_token_metrics_count_a_feature_question_as_a_document_suffix(
        monkeypatch):
    spec = quail_b.get_query("SUPPORT-7")
    corpus_rows = {"support_traces": pa.table({
        "id": ["t0", "t1"],
        "transcript": ["alpha beta", "gamma delta epsilon"]})}
    ids = {"alpha beta": [11, 12], "gamma delta epsilon": [21, 22, 23]}
    monkeypatch.setattr(
        minimum, "load_tokenizer",
        lambda name: lambda texts: [ids[text] for text in texts])
    answers = pa.table({"t": ["t0", "t1"], "features": pa.array(
        [[], []], FEATURE_LIST)})
    output = quail_b.RunOutput(
        {}, {}, None, runtime_s=1.0, measurements={"fresh_tokens": 20},
        prompt_pieces={
            "tokenizer": "test", "preamble": [1],
            "features": [{"id": "sae-features-1", "tail": [4, 5]},
                         {"id": "sae-features-2", "tail": [6]},
                         {"id": "sae-features-3", "tail": [7, 8, 9]}]},
        features_answers={f"sae-features-{i}": answers for i in (1, 2, 3)})
    metrics = minimum.token_metrics(spec, output, corpus_rows)
    # the shared preamble once, two documents of 2 and 3 tokens, and the
    # three question tails once per document: the document is computed
    # once although three prompts read it
    assert metrics["minimum_tokens"] == 1 + (2 + 3) + 2 * (2 + 1 + 3)
    # full prompts: each call reads both documents after the preamble
    assert metrics["input_tokens"] == (
        2 * (1 + 2) + 5) + (2 * (1 + 1) + 5) + (2 * (1 + 3) + 5)
    assert metrics["regret_tokens"] == 20 - metrics["minimum_tokens"]
    with pytest.raises(ValueError, match="missing features operators"):
        minimum.validate_prompt_pieces(
            spec, {"tokenizer": "t", "features": [
                {"id": "sae-features-1", "tail": []}]})
    with pytest.raises(ValueError, match="need features answers"):
        bare = quail_b.RunOutput(
            {}, {}, None, runtime_s=1.0,
            measurements={"fresh_tokens": 20},
            prompt_pieces=output.prompt_pieces)
        minimum.token_metrics(spec, bare, corpus_rows)
