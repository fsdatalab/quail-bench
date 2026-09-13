# QUAIL-B

QUAIL-B is a benchmark for relational queries whose predicates are evaluated
by a language model. The workload contains 33 queries over five document
datasets. Queries use two operations: a unary AI filter over one document and
a binary AI join over a pair of documents.

This repository publishes the query plans, input tables, reference answers,
and scoring code. It does not include a query engine.

## Contents

- [Benchmark definition](#benchmark-definition)
- [Workload](#workload)
  - [AI operations](#ai-operations)
  - [Query plans](#query-plans)
- [Data](#data)
  - [Scale factors](#scale-factors)
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

The reference answers were produced with Qwen3 32B fp8. FEVER and LePaRD also
use source labels. Reference labels are fixed benchmark data; an engine does
not produce or modify them.

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
These are 10%, 50%, and 100% of each dataset's full sampling target. A scale
factor selects different input tables and reference answers; it does not
change the queries or prompts.

The fraction applies to each dataset's primary sampling unit: IMDB reviews,
BioDEX reports, FEVER claims, LePaRD positive citation pairs, and SWE-Next
trace snapshots. Fixed or derived tables do not necessarily scale linearly.
For example, every IMDB corpus contains the same 12 aspects.

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

Use `cache_dir=` or `QUAIL_B_CACHE_DIR` to change the cache. Use `data_dir=`
to supply local input Parquet files. Local tables are checked against the
published corpus manifest before a run starts. Use `root=` to select a local
mirror of both inputs and reference labels.

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

An adapter resolves function anchors from the plan's extension declarations,
walks the relation tree, and translates it to the engine's query
representation. Filters are `FilterRel` nodes over their inputs; they are not
properties of a table or alias.

### Adapter output

The adapter returns `quail_b.RunOutput`:

| Field | Required | Contents |
| --- | --- | --- |
| `rows` | yes | final result, with one ID column per selected alias |
| `runtime_s` | yes | query execution time in seconds |
| `filter_answers` | no | filter operator ID to evaluated documents and answers |
| `join_answers` | no | join operator ID to evaluated pairs and answers |
| `measurements` | no | engine measurements, including `fresh_tokens` |
| `prompt_pieces` | no | tokenized prompt structure used for token accounting |

A filter answer table has the relation alias column and a non-null boolean
`answer` column. A join answer table has both relation alias columns and a
non-null boolean `answer` column. The tables contain every document or pair
evaluated by that operator, including answers of `FALSE`.

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

`execute_with_your_engine` is supplied by the adapter. Individual predicate
answers may be `None`. In that case, QUAIL-B still scores final output
precision and recall, but predicate accuracy and some token metrics are
unavailable.

### Run protocol

Pass the adapter to `quail_b.run`:

```python
quail_b.run(
    run_query,
    queries=["IMDB-4"],
    scale_factor=0.1,
    output_dir="results/my-run",
    metadata={
        "engine": "my-engine",
        "model": "Qwen3-4B-FP8",
        "configuration": "description of engine settings",
    },
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

## Metrics

| Metric | Definition |
| --- | --- |
| Query time | adapter-reported execution time; excludes model startup, result collection, scoring, and saving |
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
rows and expose false positive and false negative results.

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

`minimum_tokens` is the number of distinct positions in the prefix trie of
these requests. `regret_tokens`, reported as recomputed KV tokens, is the
difference between the engine's fresh-token count and this minimum. Without
`prompt_pieces`, both values are unavailable.

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

Rebuild or rescore a saved run:

```sh
quail-b report results/my-run
```

Rescoring checks that the current query definition and published corpus match
the IDs stored in the run.

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

Compare two runs only when their query definitions, corpus IDs, and reference
collections match. The runner stores a semantic hash of every query
definition. The hash covers Substrait version, functions, tables, aliases,
prompts, operator order, equality conditions, and projection.

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
