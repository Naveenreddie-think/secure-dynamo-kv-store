from dynamokv.ring import HashRing


def _sample_keys(n):
    return [f"key-{i}" for i in range(n)]


def test_get_node_is_deterministic():
    ring = HashRing(nodes=["node-1", "node-2", "node-3"])
    key = "some-key"
    assert ring.get_node(key) == ring.get_node(key)


def test_get_node_raises_when_empty():
    ring = HashRing()
    try:
        ring.get_node("foo")
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass


def test_adding_a_node_moves_close_to_one_over_n_plus_one_keys():
    initial_nodes = [f"node-{i}" for i in range(10)]
    ring = HashRing(nodes=initial_nodes, virtual_nodes=150)
    keys = _sample_keys(10_000)
    before = {k: ring.get_node(k) for k in keys}

    ring.add_node("node-10")
    after = {k: ring.get_node(k) for k in keys}

    moved = [k for k in keys if before[k] != after[k]]
    fraction_moved = len(moved) / len(keys)
    expected = 1 / 11

    assert 0.7 * expected <= fraction_moved <= 1.3 * expected
    # every key that moved, moved specifically to the new node
    assert all(after[k] == "node-10" for k in moved)


def test_removing_a_node_moves_close_to_one_over_n_keys_and_leaves_others_untouched():
    initial_nodes = [f"node-{i}" for i in range(10)]
    ring = HashRing(nodes=initial_nodes, virtual_nodes=150)
    keys = _sample_keys(10_000)
    before = {k: ring.get_node(k) for k in keys}

    ring.remove_node("node-9")
    after = {k: ring.get_node(k) for k in keys}

    affected = [k for k in keys if before[k] == "node-9"]
    unaffected = [k for k in keys if before[k] != "node-9"]

    fraction_affected = len(affected) / len(keys)
    expected = 1 / 10
    assert 0.7 * expected <= fraction_affected <= 1.3 * expected

    # keys not owned by the removed node keep their exact same owner
    assert all(before[k] == after[k] for k in unaffected)
    # keys that were on the removed node all moved somewhere else
    assert all(after[k] != "node-9" for k in affected)


def test_distribution_is_reasonably_balanced():
    nodes = [f"node-{i}" for i in range(5)]
    ring = HashRing(nodes=nodes, virtual_nodes=150)
    keys = _sample_keys(10_000)

    counts = {n: 0 for n in nodes}
    for k in keys:
        counts[ring.get_node(k)] += 1

    mean = len(keys) / len(nodes)
    for n in nodes:
        assert counts[n] <= 2 * mean
