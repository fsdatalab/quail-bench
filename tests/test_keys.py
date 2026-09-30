"""CPU checks for the integer keys that scoring joins and checks run on."""

import numpy as np
import pyarrow as pa
import pytest

from quail_b import _keys
from quail_b._keys import has_duplicates, lookup, tuple_keys


@pytest.fixture(params=["dense", "sorted"])
def path(request, monkeypatch):
    if request.param == "sorted":
        monkeypatch.setattr(_keys, "DENSE_SLOTS_PER_ROW", -10**9)
    return request.param


def test_lookup_finds_each_pair_and_marks_missing_ones(path):
    left = pa.array(["r0", "r0", "r1", "r2"])
    right = pa.array(["a0", "a1", "a0", "a1"])
    answers = np.array([1, 0, 0, 1], dtype=np.int8)
    reference, wanted, count = tuple_keys(
        [left, right],
        [pa.array(["r2", "r0", "r9", "r1", "r1"]),
         pa.array(["a1", "a0", "a0", "a1", "a0"])])
    # r9 is not in the reference, and (r1, a1) is not a labeled pair
    assert list(lookup(reference, answers, count, wanted)) == [1, 1, -1, -1, 0]


def test_has_duplicates_sees_a_repeated_pair_and_null_partners(path):
    keys, _, count = tuple_keys([pa.array(["r0", "r1", "r0"]),
                                 pa.array(["a0", "a0", "a1"])])
    assert not has_duplicates(keys, count)
    keys, _, count = tuple_keys([pa.array(["r0", "r1", "r0"]),
                                 pa.array(["a0", "a0", "a0"])])
    assert has_duplicates(keys, count)
    # a filter's labels have no partner
    keys, _, count = tuple_keys([pa.array(["r0", "r1"]),
                                 pa.array([None, None], pa.string())])
    assert not has_duplicates(keys, count)
