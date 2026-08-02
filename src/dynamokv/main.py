from typing import Dict, List, Optional

from fastapi import FastAPI

from dynamokv import config
from dynamokv.api.routes import router
from dynamokv.node import Node
from dynamokv.ring import HashRing
from dynamokv.storage.base import StorageBackend
from dynamokv.storage.memory import MemoryStorage
from dynamokv.storage.sqlite import SqliteStorage


def _build_storage_from_config() -> StorageBackend:
    if config.STORAGE_BACKEND == "memory":
        return MemoryStorage()
    return SqliteStorage(config.DB_PATH)


def _cluster_nodes_from_config() -> List[str]:
    return [n.strip() for n in config.CLUSTER_NODES.split(",") if n.strip()]


def create_app(
    storage: Optional[StorageBackend] = None,
    node_id: Optional[str] = None,
    cluster_nodes: Optional[List[str]] = None,
    n: Optional[int] = None,
    r: Optional[int] = None,
    w: Optional[int] = None,
) -> FastAPI:
    """Build the app. Production wires storage, cluster membership, and N/R/W
    from env vars; tests inject a MemoryStorage directly for isolation and
    speed, which also defaults them to an isolated single-node cluster (no
    forwarding, quorum-of-1) unless they explicitly opt into a multi-node
    cluster_nodes list and/or explicit n/r/w. That "storage is None means
    production" signal matters: without it, tests that don't pass
    cluster_nodes would default to whatever CLUSTER_NODES happens to be in
    the environment, which won't contain their own node_id, and every call
    would try to forward to a nonexistent peer.
    """
    app = FastAPI(title="dynamokv")
    production_mode = storage is None
    resolved_storage = storage if storage is not None else _build_storage_from_config()
    resolved_node_id = node_id if node_id is not None else config.NODE_ID

    if cluster_nodes is not None:
        resolved_cluster_nodes = cluster_nodes
    elif production_mode:
        resolved_cluster_nodes = _cluster_nodes_from_config()
    else:
        resolved_cluster_nodes = [resolved_node_id]

    resolved_n = n if n is not None else (config.N if production_mode else 1)
    resolved_r = r if r is not None else (config.R if production_mode else 1)
    resolved_w = w if w is not None else (config.W if production_mode else 1)

    ring = HashRing(nodes=resolved_cluster_nodes, virtual_nodes=config.VIRTUAL_NODES)
    peers: Dict[str, str] = {
        nid: f"http://{nid}:{config.PORT}"
        for nid in resolved_cluster_nodes
        if nid != resolved_node_id
    }

    app.state.node = Node(
        node_id=resolved_node_id,
        storage=resolved_storage,
        ring=ring,
        peers=peers,
        n=resolved_n,
        r=resolved_r,
        w=resolved_w,
    )
    app.include_router(router)

    return app


app = create_app()
