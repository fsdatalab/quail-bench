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
- [Classification](#classification)
- [Extraction](#extraction)
- [Maps](#maps)
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
ai_filter(prompt: string, document: string) -> boolean
ai_join(prompt: string, left: string, right: string) -> boolean
ai_classify(prompt: string, document: string,
            labels: list<string>, descriptions: list<string>) -> string
ai_classify(prompt: string, anchor: string, partner: string,
            labels: list<string>, descriptions: list<string>) -> string
ai_score(prompt: string, document: string) -> fp64
ai_extract(question: string, document: string) -> string or null
ai_map(prompt: string, document: string, max_tokens: i32) -> nonnull string
ai_sae_features(prompt: string, document: string, pooling: string)
    -> list<struct<feature: i32, strength: fp32>>
```

The prompt, question, labels, descriptions, pooling, and max_tokens arguments
are constants. `max_tokens` must be positive. The functions are declared
in [`quail_b/substrait_extensions.yaml`](../quail_b/substrait_extensions.yaml)
under the URN `extension:org.fsdatalab.quail_b:functions_ai`.

A plan names each function by its Substrait
[signature](https://substrait.io/extensions/#function-signature-compound-names):
the name, then the short name of each argument type, such as `str` for string.
A plan reader matches on these names:

| Function | Name in the plan |
| --- | --- |
| `ai_filter` | `ai_filter:str_str` |
| `ai_join` | `ai_join:str_str_str` |
| `ai_classify`, one document | `ai_classify:str_str_list_list` |
| `ai_classify`, joined rows | `ai_classify:str_str_str_list_list` |
| `ai_score` | `ai_score:str_str` |
| `ai_extract` | `ai_extract:str_str` |
| `ai_map` | `ai_map:str_str_i32` |
| `ai_sae_features` | `ai_sae_features:str_str_str` |

Each operator that asks the model, and each IN-list filter, has an operator
ID as its hint alias:

| Operator | Substrait form | ID |
| --- | --- | --- |
| AI filter | `FilterRel` calling `ai_filter` | `filter-N` |
| AI join | `JoinRel` calling `ai_join` | `join-N` |
| Classification | `ProjectRel` calling `ai_classify` | `classify-N` |
| Score | `ProjectRel` calling `ai_score` | `score-N` |
| Extraction | `ProjectRel` calling `ai_extract` | `extract-N` |
| Map | `ProjectRel` calling `ai_map` | `map-N` |
| SAE features | `ProjectRel` calling `ai_sae_features` | `sae-features-N` |
| IN-list filter | `FilterRel` with a `SingularOrList` condition | `in-list-N` |

A classification adds one label column to its relation. Its hint output
names end with `alias.column`, such as `r.sentiment`. The five-argument form
labels the rows of the join below it instead of single documents;
[Classification](#classification) describes both forms.

An IN-list filter keeps the documents whose label column holds one of the
listed labels, as SQL's `r.sentiment IN ('negative', 'mixed')` does. It asks
the model nothing.

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

Filter and join prompts start with a document. The question follows it and
begins "Evaluate TRUE or FALSE for the following question:". Because the
document comes first, an engine can compute a document's KV once and reuse it
across questions asked of that document.

For joins, `documents` follows template placeholder order. `anchor` selects the
document placed first. Both renderers end with `ANSWER:`, and the model answer
must be read as `TRUE` or `FALSE`.

Classifications have one more renderer, described under
[Classification](#classification):

```python
classify_text = render_classify_prompt(template, document, labels, descriptions)
joined_text = render_classify_prompt(template, anchor, labels, descriptions,
                                     partner=partner)
```

Extractions have their own renderer, described under
[Extraction](#extraction):

```python
from quail_b.rendering import render_extract_prompt

extract_text = render_extract_prompt(question, document)
```

Maps use the `raw-v1` layout described under [Maps](#maps), without extraction's
phrase format. They have no reference text answers.

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
| `classify_answers` | `dict[str, pa.Table] \| None` | Label accuracy and token metrics |
| `score_answers` | `dict[str, pa.Table] \| None` | Token metrics of a query with a score |
| `extract_answers` | `dict[str, pa.Table] \| None` | Span accuracy and token metrics of a query with an extraction |
| `features_answers` | `dict[str, pa.Table] \| None` | Token metrics of a query with an SAE feature call |
| `map_answers` | `dict[str, pa.Table] \| None` | Map validation and token metrics |

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

A query that returns a label column adds one string column for it. The column
name is the part of the plan's output name after the alias. For example,
IMDB-13 returns `r.sentiment`, so `rows` has a `sentiment` column. Each row
holds the label of that row's review.

A label column of joined rows holds the label of the row's two documents.
For example, IMDB-15 returns `r.aspect_sentiment`, so `rows` has an
`aspect_sentiment` column. Each row holds the label of that row's review and
aspect.

The harness rejects:

- missing or additional columns;
- null IDs;
- IDs absent from the corresponding input table;
- duplicate ID tuples.

Scoring ignores column order and row order.

A join query can also filter one of its tables with a `WHERE` condition on a
stored column. E.g., SUPPORT-2 keeps only the customer messages with
`u.role = 'user'`. The condition does not call the model. The engine applies it
before the join, so the join asks fewer pairs. QUAIL-B scores this query per
operator, like any other join query. Its reference rows are the pairs that pass
the `WHERE` conditions and the equality conditions, and that the saved join
labels answer TRUE.

A join predicate can name `pair_columns`. Its labels then cover only the pairs
whose two columns are equal. These pairs are the ones that the query's equality
condition keeps. The predicate can also name `left_where` and `right_where`, a
column and a value that each left or right row must equal. E.g., the RUNS-3
predicate keeps successful runs on the left and failed runs on the right, as
the query's `WHERE` conditions do. An answer for any other pair has no label,
so the answer fails scoring.

### Rows of a relational query

A `REL-` query selects fields of its last relational step. Its `rows` columns
are named by `output_name`: the alias for an id, the column name after the alias
for another column or a label, and the bare name for a measure. For example,
REL-AGENT-6 returns `trajectory_id`, `fixes`, `first_fix`, and `longest`. The
harness rejects more rows than the query's `LIMIT` and ids absent from the
input table. The reference rows apply the query's relational steps to the saved
labels, with a score of 1.0 for a document labeled TRUE; a query sorted by a
score is scored by precision at k. A query with a score reports its token
metrics from `score_answers` and the `scores` prompt pieces.

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

## Classification

`ai_classify(prompt, document, labels, descriptions)` returns one label from
the list for each document. `descriptions` has one entry per label; an empty
string means none. Labels are distinct, and no label continues another label
word for word.

### Classifying joined rows

A classification can label the rows of a join instead of single documents. A
joined row is the two documents, one from each table, that the join kept. The
label describes the two documents together.

For example, IMDB-15 joins each negative or mixed review with each movie
aspect the review discusses, such as "the acting". For each (review, aspect)
row, it asks what sentiment the review expresses about that aspect. The
answer is one of positive, negative, neutral, or mixed.

The two documents of a joined row are the anchor and the partner. The anchor
is the first document argument, and it comes first in the prompt, as a join's
anchor does. The partner is the second document argument. In IMDB-15, the
review is the anchor and the aspect is the partner.

The call takes five arguments:

```text
ai_classify(prompt, anchor, partner, labels, descriptions) -> string
```

- The prompt names the anchor as `{0}` and the partner as `{1}`.
- `labels` and `descriptions` follow the rules of the one-document form.
- The call's `ProjectRel` sits above the `JoinRel` of the same two relations.
  It sees only the rows that join kept.
- The label column belongs to the anchor's relation.
- An IN-list filter cannot test its label column.

IMDB-15 is the only query that classifies joined rows. Its plan has this
operator tree:

```text
Project [r.id, r.sentiment, a.id, r.aspect_sentiment]
└── Classify aspect sentiment        classify-2
    └── AI Join J1                   join-1
        ├── IN-list filter           in-list-1
        │   └── Classify sentiment   classify-1
        │       └── Scan reviews AS r
        └── Scan aspects AS a
```

`classify-1` labels each review's overall sentiment. `in-list-1` keeps
the reviews labeled negative or mixed. `join-1` pairs each kept review with
the aspects it discusses. `classify-2` labels each of those rows.

### Prompt and reference label

`render_classify_prompt` produces the text the labels are scored after:

```text
DOCUMENT:
<document>

Answer with exactly one of the categories below for the following question: <question>

Categories:
- <label 1>: <description 1>
- <label 2>
ANSWER:
```

With a `partner`, `render_classify_prompt` lays out the two documents the
way a join prompt does. The anchor comes first, then a note that names it
`{0}`. The partner follows under the heading `DOCUMENT {1}:`. The instruction,
question, categories, and answer cue come last, as in the one-document prompt:

```text
DOCUMENT:
<anchor>

(The document above is DOCUMENT {0}.)

DOCUMENT {1}:
<partner>

Answer with exactly one of the categories below for the following question: <question>

Categories:
- <label 1>: <description 1>
- <label 2>
ANSWER:
```

The question keeps `{0}` and `{1}` as written. The note and the heading tell
the model which document each name refers to. For example, IMDB-15's question
is "Judge strictly from the review in DOCUMENT {0} what sentiment it expresses
about the movie aspect in DOCUMENT {1}." Because the anchor comes first, an
engine can compute a review's KV once and reuse it for every aspect joined
with that review.

In both forms, each label follows as `" " + label`. The reference label is the
label with the largest sum of its tokens' log probabilities, each normalized
over the full vocabulary at temperature 1, from `Qwen/Qwen3-32B-FP8`. No end
marker is scored, and the earlier label wins a tie.
`quail_b.predicates.CLASSIFY_JUDGE_SPEC` records this definition.

An engine may choose labels another way, and its answers are compared with
this definition. For example, an engine can score only the first token when
the first tokens differ, or list the labels under letters and read one letter.

### Answers

`classify_answers` maps each classify operator ID to a table with the
relation's alias column and a string `label` column, one row per document the
engine classified. A document with no row cannot pass an IN-list filter or
appear with its label. Every label must be one of the call's labels.

```python
classify_answers = {
    "classify-1": pa.table({
        "r": ["rv17", "rv42"],
        "label": ["negative", "mixed"],
    }),
}
```

For joined rows, the table has two ID columns, one named by the anchor's
alias and one by the partner's, and the `label` column. It has one row per
joined row the engine classified. For IMDB-15:

```python
classify_answers = {
    "classify-2": pa.table({
        "r": ["rv17", "rv17"],
        "a": ["as0", "as6"],
        "label": ["negative", "mixed"],
    }),
}
```

A joined row with no answer has no label, so it does not appear in the
result, even when the join kept it.

`score_answers` maps each score operator ID to a table with the relation's
alias column and a float `score` column, one row per document the engine
scored. It does not change the result rows, which carry the scores the
query selects; QUAIL-B reads it for the token metrics of a query with a
score. For REL-AGENT-4:

```python
score_answers = {
    "score-1": pa.table({
        "t": ["tr17", "tr42"],
        "score": [0.91, 0.08],
    }),
}
```

With answer tables for every operator, `rows` must match the rows the answers
imply, label columns included.

### Label files

A classification label set stores `left_id` and a string `label` column where a
filter stores its boolean `answer`. The manifest's predicate lists the
`labels`.

- For a one-document classification, `right_id` is null.
- For joined rows, `left_id` holds the anchor's ID and `right_id` holds the
  partner's, as in a join's label set. The label set has a row only for the
  rows the reference join keeps.

## Extraction

`ai_extract(question, document)` returns the words of the document that
answer the question, copied from the document. It returns null when the
document does not answer the question. A span is a run of words copied from a
document, so an answer is one span or null.

- The question is the string argument as written. It has no placeholder.
- The call's `ProjectRel` adds one nullable string column to the relation.
  The column is named like a label column.
- A query returns its answers as fields of one struct column. The root
  `ProjectRel` builds the struct with a Substrait nested struct expression.
  The root names list the struct name, then its field names. For example,
  CUAD-1 returns a `contract` column of type `struct<governing_law: string>`.
- CUAD-10 returns three answers as three fields of `contract`. Each answer
  has its own extract operator, answer table, and reference spans.

### Prompt and reference answer

`render_extract_prompt` produces the text the reference answers were read
after:

```text
DOCUMENT:
<document>

Question: <question>

Copy the words that answer the question from the document. Copy them
exactly. Use the fewest words that answer it. If the document does not
answer the question, write none.
Answer in this format:
Phrase: "<copied words>"
Phrase: "
```

The text ends with the cue that opens the answer, `Phrase: "`. The answer is
the text up to the closing quote, located in the document. When the model
writes `none`, the answer is null.

An engine may wrap the text in the model's chat turn. The answers of the CUAD
queries were measured inside the chat turn. The document still comes first,
so an engine can reuse the document's KV across questions.

The CUAD reference answers are the spans lawyers marked, not a model's
answers. A document can have several spans for one question, or none. For
example, a contract that names its parties in the preamble and again in the
signature block has a span for its parties in each place.

### Answers

`extract_answers` maps each extract operator ID to a table with two columns:

- the relation's alias column, with one row per document the engine read;
- a string `answer` column, null where the engine found no answer.

For example, CUAD-1 asks for the governing law of each contract with the
operator `extract-1`. Suppose `contracts` has two rows, `ct17` and `ct42`. The
engine answers "New York" for `ct17` and finds no answer in `ct42`:

```python
extract_answers = {
    "extract-1": pa.table({
        "c": ["ct17", "ct42"],
        "answer": ["New York", None],
    }),
}
rows = pa.table({
    "c": ["ct17", "ct42"],
    "contract": pa.StructArray.from_arrays(
        [pa.array(["New York", None], pa.string())], names=["governing_law"]),
})
```

A null answer does not remove its row: `ct42` stays in `rows` with a null
`governing_law` field.

`features_answers` maps each SAE features operator ID to a table with two
columns:

- the relation's alias column, with one row per document the engine read;
- a `features` column of type
  `list<struct<feature: int32, strength: float32>>`, holding the nonzero
  features. The lists are non-null; a document with no nonzero feature
  holds an empty list.

The query returns the same lists in its feature list column, and the harness
checks that a returned list equals the answer table's.

- `rows` carries the same answers in the struct's fields. The harness checks
  that the struct's fields are strings named by their answer columns, and that
  they agree with the answer tables.
- QUAIL-B compares `rows` with the reference result on the ID and label
  columns only. It scores an answer from its answer table.

### Span accuracy

QUAIL-B scores an answer against its document's reference spans by the SQuAD
convention:

1. It normalizes the answer and each span: it lowercases them and drops
   punctuation, the articles a, an, and the, and extra spaces.
2. Exact match is 1 when the answer's words equal a span's words, and 0
   otherwise.
3. Token F1 is the harmonic mean of precision and recall over the words the
   answer and the span share. Precision is the share of the answer's words
   that are in the span. Recall is the share of the span's words that are in
   the answer.
4. The answer gets the scores of its best-matching span.

Each case scores as follows:

| Answer | Reference spans | Exact match and token F1 |
| --- | --- | --- |
| Null | None | 1 |
| Null | One or more | 0 |
| Not null | None | 0 |
| Not null | One or more | Those of the best-matching span |

Span accuracy is the mean token F1 over the engine's answers. `run.json` also
records the exact match rate.

For example, suppose the reference span for `ct17` is "the laws of the State
of New York", and `ct42` has no span. After normalization, the span has six
words and the answer "New York" has two, both in the span. Precision is 1,
recall is 1/3, and token F1 is 0.5; exact match is 0. The null answer for
`ct42` scores 1 on both. Span accuracy over the two answers is 0.75, and the
exact match rate is 0.5.

### Extraction label files

An extraction label set stores `left_id`, a null `right_id`, and an `answers`
column. `answers` lists the text of each reference span. The list is empty
where the document does not answer the question.

## Maps

`ai_map(prompt, document, max_tokens)` returns generated text, not an extracted
span or a label from a fixed list. Its Substrait signature is
`ai_map:str_str_i32`.

- `prompt` is a constant string of the form `"{0}\n\n<instruction>"`.
- `max_tokens` is a positive constant `i32`, limiting generated answer tokens,
  not input tokens.
- Decode greedily until the model ends the turn or reaches that limit.
- The call's `ProjectRel`, with hint alias `map-N`, adds a nonnull string
  column. Its output name is `alias.column`, as for a classification.
- Selected map columns are flat strings, not extraction's struct fields.

### Prompt rendering

The `raw-v1` layout is exactly:

```text
DOCUMENT:
<document>

<instruction>
ANSWER:
```

The shared preamble is `"DOCUMENT:\n"`. The tail is
`"\n\n<instruction>\nANSWER:"`, with no trailing newline. The layout adds no
phrase-format instruction or model-specific turn markers. As with extraction,
an adapter may wrap this text in the model's chat turn; wrapping is separate
from the raw layout and must be reflected in the reported token pieces.

### Answers and scoring

`map_answers` maps each map operator ID to a table with two columns:

- the relation's alias column, holding document IDs;
- a nonnull string `answer` column, with one row per evaluated document.

IDs must exist in the input table and occur at most once per map answer table.
A selected map column has the part of the plan output name after the alias as
its name. Its values must match the corresponding map answer table. For a map
returning `r.summary`:

```python
map_answers = {
    "map-1": pa.table({
        "r": ["rv17", "rv42"],
        "answer": ["A positive review.", "A mixed review."],
    }),
}
rows = pa.table({
    "r": ["rv17", "rv42"],
    "summary": ["A positive review.", "A mixed review."],
})
```

There are no reference map answers and no accuracy score for generated text.
Output scoring ignores map text, while IDs, filters, and classifications keep
their usual scoring rules when reference labels are available. For example,
IMDB-24 reuses IMDB-11's sentiment labels, but IMDB-23's new negative-review
filter has no reference labels yet.

The thirteen [map drafts](../README.md#map-queries) are excluded from the default
85 queries. Load them by ID with `accuracy=False`.

### Token accounting

In `prompt_pieces`, `maps` is a list of `{"id": "map-N", "tail": [...]}` entries,
one per map operator. Each evaluated document contributes the token lengths
of the shared preamble, the document, and that map's tail. The minimum uses
the same prefix-sharing rules as filters: repeated maps of one document can
reuse its prefix. Prompt pieces and `input_tokens` stop at the answer cue and
exclude generated answers, so input token throughput remains prompt-only.

For each row in each map answer table, the minimum also adds
`max(answer_tokens - 1, 0)`. QUAIL-B tokenizes the `answer` text with
`prompt_pieces["tokenizer"]`, without special tokens. Every answer token except
the last must be fed back once as a decode input. Empty and single-token answers
add zero. Count these decode inputs in `fresh_tokens` too: required generation
is not KV waste. The decode contribution is per answered document and map;
prompt prefixes still share KV as above.

## Measurements

`measurements` holds numbers the engine reports. QUAIL-B recognizes three keys:

| Key | Type | Meaning |
| --- | --- | --- |
| `evaluated_document_pairs` | nonnegative `int` | Pairs across all joins |
| `fresh_tokens` | nonnegative `int` | Positions run through model forward passes |
| `input_tokens` | nonnegative `int` | Full length of every evaluated prompt |

When every join has an answer table, QUAIL-B uses the sum of their row counts
in place of `evaluated_document_pairs`.

`input_tokens` counts every position of every evaluated prompt, including
positions read from KV. Report it when prompt pieces are unavailable. It
enables input token throughput and cost per million input tokens. Minimum
tokens and KV regret require `prompt_pieces`.

**Approximate KV regret.** Some engines, such as vLLM, tokenize each whole
prompt as one string. Their token count can differ slightly from the count the
pieces give, since a tokenizer can merge text across a piece boundary. Such an
engine reports `input_tokens` along with `prompt_pieces`. QUAIL-B then uses
the reported count, scales the prompt minimum by reported input tokens divided
by piece-derived input tokens, and then adds required map decode inputs without
scaling them. KV regret is floored at zero and marked approximate with
`regret_approximate`.

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
| `classifies` | `list[dict]` | One description per classification |
| `scores` | `list[dict]` | One `id` and `tail` token list per score, as for a filter |
| `maps` | `list[dict]` | One `id` and `tail` per map, through `ANSWER:` |
| `extracts` | `list[dict]` | One `id` and `tail` token list per extraction, as for a filter. The tail holds the question, the instruction, and the answer format, through the answer cue |
| `features` | `list[dict]` | One `id` and `tail` token list per SAE feature call, as for a filter. The tail is the question alone: the call generates no text and has no answer cue |

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

A classification of one document is described like a filter, with `id` and
`tail`. A classification of joined rows is described like a join, with `id`,
`anchor`, `frame`, `label`, and `tail`.

A classification's pieces come from the reference prompt that
`render_classify_prompt` produces, whatever prompt the engine sent. Its tail
holds the question, the labels by name, and the answer cue. For example, an
engine that lists AGENT-4's test results under letters still reports the
tail that lists them by name, so the minimum is the same for every engine
that uses one tokenizer.

Prompt pieces require:

- an answer table for every filter, join, classification, score, extraction,
  SAE feature call, and map;
- every such operator listed exactly once;
- `measurements["fresh_tokens"]`;
- the same tokenizer and token layout used during execution, or a reported
  `input_tokens` total when the engine tokenized full prompts itself.

QUAIL-B tokenizes the documents after execution and derives input tokens,
minimum tokens, recomputed tokens, and KV regret.

## Metric requirements

| Metric | Adapter data |
| --- | --- |
| Query time | `runtime_s` |
| Output precision, recall, F1, exact match | `rows` |
| Document throughput | `runtime_s`, for a query with zero joins |
| Join throughput | Answer tables for every join, or a reported pair count |
| Predicate-level accuracy | Predicate answers |
| Label accuracy | Classification answers |
| Span accuracy | Extraction answers |
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

### Predicate-level accuracy

Predicate-level accuracy is the share of the engine's filter and join answers
that match the reference labels. It counts only the tuples the engine
evaluated. `run.json` also records true and false positives and negatives for
each predicate.

### Label accuracy

Label accuracy is the share of the engine's classification answers that match
their reference labels. It is reported beside predicate-level accuracy, not
merged into it. `run.json` records `correct`, `evaluated`, `unlabeled`, and
`accuracy` for each classification and in total.

Each answer is counted by whether it has a reference label:

| Answer over | Reference label | Counted as |
| --- | --- | --- |
| A document | Present | `evaluated` |
| A document | Missing | Error |
| A joined row | Present | `evaluated` |
| A joined row | Missing | `unlabeled` |

`correct` counts the evaluated answers that match. Accuracy is `correct`
divided by `evaluated`, so unlabeled answers do not affect it.

A joined row can lack a reference label because reference labels exist only
for the rows the reference join keeps. An engine's join can keep other rows.
For example, if an engine's join keeps a review joined with "the soundtrack"
and the reference join does not, the engine's label for that row is
`unlabeled`.

The reference result needs a label for each of its joined rows. For IMDB-15,
these are the rows the reference join keeps whose review has a negative or mixed
reference sentiment. If one of them has no reference label, scoring raises an
error.

Accuracy is not a focus of this benchmark. See
[Reference answers](../README.md#reference-answers) for how the labels were
made.

### Throughput

| Metric | Definition |
| --- | --- |
| Document throughput | Input documents divided by `runtime_s` |
| Join throughput | Evaluated document pairs, summed across all joins, divided by `runtime_s` |
| Input token throughput | Input tokens divided by `runtime_s` |

### Tokens and KV

| Metric | Definition |
| --- | --- |
| Input tokens | Full evaluated prompts, including tokens served from KV |
| Fresh tokens | Input positions processed by model forward passes |
| Minimum tokens | Input positions required with an unlimited prefix KV cache |
| Recomputed tokens | Fresh tokens minus minimum tokens |
| KV regret | Recomputed tokens divided by fresh tokens, as a percentage |

#### Input tokens

Input tokens are the full length of every prompt that the plan evaluates. They
include tokens that the engine serves from KV. Thus input tokens depend on
which prompts the plan evaluates, not on how the engine computes them.
Generated map answers are excluded from input tokens and input token throughput.

#### Fresh tokens

Fresh tokens are the input positions that model forward passes process. They
measure model computation, including generated answer tokens fed back as decode
inputs. Two engines can have the same input tokens and different fresh tokens.

#### Minimum tokens

The minimum is the number of input tokens that the requests need when the KV of
every prompt prefix stays in memory. It counts each distinct prompt prefix one
time:

- A document counts one time, however many questions ask about it. Rows with
  the same text count as one document, even under different IDs or tables,
  because their prompts are the same.
- If two questions about one document start with the same tokens, these tokens
  count one time.
- In a join, the anchor document, the frame, and the partner label count one
  time for each anchor. The partner label is the same for every pair of that
  anchor.
- Each pair adds its partner document and its answer cue. If two partners of
  the same anchor start with the same tokens, these tokens count one time.

A classification counts as follows:

- The classification tail (the question, the labels, and the answer cue) counts
  like a filter question: one time for each document. If it starts with the
  same tokens as another question about the document, these tokens count one
  time.
- A classification of joined rows counts like a join.
- The minimum stops at the answer cue. It does not depend on how an engine
  reads the label.

For example, an engine asks one filter question about each of 100 reviews, and
then classifies each review. The minimum counts:

- the tokens of each review, one time;
- for each review, the filter question and the classification tail, less the
  tokens that they share at their start.

Maps share prompt prefixes by the same rules. In addition, each answered row of
each map contributes `max(answer_tokens - 1, 0)` required decode inputs, using
the answer table and the query's tokenizer without special tokens. This is
necessary generation, not KV regret; it does not change the prompt-only
`input_tokens` metric.

#### Recomputed tokens

Recomputed tokens are fresh tokens minus minimum tokens. They are the work that
a prefix KV cache with unlimited memory does not do. In the example above, these
are recomputed tokens:

- a review that the engine computes two times;
- a lettered list of categories that is longer than the reference tail;
- label tokens that the engine feeds after the answer cue to score each label.

#### KV regret

KV regret is recomputed tokens divided by fresh tokens, as a percentage. For
example, an engine that computes 100 fresh tokens for requests with a minimum
of 80 tokens has 20 recomputed tokens and a KV regret of 20%.

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
