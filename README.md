# QUAIL-B

QUAIL-B measures how an AI query engine executes filters and joins over
document tables. The benchmark contains 31 Substrait queries across five
datasets and three scale factors.

QUAIL-B provides the plans, input tables, reference answers, validation,
scoring, and reports. Your engine adapter executes one plan at a time.

## Before you start

Your system must be able to:

- load input tables from PyArrow;
- execute the relational operations in a Substrait 0.103 plan;
- evaluate `ai_filter` and `ai_join` with the plan's prompts;
- return the selected document IDs as a PyArrow table.

You implement one Python callback for the workload. QUAIL-B calls it once for
each selected query. The callback can translate plans generically or dispatch
on `query.id`.

Read the [adapter contract](docs/adapter-contract.md) before implementing the
callback. It defines prompt rendering, result schemas, timing, and validation.

## Run QUAIL-B against your engine

### 1. Install the harness

QUAIL-B requires Python 3.12. From your adapter project, run:

```sh
uv add "quail-b @ git+https://github.com/fsdatalab/quail-bench.git"
```

### 2. Implement the callback

The integration point is:

```python
def run_query(
    query: quail_b.QuerySpec,
    tables: dict[str, pyarrow.Table],
) -> quail_b.RunOutput:
    ...
```

`query.plan` is the parsed Substrait plan. `tables` contains only the physical
tables used by that plan.

The smallest valid result contains the final rows and execution time:

```python
return quail_b.RunOutput(
    filter_answers=None,
    join_answers=None,
    rows=rows,
    runtime_s=runtime_s,
)
```

`rows` uses relation aliases from the plan as column names. For example, a
query that selects `r.id` and `a.id` returns columns named `r` and `a`.

Your engine provides plan translation, inference, synchronization, and result
collection.

### 3. Run one filter query

Start with IMDB-1 at scale factor 0.1:

```python
import quail_b

from my_adapter import run_query


quail_b.run(
    run_query,
    queries=["IMDB-1"],
    scale_factor=0.1,
    output_dir="results/imdb_1",
    metadata={"engine": "my_engine", "model": "my_model"},
)
```

The harness downloads and caches the required inputs and labels in
`~/.cache/quail-b`. Choose a new output directory for each run.

### 4. Check the result

A successful run creates `results/imdb_1/report.md`. Confirm that:

- the run status is `complete`;
- IMDB-1 has an execution time;
- output precision and recall are present.

The report shows `unavailable` for predicate and token metrics until the
adapter returns the optional traces described in the
[adapter contract](docs/adapter-contract.md).

### 5. Expand coverage

Use this order while bringing up an adapter:

1. IMDB-1: one AI filter;
2. IMDB-2: one AI join;
3. IMDB-4: two filters followed by a join;
4. all queries at scale factor 0.1;
5. scale factors 0.5 and 1.0.

Omit `queries` to run all 31 queries:

```python
quail_b.run(
    run_query,
    scale_factor=0.1,
    output_dir="results/full_0.1",
    metadata={"engine": "my_engine", "model": "my_model"},
)
```

Use a new output directory for every run.

## Division of responsibility

| QUAIL-B | Your adapter |
| --- | --- |
| Selects queries and scale factor | Translates or dispatches each plan |
| Loads published tables and labels | Loads the provided tables into the engine |
| Supplies exact plans and prompts | Executes relational and AI operators |
| Validates IDs and result schemas | Synchronizes execution and measures it |
| Scores results and writes reports | Returns `quail_b.RunOutput` |

Your infrastructure starts the model server. Your adapter chooses the execution
strategy and invokes the engine runtime.

## Documentation

- [Adapter contract](docs/adapter-contract.md): callback inputs, outputs,
  prompts, traces, and validation.
- [Workload](docs/workload.md): operators, query families, scale factors, and
  a worked plan.
- [Scoring and results](docs/scoring-and-results.md): metric availability,
  definitions, output files, and report regeneration.

The published ProtoJSON plans are in
[`quail_b/plans/`](quail_b/plans/). The AI function declarations are in
[`quail_b/substrait_extensions.yaml`](quail_b/substrait_extensions.yaml).
