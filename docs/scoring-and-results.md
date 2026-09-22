# Scoring and results

QUAIL-B separates final output quality from the behavior of individual AI
operators. Every adapter can report output quality and execution time.
Predicate and token metrics require additional instrumentation.

## Metric requirements

| Metric group | Adapter data |
| --- | --- |
| Query time | Final rows and runtime |
| Output precision, recall, F1, exact match | Final rows |
| Document throughput | Runtime for a query with zero joins |
| Join pair throughput | Answer tables for every join, or a reported pair count |
| Predicate accuracy | Predicate answers |
| Fresh tokens | A reported fresh token count |
| Input tokens and their throughput | Prompt pieces, or reported input tokens |
| Minimum and KV metrics | All answer tables, prompt pieces, and fresh tokens |
| GPU cost | GPU count and hourly price |
| Cost per million input tokens | Input tokens, GPU count, and hourly price |

See the [adapter contract](adapter-contract.md) for the fields that enable each
level. A metric that lacks its data appears as `unavailable` in `report.md` and as
null in `run.json` and `measurements.parquet`. QUAIL-B never reports a missing
count as zero.

## Output quality

The harness compares the distinct ID tuples in `RunOutput.rows` with the
reference result. Precision is the matching share of returned rows, while
recall is the returned share of expected rows. F1 combines precision and
recall. Exact match means that the two row sets are equal.

Final rows enable output scoring.

## Predicate accuracy

When an adapter returns predicate answers, QUAIL-B compares each recorded
boolean answer with its published reference label. Accuracy applies only to
the documents and pairs the engine evaluated.

Most reference labels use `Qwen/Qwen3-32B-FP8`. Selected FEVER and LePaRD
labels use dataset annotations.

Predicate accuracy and output quality answer different questions. Predicate
accuracy measures recorded evaluations. Output quality also reflects which
documents and pairs the execution strategy selected.

## Throughput

`runtime_s` is the denominator for throughput metrics.

- For a query with zero joins, throughput is input documents divided by time.
- Join throughput is evaluated document pairs divided by query time.

For a query with multiple joins, evaluated pairs are summed across operators.
QUAIL-B derives the sum when every join has an answer table. Otherwise, an
adapter can report `measurements["evaluated_document_pairs"]`.

## Token and KV metrics

Token accounting follows one of two paths:

- With `prompt_pieces`, an answer table for every operator, and
  `measurements["fresh_tokens"]`, QUAIL-B computes input, minimum, and
  recomputed tokens itself.
- With only `measurements["input_tokens"]`, QUAIL-B reports input tokens and
  input token throughput. Minimum tokens and KV regret stay unavailable,
  because computing them requires the prompt layout.

| Metric | Definition |
| --- | --- |
| Input tokens | Full evaluated prompts, including tokens served from KV |
| Fresh tokens | Input positions processed by model forward passes |
| Minimum tokens | Input positions required with an unlimited prefix KV cache |
| Recomputed tokens | Fresh tokens minus minimum tokens |
| Input token throughput | Input tokens divided by query time |
| KV regret | Recomputed tokens divided by fresh tokens, as a percentage |

The minimum counts each distinct document prefix once. It also counts the
question and framing suffixes required by each evaluated filter or join. Work
required once for each pair contributes to the minimum.

Input tokens measure the requests selected by an execution strategy. Fresh
tokens measure model computation. Two engines can therefore have the same
input token count and different fresh token counts.

## GPU cost

Pass `gpu_count` and `gpu_hourly_rate_usd` to `quail_b.run`, as shown in
[running the benchmark](running-the-benchmark.md). Query cost is:

```text
runtime_s / 3600 * gpu_count * gpu_hourly_rate_usd
```

Cost per million input tokens is available when the run also has a positive
input token count.

## Output files

A run writes:

```text
results/my_run/
├── run.json
├── report.md
├── measurements.parquet
└── IMDB-1/
    ├── plan.substrait
    ├── rows.parquet
    ├── filters-0.parquet
    ├── joins-0.parquet
    └── prompt_pieces.json
```

Only files supplied by the adapter are present in a query directory.

| Path | Contents |
| --- | --- |
| `report.md` | Run summary for people |
| `measurements.parquet` | One flat metrics row per completed query |
| `run.json` | Configuration, identities, status, files, and nested metrics |
| `<query_id>/plan.substrait` | Exact serialized plan used for the query |
| `<query_id>/rows.parquet` | Final rows returned by the adapter |
| `<query_id>/filters-*.parquet` | Optional selection answers |
| `<query_id>/joins-*.parquet` | Optional join answers |
| `<query_id>/prompt_pieces.json` | Optional token layout |

The run record includes the QUAIL-B version, corpus ID, reference collection
ID, and a hash of each query definition.

## Failure status

QUAIL-B stops at the first adapter or scoring error. It writes the error and
partial status to `run.json` and updates `report.md`.

The harness saves a returned output before scoring it. If scoring fails,
the query status is `scoring_failed`, and its saved files remain available for
inspection or correction.

## Regenerate or rescore a report

Rescore the saved files against the recorded corpus and label collection:

```sh
quail-b report results/my_run
```

Rescoring requires corpus and query definition hashes that match the installed
benchmark. It reads the saved outputs and updates `run.json`, `report.md`, and
`measurements.parquet`.
