import random

from dynamokv.vector_clock import VectorClock, Version, reconcile


def test_compare_equal():
    a = VectorClock({"node-1": 2, "node-2": 1})
    b = VectorClock({"node-1": 2, "node-2": 1})
    assert a.compare(b) == "equal"
    assert a == b


def test_compare_equal_treats_missing_entries_as_zero():
    a = VectorClock({"node-1": 2})
    b = VectorClock({"node-1": 2, "node-2": 0})
    assert a.compare(b) == "equal"


def test_compare_dominates_and_dominated():
    a = VectorClock({"node-1": 2, "node-2": 1})
    b = VectorClock({"node-1": 1, "node-2": 1})
    assert a.compare(b) == "dominates"
    assert b.compare(a) == "dominated"


def test_compare_concurrent():
    a = VectorClock({"node-1": 1})
    b = VectorClock({"node-2": 1})
    assert a.compare(b) == "concurrent"
    assert b.compare(a) == "concurrent"


def test_compare_self():
    a = VectorClock({"node-1": 3})
    assert a.compare(a) == "equal"


def test_incremented_does_not_mutate_original():
    a = VectorClock({"node-1": 1})
    b = a.incremented("node-1")
    assert a.get("node-1") == 1
    assert b.get("node-1") == 2


def test_merge_takes_elementwise_max():
    a = VectorClock({"node-1": 3, "node-2": 1})
    b = VectorClock({"node-1": 1, "node-2": 5, "node-3": 2})
    merged = VectorClock.merge([a, b])
    assert merged.counters == {"node-1": 3, "node-2": 5, "node-3": 2}


def test_merge_of_empty_is_empty():
    assert VectorClock.merge([]).counters == {}


def test_reconcile_sequential_increments_never_conflict():
    v1 = Version("a", VectorClock({"node-1": 1}))
    v2 = Version("b", VectorClock({"node-1": 2}))
    result = reconcile([v1], v2)
    assert result == [v2]


def test_reconcile_concurrent_versions_both_survive():
    v1 = Version("a", VectorClock({"node-1": 1}))
    v2 = Version("b", VectorClock({"node-2": 1}))
    result = reconcile([v1], v2)
    assert set(r.value for r in result) == {"a", "b"}


def test_reconcile_drops_dominated_existing_entries():
    v1 = Version("a", VectorClock({"node-1": 1}))
    v2 = Version("b", VectorClock({"node-1": 1, "node-2": 1}))
    result = reconcile([v1], v2)
    assert result == [v2]


def test_reconcile_drops_stale_incoming():
    v1 = Version("a", VectorClock({"node-1": 1, "node-2": 1}))
    v2 = Version("b", VectorClock({"node-1": 1}))
    result = reconcile([v1], v2)
    assert result == [v1]


def test_reconcile_dedupes_identical_retransmission():
    v1 = Version("a", VectorClock({"node-1": 1}))
    v1_again = Version("a", VectorClock({"node-1": 1}))
    result = reconcile([v1], v1_again)
    assert result == [v1]


def _brute_force_maximal(versions):
    return [
        v
        for i, v in enumerate(versions)
        if not any(j != i and other.clock.compare(v.clock) == "dominates" for j, other in enumerate(versions))
    ]


def _as_clock_set(versions):
    return frozenset(frozenset(v.clock.counters.items()) for v in versions)


def test_reconcile_matches_brute_force_maximal_antichain_regardless_of_order():
    rng = random.Random(1234)
    node_ids = ["node-1", "node-2", "node-3"]

    for _trial in range(20):
        candidates = []
        for i in range(rng.randint(3, 8)):
            counters = {n: rng.randint(0, 3) for n in node_ids if rng.random() < 0.6}
            candidates.append(Version(f"val-{i}", VectorClock(counters)))

        expected = _as_clock_set(_brute_force_maximal(candidates))

        for _shuffle in range(5):
            shuffled = candidates[:]
            rng.shuffle(shuffled)
            survivors: list = []
            for v in shuffled:
                survivors = reconcile(survivors, v)
            assert _as_clock_set(survivors) == expected
