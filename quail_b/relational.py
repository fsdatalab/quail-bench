"""Reference results and accuracy for queries with relational operators.

A relational query reads one relation, tests columns, filters, labels,
or scores its documents with the model, and then groups, sorts, and
bounds the rows. The reference result applies the same steps with the
saved labels in place of the model: a filter keeps the documents
labeled TRUE, a label column holds the reference label, and a score
column holds 1.0 for a document labeled TRUE and 0.0 otherwise.
"""

from __future__ import annotations

import pandas as pd
import pyarrow as pa

from quail_b.data import _ids
from quail_b.queries import QuerySpec
from quail_b.substrait import Aggregate, Fetch, Having, Sort, output_name

_COMPARE = {
    "=": pd.Series.eq, "<>": pd.Series.ne, "<": pd.Series.lt,
    "<=": pd.Series.le, ">": pd.Series.gt, ">=": pd.Series.ge,
}
_ROUND = 6


def _labels(ground_truth, template: str):
    return ground_truth.predicates[ground_truth.key_for_template(template)]


def reference_table(spec: QuerySpec, ground_truth, corpus_rows) -> pd.DataFrame:
    """Return the AI tree's rows under the reference labels.

    Columns are named as the plan's fields: `alias.column` for source,
    label, and score columns.

    Raises:
        KeyError: A document the query reads has no label.
    """
    info = spec.info
    (relation,) = info.relations
    rows = corpus_rows[relation.table]
    table = (rows.to_pandas() if isinstance(rows, pa.Table)
             else pd.DataFrame(list(rows)))
    table["id"] = [str(row_id) for row_id in _ids(rows)]
    table = table.rename(columns={
        name: f"{relation.alias}.{name}" for name in table.columns})
    ids = table[f"{relation.alias}.id"]
    for test in info.column_tests:
        column = table[f"{test.relation}.{test.column}"]
        table = table[_COMPARE[test.comparison](column, test.value).fillna(False)]
        ids = table[f"{relation.alias}.id"]
    for operator in info.operators:
        if operator in info.column_tests:
            continue
        if operator in info.in_lists:
            column = table[f"{operator.relation}.{operator.output}"]
            table = table[column.isin(operator.accepted)]
            ids = table[f"{relation.alias}.id"]
            continue
        labels = _labels(ground_truth, operator.prompt)
        answers = labels.answers
        unknown = [row_id for row_id in ids if (row_id, None) not in answers]
        if unknown:
            raise KeyError(
                f"no ground truth for {labels.key} and {len(unknown)} rows")
        values = [answers[(row_id, None)] for row_id in ids]
        if operator in info.filters:
            table = table[[bool(value) for value in values]]
        elif operator in info.scores:
            table[f"{operator.relation}.{operator.output}"] = [
                1.0 if value else 0.0 for value in values]
        else:
            table[f"{operator.relation}.{operator.output}"] = values
        ids = table[f"{relation.alias}.id"]
    return table.reset_index(drop=True)


def _aggregate(table: pd.DataFrame, step: Aggregate) -> pd.DataFrame:
    """Group a table by the step's keys and compute its measures."""
    if not step.measures:
        return table[list(step.keys)].drop_duplicates().reset_index(drop=True)
    if step.keys:
        grouped = table.groupby(list(step.keys), sort=False, dropna=False)
    else:
        grouped = table.groupby(lambda _index: 0, sort=False)
    columns = {}
    for name, function, argument in step.measures:
        if function == "count" and argument is None:
            columns[name] = grouped.size()
        elif function == "count":
            columns[name] = grouped[argument].count()
        elif function == "count_distinct":
            columns[name] = grouped[argument].nunique()
        else:
            columns[name] = getattr(grouped[argument],
                                    {"avg": "mean"}.get(function, function))()
    result = pd.DataFrame(columns)
    if step.keys:
        result = result.reset_index()
    else:
        result = result.reset_index(drop=True)
    return result


def apply_tail(table: pd.DataFrame, spec: QuerySpec,
               fetch: bool = True) -> pd.DataFrame:
    """Apply the plan's relational steps to a table of its fields.

    Args:
        table: The AI tree's rows, columns named as the plan's fields.
        spec: The query.
        fetch: Whether to apply the fetch step.
    """
    for step in spec.info.tail:
        if isinstance(step, Aggregate):
            table = _aggregate(table, step)
        elif isinstance(step, Having):
            for name, comparison, value in step.tests:
                table = table[_COMPARE[comparison](table[name], value)]
        elif isinstance(step, Sort):
            table = table.sort_values(
                [name for name, _ in step.keys],
                ascending=[not descending for _, descending in step.keys],
                kind="stable", na_position="last")
        elif isinstance(step, Fetch) and fetch:
            table = table.iloc[step.offset:step.offset + step.count]
    return table.reset_index(drop=True)


