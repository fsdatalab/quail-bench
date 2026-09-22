# Scoring and results

QUAIL-B separates final-output quality from the behavior of individual AI
operators. Every adapter can report output quality and execution time.
Predicate and token metrics require additional instrumentation.

## Metric availability

| Metric group | Final rows | Predicate traces | Complete traces and prompt pieces |
| --- | :---: | :---: | :---: |
| Query time | Yes | Yes | Yes |
| Output precision, recall, F1, exact match | Yes | Yes | Yes |
| Filter-only document throughput | Yes | Yes | Yes |
| Join pair throughput | With pair count | With all joins traced | Yes |
| Predicate accuracy | No | Yes | Yes |
| Fresh tokens | With a reported count | With a reported count | Yes |
| Input tokens and input-token throughput | No | No | Yes |
| Minimum tokens, recomputed tokens, KV regret | No | No | Yes |
| GPU cost | With GPU data | With GPU data | With GPU data |
| Cost per million input tokens | No | No | With GPU count and price |

See the [adapter contract](adapter-contract.md) for the fields that enable each
level.

## Output quality

The harness compares the distinct ID tuples in `RunOutput.rows` with the
reference result:

- precision is the matching share of returned rows;
- recall is the returned share of expected rows;
- F1 is the harmonic mean of precision and recall;
- exact match is true when the two row sets are equal.

Output scoring is available without predicate traces.

## Predicate accuracy

When an adapter returns filter or join traces, QUAIL-B compares each recorded
boolean answer with its published reference label. Accuracy applies only to
the documents and pairs the engine evaluated.

Most reference labels use `Qwen/Qwen3-32B-FP8`. Selected FEVER and LePaRD
labels use dataset annotations.

Predicate accuracy and output quality answer different questions. An engine
can agree on its evaluated predicates but miss output rows because its
execution strategy did not evaluate a necessary document or pair.

## Throughput

`runtime_s` is the denominator for throughput metrics.

- Filter-only throughput is the sum of input documents divided by query time.
- Join throughput is evaluated document pairs divided by query time.

For a multi-join query, evaluated pairs are summed across join operators.
QUAIL-B derives the sum from complete join traces. Without complete traces,
the adapter can report `measurements["evaluated_document_pairs"]`.

## Token and KV metrics

Full token accounting requires complete predicate traces, `prompt_pieces`, and
`measurements["fresh_tokens"]`.

| Metric | Definition |
| --- | --- |
| Input tokens | Full evaluated prompts, including tokens served from KV |
| Fresh tokens | Input positions processed instead of read from KV |
| Minimum tokens | Input positions required with an unlimited prefix KV cache |
| Recomputed tokens | Fresh tokens minus minimum tokens |
| Input-token throughput | Input tokens divided by query time |
| KV regret | Recomputed tokens divided by fresh tokens, as a percentage |

The minimum counts each distinct document prefix once. It also counts the
question and framing suffixes required by each evaluated filter or join. Work
that must occur once for each pair is part of the minimum, not regret.

Input tokens measure the requests selected by an execution strategy. Fresh
tokens measure model computation. Two engines can therefore have the same
input-token count and different fresh-token counts.

## GPU cost

Pass GPU count and hourly price to `quail_b.run`:

```python
quail_b.run(
    run_query,
    scale_factor=0.1,
    output_dir="results/costed-run",
    gpu_count=1,
    gpu_hourly_rate_usd=3.95,
)
```

Query cost is:

```text
runtime_s / 3600 * gpu_count * gpu_hourly_rate_usd
```

Cost per million input tokens is available when the run also has a positive
input-token count.

## Output files

A run writes:

```text
results/my-run/
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
| `report.md` | Human-readable run summary |
| `measurements.parquet` | One flat metrics row per completed query |
| `run.json` | Configuration, identities, status, files, and nested metrics |
| `<QUERY-ID>/plan.substrait` | Exact serialized plan used for the query |
| `<QUERY-ID>/rows.parquet` | Final rows returned by the adapter |
| `<QUERY-ID>/filters-*.parquet` | Optional filter traces |
| `<QUERY-ID>/joins-*.parquet` | Optional join traces |
| `<QUERY-ID>/prompt_pieces.json` | Optional token layout |

The run record includes the QUAIL-B version, corpus ID, reference collection
ID, and a hash of each query definition.

## Failure status

QUAIL-B stops at the first callback or scoring error. It writes the error and
partial status to `run.json` and updates `report.md`.

The harness saves a returned output before scoring it. If scoring fails,
the query status is `scoring_failed`, and its saved files remain available for
inspection or correction.

## Regenerate or rescore a report

Rescore the saved files against the recorded corpus and label collection:

```sh
quail-b report results/my-run
```

Rescoring rejects a run if its corpus or query-definition hashes do not match
the installed benchmark. It updates `run.json`, `report.md`, and
`measurements.parquet` without executing the engine again.
