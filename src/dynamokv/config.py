import os
import warnings

# Deliberately minimal: just enough for each phase to run. The full config
# system (Pydantic Settings, TLS paths) is Phase 9 scope.

NODE_ID = os.environ.get("NODE_ID", "node-1")
STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "sqlite")
DB_PATH = os.environ.get("DB_PATH", f"data/{NODE_ID}.db")

# CLUSTER_NODES: comma-separated node_ids. Peer URLs are derived by
# convention as http://{node_id}:{PORT}, relying on Docker Compose's
# service-name DNS resolution -- no need to configure full URLs by hand.
CLUSTER_NODES = os.environ.get("CLUSTER_NODES", NODE_ID)
PORT = int(os.environ.get("PORT", "8000"))
VIRTUAL_NODES = int(os.environ.get("VIRTUAL_NODES", "150"))

# N/R/W quorum defaults, matching PLAN.md's own example (N=3, R=2, W=2).
N = int(os.environ.get("N", "3"))
R = int(os.environ.get("R", "2"))
W = int(os.environ.get("W", "2"))

if W + R <= N:
    warnings.warn(
        f"W + R <= N (W={W}, R={R}, N={N}) -- read and write quorums are not "
        "guaranteed to overlap, so reads may miss the latest write.",
        stacklevel=1,
    )

# Gossip: how often a node exchanges heartbeats with a random peer, and how
# many missed intervals before that peer is presumed down. 6s to
# believed-down by default -- comfortably inside PLAN.md's 60s test window.
GOSSIP_INTERVAL_SECONDS = float(os.environ.get("GOSSIP_INTERVAL_SECONDS", "2.0"))
GOSSIP_FAILURE_TIMEOUT_MULTIPLIER = float(os.environ.get("GOSSIP_FAILURE_TIMEOUT_MULTIPLIER", "3"))
