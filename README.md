# QUAIL-B

QUAIL-B evaluates relational query execution with model-backed selection and
join predicates. The benchmark contains 33 queries over five document
datasets at three scale factors.

This repository contains the workload, data definitions, reference labels,
and scoring code. An engine runs the workload through a Python adapter.

## Contents

- [Workload](#workload)
- [Data](#data)
- [Query plans](#query-plans)
- [Installation](#installation)
- [Running the benchmark](#running-the-benchmark)
- [Metrics](#metrics)
- [Results](#results)
- [Reproducibility](#reproducibility)
- [Repository layout](#repository-layout)

## Workload

QUAIL-B defines two model-backed predicates:

- `ai_filter(prompt, document) -> boolean` is a selection predicate.
- `ai_join(prompt, left, right) -> boolean` is a join predicate.

| Dataset | Queries | Relations | Query structures |
| --- | ---: | --- | --- |
| IMDB | 10 | `reviews`, `aspects` | selections, joins, repeated aliases |
| BioDEX | 3 | `reports`, `terms` | selections and joins over long reports |
| FEVER | 10 | `claims`, `evidence` | two-sided selections and multiway joins |
| LePaRD | 8 | `citation_contexts`, `citation_passages` | selection chains and joins |
| SWE-Next | 2 | `agent_traces` | selections over trace snapshots |

The workload includes selection chains of depth one to five, one- to
three-stage joins, repeated relations under different aliases, and an AI join
combined with an equality predicate. Two PrivacyPolicies queries are defined
separately and are excluded from the default workload.

[All query plans](figures/quailb_anatomy.pdf) are shown in one figure.

Official configurations use `Qwen/Qwen3-4B-FP8` or
`Qwen/Qwen3-32B-FP8`, with one model copy per H100.

## Data

The supported scale factors are `0.1`, `0.5`, and `1.0`. They select fixed
corpora containing 10%, 50%, and 100% of each dataset's benchmark sampling
target. A scale factor changes the input cardinalities and reference labels,
but not the queries or prompts.

| Dataset | Relation | 0.1 tuples | 0.5 tuples | 1.0 tuples |
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

Each published corpus has an immutable corpus ID and table hashes. Source
revisions and the sampling seed are defined in
[`quail_b/data.py`](quail_b/data.py).

Reference labels use `Qwen/Qwen3-32B-FP8` at the revision recorded in
[`quail_b/predicates.py`](quail_b/predicates.py), with a one-token
`TRUE`/`FALSE` decision. FEVER and LePaRD use source labels where available.
Accuracy is agreement with this published reference collection.

Read one query without downloading data:

```python
import quail_b

query = quail_b.get_query("IMDB-4")
plan = query.plan
```

Load the input relations for one query:

```python
benchmark = quail_b.load_benchmark(
    "IMDB-4",
    scale_factor=0.1,
    accuracy=False,
)

reviews = benchmark.tables["reviews"]
aspects = benchmark.tables["aspects"]
```

Remove `accuracy=False` to load reference labels. To load one relation:

```python
reviews = quail_b.load_table("reviews", scale_factor=0.1)
```

Data is read anonymously from `s3://quail-bench` and cached under
`~/.cache/quail-b` by default. Set `QUAIL_B_CACHE_DIR` to change the cache.
Pass `data_dir=` for local input Parquet files or `root=` for a local mirror.
Local inputs are checked against the published corpus manifest.

The current loader holds the input relations and reference collection in host
memory:

| Scale factor | Loader memory | Recommended host memory |
| ---: | ---: | ---: |
| 0.1 | 0.84 GiB measured | at least 2 GiB |
| 0.5 | 10–12 GiB estimated | at least 16 GiB |
| 1.0 | 30–35 GiB estimated | at least 48 GiB |

## Query plans

Queries are Substrait 0.103 plans stored as ProtoJSON in
[`quail_b/plans/`](quail_b/plans/). These files are the benchmark query
definitions.

| Relational operation | Substrait representation |
| --- | --- |
| Relation scan | `ReadRel` with a `NamedTable` |
| AI selection | `FilterRel` calling `ai_filter` |
| AI join | inner `JoinRel` calling `ai_join` |
| Equality predicate | `equal` |
| Projection | `ProjectRel` and `RelRoot` |

The AI functions are declared in
[`quail_b/substrait_extensions.yaml`](quail_b/substrait_extensions.yaml).
Relation aliases and operator IDs use `RelCommon.hint.alias`.

For example, IMDB-4 is:

```sql
-- Pseudocode; AI SQL syntax is engine-specific.
SELECT r.id, a.id
FROM reviews AS r
AI JOIN aspects AS a ON J1(r.body, a.aspect)
WHERE AI_FILTER(F1, r.body)
  AND AI_FILTER(F4, r.body);
```

Its relation tree is:

```text
Project [r.id, a.id]
└── AI Join J1                                      join-1
    ├── AI Selection F4                             filter-2
    │   └── AI Selection F1                         filter-1
    │       └── Scan reviews AS r
    └── Scan aspects AS a
```

`F1`, `F4`, and `J1` are full prompt literals in the Substrait plan. Exact
prompt rendering is defined in
[`quail_b/rendering.py`](quail_b/rendering.py).

Regenerate the plans after changing a query or prompt:

```sh
uv run python tools/make_substrait_plans.py
```

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

An engine supplies:

```python
run_query(query: QuerySpec, tables: dict[str, pyarrow.Table]) -> RunOutput
```

`query.plan` is the Substrait plan. `tables` maps each `NamedTable` in the
plan to a PyArrow table.

`RunOutput` contains:

| Field | Value |
| --- | --- |
| `rows` | final result tuples, with one ID column per selected alias |
| `runtime_s` | query latency in seconds |
| `filter_answers` | operator ID to evaluated document IDs and boolean answers |
| `join_answers` | operator ID to evaluated ID pairs and boolean answers |
| `measurements` | engine measurements, including `fresh_tokens` |
| `prompt_pieces` | tokenized prompt structure for token accounting |

The two answer dictionaries are constructor arguments and may be `None`.
A filter answer table has one alias column and an `answer` column. A join
answer table has two alias columns and an `answer` column. Complete answer
tables contain every tuple evaluated by each operator, including `FALSE`
answers.

IDs and answers must be non-null. IDs must occur in the corresponding input
relation. Answer tuples and final result tuples must be unique. Result order
is ignored.

A minimal adapter result is:

```python
return quail_b.RunOutput(
    filter_answers=None,
    join_answers=None,
    rows=result_rows,
    runtime_s=query_latency,
)
```

Predicate and token metrics require complete answer dictionaries. Token
metrics also require `measurements["fresh_tokens"]` and `prompt_pieces`:

```python
prompt_pieces = {
    "tokenizer": "Qwen/Qwen3-4B-FP8",
    "preamble": [...],
    "filters": [{"id": "filter-1", "tail": [...]}],
    "joins": [{
        "id": "join-1",
        "anchor": "r",
        "frame": [...],
        "label": [...],
        "tail": [...],
    }],
}
```

The lists contain token IDs. See
`quail_b.minimum.validate_prompt_pieces` for the complete schema.

Measure `runtime_s` from the first query-specific engine call through
materialization of the final result. Include planning, tokenization, queueing,
model execution, and relational execution. Exclude model loading, warmup,
corpus loading, scoring, and conversion to `RunOutput`.

Run one query:

```python
quail_b.run(
    run_query,
    queries=["IMDB-4"],
    scale_factor=0.1,
    output_dir="results/my-run",
    metadata={
        "engine": "my-engine",
        "model": "Qwen/Qwen3-4B-FP8",
        "model_revision": "immutable revision",
        "tokenizer_revision": "immutable revision",
        "gpu": "H100",
        "cache_state": "cleared",
    },
    gpu_count=1,
    gpu_hourly_rate_usd=3.9492,
)
```

Omit `queries=` to run all 33 default queries. The output directory must not
already exist.

## Metrics

| Metric | Definition |
| --- | --- |
| Query latency | `runtime_s`; excludes model startup and result conversion |
| Filter throughput | input tuples / query latency |
| Join throughput | evaluated pairs across all join operators / query latency |
| GPU cost | latency in hours × GPU count × price per GPU-hour |
| Predicate accuracy | agreement on evaluated predicate answers |
| Result precision and recall | agreement on final result tuples |
| Fresh tokens | input token positions computed by model forward passes |
| Minimum tokens | distinct request-prefix positions with unlimited KV |
| Recomputed KV tokens | fresh tokens − minimum tokens |

For join queries, the evaluated-pair count is the sum of the join answer-table
cardinalities. If complete answer tables are unavailable, an adapter may
report `measurements["evaluated_document_pairs"]`.

QUAIL-B reconstructs minimum tokens from the predicate answer tables, corpus
documents, and `prompt_pieces`. Recomputed KV tokens are also stored as
`regret_tokens`.

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

Predicate-answer files and `prompt_pieces.json` are omitted when the adapter
does not return them.

Rebuild or rescore a saved run:

```sh
quail-b report results/my-run
```

## Reproducibility

Correctness comparisons require matching query-definition hashes, corpus IDs,
and reference collections. Performance comparisons also require the same
model and tokenizer revisions, engine configuration, hardware, query order,
warmup, and cache state.

Record those values in `metadata`. `run.json` records the benchmark version,
query hashes, corpus ID, reference collection, GPU count, price, and metrics.

## Repository layout

| Path | Contents |
| --- | --- |
| `quail_b/plans/` | Substrait query plans and catalog |
| `quail_b/prompts.py` | predicate prompts |
| `quail_b/rendering.py` | prompt rendering |
| `quail_b/data.py` | corpus construction and identity |
| `quail_b/labels.py` | reference-label loading |
| `quail_b/scoring.py` | accuracy metrics |
| `quail_b/minimum.py` | token accounting |
| `quail_b/run.py` | run protocol and result format |
| `tools/make_substrait_plans.py` | plan generation |
