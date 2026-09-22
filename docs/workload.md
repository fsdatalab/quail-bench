# Workload

QUAIL-B tests AI filters and joins inside relational query plans. Its 31
default queries vary operator order, filter placement, and join topology while
using fixed prompts and document sets.

## AI operators

The workload adds two scalar functions to standard Substrait:

```text
ai_filter(prompt, document) -> boolean
ai_join(prompt, left_document, right_document) -> boolean
```

An AI filter decides whether one document satisfies a prompt. An AI join
decides whether a pair of documents satisfies a prompt.

The plans combine these functions with scans, ordinary equality conditions,
boolean conjunction, and projection. Prompts are string literals in the plans.

## Query families

| Family | Queries | Tables | Task |
| --- | ---: | --- | --- |
| IMDB | IMDB-1 to IMDB-10 | `reviews`, `aspects` | Review aspects and sentiment |
| BioDEX | BIO-1 to BIO-4 | `reports`, `terms` | Adverse drug reactions |
| FEVER | FEV-1 to FEV-10 | `claims`, `evidence` | Fact verification |
| LePaRD | LEP-1 to LEP-5 | `citation_contexts`, `citation_passages` | Legal citations |
| SWE-Next | AGENT-1 to AGENT-2 | `agent_traces` | Software agent trajectories |

The workload includes:

- individual filters and joins;
- chains of filters on one relation;
- filters pushed to both sides of a join;
- multiple joins against aliases of the same table;
- chains of three joins;
- joins constrained by ordinary equality conditions.

The exact query order and descriptions are in
[`quail_b/plans/catalog.json`](../quail_b/plans/catalog.json). Each query plan
is stored beside it as Substrait ProtoJSON.

## Adapter implementation sequence

These queries introduce the main execution shapes in increasing complexity:

| Query | Shape | Purpose |
| --- | --- | --- |
| IMDB-1 | One filter | Validate scans, prompt rendering, and final IDs |
| IMDB-2 | One join | Validate pair evaluation and output with two columns |
| IMDB-4 | Two filters, then one join | Validate operator order and pushdown |
| FEV-5 | Filters on both join inputs | Validate pushdown on both sides |
| IMDB-8 | Two joins with one anchor | Validate aliases of one table |
| FEV-8 | Chain of three joins | Validate execution with multiple joins |
| FEV-10 | Filtered join with equality | Validate ordinary and AI conditions |

Passing these queries does not replace running the full workload. It provides
shorter checkpoints while implementing an adapter.

## Worked plan: IMDB-4

IMDB-4 asks for reviews that pass two filters and the aspects discussed by
those reviews:

```sql
SELECT r.id, a.id
FROM reviews AS r
AI JOIN aspects AS a ON J1(r.body, a.aspect)
WHERE AI_FILTER(F1, r.body)
  AND AI_FILTER(F4, r.body);
```

Its operator tree is:

```text
Project [r.id, a.id]
└── AI Join J1                                      join-1
    ├── AI Selection F4                             filter-2
    │   └── AI Selection F1                         filter-1
    │       └── Scan reviews AS r
    └── Scan aspects AS a
```

The plan supplies `reviews` and `aspects` as physical table names. It uses `r`
and `a` as aliases. The adapter therefore receives tables keyed by `reviews`
and `aspects`, but returns final columns named `r` and `a`.

`F1`, `F4`, and `J1` represent prompt templates stored as string literals in
the plan. `filter-1`, `filter-2`, and `join-1` are operator IDs used by optional
predicate traces.

## Scale factors

QUAIL-B publishes scale factors 0.1, 0.5, and 1.0. A scale factor selects a
fixed input corpus and its matching reference labels. It does not change the
query plans.

| Dataset | Table | 0.1 | 0.5 | 1.0 |
| --- | --- | ---: | ---: | ---: |
| IMDB | `reviews` | 5,000 | 25,000 | 50,000 |
| IMDB | `aspects` | 12 | 12 | 12 |
| BioDEX | `reports` | 500 | 2,500 | 5,000 |
| BioDEX | `terms` | 1,127 | 2,934 | 4,144 |
| FEVER | `claims` | 500 | 2,500 | 5,000 |
| FEVER | `evidence` | 287 | 1,037 | 1,478 |
| LePaRD | `citation_contexts` | 500 | 2,496 | 4,972 |
| LePaRD | `citation_passages` | 433 | 1,756 | 2,991 |
| SWE-Next | `agent_traces` | 1,772 | 8,859 | 17,711 |

Scale factor 0.1 is intended for adapter development. Larger factors measure
scaling after the adapter executes all query shapes correctly.
