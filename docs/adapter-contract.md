# Adapter contract

This page is the reference for integrating an execution engine with QUAIL-B.
The harness calls one adapter function for every selected query.

## Callback

```python
def run_query(
    query: quail_b.QuerySpec,
    tables: dict[str, pyarrow.Table],
) -> quail_b.RunOutput:
    ...
```

The callback must finish one query or raise an exception. QUAIL-B stops the run
at the first exception and records the failure in `run.json`.

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

Each table contains its published `id` and document columns. Relation aliases
belong to the plan, not this dictionary. If the plan reads `reviews AS r`, the
dictionary key is `reviews`, while result and trace columns use `r`.

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

For joins, `documents` follows template-placeholder order. `anchor` selects the
document placed first for prefix reuse. Both renderers end with `ANSWER:`. The
model answer must be interpreted as `TRUE` or `FALSE`.

Do not replace the plan's templates or use a chat wrapper. A prompt-format
change defines a different predicate from the one represented by the published
labels.

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

A result-only adapter returns:

```python
quail_b.RunOutput(
    filter_answers=None,
    join_answers=None,
    rows=rows,
    runtime_s=runtime_s,
)
```

### Final rows

`rows` contains one ID column per relation alias selected by the plan. Its
column names must exactly match the selected aliases.

IMDB-4 selects `r.id` and `a.id`, so a valid shape is:

```python
pa.table({
    "r": ["review-17", "review-42"],
    "a": ["acting", "plot"],
})
```

The harness rejects:

- missing or additional columns;
- null IDs;
- IDs absent from the corresponding input table;
- duplicate ID tuples.

Column order and row order do not affect scoring.

### Runtime

`runtime_s` is a finite, nonnegative number of seconds. It includes query
execution through completion of asynchronous model or GPU work. It excludes
engine startup, model loading, result collection, scoring, and result saving.

Use the same timing boundary for every system being compared.

## Predicate traces

Predicate traces are optional. A partial trace enables accuracy scoring for
the evaluations it contains. A complete trace enables result-consistency
checks and is a prerequisite for token metrics.

Dictionary keys are operator IDs from the plan, such as `filter-1` and
`join-1`.

### Filter trace

A filter table contains the filtered relation alias and a boolean `answer`:

```python
{
    "filter-1": pa.table({
        "r": ["review-17", "review-42"],
        "answer": [True, False],
    }),
}
```

### Join trace

A join table contains both relation aliases and a boolean `answer`:

```python
{
    "join-1": pa.table({
        "r": ["review-17", "review-17"],
        "a": ["acting", "plot"],
        "answer": [True, False],
    }),
}
```

Trace IDs and answers cannot be null. Every ID must exist in its input table.
An ID or ID tuple can occur only once per operator table.

When both trace dictionaries cover every operator, `rows` must represent the
result implied by those answers. The harness checks the implied row count and
validates a sample of returned rows.

Use `{}` when a complete trace has no operators of one kind. Use `None` when
that kind was not traced.

## Measurements

The harness recognizes two engine measurements:

| Key | Type | Meaning |
| --- | --- | --- |
| `evaluated_document_pairs` | nonnegative `int` | Pairs across all joins |
| `fresh_tokens` | nonnegative `int` | Positions computed, not read from KV |

Complete join traces override `evaluated_document_pairs` with the sum of their
row counts.

Other JSON-serializable measurements may be stored for engine-specific use,
but QUAIL-B does not score them.

## Prompt pieces

`prompt_pieces` describes the token IDs around each document. It is required
only for input-token and KV metrics.

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
