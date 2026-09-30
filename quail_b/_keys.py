"""Integer keys for id tuples, so joins and checks run on numbers."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

# a dense lookup table is used while it holds at most this many slots
# per keyed row; beyond that the keys are sorted instead
DENSE_SLOTS_PER_ROW = 4


def _codes(column, values: pa.Array) -> np.ndarray:
    """Return each value's index in `values`, or -1 when it is absent."""
    column = pc.cast(column, pa.string())
    codes = pc.index_in(column, value_set=values)
    return np.asarray(pc.fill_null(codes, -1), dtype=np.int64)


def tuple_keys(reference: list, others: list | None = None):
    """Encode rows of id columns as one int64 key per row.

    Args:
        reference: The id columns whose distinct values set the codes.
        others: Id columns of another table, in the same order, encoded
            with the reference's codes.

    Returns:
        (reference keys, other keys, key count): other keys are -1 where
        a value is not in the reference; None when `others` is None.
    """
    reference_keys = np.zeros(len(reference[0]), dtype=np.int64)
    other_keys = (None if others is None
                  else np.zeros(len(others[0]), dtype=np.int64))
    missing = None if others is None else np.zeros(len(others[0]), bool)
    count = 1
    for position, column in enumerate(reference):
        values = pc.unique(pc.cast(column, pa.string()))
        reference_keys = reference_keys * len(values) + _codes(column, values)
        if others is not None:
            codes = _codes(others[position], values)
            missing |= codes < 0
            other_keys = other_keys * len(values) + codes
        count *= len(values)
    if others is not None:
        other_keys[missing] = -1
    return reference_keys, other_keys, count


def has_duplicates(keys: np.ndarray, count: int) -> bool:
    """Whether any key repeats."""
    if count <= DENSE_SLOTS_PER_ROW * len(keys) + 1_000_000:
        return bool(len(keys)) and np.bincount(keys, minlength=count).max() > 1
    ordered = np.sort(keys)
    return bool(np.any(ordered[1:] == ordered[:-1]))


def lookup(keys: np.ndarray, values: np.ndarray, count: int,
           wanted: np.ndarray) -> np.ndarray:
    """Return the value of each wanted key, and -1 where it has none.

    Args:
        keys: Distinct keys.
        values: One int8 value per key.
        count: The number of possible keys.
        wanted: The keys to look up; -1 matches nothing.
    """
    if count <= DENSE_SLOTS_PER_ROW * len(keys) + 1_000_000:
        dense = np.full(count + 1, -1, dtype=np.int8)
        dense[keys] = values
        return dense[np.where(wanted < 0, count, wanted)]
    order = np.argsort(keys, kind="stable")
    ordered = keys[order]
    position = np.clip(np.searchsorted(ordered, wanted), 0, len(ordered) - 1)
    found = (ordered[position] == wanted) if len(ordered) else np.zeros(
        len(wanted), bool)
    result = np.full(len(wanted), -1, dtype=np.int8)
    result[found] = values[order[position[found]]]
    return result
