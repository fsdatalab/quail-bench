# QUAIL-B

QUAIL-B is an academic benchmark of 33 AI SQL queries over document tables.
AI SQL is SQL with LLM-powered operators.

This repository publishes the queries, input tables, reference labels, and
scoring code. It does not include a query engine. An adapter translates each
query into an engine's AI SQL, executes it, and returns the result.

## Contents

- [Scale factors](#scale-factors)
- [Queries](#queries)
- [Installation](#installation)
- [How to adapt](#how-to-adapt)
- [Running the benchmark](#running-the-benchmark)
- [Metrics](#metrics)
- [Results](#results)
- [Source files](#source-files)

## Scale factors

There are three scale factors: `0.1`, `0.5`, and `1.0`. They are 10%, 50%,
and 100% of each dataset's sampling target. A scale factor changes the input
tables and reference labels. It does not change the 33 queries.

| Dataset | Table | 0.1 | 0.5 | 1.0 |
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

## Queries

The two LLM-powered operators are:

- `ai_filter(prompt, document) -> boolean`
- `ai_join(prompt, left, right) -> boolean`

Each query is a Substrait 0.103 plan in [`quail_b/plans/`](quail_b/plans/).
[All 33 plans](figures/quailb_anatomy.pdf) are shown in one figure.

| Dataset | Queries | Tables |
| --- | ---: | --- |
| IMDB | 10 | `reviews`, `aspects` |
| BioDEX | 3 | `reports`, `terms` |
| FEVER | 10 | `claims`, `evidence` |
| LePaRD | 8 | `citation_contexts`, `citation_passages` |
| SWE-Next | 2 | `agent_traces` |

IMDB-4 is:

```sql
-- Pseudocode. The adapter emits the engine's AI SQL.
SELECT r.id, a.id
FROM reviews AS r
AI JOIN aspects AS a ON J1(r.body, a.aspect)
WHERE AI_FILTER(F1, r.body)
  AND AI_FILTER(F4, r.body);
```

`F1`, `F4`, and `J1` are the prompt literals in the Substrait plan.

## Installation

Python 3.12.

```sh
uv add "quail-b @ git+https://github.com/fsdatalab/quail-bench.git"
```

## How to adapt

QUAIL-B calls `run_query(query, tables)` once per query.

- `query.plan` is the Substrait plan.
- `tables` is a dictionary of Arrow tables for the named relations.

The adapter translates the plan into the engine's AI SQL, for example
BigQuery AI SQL, Lotus, or Palimpzest, executes it, and returns a
`RunOutput`.

Required:

- `rows`: the final result, one ID column per selected alias
- `runtime_s`: query time in seconds, excluding model startup

Optional, needed for predicate accuracy and token metrics:

- `filter_answers`: operator ID to evaluated documents and boolean answers
- `join_answers`: operator ID to evaluated pairs and boolean answers
- `measurements["fresh_tokens"]`: input tokens computed by the model
- `prompt_pieces`: token IDs around each document

```python
import pyarrow as pa
import quail_b


def run_query(query, tables):
    sql = to_engine_sql(query.plan)
    result = execute(sql, tables)

    return quail_b.RunOutput(
        filter_answers={
            "filter-1": pa.table({
                "r": result.f1_ids, "answer": result.f1_answers,
            }),
            "filter-2": pa.table({
                "r": result.f4_ids, "answer": result.f4_answers,
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

`to_engine_sql` and `execute` are supplied by the adapter. If the engine
does not record individual predicate answers, pass `None` for the two
answer dictionaries.

Load a plan or table without running the engine:

```python
query = quail_b.get_query("IMDB-4")
reviews = quail_b.load_table("reviews", scale_factor=0.1)
```

## Running the benchmark

```python
quail_b.run(
    run_query,
    queries=["IMDB-4"],
    scale_factor=0.1,
    output_dir="results/my-run",
    metadata={"engine": "bigquery", "model": "Qwen/Qwen3-4B-FP8"},
)
```

Omit `queries=` to run all 33 queries. Data is downloaded from
`s3://quail-bench` and cached in `~/.cache/quail-b`.

## Metrics

| Metric | Definition |
| --- | --- |
| Query time | `runtime_s` |
| Filter throughput | input documents / query time |
| Join throughput | evaluated pairs / query time |
| GPU cost | query time in hours × GPUs × hourly price |
| Predicate accuracy | agreement with the reference labels on evaluated answers |
| Result precision and recall | agreement on the final result tuples |
| Fresh tokens | input tokens computed by the model |
| Minimum tokens | tokens the same requests need with unlimited KV |
| Recomputed KV tokens | fresh tokens − minimum tokens |

Reference labels are Qwen3 32B fp8. FEVER and LePaRD also use source labels.

## Results

```text
results/my-run/
├── run.json
├── report.md
├── measurements.parquet
└── IMDB-4/
    ├── plan.substrait
    ├── rows.parquet
    └── ...
```

```sh
quail-b report results/my-run
```

Compare runs that use the same scale factor, corpus ID, and reference
collection.

## Source files

| Path | Contents |
| --- | --- |
| `quail_b/plans/` | Substrait query plans |
| `quail_b/prompts.py` | operator prompts |
| `quail_b/data.py` | tables and scale factors |
| `quail_b/scoring.py` | accuracy metrics |
| `quail_b/run.py` | runner |
