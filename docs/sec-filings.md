# SEC executive changes

QUAIL-B defines six SEC queries over a fixed sample of 100,000 unique 8-K
filings. They use extraction, filtering, classification, and aggregation.
The plans are available through `get_query("SEC-1")` through
`get_query("SEC-6")`.

The input builder is ready to use. Reference labels are not published yet,
so these queries are marked `draft` in the catalog and are excluded from the
default scored suite of 85 queries. Select the SEC queries explicitly and
run them separately at `scale_factor=1.0`. This workload has no smaller
scale factors.

## Inputs

The source is the `8-K` directory of
[TeraflopAI/SEC-EDGAR](https://huggingface.co/datasets/TeraflopAI/SEC-EDGAR),
pinned to revision `43de32c4c3cd4a56c26757345a4ec6db04fccda0`.
The dataset card lists 1,952,207 8-K records. QUAIL-B reads all 217 source
Parquet files before selecting the sample.

The builder keeps records with a valid accession number, a valid filing
date, and nonempty parsed text. For repeated accession numbers, it keeps the
first valid row in sorted source-file order. It ranks unique filings by
the SHA-256 hash of `"20260818:<accession>"` and keeps the lowest 100,000
hashes. It sorts the selected rows by accession number before saving them.
The sample is not restricted to executive changes, dates, companies, or
document lengths.

`sec_filings.parquet` has these columns:

| Column | Type | Meaning |
| --- | --- | --- |
| `id` | string | SEC accession number |
| `text` | string | The source's parsed `text`, unchanged |
| `filing_date` | string | Filing date as `YYYY-MM-DD` |
| `filing_month` | string | Filing month as `YYYY-MM` |

The builder does not truncate text, fetch additional exhibits, or use the
source's raw `content` column. Engines must handle the supplied document
lengths. A statement absent from the supplied text has no extraction answer,
even if another document on EDGAR contains it.

The manifest records the source revision, sampling rule, source counts, and
the hash of every input column in row order. The loader checks the table
against that manifest. The SEC corpus has its own identity and does not
change the identities of the existing published corpora.

## Queries

Every query reads `sec_filings AS f`. The exact prompts are in
[`quail_b/prompts.py`](../quail_b/prompts.py), and the Substrait plans are in
[`quail_b/plans/`](../quail_b/plans/).

| Query | Task | Operators |
| --- | --- | --- |
| SEC-1 | Find CEO departures | Filter |
| SEC-2 | Extract the departing CEO's name | Extract |
| SEC-3 | Extract the effective departure date | Extract |
| SEC-4 | Extract name, date, and reason for departures | Filter, three extractions |
| SEC-5 | Classify the immediate successor's status | Filter, classify |
| SEC-6 | Count filings by month and status | Filter, classify, group, count, sort |

SEC-1 returns `f`, the accession number. SEC-2 through SEC-4 return `f` and
an answer struct named `departure`. SEC-2 has the field `ceo_name`, and SEC-3
has `departure_date`. SEC-4 has `ceo_name`, `departure_date`, and
`departure_reason`. SEC-5 returns `f` and `successor_status`. SEC-6 returns
`filing_month`, `successor_status`, and `filings`, sorted by month and status.

A qualifying departure is an announced resignation, retirement, removal,
death, or other end of service as CEO of the filing company. Interim and
co-CEOs count. The departure can be effective now or in the future.
Hypothetical departures, past departures mentioned only as background, and
departures from other roles or companies do not count.

If a filing announces several qualifying departures, every field refers to
the departing CEO first mentioned in document order. SEC-6 counts filings,
so it counts that filing once. Its month comes from the stored filing date,
not the extracted departure date.

Extraction returns a copied span or null. SEC-2 and SEC-3 return one row per
input filing, including null answers. SEC-4 returns only filings kept by its
filter. Dates remain as written, including phrases such as "effective
immediately". Reasons must be explicit in the text.

The successor classification refers to the person taking over immediately.
An explicitly interim, acting, or temporary successor is `interim`. An
explicitly permanent or non-interim successor is `permanent`. An absent
successor or unstated status is `unspecified`. The absence of the word
"interim" alone does not establish permanent status.

## Building and loading the sample

From this repository, run:

```sh
uv run --frozen python tools/make_sec_corpus.py
```

The command downloads the required columns from the pinned source into
`results/sec-source/` and writes the input table and `manifest.json` into
`results/sec/`. Both directories are ignored by Git. Downloads are cached
by revision, and an existing completed sample is validated and reused.
Use `--source-dir` and `--output-dir` to choose other local directories.

Load the inputs and pass them to an existing engine adapter:

```python
import quail_b

suite = quail_b.load_benchmark(
    [f"SEC-{i}" for i in range(1, 7)],
    scale_factor=1.0,
    data_dir="results/sec",
    accuracy=False,
)

for query in suite.queries:
    output = my_engine_adapter(query, suite.tables)
    print(query.id, output.runtime_s)
```

The adapter receives the usual `QuerySpec` and PyArrow table and returns
`RunOutput`. QUAIL-B does not add an execution engine. To list the definitions,
use `quail_b.queries.queries(include_draft=True)`.

## Reference answers

The source dataset supplies documents and metadata, not answers to these
questions. `SEC_PREDICATES` in `quail_b/predicates.py` defines the five distinct
questions used across the six plans. The extraction questions reuse the
existing span and null scoring contract.

A future reference collection must include filter answers for the corpus,
name and date spans for every filing, and reason spans and successor labels
for the reference filter's matching filings. Empty span lists represent
absent answers. Reuse the same labels wherever a question recurs.

`quail_b.run` requires reference labels. Until they exist, use the input
loading example above for adapter runs. A completed collection can be
passed explicitly with `collection_id`, using the SEC manifest's corpus
identity and the existing label-file format. Do not use model outputs as
reference labels without recording and reviewing how they were produced.
