# QUAIL-B

QUAIL-B is a benchmark for AI functions in SQL, or AI-SQL. It is actively being
developed.

For example, query IMDB-4 finds the movie aspects that each review discusses,
for reviews that praise the movie and discuss its ending:

```sql
SELECT r.id, a.id
FROM reviews AS r
AI JOIN aspects AS a ON J1(r.body, a.aspect)  -- does the review discuss the aspect?
WHERE AI_FILTER(F1, r.body)                   -- does it mention a positive aspect?
  AND AI_FILTER(F4, r.body);                  -- does it discuss the ending?
```

The benchmark contains 31 such queries over five document collections: movie
reviews, adverse drug reaction reports, claims and evidence for fact
verification, legal citations, and software agent trajectories. Each collection
comes at three scale factors, with reference answers for every filter and join.

To benchmark your engine, you write an adapter: a Python function that receives
one query and its input tables, runs the query on your engine, and returns the
result rows. QUAIL-B

- supplies each query as a [Substrait](https://substrait.io/) plan, with its
  prompts and [PyArrow](https://arrow.apache.org/docs/python/) input tables,
- validates your results and scores them against the reference answers, and
- writes a report of runtime, accuracy, and, if your adapter records them,
  token and KV metrics.

## Getting started

QUAIL-B requires Python 3.12:

```sh
uv add "quail-b @ git+https://github.com/fsdatalab/quail-bench.git"
```

Write an adapter and run IMDB-4 at the smallest scale factor:

```python
import pyarrow as pa
import quail_b


def run_query(query: quail_b.QuerySpec, tables: dict[str, pa.Table]):
    # query.plan is the Substrait plan; tables maps table names to data
    rows, runtime_s = my_engine.execute(query.plan, tables)
    return quail_b.RunOutput(
        filter_answers=None,  # optional: the answer for each document
        join_answers=None,    # optional: the answer for each pair
        rows=rows,
        runtime_s=runtime_s,
    )


quail_b.run(
    run_query,
    queries=["IMDB-4"],
    scale_factor=0.1,
    output_dir="results/imdb_4",
    metadata={"engine": "my_engine", "model": "Qwen/Qwen3-4B-FP8"},
)
```

`my_engine.execute` represents a call to your own engine code, which you
write. Typically it translates the Substrait plan into your engine's AI SQL
dialect, such as
[BigQuery AI SQL](https://cloud.google.com/bigquery/docs/generative-ai-overview),
and runs it. It returns the result rows and the query execution time, measured
once all model and GPU work has finished. Engine startup and model loading
stay outside the timer.

`rows` holds document IDs, with one column for each alias in the `SELECT` list.
IMDB-4 selects `r.id` and `a.id`, so its rows look like:

```python
pa.table({"r": ["rv17", "rv17", "rv42"], "a": ["as0", "as6", "as6"]})
```

When the run finishes, `results/imdb_4/report.md` lists the query's runtime and
the precision and recall of its rows against the reference result. Omit
`queries` to run all 31 queries. The first run downloads the tables and
reference answers and caches them in `~/.cache/quail-b`; each run writes to a
new `output_dir`. [Running the benchmark](docs/running-the-benchmark.md)
covers every `quail_b.run` option, including GPU cost, and how to inspect
queries and tables before you run them.

A good order for bringing up a new engine is IMDB-1 (one filter), then IMDB-2
(one join), then IMDB-4, then the full workload.

## Learn more

- [Running the benchmark](docs/running-the-benchmark.md): run options, run
  metadata, GPU cost, data downloads and memory use, reference collections,
  and inspecting queries and tables.
- [Adapter contract](docs/adapter-contract.md): the exact inputs and outputs,
  prompt rendering, and the optional predicate answers and token counts that enable
  accuracy and token metrics.
- [Workload](docs/workload.md): the query families, their plans, and table
  sizes at each scale factor.
- [Scoring and results](docs/scoring-and-results.md): how each metric is
  computed and what each output file contains.

The query plans are in [`quail_b/plans/`](quail_b/plans/), and the AI functions
are declared in
[`quail_b/substrait_extensions.yaml`](quail_b/substrait_extensions.yaml).

## Development

To work on QUAIL-B itself:

```sh
git clone https://github.com/fsdatalab/quail-bench.git
cd quail-bench
uv sync
uv run pytest -q
```
