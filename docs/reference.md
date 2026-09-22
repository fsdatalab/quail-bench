# QUAIL-B reference

This page specifies what an adapter receives and returns, and exactly how
QUAIL-B computes each metric. The [README](../README.md) covers installation,
running the benchmark, the queries, and the results.

- [Adapter function](#adapter-function)
- [`QuerySpec`](#queryspec)
- [Input tables](#input-tables)
- [Prompt rendering](#prompt-rendering)
- [`RunOutput`](#runoutput)
- [Predicate answers](#predicate-answers)
- [Measurements](#measurements)
- [Prompt pieces](#prompt-pieces)
- [Metric requirements](#metric-requirements)
- [Metric definitions](#metric-definitions)
- [Run records and rescoring](#run-records-and-rescoring)

## Adapter function

```python
def run_query(
    query: quail_b.QuerySpec,
    tables: dict[str, pyarrow.Table],
) -> quail_b.RunOutput:
    ...
```

The adapter must finish one query or raise an exception. QUAIL-B stops the run
at the first exception and records the failure in `run.json`.

The smallest valid result is:

```python
quail_b.RunOutput(
    filter_answers=None,
    join_answers=None,
    rows=rows,
    runtime_s=runtime_s,
)
```

## `QuerySpec`

| Attribute | Type | Meaning |
| --- | --- | --- |
| `id` | `str` | Stable query ID, such as `IMDB-4` |
| `description` | `str` | Short description of the query shape |
| `plan` | `substrait.plan_pb2.Plan` | Parsed Substrait 0.103 plan |
| `plan_bytes` | `bytes` | Serialized form of the same plan |

The plan defines table scans, relation aliases, prompts, filters, joins,
equality conditions, and the final projection. It uses the standard Substrait
relational operators and these extension functions:

```text
ai_filter:str_str
ai_join:str_str_str
```

Their declarations and URN are in
[`quail_b/substrait_extensions.yaml`](../quail_b/substrait_extensions.yaml).

## Input tables

`tables` maps physical table names from the plan to `pyarrow.Table` values. It
contains only the tables the current query scans. For IMDB-4:

```python
{
    "reviews": reviews_table,
    "aspects": aspects_table,
}
```

Each table contains its published `id` and document columns. Physical table
names are the dictionary keys. Relation aliases are the column names of `rows`
and the answer tables. For `reviews AS r`, the dictionary key is `reviews` and
the column name is `r`.

## Prompt rendering

The string argument of each AI function is a prompt template. Reference labels
correspond to the complete prompt produced from that template and its document
or documents.

Use the rendering functions in `quail_b.rendering`, or produce identical text:

```python
from quail_b.rendering import render_filter_prompt, render_join_prompt

filter_text = render_filter_prompt(template, document)
join_text = render_join_prompt(template, documents, anchor=0)
```

Every prompt starts with a document. The question follows it and begins
"Evaluate TRUE or FALSE for the following question:". Because the document
comes first, an engine can compute a document's KV once and reuse it across
every question asked of that document.

For joins, `documents` follows template placeholder order. `anchor` selects the
document placed first. Both renderers end with `ANSWER:`, and the model answer
must be read as `TRUE` or `FALSE`.

The published labels use these templates and this rendering. A different
prompt defines a different predicate, and its results are incomparable with
the labels.

## `RunOutput`

| Field | Type | Required for |
| --- | --- | --- |
| `filter_answers` | `dict[str, pa.Table] \| None` | Predicate and token metrics |
| `join_answers` | `dict[str, pa.Table] \| None` | Predicate and token metrics |
| `rows` | `pa.Table` | Every run |
| `runtime_s` | `float` | Every run |
| `measurements` | `dict` | Optional engine measurements |
| `prompt_pieces` | `dict \| None` | Token and KV metrics |

### Result rows

`rows` contains one ID column per relation alias selected by the plan. Its
column names must match the selected aliases exactly. IMDB-4 selects `r.id`
and `a.id`:

```python
pa.table({
    "r": ["rv17", "rv42"],
    "a": ["as0", "as1"],
})
```

The harness rejects:

- missing or additional columns;
- null IDs;
- IDs absent from the corresponding input table;
- duplicate ID tuples.

Scoring ignores column order and row order.

### Runtime

`runtime_s` is a finite, nonnegative number of seconds. It includes query
execution through completion of asynchronous model or GPU work. It excludes
engine startup, model loading, result collection, scoring, and result saving.
Use the same timing boundary for every system you compare.

## Predicate answers

`rows` shows only the final result. `filter_answers` and `join_answers` show
how the engine got there: the value of each AI predicate for every tuple the
engine evaluated it on. QUAIL-B compares each value with its reference label to
measure the accuracy of each operator.

Both fields are optional. Each maps an operator ID from the plan to a table
with one row per evaluated tuple and a boolean `answer` column.

### Example

IMDB-4 has three AI operators:

```text
Project [r.id, a.id]
└── AI Join J1                   join-1
    ├── AI Selection F4          filter-2
    │   └── AI Selection F1      filter-1
    │       └── Scan reviews AS r
    └── Scan aspects AS a
```

Suppose `reviews` has three rows and `aspects` has two. The engine evaluates
`filter-1` on every review, `filter-2` on the reviews that pass `filter-1`, and
`join-1` on each remaining review paired with each aspect:

```python
filter_answers = {
    "filter-1": pa.table({
        "r": ["rv17", "rv42", "rv50"],
        "answer": [True, True, False],
    }),
    "filter-2": pa.table({
        "r": ["rv17", "rv42"],
        "answer": [True, False],
    }),
}
join_answers = {
    "join-1": pa.table({
        "r": ["rv17", "rv17"],
        "a": ["as0", "as6"],
        "answer": [True, False],
    }),
}
rows = pa.table({"r": ["rv17"], "a": ["as0"]})
```

A selection table has one ID column, named by the alias of the relation it
filters. A join table has one ID column for each input alias. Each table lists
only the tuples the engine evaluated, so a different plan or operator order
produces different tables for the same query.

### Rules

- IDs and answers must be nonnull, and every ID must exist in its input table.
- Each ID, or each ID pair for a join, appears at most once per table.
- Return a table for some operators to score only those operators.
- Return a table for every operator to enable the checks and metrics below.
  Use `{}` for a query with zero operators of one kind, and `None` to omit a
  kind entirely.

When every operator has a table, QUAIL-B also:

- checks that `rows` matches the result those answers produce: it compares the
  row count and verifies a sample of rows;
- counts evaluated join pairs from the join tables; and
- computes input and minimum tokens from these tables and `prompt_pieces`.

## Measurements

`measurements` holds numbers the engine reports. QUAIL-B recognizes three keys:

| Key | Type | Meaning |
| --- | --- | --- |
| `evaluated_document_pairs` | nonnegative `int` | Pairs across all joins |
| `fresh_tokens` | nonnegative `int` | Positions processed by model forward passes |
| `input_tokens` | nonnegative `int` | Full length of every evaluated prompt |

When every join has an answer table, QUAIL-B uses the sum of their row counts
in place of `evaluated_document_pairs`.

`input_tokens` counts every position of every evaluated prompt, including
positions read from KV. Report it when prompt pieces are unavailable. It
enables input token throughput and cost per million input tokens. Minimum
tokens and KV regret require `prompt_pieces`; with pieces present, QUAIL-B
computes input tokens itself and ignores the reported total.

QUAIL-B saves other JSON serializable measurements as engine metadata.

## Prompt pieces

`prompt_pieces` describes the token IDs around each document. With it, QUAIL-B
can count the tokens every evaluated prompt contains and the minimum an engine
must compute.

| Entry | Type | Meaning |
| --- | --- | --- |
| `tokenizer` | `str` | Hugging Face tokenizer name used for documents |
| `preamble` | `list[int]` | Tokens before each first document |
| `filters` | `list[dict]` | One `id` and `tail` token list per filter |
| `joins` | `list[dict]` | One join description per join |

Each filter description has:

```text
id: operator ID
tail: tokens after the document
```

Each join description has:

```text
id: operator ID
anchor: alias of the document placed first
frame: tokens after the anchor document
label: tokens before the partner document
tail: tokens after the partner document
```

Prompt pieces require:

- an answer table for every filter and join;
- every filter and join listed exactly once;
- `measurements["fresh_tokens"]`;
- the same tokenizer and token layout used during execution.

QUAIL-B tokenizes the documents after execution and derives input tokens,
minimum tokens, recomputed tokens, and KV regret.

## Metric requirements

| Metric | Adapter data |
| --- | --- |
| Query time | `runtime_s` |
| Output precision, recall, F1, exact match | `rows` |
| Document throughput | `runtime_s`, for a query with zero joins |
| Join throughput | Answer tables for every join, or a reported pair count |
| Predicate accuracy | Predicate answers |
| Fresh tokens | `measurements["fresh_tokens"]` |
| Input tokens and their throughput | Prompt pieces, or reported input tokens |
| Minimum tokens and KV regret | All answer tables, prompt pieces, and fresh tokens |
| GPU cost | `gpu_count` and `gpu_hourly_rate_usd` |
| Cost per million input tokens | GPU cost and a positive input token count |

A metric that lacks its data is `unavailable` in `report.md` and null in
`run.json` and `measurements.parquet`. QUAIL-B never reports a missing count as
zero.

## Metric definitions

### Output quality

QUAIL-B compares the distinct ID tuples in `rows` with the reference result.
Precision is the share of returned rows that are in the reference result.
Recall is the share of reference rows that were returned. F1 is their harmonic
mean. Exact match means the two row sets are equal.

### Predicate accuracy

Predicate accuracy covers only the tuples the engine evaluated. It measures the
engine's individual answers, while output quality also reflects which tuples
the plan chose to evaluate.

### Throughput

Document throughput is input documents divided by `runtime_s`. Join throughput
is evaluated document pairs, summed across all joins, divided by `runtime_s`.

### Tokens and KV

| Metric | Definition |
| --- | --- |
| Input tokens | Full evaluated prompts, including tokens served from KV |
| Fresh tokens | Input positions processed by model forward passes |
| Minimum tokens | Input positions required with an unlimited prefix KV cache |
| Recomputed tokens | Fresh tokens minus minimum tokens |
| KV regret | Recomputed tokens divided by fresh tokens, as a percentage |
| Input token throughput | Input tokens divided by `runtime_s` |

The minimum counts each distinct prompt prefix once, so a document's questions
share the document and any leading tokens they have in common. For joins, each
pair's label, partner document, and answer cue count once per pair. Recomputed
tokens are therefore the work a perfect prefix KV cache would have avoided.

Input tokens depend on which prompts the plan evaluates. Fresh tokens measure
model computation. Two engines can therefore have the same input tokens and
different fresh tokens.

### Cost

```text
GPU cost = runtime_s / 3600 * gpu_count * gpu_hourly_rate_usd
cost per million input tokens = GPU cost / input tokens * 1,000,000
```

## Run records and rescoring

`run.json` records the QUAIL-B version, the corpus ID, the reference collection
ID, a hash of each query definition, each query's status, and its metrics. If
scoring fails, the query status is `scoring_failed` and its saved files remain
in place.

`quail-b report` rescores saved files against the recorded corpus and
reference collection. It requires corpus and query hashes that match the
installed QUAIL-B, and it rewrites `run.json`, `report.md`, and
`measurements.parquet`.
