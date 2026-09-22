# QUAIL-B

QUAIL-B is a benchmark harness for AI query engines. Its workload contains 31
queries over document tables. Each query combines relational operators with two
LLM-powered operators:

- `ai_filter(prompt, document) -> boolean`
- `ai_join(prompt, left_document, right_document) -> boolean`

This package supplies the query plans, input tables, reference answers, result
validation, and scoring. You supply the engine and a Python callback that runs
one plan.

## How a benchmark run works

For each selected query, the harness:

1. loads its Substrait plan and input tables;
2. calls your `run_query(query, tables)` function once;
3. validates the returned IDs and timing;
4. compares the final rows with the reference output;
5. writes the results and a report.

You write one callback for the whole workload, not one callback per query. A
generic adapter can translate every Substrait plan. An adapter may instead
dispatch on `query.id` if its engine needs query-specific code.

The package does not start a model server, choose an execution strategy, or
translate plans into an engine-specific language.

## Quick start

QUAIL-B requires Python 3.12.

```sh
uv add "quail-b @ git+https://github.com/fsdatalab/quail-bench.git"
```

Start with one query at scale factor 0.1:

```python
import time

import pyarrow as pa
import quail_b


def run_query(
    query: quail_b.QuerySpec,
    tables: dict[str, pa.Table],
) -> quail_b.RunOutput:
    engine_plan = translate_substrait(query.plan)

    started = time.perf_counter()
    result = execute(engine_plan, tables)
    runtime_s = time.perf_counter() - started

    return quail_b.RunOutput(
        filter_answers=None,
        join_answers=None,
        rows=result.rows,
        runtime_s=runtime_s,
    )


quail_b.run(
    run_query,
    queries=["IMDB-1"],
    scale_factor=0.1,
    output_dir="results/first-run",
    metadata={"engine": "my-engine", "model": "my-model"},
)
```

`translate_substrait` and `execute` are placeholders for your engine
integration. The harness downloads the selected inputs and labels from the
public data store and caches them in `~/.cache/quail-b`.

When the first query works, omit `queries` to run all 31 queries:

```python
quail_b.run(
    run_query,
    scale_factor=0.1,
    output_dir="results/full-run",
    metadata={"engine": "my-engine", "model": "my-model"},
)
```

Each run needs a new `output_dir`.

## Adapter contract

The callback has this interface:

```python
def run_query(
    query: quail_b.QuerySpec,
    tables: dict[str, pyarrow.Table],
) -> quail_b.RunOutput:
    ...
```

### Inputs

`query` describes one workload query:

- `query.id` is its stable identifier, such as `"IMDB-4"`.
- `query.description` is a short summary.
- `query.plan` is a parsed Substrait 0.103 `Plan`.
- `query.plan_bytes` is the serialized plan.

The plan contains the table scans, relation aliases, prompt strings, AI
operators, ordinary equality conditions, and final projection. The custom AI
functions are declared in
[`quail_b/substrait_extensions.yaml`](quail_b/substrait_extensions.yaml).

`tables` maps every physical table used by the plan to a `pyarrow.Table`. For
example, IMDB-4 receives `tables["reviews"]` and `tables["aspects"]`. Load these
tables into your engine before executing the plan.

### Required output

Return a `quail_b.RunOutput`. For a basic integration, set:

- `rows` to the final query result as a `pyarrow.Table`;
- `runtime_s` to execution time in seconds;
- `filter_answers` and `join_answers` to `None`.

The `rows` table must:

- contain one column for each alias selected by the plan;
- use alias names as column names, such as `r` and `a`;
- contain IDs from the corresponding input tables;
- contain no null IDs or duplicate rows.

For example, IMDB-4 selects `r.id` and `a.id`, so its result has columns `r`
and `a`:

```python
rows = pa.table({
    "r": result.review_ids,
    "a": result.aspect_ids,
})
```

Measure `runtime_s` around query execution. Exclude engine startup, model
loading, and result collection so runs remain comparable.

### Optional predicate traces

Return predicate traces to measure operator accuracy and token efficiency:

- `filter_answers` maps each filter operator ID to the documents your engine
  evaluated and its boolean answers.
- `join_answers` maps each join operator ID to the document pairs your engine
  evaluated and its boolean answers.
- `measurements["fresh_tokens"]` records input token positions processed by
  model forward passes instead of read from KV.
- `prompt_pieces` records tokenized prompt parts for input-token and KV
  accounting.

