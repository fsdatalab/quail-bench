# SEC filings

Use these six queries to test an engine on 100,000 company filings. The
queries find CEO departures, extract details about them, and classify the
people who replace the departing CEOs.

The documents are 8-K filings, which companies file with the SEC to report
important events. They come from
[TeraflopAI/SEC-EDGAR](https://huggingface.co/datasets/TeraflopAI/SEC-EDGAR).
The sample includes filings about all topics, not just CEO changes.

You can download the data and run the queries with your engine. Reference
answers are not available yet, so you cannot measure accuracy with this
sample yet. The queries are separate from the default set of 85 queries.

## Download the data

Run this command from the repository:

```sh
uv run --frozen python tools/make_sec_corpus.py
```

The command reads the required columns from all 217 source files and selects
100,000 unique filings. It saves these files:

- `results/sec/sec_filings.parquet` contains the selected filings.
- `results/sec/manifest.json` records the source, sample rules, and file checks.
- `results/sec-source/` contains downloaded data for reuse.

Git ignores both directories. Running the command again reuses the downloads
and checks any completed sample. Use `--source-dir` and `--output-dir` to
choose other directories.

## Load the queries

Load all six queries and pass each one to your engine adapter:

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

Replace `my_engine_adapter` with your adapter function. It receives a
`QuerySpec` and a dictionary of PyArrow tables, and returns a `RunOutput`.
QUAIL-B supplies the queries and data; your engine executes them.

Use `scale_factor=1.0` for the full 100,000 filings. Smaller samples are not
supported. Run the SEC queries separately from the other queries.

Use `accuracy=False` because reference answers are not available.
`quail_b.run` requires reference answers, so use the adapter loop above for
now. The SEC queries are marked `draft` in the catalog. To list them along
with the other queries, use `quail_b.queries.queries(include_draft=True)`.

## What the queries do

Every query reads the `sec_filings` table. In the plans, `f` refers to a
filing's ID.

| Query | Task |
| --- | --- |
| SEC-1 | Keep filings that announce a CEO departure. |
| SEC-2 | Extract the departing CEO's name from each filing. |
| SEC-3 | Extract the date when the CEO leaves from each filing. |
| SEC-4 | Keep CEO departure filings, then extract the name, date, and reason. |
| SEC-5 | Keep CEO departure filings, then classify the replacement CEO. |
| SEC-6 | Count those filings by filing month and replacement CEO category. |

A departure means the CEO resigns, retires, is removed, dies, or otherwise
ends their service at the filing company. Interim CEOs and co-CEOs count.
The departure may take effect now or in the future. Hypothetical departures,
past departures mentioned as background, and departures from other roles or
companies do not count.

If a filing announces more than one CEO departure, use the first departing
CEO mentioned in the text for every answer.

Extracted answers must be copied from the filing. Return null when an answer
is missing. Keep dates as written, such as "effective immediately". Return
a reason only when the filing states one. SEC-2 and SEC-3 return one row for
every filing. SEC-4 returns rows only for filings that pass the filter.

For SEC-5 and SEC-6, classify the person who takes over immediately:

- `interim` means the filing says the replacement is interim, acting, or temporary.
- `permanent` means the filing says the replacement is permanent or non-interim.
- `unspecified` means no replacement is named or their status is not stated.

Do not assume a replacement is permanent just because "interim" is absent.
SEC-6 counts each matching filing once. It uses the filing month, not the
extracted departure date, and sorts by month and category.

### Returned columns

| Query | Columns |
| --- | --- |
| SEC-1 | `f` |
| SEC-2 | `f`, `departure` with field `ceo_name` |
| SEC-3 | `f`, `departure` with field `departure_date` |
| SEC-4 | `f`, `departure` with `ceo_name`, `departure_date`, `departure_reason` |
| SEC-5 | `f`, `successor_status` |
| SEC-6 | `filing_month`, `successor_status`, `filings` |

`departure` is a PyArrow struct column that groups the extracted fields.
`successor_status` contains one of the three categories above.

The exact questions are in [`quail_b/prompts.py`](../quail_b/prompts.py).
The query plans are in [`quail_b/plans/`](../quail_b/plans/).

## How the sample is selected

The download uses the dataset's `8-K` directory at revision
`43de32c4c3cd4a56c26757345a4ec6db04fccda0`. It contains 1,952,207 records.
The same source and selection rules produce the same sample each time:

1. Read the source files in filename order.
2. Keep rows with a valid SEC accession number, a valid date, and nonempty text.
   An accession number is the SEC's identifier for a filing.
3. For duplicate accession numbers, keep the first valid row.
4. Compute SHA-256 for `"20260818:<accession>"` and select the 100,000 lowest hashes.
5. Sort the selected filings by accession number.

There are no extra restrictions on topic, date, company, or document length.
The saved table has these columns, all stored as strings:

| Column | Value |
| --- | --- |
| `id` | SEC accession number |
| `text` | Parsed text from the source, unchanged |
| `filing_date` | Date in `YYYY-MM-DD` format |
| `filing_month` | Month in `YYYY-MM` format |

The download does not use the raw `content` column or fetch extra exhibits.
It does not shorten the text. Engines must handle the supplied text lengths
and answer only from that text.

The manifest records the source revision, sample rules, row counts, and a
hash of each column in row order. The loader checks the table against those
values. The sample has its own corpus ID, which identifies the input data.
Existing datasets and their IDs are unchanged.

## Adding reference answers later

Hugging Face supplies the filings, but not answers to these queries. A
reference answer collection must include:

- A CEO departure filter answer for every filing.
- Name and date answers for every filing.
- Reason answers and replacement CEO categories for filings that pass the
  reference filter.

Use an empty list for a missing extraction answer. Reuse the same reference
answers when a question appears in more than one query. The five questions
are defined in `SEC_PREDICATES` in `quail_b/predicates.py`.

Use the existing reference label format and the SEC sample's corpus ID.
Record and review how model-generated reference answers were produced.
Once the collection is ready, pass its `collection_id` to `quail_b.run` to
score results with the existing extraction and classification metrics.
