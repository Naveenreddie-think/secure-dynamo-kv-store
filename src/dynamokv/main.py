from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI

from dynamokv import config
from dynamokv.api.routes import internal_router, public_router
from dynamokv.audit import add_audit_middleware
from dynamokv.auth import load_auth_tokens
from dynamokv.crypto import EncryptedStorage, load_or_create_encryption_key
from dynamokv.gossip_worker import GossipWorker
from dynamokv.node import Node
from dynamokv.ring import HashRing
from dynamokv.storage.base import StorageBackend
from dynamokv.storage.memory import MemoryStorage
from dynamokv.storage.sqlite import SqliteStorage


def _node_encryption_key_path() -> str:
    """One key per node, covering all of its own on-disk data (main store
    and hint store alike) -- no node ever needs to decrypt another node's
    disk, so there's no reason for more than one key per node."""
    return str(Path(config.DB_PATH).parent / f"{config.NODE_ID}.key")


def build_storage_from_config() -> StorageBackend:
    if config.STORAGE_BACKEND == "memory":
        return MemoryStorage()
    inner = SqliteStorage(config.DB_PATH)
    return EncryptedStorage(inner, load_or_create_encryption_key(_node_encryption_key_path()))


def build_hint_storage_from_config() -> StorageBackend:
    """A second, dedicated backend for hints -- entirely separate from real
    application data, so a client-chosen key can never collide with
    internal hint bookkeeping."""
    if config.STORAGE_BACKEND == "memory":
        return MemoryStorage()
    inner = SqliteStorage(f"{config.DB_PATH}.hints")
    return EncryptedStorage(inner, load_or_create_encryption_key(_node_encryption_key_path()))


def cluster_nodes_from_config() -> List[str]:
    return [n.strip() for n in config.CLUSTER_NODES.split(",") if n.strip()]


def default_audit_log_path() -> str:
    return str(Path(config.DB_PATH).parent / f"{config.NODE_ID}.audit.log")