A filter trace has the relation alias and a non-null boolean `answer` column:

```python
filter_answers = {
    "filter-1": pa.table({
        "r": evaluated_review_ids,
        "answer": filter_decisions,
    }),
}
```

A join trace has both relation aliases and an `answer` column:

```python
join_answers = {
    "join-1": pa.table({
        "r": evaluated_review_ids,
        "a": evaluated_aspect_ids,
        "answer": join_decisions,
    }),
}
```

Operator IDs come from the Substrait plan. If you return traces for every
operator, the final `rows` must be exactly the result implied by those answers.
The harness checks this relationship.

## Workload

The 31 queries cover five datasets:

| Dataset | Queries | Tables | Task |
| --- | ---: | --- | --- |
| IMDB | 10 | `reviews`, `aspects` | Review aspects and sentiment |
| BioDEX | 4 | `reports`, `terms` | Adverse drug reactions |
| FEVER | 10 | `claims`, `evidence` | Fact verification |
| LePaRD | 5 | `citation_contexts`, `citation_passages` | Legal citations |
| SWE-Next | 2 | `agent_traces` | Software-agent trajectories |

The workload includes filters, joins, filter pushdown on either side of a join,
and multi-join chains. Prompts are string literals in the plans. The plans use
standard Substrait relational operators plus the two AI functions above.

All plans are available as Substrait ProtoJSON in
[`quail_b/plans/`](quail_b/plans/). The
[workload figure](figures/quailb_anatomy.pdf) diagrams every plan.

### Example: IMDB-4

IMDB-4 filters reviews with two prompts and joins the surviving reviews to
movie aspects:

```sql
SELECT r.id, a.id
FROM reviews AS r
AI JOIN aspects AS a ON J1(r.body, a.aspect)
WHERE AI_FILTER(F1, r.body)
  AND AI_FILTER(F4, r.body);
```

Its operator tree is:

```text
Project [r.id, a.id]
└── AI Join J1                                      join-1
    ├── AI Selection F4                             filter-2
    │   └── AI Selection F1                         filter-1
    │       └── Scan reviews AS r
    └── Scan aspects AS a
```

## Scale factors

Choose `scale_factor=0.1`, `0.5`, or `1.0`. A scale factor changes the input
cardinalities and matching reference labels, but not the query plans.

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

Use scale factor 0.1 while developing an adapter. Use larger factors to measure
scaling behavior after the full workload runs correctly.

## Metrics

Every integration reports:

- query execution time;
- output precision, recall, F1, and exact match;
- document throughput for filter-only queries;
- evaluated-pair throughput for joins when pair counts are available.

Pass `gpu_count` and `gpu_hourly_rate_usd` to `quail_b.run` to report cost per
query:

```python
quail_b.run(
    run_query,
    scale_factor=0.1,
    output_dir="results/costed-run",
    gpu_count=1,
    gpu_hourly_rate_usd=3.95,
)
```

Predicate traces add filter and join accuracy. Predicate traces,
`prompt_pieces`, and `fresh_tokens` add:

- full input tokens, including tokens served from KV;
- input-token throughput;
- the minimum token computation under an unlimited prefix KV cache;
- recomputed KV tokens;
- KV regret, the recomputed share of fresh tokens;
- GPU cost per million input tokens when GPU pricing is also supplied.

Reference predicate labels use `Qwen/Qwen3-32B-FP8`. FEVER and LePaRD also use
their published dataset ground truth for output scoring.

## Results

A run writes:

```text
results/my-run/
├── run.json
├── report.md
├── measurements.parquet
└── IMDB-1/
    ├── plan.substrait
    └── rows.parquet
```

Traced runs also write predicate-answer Parquet files and
`prompt_pieces.json` in each query directory.

- `report.md` is the human-readable summary.
- `measurements.parquet` has one metrics row per completed query.
- `run.json` contains the run configuration, status, files, and metrics.
- Each query directory preserves the exact plan and returned data.

Rebuild the report from a saved run with:

```sh
quail-b report results/my-run
```

## Inspect queries and data

You can inspect a plan or table without starting a benchmark run:

```python
import quail_b

query = quail_b.get_query("IMDB-4")
plan = query.plan

reviews = quail_b.load_table("reviews", scale_factor=0.1)
```

For repository development:

```sh
git clone https://github.com/fsdatalab/quail-bench.git
cd quail-bench
uv sync
```
