# QUAIL-B

QUAIL-B is a benchmark for AI functions in SQL, or AI-SQL. It is actively being
developed.

For example, query IMDB-4 finds the movie aspects that each review discusses,
for reviews that praise the movie and discuss its ending:

```sql
SELECT r.id AS r, a.id AS a
FROM reviews AS r
JOIN aspects AS a
  ON AI.IF(('Does this review discuss this movie aspect? Review: ', r.body,
            ' Aspect: ', a.aspect))
WHERE AI.IF(('This review mentions a positive aspect of the movie: ', r.body))
  AND AI.IF(('This review discusses the ending of the movie: ', r.body));
```

The query is written with BigQuery's
[`AI.IF`](https://cloud.google.com/bigquery/docs/reference/standard-sql/bigqueryml-syntax-ai-if)
function, and its prompts are shortened. QUAIL-B publishes each query as a
Substrait plan with the exact prompt text.

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

## Contents

- [Getting started](#getting-started)
- [Running the benchmark](#running-the-benchmark)
- [Queries](#queries)
- [Scale factors](#scale-factors)
- [Metrics](#metrics)
- [Results](#results)
- [Development](#development)

The [reference](docs/reference.md) specifies the full adapter contract,
including the optional predicate answers and token data, and the exact rules
for each metric.

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

`tables` is keyed by the physical table names in the plan, such as `reviews`
and `aspects`. `rows` holds document IDs, with one column for each alias in the
`SELECT` list. IMDB-4 selects `r.id` and `a.id`, so its rows look like:

```python
pa.table({"r": ["rv17", "rv17", "rv42"], "a": ["as0", "as6", "as6"]})
```

When the run finishes, `results/imdb_4/report.md` lists the query's runtime and
the precision and recall of its rows against the reference result.

## Running the benchmark

A full call to `quail_b.run` looks like:

```python
quail_b.run(
    run_query,
    queries=None,                        # None runs all 31 queries
    scale_factor=0.1,                    # 0.1, 0.5, or 1.0
    output_dir="results/vllm_qwen3_4b",  # must be a new directory
    metadata={"engine": "vllm", "model": "Qwen/Qwen3-4B-FP8"},
    gpu_count=1,
    gpu_hourly_rate_usd=3.9492,
)
```

QUAIL-B calls `run_query` once per query, in the order given. After each query
it saves the output, scores it, and updates `run.json`. At the end it writes
`report.md` and `measurements.parquet`, and returns the run record.

| Parameter | Default | Meaning |
| --- | --- | --- |
| `run_query` | required | Your adapter |
| `queries` | `None` | Query IDs to run; `None` runs all 31 |
| `scale_factor` | `0.1` | Published scale factor: `0.1`, `0.5`, or `1.0` |
| `output_dir` | required | New directory for this run's results |
| `metadata` | `None` | JSON object saved with the run: engine, model, settings |
| `gpu_count` | `1` | GPUs used by each query, for cost |
| `gpu_hourly_rate_usd` | `None` | Price per GPU hour; `None` leaves cost unreported |
| `collection_id` | `None` | Reference collection; `None` uses the published one |
| `cache_dir` | `None` | Download cache; `None` uses `~/.cache/quail-b` |
| `data_dir` | `None` | Local input Parquet files, used in place of the download |
| `root` | `None` | Local mirror of the published data, for offline runs |

Record everything that affects performance in `metadata`: engine version,
model, batch sizes, cache settings, and warmup. The report shows only QUAIL-B's
measurements, so `metadata` is how you tell two runs apart later.

### Data and caching

The first run downloads the input tables and reference answers for the selected
queries from the public `s3://quail-bench` bucket. Later runs read them from
`~/.cache/quail-b`. Set `QUAIL_B_CACHE_DIR` or pass `cache_dir` to use another
location. QUAIL-B checks every loaded table against the published corpus
identity, so local files that differ from the published data fail the run.

Each reference answer takes about 25 bytes in memory. Loading every answer at
scale factor 0.1, 1.21 million answers, takes about 2 seconds from the cache
and peaks at 0.62 GiB, input tables included. At scale factor 1.0, 51.8 million
answers, budget about 3 GiB.

### Inspecting queries and tables

You can load any query or table directly while writing an adapter:

```python
query = quail_b.get_query("IMDB-4")
print(query.description)  # F1 -> F4 -> J1, 2 filters then 1 join
plan = query.plan         # a substrait.plan_pb2.Plan

reviews = quail_b.load_table("reviews", scale_factor=0.1)
print(reviews.num_rows)   # 5000
```

## Queries

The queries use two AI functions, declared as Substrait extensions in
[`quail_b/substrait_extensions.yaml`](quail_b/substrait_extensions.yaml):

```text
ai_filter(prompt, document) -> boolean
ai_join(prompt, left_document, right_document) -> boolean
```

The plans combine them with scans, equality conditions, conjunction, and
projection.

| Dataset | Queries | Tables | Task |
| --- | --- | --- | --- |
| IMDB | IMDB-1 to IMDB-10 | `reviews`, `aspects` | Review aspects and sentiment |
| BioDEX | BIO-1 to BIO-4 | `reports`, `terms` | Adverse drug reactions |
| FEVER | FEV-1 to FEV-10 | `claims`, `evidence` | Fact verification |
| LePaRD | LEP-1 to LEP-5 | `citation_contexts`, `citation_passages` | Legal citations |
| SWE-Next | AGENT-1 to AGENT-2 | `agent_traces` | Software agent trajectories |

Within each dataset, the first queries have a single filter or join. Later
queries chain filters, filter both join inputs, scan one table under two
aliases, and chain three joins. The plans are stored as Substrait
ProtoJSON in [`quail_b/plans/`](quail_b/plans/), with their order and
descriptions in [`catalog.json`](quail_b/plans/catalog.json).

### Example: IMDB-4

IMDB-4, the query at the top of this page, has this operator tree:

```text
Project [r.id, a.id]
└── AI Join J1                   join-1
    ├── AI Selection F4          filter-2
    │   └── AI Selection F1      filter-1
    │       └── Scan reviews AS r
    └── Scan aspects AS a
```

`F1`, `F4`, and `J1` name the three prompts in the SQL above: positive aspect,
ending, and review discusses aspect. The plan stores them as string literals.
`filter-1`, `filter-2`, and `join-1` are operator IDs. The prompt text
comes from [`quail_b/prompts.py`](quail_b/prompts.py) and
[`quail_b/rendering.py`](quail_b/rendering.py). Every prompt starts with a
document, followed by a question that begins "Evaluate TRUE or FALSE for the
following question:", so an engine can reuse a document's KV across questions.

### Developing an adapter

The 31 queries have several different shapes: how many filters and joins they
have, and how those operators are arranged in the plan. The table below lists
one query for each distinct shape, from simplest to most complex. Test your
adapter on these queries first, then run it on all 31.

| Query | Shape | What it tests |
| --- | --- | --- |
| IMDB-1 | One filter | Scans, prompt rendering, and result IDs |
| IMDB-2 | One join | Pair evaluation and two output columns |
| IMDB-4 | Two filters, then one join | Operator order |
| FEV-5 | Filters on both join inputs | Filters on each side of a join |
| IMDB-8 | Two joins sharing one input | Two aliases of one table |
| FEV-8 | Chain of three joins | Multiple joins |
| FEV-10 | Filtered join with equality | Equality and AI conditions together |
| BIO-4 | Three filters, two joins | Filters on two aliases of one table |

## Scale factors

Scale factors 0.1, 0.5, and 1.0 sample 10%, 50%, and 100% of each dataset's
target size. Each corpus is sampled with a fixed seed from pinned upstream
revisions, listed in [`quail_b/data.py`](quail_b/data.py). The queries are the
same at every scale factor. Use 0.1 while developing an adapter.

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

Each scale factor has one published collection of reference answers. Most
labels come from `Qwen/Qwen3-32B-FP8`; FEVER and LePaRD also score against
their datasets' own annotations.

| Scale factor | Reference collection |
| --- | --- |
| 0.1 | `gt_cd3ebdb784f64b9e028e50ea73cdedd0` |
| 0.5 | `gt_68f9ce9439bd7615de92b33d576dff9e` |
| 1.0 | `gt_e87691add604b02c4e43f0ff5bf0cc4f` |

`run.json` records the corpus and collection IDs. Compare results only across
runs with the same IDs.

## Metrics

Every run reports query time and output quality. The other metrics appear when
the adapter returns the data they need; the
[reference](docs/reference.md#metric-requirements) lists what each one
requires. A metric that lacks its data shows as `unavailable`; QUAIL-B never
reports it as zero.

| Metric | Definition |
| --- | --- |
| Query time | `runtime_s`, in seconds |
| Output precision and recall | Returned rows compared with the reference result |
| Predicate accuracy | Each recorded filter and join answer compared with its label |
| Document throughput | Input documents per second, for queries with zero joins |
| Join throughput | Evaluated document pairs per second |
| GPU cost | `runtime_s / 3600 * gpu_count * gpu_hourly_rate_usd` |
| Input tokens | Full length of every evaluated prompt, including KV hits |
| Fresh tokens | Positions processed by model forward passes |
| Minimum tokens | Positions needed with an unlimited prefix KV cache |
| KV regret | Fresh tokens above the minimum, as a percentage of fresh tokens |
| Input token throughput | Input tokens per second |
| Cost per million input tokens | GPU cost divided by input tokens, times one million |

## Results

Each run writes to its `output_dir`:

```text
results/vllm_qwen3_4b/
├── report.md             # summary table of every metric
├── run.json              # configuration, data IDs, status, and metrics
├── measurements.parquet  # one row of metrics per query
└── IMDB-4/
    ├── plan.substrait    # the exact plan that ran
    ├── rows.parquet      # the result rows
    ├── filters-0.parquet # optional filter answers
    ├── joins-0.parquet   # optional join answers
    └── prompt_pieces.json
```

The run stops at the first adapter or scoring error and records it in
`run.json`. Outputs are saved before scoring, so you can rescore a run from its
saved files:

```sh
quail-b report results/vllm_qwen3_4b
```

## Development

To work on QUAIL-B itself:

```sh
git clone https://github.com/fsdatalab/quail-bench.git
cd quail-bench
uv sync
uv run pytest -q
```
