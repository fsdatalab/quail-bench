# QUAIL-B

QUAIL-B is a benchmark for relational queries whose predicates are evaluated
by a language model. The workload contains 33 queries over five document
datasets. Queries use two AI operations: a unary AI filter over one document
and a binary AI join over a pair of documents. One query also uses an ordinary
equality predicate.

This repository publishes the query plans, input tables, reference answers,
and scoring code. It does not include a query engine.

## Contents

- [Benchmark definition](#benchmark-definition)
- [Workload](#workload)
  - [AI operations](#ai-operations)
  - [Query plans](#query-plans)
- [Data](#data)
  - [Scale factors](#scale-factors)
  - [Reference labels](#reference-labels)
  - [Loading data](#loading-data)
  - [Memory requirements](#memory-requirements)
- [Installation](#installation)
- [Running the benchmark](#running-the-benchmark)
  - [Adapter input](#adapter-input)
  - [Adapter output](#adapter-output)
  - [Run protocol](#run-protocol)
- [Metrics](#metrics)
  - [Token accounting](#token-accounting)
- [Results](#results)
- [Reproducibility](#reproducibility)
- [Repository layout](#repository-layout)

## Benchmark definition

A QUAIL-B run is defined by:

1. one or more query IDs;
2. a scale factor (`0.1`, `0.5`, or `1.0`);
3. the corpus ID for that scale factor;
4. a reference-label collection tied to that corpus; and
5. an engine adapter that executes each query.

The default workload has 33 queries:

| Dataset | Queries | Input tables | Main query structures |
| --- | ---: | --- | --- |
| IMDB | 10 | `reviews`, `aspects` | filter chains, joins, repeated aliases |
| BioDEX | 3 | `reports`, `terms` | filters and joins over long reports |
| FEVER | 10 | `claims`, `evidence` | two-sided filters and multi-stage joins |
| LePaRD | 8 | `citation_contexts`, `citation_passages` | deep filter chains and joins |
| SWE-Next | 2 | `agent_traces` | filters over agent trace snapshots |

Two PrivacyPolicies queries are defined separately and are not part of the
default workload.

Official evaluated-model configurations use `Qwen/Qwen3-4B-FP8` or
`Qwen/Qwen3-32B-FP8`. Each model copy runs on one H100; tensor-parallel weight
sharding is outside the benchmark scope. Every reported run must identify the
model and tokenizer repositories and revisions, quantization format, inference
dtype, engine version, and GPU type. Results that differ in any of these
values are different benchmark configurations.

## Workload

### AI operations

An AI filter evaluates a boolean prompt over one document:

```text
ai_filter(prompt, document) -> boolean
```

A document is retained when the answer is `TRUE`.

An AI join evaluates a boolean prompt over two documents:

```text
ai_join(prompt, left_document, right_document) -> boolean
```

A pair is retained when the answer is `TRUE`. A join may also contain an
ordinary equality condition.

The workload includes:

- filter-only queries with one to five filter stages;
- one-stage joins with zero or more filters on either input;
- two- and three-stage joins;
- repeated uses of the same input table under different aliases; and
- one join that combines an AI predicate with an equality predicate.

The [query-plan figure](figures/quailb_anatomy.pdf) shows all 33 default
queries. Percentages in the figure are fixed planning selectivity estimates
from the scale-0.1 reference labels. The same estimates are used at every
scale factor.

### Query plans

Each query is a Substrait 0.103 plan. The checked-in ProtoJSON plans under
[`quail_b/plans/`](quail_b/plans/) are the benchmark query definitions.
`QuerySpec.plan` returns the parsed protobuf plan, and `QuerySpec.plan_bytes`
returns its deterministic binary serialization.

The plans use these Substrait relations:

| Operation | Substrait representation |
| --- | --- |
| Read a table | `ReadRel` with a `NamedTable` |
| AI filter | `FilterRel` calling `ai_filter` |
| AI join | inner `JoinRel` calling `ai_join` |
| Equality condition | `equal`, combined with `and` when needed |
| Output columns | `ProjectRel` and `RelRoot` |

The AI functions are declared in
[`quail_b/substrait_extensions.yaml`](quail_b/substrait_extensions.yaml)
under the URN `extension:org.fsdatalab.quail_b:functions_ai`. Equality and
conjunction use the standard Substrait comparison and boolean extension URNs.

Relation aliases and operator IDs use `RelCommon.hint.alias`. A `ReadRel`
stores a relation alias such as `r`. A `FilterRel` or `JoinRel` stores an
operator ID such as `filter-1` or `join-1`. Operator IDs are stable keys for
the predicate-answer tables returned by an adapter.

For example, IMDB-4 represents:

```sql
-- Pseudocode; AI SQL syntax differs between engines.
SELECT r.id, a.id
FROM reviews AS r
AI JOIN aspects AS a ON J1(r.body, a.aspect)
WHERE AI_FILTER(F1, r.body)
  AND AI_FILTER(F4, r.body);
```

Its Substrait relation tree is:

```text
RelRoot [r, a]
└── ProjectRel [r.id, a.id]
    └── JoinRel INNER [ai_join(J1, r.body, a.aspect)]  join-1
        ├── FilterRel [ai_filter(F4, r.body)]           filter-2
        │   └── FilterRel [ai_filter(F1, r.body)]       filter-1
        │       └── ReadRel reviews [id, body]          r
        └── ReadRel aspects [id, aspect]                a
```

`F1`, `F4`, and `J1` are full prompt literals in the plan. The same strings
are defined in [`quail_b/prompts.py`](quail_b/prompts.py).

The plans are generated by
[`tools/make_substrait_plans.py`](tools/make_substrait_plans.py). A test
regenerates all plans and compares them with the checked-in files.

## Data

### Scale factors

QUAIL-B publishes fixed corpora at scale factors `0.1`, `0.5`, and `1.0`.
These are 10%, 50%, and 100% of a benchmark-specific sampling target, not a
fraction of every row in the upstream source. A scale factor selects different
input tables and reference answers; it does not change the queries or prompts.

The fraction applies to each dataset's primary sampling unit: IMDB reviews,
BioDEX reports, FEVER claims, LePaRD positive citation pairs, and SWE-Next
trace snapshots. Fixed or derived tables do not necessarily scale linearly.
For example, every IMDB corpus contains the same 12 aspects.

The full targets are 50,000 IMDB reviews, 5,000 BioDEX reports, 5,000 FEVER
claims, 5,000 LePaRD positive citation pairs, and up to 17,718 eligible
SWE-Next trace snapshots. The published SWE-Next source yields 17,711 eligible
snapshots. Corpus identity is determined by the corpus ID and table hashes,
not by the scale-factor number alone.

| Dataset | Table | 0.1 rows | 0.5 rows | 1.0 rows |
| --- | --- | ---: | ---: | ---: |
| IMDB | `reviews` | 5,000 | 25,000 | 50,000 |
| IMDB | `aspects` | 12 | 12 | 12 |
| BioDEX | `reports` | 500 | 2,500 | 5,000 |
| BioDEX | `terms` | 1,127 | 2,934 | 4,144 |
| FEVER | `claims` | 500 | 2,500 | 5,000 |
| FEVER | `evidence` | 287 | 1,037 | 1,478 |
| LePaRD | `citation_contexts` | 500 | 2,496 | 4,972 |
| LePaRD | `citation_passages` | 433 | 1,756 | 2,991 |
| SWE-Next | `agent_traces` | 1,772 | 8,859 | 17,711 |

Each corpus was built from pinned source revisions with a fixed sampling seed.
Its manifest records an exact corpus ID and hashes of the input tables.

### Reference labels

The model-based reference labels use:

| Setting | Value |
| --- | --- |
| Model | `Qwen/Qwen3-32B-FP8` |
| Model revision | `aa55da1ecc13d006e8b8e4f54579b1ea8c3db2df` |
| Tokenizer revision | the same revision |
| Temperature | `0.0` |
| Output length | one token |
| Random seed | `20260818` |
| Maximum model length | 32,768 tokens |
| Answer | one-token choice between `TRUE` and `FALSE` |

At the token position after `ANSWER:`, the labeling pass compares the token
IDs returned by `quail_b.rendering.true_false_ids(tokenizer)`. These are the
first tokens of `TRUE`, ` TRUE`, `True`, and ` True`, and the corresponding
`FALSE` forms, with special tokens disabled. Candidate aggregation and tie
handling are implemented in Quail's `quail/bench/labeling.py`, not this
repository. This repository therefore cannot regenerate the model labels
independently. Published labels are the reference values used for scoring.

The exact text sent for each predicate is produced by
[`quail_b/rendering.py`](quail_b/rendering.py). Filter prompts place the
document before the question. Join prompts place the anchor document first,
then the question and partner document. Both end with `ANSWER:`. An engine
must evaluate this rendered text and the same `TRUE`/`FALSE` decision.

FEVER's source rule applies to a claim paired with the Wikipedia page named by
its `evidence_wiki_url`: `SUPPORTS` maps to `TRUE` and `REFUTES` maps to
`FALSE`. The LePaRD citation rule is `TRUE` when the context's
`cited_passage_ids` intersects the candidate's `passage_ids`. The labeling
pass combines these source rules with model judgments according to the
predicate source policy. That combining code also lives in
`quail/bench/labeling.py`. The sources used for each published label set are
recorded in its manifest.

Reference labels are fixed benchmark data. Their identities hash the corpus,
predicate text and rendering, model revision, decoding settings, and derived
source identities. They do not hash Quail's label-combination implementation.

### Loading data

Reading a query plan does not download data:

```python
import quail_b

query = quail_b.get_query("IMDB-4")
plan = query.plan
```

Load the tables required by one query:

```python
benchmark = quail_b.load_benchmark(
    "IMDB-4",
    scale_factor=0.1,
    accuracy=False,
)

reviews = benchmark.tables["reviews"]
aspects = benchmark.tables["aspects"]
print(benchmark.corpus_id)
```

`accuracy=False` skips the reference labels. Without it,
`benchmark.ground_truth` contains the selected reference collection.

Load one table directly:

```python
reviews = quail_b.load_table("reviews", scale_factor=0.1)
```

By default, data is read anonymously from `s3://quail-bench`. Immutable
Parquet files and manifests are cached in `~/.cache/quail-b`, or in
`$XDG_CACHE_HOME/quail-b` when that variable is set.

For direct `load_table` and `load_benchmark` calls, set
`QUAIL_B_CACHE_DIR` to change the cache. `quail_b.run` also accepts
`cache_dir=`. Use `data_dir=` to supply local input Parquet files. Local
tables are checked against the published corpus manifest before a run starts.
Use `root=` to select a local mirror of both inputs and reference labels.

### Memory requirements

The current loader holds the selected input tables and the full reference
collection in host memory. Selecting one query does not reduce the reference
collection loaded for its scale factor.

| Scale factor | Reference answers | Loader peak RAM | Recommended host RAM |
| ---: | ---: | ---: | ---: |
| 0.1 | 1.21 million | 0.84 GiB measured | at least 2 GiB |
| 0.5 | 17.62 million | 10–12 GiB estimated | at least 16 GiB |
| 1.0 | 51.80 million | 30–35 GiB estimated | at least 48 GiB |

The estimates for `0.5` and `1.0` scale the measured `0.1` memory cost by the
published answer counts. They exclude the engine, model, and result tables.

## Installation

QUAIL-B requires Python 3.12.

```sh
uv add "quail-b @ git+https://github.com/fsdatalab/quail-bench.git"
```

For development:

```sh
git clone https://github.com/fsdatalab/quail-bench.git
cd quail-bench
uv sync
```

## Running the benchmark

An engine provides one function:

```python
run_query(query: QuerySpec, tables: dict[str, pyarrow.Table]) -> RunOutput
```

QUAIL-B calls this function once for each selected query.

### Adapter input

`query` contains:

- `id`: the benchmark query ID;
- `description`: a short description; and
- `plan`: the parsed Substrait plan.

`tables` maps every `NamedTable` used by the plan to a PyArrow table. Every
input table has an `id` column. Text and auxiliary columns are named by the
`ReadRel` schema.

Every field referenced by a benchmark plan is declared
`NULLABILITY_REQUIRED`, and published tables contain non-null values for those
fields. The workload does not exercise SQL three-valued logic. The ordinary
equality condition in FEV-10 compares two non-null strings.

An adapter resolves function anchors from the plan's extension declarations,
walks the relation tree, and translates it to the engine's query
representation. Filters are `FilterRel` nodes over their inputs; they are not
properties of a table or alias.

### Adapter output

The adapter returns `quail_b.RunOutput`:

| Field | Runner requirement | Contents |
| --- | --- | --- |
| `filter_answers` | argument required; value may be `None` | filter operator ID to evaluated documents and answers |
| `join_answers` | argument required; value may be `None` | join operator ID to evaluated pairs and answers |
| `rows` | required | final result, with one ID column per selected alias |
| `runtime_s` | required | query execution time in seconds |
| `measurements` | optional | engine measurements, including `fresh_tokens` |
| `prompt_pieces` | optional | tokenized prompt structure used for token accounting |

A filter answer table contains one relation-alias column and one non-null
boolean `answer` column. Each document ID occurs at most once. A join answer
table contains two relation-alias columns and `answer`. Each pair occurs at
most once, although one document ID may occur in many pairs. All IDs must be
non-null and present in the corresponding input table.

A complete trace has dictionary keys for every filter and join operator in the
plan. Use an empty dictionary when a query has no operator of that kind. Each
table contains every document or pair for which that operator produced an
answer, including `FALSE`. The runner validates supplied rows but cannot
detect an omitted evaluation; a trace with omissions is not a complete
benchmark result.

Final rows have set semantics. Row order does not matter. Every row must
contain one non-null corpus ID for each selected relation alias. Duplicate
final rows are invalid.

For IMDB-4:

```python
import pyarrow as pa
import quail_b


def run_query(query, tables):
    result = execute_with_your_engine(query.plan, tables)

    return quail_b.RunOutput(
        filter_answers={
            "filter-1": pa.table({
                "r": result.f1_ids,
                "answer": result.f1_answers,
            }),
            "filter-2": pa.table({
                "r": result.f4_ids,
                "answer": result.f4_answers,
            }),
        },
        join_answers={
            "join-1": pa.table({
                "r": result.join_review_ids,
                "a": result.join_aspect_ids,
                "answer": result.join_answers,
            }),
        },
        rows=pa.table({
            "r": result.output_review_ids,
            "a": result.output_aspect_ids,
        }),
        runtime_s=result.query_seconds,
        measurements={"fresh_tokens": result.fresh_tokens},
        prompt_pieces=result.prompt_pieces,
    )
```

`execute_with_your_engine` is supplied by the adapter. The complete
`filter_answers` or `join_answers` dictionary may be `None`; values inside a
supplied answer table may not be null. When either dictionary is absent,
QUAIL-B still scores final output precision and recall, but predicate
accuracy and some token metrics are unavailable.

Start `runtime_s` immediately before the first query-specific call into the
engine. The interval includes plan compilation, tokenization, request
construction, submission, queueing, model execution, and relational
processing. Stop after all operators have completed, the asynchronous backend
has synchronized, and final ID rows are materialized in the engine's result
object. Exclude model loading, warmup, corpus loading performed before the
callback, and conversion or transfer of the completed engine result into
`RunOutput`. The generated report calls this last step “result collection.”
Also exclude QUAIL-B validation, scoring, and file writes.

### Run protocol

Pass the adapter to `quail_b.run`:

```python
run_metadata = {
    "engine": "my-engine",
    "engine_version": "version or commit",
    "model": "Qwen/Qwen3-4B-FP8",
    "model_revision": "immutable model revision",
    "tokenizer": "Qwen/Qwen3-4B-FP8",
    "tokenizer_revision": "immutable tokenizer revision",
    "quantization": "fp8",
    "inference_dtype": "engine dtype",
    "gpu": "H100",
    "warmup": "procedure used",
    "initial_cache": "cleared or retained state",
    "price_provider": "Modal",
    "price_retrieved": "2026-09-13",
}

quail_b.run(
    run_query,
    queries=["IMDB-4"],
    scale_factor=0.1,
    output_dir="results/my-run",
    metadata=run_metadata,
    gpu_count=1,
    gpu_hourly_rate_usd=3.9492,
)
```

For each query, the runner:

1. loads and validates the selected corpus and reference collection;
2. passes the required Arrow tables and `QuerySpec` to the adapter;
3. saves the adapter output;
4. validates IDs, answer schemas, and final rows;
5. scores the output against the reference labels; and
6. writes the run record, report, and measurements table.

The output directory must not already exist. A failed execution or scoring
step is recorded in `run.json` before the exception is raised.

Omit `queries=` to run all 33 default queries:

```python
quail_b.run(
    run_query,
    scale_factor=0.1,
    output_dir="results/full-0.1",
    metadata=run_metadata,
)
```

The current `RunOutput` constructor requires both answer-dictionary
arguments. A minimal runner-valid result is:

```python
quail_b.RunOutput(
    filter_answers=None,
    join_answers=None,
    rows=rows,
    runtime_s=runtime_s,
)
```

A complete benchmark result instead supplies all filter and join answer
tables, `measurements["fresh_tokens"]`, and `prompt_pieces`, because the
reported predicate-accuracy and token metrics require them.

## Metrics

| Metric | Definition |
| --- | --- |
| Query time | adapter-reported execution time; excludes model startup, warmup, conversion to `RunOutput`, scoring, and saving |
| Filter throughput | input document rows divided by query time |
| Join throughput | evaluated document pairs across all join stages divided by query time |
| GPU cost | query time in hours × GPU count × price per GPU-hour |
| Predicate accuracy | agreement with reference labels on evaluated predicate answers |
| Output precision and recall | agreement between returned and reference final rows |
| Input rows | rows in each input relation before filters; repeated aliases count separately |
| Fresh tokens | input token positions processed by model forward passes |
| Minimum tokens | distinct prefix positions required when all reusable KV is retained |
| Recomputed KV tokens | `fresh_tokens - minimum_tokens` |

Predicate accuracy and output accuracy measure different results. Two engines
may evaluate different intermediate documents or pairs, so their predicate
accuracy denominators can differ. Output precision and recall compare final
rows and expose false positive and false negative results. These accuracy
metrics measure agreement with the published reference collection; they are
not estimates of agreement with independent human judgments.

Let \(R\) be the distinct returned rows, \(G\) the distinct reference rows,
and \(M = R \cap G\). Output precision is \(|M| / |R|\), and recall is
\(|M| / |G|\). A zero denominator produces `0.0`, except that precision and
recall are both `1.0` when both sets are empty. Predicate accuracy is the
number of correct evaluated answers divided by the number evaluated. It is
unavailable when the adapter supplies no predicate answers. Saved accuracy
values are rounded to six decimal places.

When all join answer tables are supplied, the evaluated-pair count is the sum
of their row counts. This value takes precedence over
`measurements["evaluated_document_pairs"]`. If the answer tables are absent or
incomplete, the runner uses that measurement instead. It counts pairs for
which the AI join produced an answer, including `FALSE` answers.

`gpu_hourly_rate_usd` is supplied by the caller; the package has no default
price. The example rate of `$3.9492` per H100-hour is from
[Modal's pricing page](https://modal.com/pricing), accessed 2026-09-13. A
reported cost must state its provider, retrieval date, and hourly rate.

### Token accounting

`fresh_tokens` is reported by the engine. Repeated computation counts again.
It includes new prompt tokens, document tokens, and recomputed prefix tokens.

QUAIL-B computes `minimum_tokens` from the requests the engine actually made.
The adapter supplies the tokenizer and the token IDs placed around each
document:

```python
prompt_pieces = {
    "tokenizer": "Qwen/Qwen3-4B-FP8",
    "preamble": [...],
    "filters": [
        {"id": "filter-1", "tail": [...]},
    ],
    "joins": [
        {
            "id": "join-1",
            "anchor": "r",
            "frame": [...],
            "label": [...],
            "tail": [...],
        },
    ],
}
```

All values shown as `[...]` are lists of token IDs. A filter request is
`preamble + document + tail`. A join request is
`preamble + anchor document + frame + label + partner document + tail`.

The current saved format passes `prompt_pieces["tokenizer"]` to
`AutoTokenizer.from_pretrained` and does not store a tokenizer revision in
that field. Record the tokenizer repository and immutable revision in run
metadata. Rescoring token metrics is reproducible only when it resolves the
same tokenizer files.

To reconstruct the requests, QUAIL-B combines `prompt_pieces` with the
document IDs in the filter and join answer tables, reads those documents from
the selected corpus, and tokenizes them with the named tokenizer. Only
requests represented in the answer tables are counted.

`minimum_tokens` is the number of distinct positions in the prefix trie of
these requests. It assumes exact prefix matching and enough KV capacity to
retain every reusable prefix for the full query. It does not model a finite
cache, scheduler constraints, or model computation after the boolean answer.
`regret_tokens`, reported as recomputed KV tokens, is the difference between
the engine's fresh-token count and this minimum.

For a comparable complete result, both metrics require complete
predicate-answer tables, `prompt_pieces`, and a nonnegative integer
`fresh_tokens` measurement. The current runner does not detect omitted
evaluations and can compute a value from a partial trace; such a value is not
a complete benchmark result. The metrics are unavailable when
`prompt_pieces` is absent. The runner rejects a reported fresh-token count
below the computed minimum.

## Results

A run writes:

```text
results/my-run/
├── run.json
├── report.md
├── measurements.parquet
└── IMDB-4/
    ├── plan.substrait
    ├── rows.parquet
    ├── filters-0.parquet
    ├── filters-1.parquet
    ├── joins-0.parquet
    └── prompt_pieces.json
```

`run.json` records the benchmark version, query-definition hashes, scale
factor, corpus ID, reference collection, engine metadata, file paths, status,
and metrics. `measurements.parquet` contains one flat row per completed query.
`report.md` presents query time, throughput, cost, accuracy, input rows, and
token counts.

`plan.substrait` and `rows.parquet` are always written for a completed adapter
call. Filter files, join files, and `prompt_pieces.json` are written only when
the adapter supplies the corresponding optional data.

Rebuild or rescore a saved run:

```sh
quail-b report results/my-run
```

Rescoring checks that the current query definition and published corpus match
the IDs stored in the run.

When complete predicate traces are supplied, final-row validation computes
the implied row count. For results of at most 100,000 rows it validates every
row against the trace. For larger results it checks a sample of at most
100,000 rows selected at a fixed step by the current implementation. This is
not an exhaustive membership check for a larger result.

## Reproducibility

Report these values with benchmark results:

- QUAIL-B version or commit;
- query IDs;
- scale factor and corpus ID;
- reference collection and reference model;
- engine and model versions;
- engine configuration, including scheduling and caching settings;
- number and type of GPUs;
- GPU hourly price when reporting cost; and
- whether model startup and caches were warmed before measurement.

Correctness comparisons require matching query definitions, corpus IDs, and
reference collections. Performance and token comparisons additionally
require the same model and tokenizer revisions, quantization, inference dtype,
GPU type and count, query order, warmup procedure, and initial cache state.

`quail_b.run` invokes each query once and does not reset or warm engine state.
For isolated query measurements, clear query-derived KV before each query. If
state is retained between queries, report the order and retained state and
compare only with runs using the same protocol.

The runner stores a semantic hash of every query definition. The hash covers
Substrait version, functions, tables, aliases, prompts, operator order,
equality conditions, and projection.

Run the repository checks before changing the benchmark:

```sh
uv run ruff check quail_b tests tools
uv run python tools/check_long_strings.py
uv run vulture
uv run pytest -q
```

## Repository layout

| Path | Contents |
| --- | --- |
| `quail_b/plans/` | Substrait ProtoJSON query plans and catalog |
| `quail_b/prompts.py` | AI predicate prompts |
| `quail_b/data.py` | source revisions, sampling, schemas, and corpus identity |
| `quail_b/labels.py` | reference-label loading |
| `quail_b/scoring.py` | predicate and final-row scoring |
| `quail_b/minimum.py` | minimum-token and recomputed-KV accounting |
| `quail_b/run.py` | execution protocol and saved run format |
| `quail_b/reporting.py` | reports and measurements table |
| `quail_b/substrait.py` | validation of the supported Substrait subset |
| `tools/make_substrait_plans.py` | deterministic plan generation |
