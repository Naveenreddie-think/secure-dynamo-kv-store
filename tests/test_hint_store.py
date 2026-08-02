from dynamokv.hint_store import HintStore
from dynamokv.storage.memory import MemoryStorage
from dynamokv.vector_clock import VectorClock, Version


def _store():
    return HintStore(MemoryStorage())


def test_add_then_pending_for_round_trips():
    store = _store()
    v = Version("bar", VectorClock({"node-1": 1}))
    store.add("node-3", "foo", v)

    pending = store.pending_for("node-3")
    assert list(pending.keys()) == ["foo"]
    assert pending["foo"] == [v]


def test_has_pending_reflects_state():
    store = _store()
    assert store.has_pending("node-3") is False

    store.add("node-3", "foo", Version("bar", VectorClock({"node-1": 1})))
    assert store.has_pending("node-3") is True


def test_pending_for_unknown_target_is_empty():
    store = _store()
    assert store.pending_for("node-99") == {}


def test_repeated_add_to_same_key_compacts_via_reconcile_dominated_dropped():
    store = _store()
    store.add("node-3", "foo", Version("v1", VectorClock({"node-1": 1})))
    store.add("node-3", "foo", Version("v2", VectorClock({"node-1": 2})))  # supersedes v1

    pending = store.pending_for("node-3")
    assert len(pending["foo"]) == 1
    assert pending["foo"][0].value == "v2"


def test_repeated_add_to_same_key_keeps_concurrent_siblings():
    store = _store()
    store.add("node-3", "foo", Version("v1", VectorClock({"node-1": 1})))
    store.add("node-3", "foo", Version("v2", VectorClock({"node-2": 1})))  # concurrent, not dominated

    pending = store.pending_for("node-3")
    assert {v.value for v in pending["foo"]} == {"v1", "v2"}


def test_clear_key_leaves_sibling_keys_for_same_target_untouched():
    store = _store()
    store.add("node-3", "foo", Version("a", VectorClock({"node-1": 1})))
    store.add("node-3", "bar", Version("b", VectorClock({"node-1": 1})))

    store.clear_key("node-3", "foo")

    pending = store.pending_for("node-3")
    assert list(pending.keys()) == ["bar"]
    assert store.has_pending("node-3") is True


def test_clearing_last_key_removes_the_blob_entirely():
    store = _store()
    store.add("node-3", "foo", Version("a", VectorClock({"node-1": 1})))

    store.clear_key("node-3", "foo")

    assert store.has_pending("node-3") is False
    assert store.pending_for("node-3") == {}


def test_hints_for_different_targets_never_collide():
    store = _store()
    store.add("node-2", "foo", Version("for-node-2", VectorClock({"node-1": 1})))
    store.add("node-3", "foo", Version("for-node-3", VectorClock({"node-1": 1})))

    assert store.pending_for("node-2")["foo"][0].value == "for-node-2"
    assert store.pending_for("node-3")["foo"][0].value == "for-node-3"