def default_internal_audit_log_path() -> str:
    """Separate file from the public audit log -- internal (node-to-node)
    traffic and public (client-facing) traffic are different trust domains,
    worth being able to inspect independently. Phase 7's taxonomy category
    10a: before this, create_internal_app() attached no audit middleware at
    all, so every internal-port attack (forged gossip/hints/clock/delete)
    left zero record anywhere -- not a design limitation, a wiring gap."""
    return str(Path(config.DB_PATH).parent / f"{config.NODE_ID}.internal-audit.log")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Starts the gossip background thread only when a real ASGI server
    actually drives this app (uvicorn, or `with TestClient(app) as c:`) --
    not merely when create_app() constructs it, which happens once at
    module import time and again in every test that builds its own app."""
    worker: Optional[GossipWorker] = app.state.gossip_worker
    if worker is not None:
        worker.start()
    yield
    if worker is not None:
        worker.stop()


def create_app(
    storage: Optional[StorageBackend] = None,
    node_id: Optional[str] = None,
    cluster_nodes: Optional[List[str]] = None,
    n: Optional[int] = None,
    r: Optional[int] = None,
    w: Optional[int] = None,
    auth_tokens: Optional[Dict[str, dict]] = None,
    audit_log_path: Optional[str] = None,
) -> FastAPI:
    """Build the app. Production wires storage, cluster membership, N/R/W,
    auth tokens, and the audit log from env vars; tests inject a
    MemoryStorage directly for isolation and speed, which also defaults
    them to an isolated single-node cluster (no forwarding, quorum-of-1),
    auth disabled, and no audit log, unless they explicitly opt into any of
    cluster_nodes/n/r/w/auth_tokens/audit_log_path. That "storage is None
    means production" signal matters throughout: without it, tests that
    don't pass cluster_nodes would default to whatever CLUSTER_NODES
    happens to be in the environment, which won't contain their own
    node_id, and every call would try to forward to a nonexistent peer --
    the same reasoning is why auth stays off and no audit file is written
    unless a test explicitly asks for either.

    Includes both public_router (client-facing, gated by token auth) and
    internal_router (node-to-node, gated by mTLS at the transport layer
    instead) on one app -- this is what every existing multi-node test
    fixture (test_node_quorum.py etc.) needs, since they wire cross-node
    HTTP forwarding entirely in-process via mounted transports against a
    single combined app. The production entrypoint (run.py) instead builds
    two separate single-router apps via create_public_app()/
    create_internal_app() below, served on two separate TLS ports.
    """
    app = FastAPI(title="dynamokv", lifespan=lifespan)
    production_mode = storage is None
    resolved_storage = storage if storage is not None else build_storage_from_config()
    resolved_node_id = node_id if node_id is not None else config.NODE_ID

    if cluster_nodes is not None:
        resolved_cluster_nodes = cluster_nodes
    elif production_mode:
        resolved_cluster_nodes = cluster_nodes_from_config()
    else:
        resolved_cluster_nodes = [resolved_node_id]

    resolved_n = n if n is not None else (config.N if production_mode else 1)
    resolved_r = r if r is not None else (config.R if production_mode else 1)
    resolved_w = w if w is not None else (config.W if production_mode else 1)
    resolved_gossip_failure_timeout = (
        config.GOSSIP_INTERVAL_SECONDS * config.GOSSIP_FAILURE_TIMEOUT_MULTIPLIER if production_mode else 6.0
    )

    if auth_tokens is not None:
        resolved_auth_tokens = auth_tokens
    elif production_mode:
        resolved_auth_tokens = load_auth_tokens(config.AUTH_TOKENS_FILE)  # {} if unset -- deny by default
    else:
        resolved_auth_tokens = None  # auth disabled for tests that don't opt in

    if audit_log_path is not None:
        resolved_audit_log_path = audit_log_path
    elif production_mode:
        resolved_audit_log_path = default_audit_log_path()
    else:
        resolved_audit_log_path = None

    ring = HashRing(nodes=resolved_cluster_nodes, virtual_nodes=config.VIRTUAL_NODES)
    peers: Dict[str, str] = {
        nid: f"http://{nid}:{config.PORT}"
        for nid in resolved_cluster_nodes
        if nid != resolved_node_id
    }

    node = Node(
        node_id=resolved_node_id,
        storage=resolved_storage,
        ring=ring,
        peers=peers,
        n=resolved_n,
        r=resolved_r,
        w=resolved_w,
        hint_storage=build_hint_storage_from_config() if production_mode else None,
        gossip_failure_timeout=resolved_gossip_failure_timeout,
    )
    app.state.node = node
    app.state.auth_tokens = resolved_auth_tokens
    app.state.gossip_worker = (
        GossipWorker(node, config.GOSSIP_INTERVAL_SECONDS) if production_mode and peers else None
    )
    app.include_router(public_router)
    app.include_router(internal_router)
    if resolved_audit_log_path is not None:
        add_audit_middleware(app, resolved_audit_log_path)

    return app


def create_public_app(node: Node, auth_tokens: Dict[str, dict], audit_log_path: str) -> FastAPI:
    """Client-facing app: public_router only, token auth, audit logging.
    Served on the plain TLS port (no client certificate required) by
    run.py -- see config.PORT."""
    app = FastAPI(title="dynamokv (public)")
    app.state.node = node
    app.state.auth_tokens = auth_tokens
    app.state.gossip_worker = None
    app.include_router(public_router)
    add_audit_middleware(app, audit_log_path)
    return app


def create_internal_app(
    node: Node, gossip_worker: Optional[GossipWorker], audit_log_path: Optional[str] = None
) -> FastAPI:
    """Node-to-node app: internal_router only, no token auth at all -- this
    app is only ever served on the mTLS-required internal port, so trust
    comes entirely from the client certificate the transport layer already
    demanded before any request reaches here. Owns the gossip worker's
    lifespan, since gossip traffic only ever flows over this port.

    audit_log_path defaults to None (no middleware, matching every existing
    call site's prior behavior) so nothing breaks that doesn't opt in --
    run.py's production entrypoint passes default_internal_audit_log_path()
    explicitly."""
    app = FastAPI(title="dynamokv (internal)", lifespan=lifespan)
    app.state.node = node
    app.state.gossip_worker = gossip_worker
    app.include_router(internal_router)
    if audit_log_path is not None:
        add_audit_middleware(app, audit_log_path)
    return app


app = create_app()
