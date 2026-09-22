# Adapter contract

QUAIL-B calls one adapter function for every selected query. A basic adapter
returns final rows and execution time. Predicate answers and token data add
deeper metrics later.

The key naming rule is simple: input tables use their physical names, while
output columns use relation aliases from the plan.

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
ordinary equality conditions, and the final projection. It uses the standard
Substrait relational operators and these extension functions:

```text
ai_filter:str_str
ai_join:str_str_str
```

Their declarations and URN are in
[`quail_b/substrait_extensions.yaml`](../quail_b/substrait_extensions.yaml).

## Input tables

`tables` maps physical table names from the plan to `pyarrow.Table` values. It
contains only the tables needed by the current query.

For example, IMDB-4 receives:

```python
{
    "reviews": reviews_table,
    "aspects": aspects_table,
}
```

Each table contains its published `id` and document columns. Physical table
names form the dictionary keys. Relation aliases form the column names of
`rows` and the answer tables. For `reviews AS r`, the dictionary key is `reviews` and the
column name is `r`.

## Prompt rendering

The string argument of each AI function is a prompt template. Reference labels
correspond to the complete raw prompt produced from that template and its
document or documents.

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
document placed first for prefix reuse. Both renderers end with `ANSWER:`. The
model answer must be interpreted as `TRUE` or `FALSE`.

Published labels use the plan's templates and raw prompt rendering. A different
prompt format defines a different predicate.

## `RunOutput`

`run_query` returns `quail_b.RunOutput`.

| Field | Type | Required for |
| --- | --- | --- |
| `filter_answers` | `dict[str, pa.Table] \| None` | Predicate and token metrics |
| `join_answers` | `dict[str, pa.Table] \| None` | Predicate and token metrics |
| `rows` | `pa.Table` | Every run |
| `runtime_s` | `float` | Every run |
| `measurements` | `dict` | Optional engine measurements |
| `prompt_pieces` | `dict \| None` | Token and KV metrics |

### Final rows

`rows` contains one ID column per relation alias selected by the plan. Its
column names must exactly match the selected aliases.

IMDB-4 selects `r.id` and `a.id`, so a valid shape is:

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

Use the same timing boundary for every system being compared.

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

The harness recognizes three engine measurements:

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

QUAIL-B preserves other JSON serializable measurements as engine metadata.

## Prompt pieces

`prompt_pieces` describes the token IDs around each document. It is required
only for input token and KV metrics.

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

- complete `filter_answers` and `join_answers` dictionaries;
- every filter and join listed exactly once;
- `measurements["fresh_tokens"]`;
- the same tokenizer and token layout used during execution.

QUAIL-B tokenizes the documents after execution and derives full input tokens,
minimum tokens, recomputed tokens, and KV regret.