def _select(table: pd.DataFrame, spec: QuerySpec) -> pd.DataFrame:
    selected = table[list(spec.info.select)]
    return selected.rename(columns={
        name: output_name(name) for name in spec.info.select})


def expected_result(spec: QuerySpec, ground_truth, corpus_rows) -> pa.Table:
    """Return the rows the labels say a relational query should return."""
    table = apply_tail(reference_table(spec, ground_truth, corpus_rows), spec)
    return pa.Table.from_pandas(_select(table, spec), preserve_index=False)


def score_fields(spec: QuerySpec) -> set[str]:
    """Return the fields whose values come from a score: scores and their measures."""
    fields = {f"{score.relation}.{score.output}" for score in spec.info.scores}
    aggregate = spec.info.aggregate
    if aggregate is not None:
        fields.update(name for name, _function, argument in aggregate.measures
                      if argument in fields)
    return fields


def _rows(table: pa.Table | pd.DataFrame, columns) -> list[tuple]:
    """Return the rows as tuples of strings, numbers rounded."""
    frame = table.to_pandas() if isinstance(table, pa.Table) else table
    rows = []
    for values in frame[list(columns)].itertuples(index=False, name=None):
        rows.append(tuple(
            str(round(float(value), _ROUND)) if isinstance(value, float)
            else str(value) for value in values))
    return rows


def _row_metrics(predicted: list[tuple], expected: list[tuple]) -> dict:
    from quail_b.scoring import _row_metrics as metrics

    remaining = {}
    for row in expected:
        remaining[row] = remaining.get(row, 0) + 1
    matched = 0
    for row in predicted:
        if remaining.get(row):
            remaining[row] -= 1
            matched += 1
    return metrics(len(predicted), len(expected), matched)


def relational_accuracy(spec: QuerySpec, rows: pa.Table, ground_truth,
                        corpus_rows) -> dict:
    """Score a relational query's rows against the reference result.

    Without a score column, the result is determined by the labels, so
    the rows are compared as a multiset, and as a sequence when the
    query sorts. With a score column feeding a sort and a fetch, the
    reference ranks ties arbitrarily, so the rows are scored as the
    fraction whose non-score columns are among the rows that rank at
    least as well as the fetch's last row under the reference scores:
    precision at k.
    """
    info = spec.info
    columns = [output_name(name) for name in info.select]
    scored = score_fields(spec)
    sort_scored = info.sort is not None and any(
        name in scored for name, _ in info.sort.keys)
    reference = reference_table(spec, ground_truth, corpus_rows)
    if not (sort_scored and info.fetch is not None):
        expected = _select(apply_tail(reference, spec), spec)
        predicted = _rows(rows, columns)
        result = _row_metrics(predicted, _rows(expected, columns))
        if info.sort is not None:
            result["ordered_match"] = predicted == _rows(expected, columns)
        return result
    ranked = apply_tail(reference, spec, fetch=False)
    identity = [name for name in info.select if name not in scored]
    keys = [(name, descending) for name, descending in info.sort.keys
            if name in scored]
    fetch = info.fetch
    last = fetch.offset + fetch.count - 1
    if len(ranked) <= last:
        eligible = ranked
    else:
        threshold = ranked.iloc[last]
        mask = pd.Series(True, index=ranked.index)
        for name, descending in keys:
            mask &= (ranked[name] >= threshold[name] if descending
                     else ranked[name] <= threshold[name])
        eligible = ranked[mask]
    eligible_rows = set(_rows(eligible, identity))
    predicted = _rows(rows, [output_name(name) for name in identity])
    hits = sum(row in eligible_rows for row in predicted)
    return {
        "predicted_rows": len(predicted),
        "k": fetch.count,
        "eligible_rows": len(eligible_rows),
        "matching_rows": hits,
        "precision_at_k": round(hits / len(predicted), 6) if predicted else 0.0,
        "exact_match": bool(predicted) and hits == len(predicted),
    }
