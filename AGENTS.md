# QUAIL-B

QUAIL-B is an academic benchmark of 85 AI SQL queries over document tables.
It publishes Substrait query plans, input tables, reference labels, and a
scoring harness. It does not include an execution engine.

Drafts are excluded from the default 85 queries. Load them by ID with
`accuracy=False`:

- Extraction and SEC drafts: IMDB-16, IMDB-17, WRENCH-6 to WRENCH-8, CUAD-16,
  and SEC-1 to SEC-10 lack reference answers.
- SAE drafts: IMDB-18, IMDB-19, BIO-7, SUPPORT-7, and WRENCH-9 lack reference
  features.
- Map drafts: IMDB-20 to IMDB-28 and CUAD-17 to CUAD-21 generate
  text without reference map answers. IDs, filters, and classifications still
  use the usual scoring rules when reference labels are available.

Read `README.md` for the public benchmark contract and metric definitions.

## Repository map

- `quail_b/plans/`: Substrait 0.103 query plans and workload catalog.
- `quail_b/queries.py` and `quail_b/substrait.py`: plan loading and parsing.
- `quail_b/prompts.py`, `rendering.py`, and `predicates.py`: prompt definitions
  and exact rendered text.
- `quail_b/data.py` and `labels.py`: published inputs and reference labels.
- `quail_b/sec.py`: download SEC filings, select the sample, and check the data.
- `quail_b/run.py`, `scoring.py`, `minimum.py`, and `reporting.py`: adapter
  validation, scoring, token accounting, and result reports.
- `tests/`: tests for the public contract and benchmark data.
- `tools/`: checks and generators for plans and figures.

## Repository boundaries

- Keep this package engine-neutral. An engine adapter receives a `QuerySpec`
  and input tables, then returns a `RunOutput`.
- Do not add engine runtime code, GPU infrastructure, or experiment reports
  here.
- The AI operators are `ai_filter`, `ai_join`, `ai_classify`, `ai_score`,
  `ai_extract`, `ai_sae_features`, and `ai_map`. Relational operators appear in
  the `REL-` queries, the agent trace queries (`SUPPORT-`, `RUNS-`, `WRENCH-`),
  the sales call queries (`SALES-`), and the contract queries (`CUAD-`). Do not
  add speculation or forking unless the task changes the benchmark scope.
- `ai_map` greedily generates nonnull text under a positive constant answer
  token limit. Preserve its `raw-v1` prompt layout and keep generated answer
  tokens out of prompt-piece accounting. Map text has no accuracy score.
- Preserve query IDs, operator IDs, prompt text, corpus identities, scale
  factors, and label identities unless the task explicitly changes that
  public contract.
- When a query changes, update its plan, catalog entry, tests, and
  documentation together. Do not compare results from different query or
  corpus definitions.
- Do not commit benchmark run output, downloaded data, or local caches.

## Code and writing

- Use Python 3.12 and `uv` for dependencies and commands.
- Keep lines at or below 88 characters.
- Follow Google-style docstrings. Keep them short and describe behavior.
- Comments should explain constraints that the code cannot show.
- Use plain, direct language and short sentences.
- Call the benchmark QUAIL-B. Call the separate execution project Quail.
- Call the KV cache "KV".

## Verification

Run these checks before pushing:

```sh
uv run --frozen ruff check quail_b tests tools
uv run --frozen python tools/check_long_strings.py
uv run --frozen vulture
uv run --frozen pytest -q
```
