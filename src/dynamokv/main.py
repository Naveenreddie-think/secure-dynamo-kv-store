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
) -> FastAPI:
    """Build the app. Production wires storage AND cluster membership from
    env vars; tests inject a MemoryStorage directly for isolation and speed,
    which also defaults them to an isolated single-node cluster (no
    forwarding) unless they explicitly opt into a multi-node cluster_nodes
    list. That "storage is None means production" signal matters: without
    it, tests that don't pass cluster_nodes would default to whatever
    CLUSTER_NODES happens to be in the environment, which won't contain
    their own node_id, and every call would try to forward to a nonexistent
    peer.
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

    ring = HashRing(nodes=resolved_cluster_nodes, virtual_nodes=config.VIRTUAL_NODES)
    peers: Dict[str, str] = {
        nid: f"http://{nid}:{config.PORT}"
        for nid in resolved_cluster_nodes
        if nid != resolved_node_id
    }

    app.state.node = Node(
        node_id=resolved_node_id, storage=resolved_storage, ring=ring, peers=peers
    )
    app.include_router(router)

    return app


app = create_app()
