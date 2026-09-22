# Running the benchmark

This guide covers everything `quail_b.run` does around your adapter: choosing
queries, recording run metadata and GPU cost, downloading data, and inspecting
queries and tables. See the [adapter contract](adapter-contract.md) for what
the adapter itself receives and returns.

## A full run

```python
import quail_b

quail_b.run(
    run_query,                    # your adapter
    queries=["IMDB-4"],           # omit to run all 31 queries
    scale_factor=0.1,             # 0.1, 0.5, or 1.0
    output_dir="results/vllm_qwen3_4b",  # must be a new directory
    metadata={"engine": "vllm", "model": "Qwen/Qwen3-4B-FP8"},
    gpu_count=1,
    gpu_hourly_rate_usd=3.9492,
)
```

QUAIL-B runs the queries in the order given, calling `run_query` once per
query. After each query it saves the returned output, scores it, and updates
`run.json`. When the run ends, it writes `report.md` and
`measurements.parquet`. `quail_b.run` returns the run record, the same data as
`run.json`.

## Options

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
| `cache_dir` | `None` | Download cache; `None` uses the default below |
| `data_dir` | `None` | Local input Parquet files, used in place of the download |
| `root` | `None` | Local mirror of the published data, for offline runs |

Record everything that affects performance in `metadata`: engine version,
model, batch sizes, cache settings, and warmup. The report shows only QUAIL-B's
measurements, so `metadata` is how you tell two runs apart later.

Query cost is `runtime_s / 3600 * gpu_count * gpu_hourly_rate_usd`.

## Data and caching

The first run downloads the input tables and reference answers for the selected
queries from the public `s3://quail-bench` bucket. Later runs read them from
`~/.cache/quail-b`. Set `QUAIL_B_CACHE_DIR` or pass `cache_dir` to use another
location.

QUAIL-B checks every loaded table against the published corpus identity, so a
run with `data_dir` or `root` fails if its files differ from the published data.

QUAIL-B loads only the reference answers for the selected queries. Each answer
takes about 25 bytes in memory. Loading every published answer at scale factor
0.1, 1.21 million answers, takes about 2 seconds from the cache and peaks at
0.62 GiB, input tables included. At scale factor 1.0, 51.8 million answers,
budget about 3 GiB.

## Reference collections

Each scale factor has one published collection of reference answers:

| Scale factor | Reference collection |
| --- | --- |
| 0.1 | `gt_cd3ebdb784f64b9e028e50ea73cdedd0` |
| 0.5 | `gt_68f9ce9439bd7615de92b33d576dff9e` |
| 1.0 | `gt_e87691add604b02c4e43f0ff5bf0cc4f` |

`run.json` records the corpus and collection IDs of every run. Compare results
only across runs with the same IDs.

## Inspect queries and tables

You can load any query or table directly, which helps while writing an
adapter:

```python
import quail_b

query = quail_b.get_query("IMDB-4")
print(query.description)  # F1 -> F4 -> J1, 2 filters then 1 join
plan = query.plan         # a substrait.plan_pb2.Plan

reviews = quail_b.load_table("reviews", scale_factor=0.1)
print(reviews.num_rows)   # 5000
```

The plans are also readable as ProtoJSON in
[`quail_b/plans/`](../quail_b/plans/). The prompt templates are in
[`quail_b/prompts.py`](../quail_b/prompts.py), and the exact prompt text is
built by [`quail_b/rendering.py`](../quail_b/rendering.py).

## Failures and rescoring

The run stops at the first adapter or scoring error and records it in
`run.json`. Outputs are saved before scoring, so after fixing a scoring problem
you can rescore the saved outputs directly:

```sh
quail-b report results/vllm_qwen3_4b
```

See [scoring and results](scoring-and-results.md) for each metric and output
file.
