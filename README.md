# QUAIL-B

QUAIL-B measures AI filters and joins over document tables. It provides 31
Substrait queries, their input data, reference answers, and a scoring harness.

The whole engine integration is one Python function. QUAIL-B passes it a query
plan and the required PyArrow tables. The function runs the plan and returns
the selected document IDs.

## Install QUAIL-B

QUAIL-B requires Python 3.12. Add it to the project that contains your engine
adapter:

```sh
uv add "quail-b @ git+https://github.com/fsdatalab/quail-bench.git"
```

## Connect your engine

A benchmark runner has this shape:

```python
import pyarrow as pa
import quail_b


def run_query(
    query: quail_b.QuerySpec,
    tables: dict[str, pa.Table],
) -> quail_b.RunOutput:
    rows, runtime_s = execute_with_my_engine(query.plan, tables)

    return quail_b.RunOutput(
        filter_answers=None,
        join_answers=None,
        rows=rows,
        runtime_s=runtime_s,
    )

quail_b.run(
    run_query,
    queries=["IMDB-1"],
    scale_factor=0.1,
    output_dir="results/imdb_1",
    metadata={"engine": "my_engine", "model": "my_model"},
)
```

Replace `execute_with_my_engine` with the call into your engine. It receives a
parsed Substrait 0.103 plan and the physical tables used by that plan. Return
the query execution time after any asynchronous model or GPU work completes.

The returned table contains one ID column for each selected relation alias. A
query that selects `r.id` and `a.id`, for example, returns columns named `r`
and `a`. This naming rule is the most common source of adapter errors.

Start with IMDB-1. It contains one AI filter and gives you the shortest path
through plan loading, inference, validation, and scoring. A successful run
creates `results/imdb_1/report.md` with status `complete`.

Continue with IMDB-2 for joins and IMDB-4 for filters followed by a join. Once
those query shapes work, run the full workload by removing the `queries`
argument:

```python
quail_b.run(
    run_query,
    scale_factor=0.1,
    output_dir="results/full_0.1",
    metadata={"engine": "my_engine", "model": "my_model"},
)
```

QUAIL-B downloads the selected tables and labels on the first run, then caches
them in `~/.cache/quail-b`. Each run writes to a new output directory.

## What gets measured

Every run reports query time and the precision and recall of the final rows.
Your adapter can also return predicate traces for operator accuracy and token
data for KV metrics. Start with final rows. Add traces after all query shapes
run correctly.

Read the [adapter contract](docs/adapter-contract.md) for the exact adapter
interface and result schemas. The [workload guide](docs/workload.md) explains the query
families and scale factors. [Scoring and results](docs/scoring-and-results.md)
defines each metric and output file.

The published ProtoJSON plans are in
[`quail_b/plans/`](quail_b/plans/). The AI function declarations are in
[`quail_b/substrait_extensions.yaml`](quail_b/substrait_extensions.yaml).
