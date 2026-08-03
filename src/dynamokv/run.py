"""Production entrypoint: `python -m dynamokv.run`.

Runs two independent uvicorn.Server instances concurrently in one process:
- the public app (config.PORT) -- client-facing, server-only TLS, no client
  certificate required, token auth + audit logging via create_public_app().
- the internal app (config.INTERNAL_PORT) -- node-to-node only, mTLS
  required (ssl_cert_reqs=CERT_REQUIRED against the cluster CA), never
  published to the Docker host in docker-compose.yml so it's unreachable
  from outside the compose network by construction, not just by the
  handshake.

Node's own outbound httpx.Client is built here with the node's client
certificate and the CA bundle, so every internal call it makes (replica
writes/reads, gossip, hints) presents mTLS credentials. Node itself needs
no changes for this -- it already accepts an injectable http_client.
"""
import asyncio
import ssl

import httpx
import uvicorn

from dynamokv import config
from dynamokv.auth import load_auth_tokens
from dynamokv.gossip_worker import GossipWorker
from dynamokv.main import (
    build_hint_storage_from_config,
    build_storage_from_config,
    cluster_nodes_from_config,
    create_internal_app,
    create_public_app,
    default_audit_log_path,
    default_internal_audit_log_path,
)
from dynamokv.mtls import build_mtls_client_context
from dynamokv.node import Node
from dynamokv.ring import HashRing


def _build_node() -> Node:
    cluster_nodes = cluster_nodes_from_config()
    ring = HashRing(nodes=cluster_nodes, virtual_nodes=config.VIRTUAL_NODES)
    # Internal peer traffic goes over the mTLS port, not the public one.
    peers = {
        nid: f"https://{nid}:{config.INTERNAL_PORT}"
        for nid in cluster_nodes
        if nid != config.NODE_ID
    }
    http_client = httpx.Client(
        timeout=5.0,
        verify=build_mtls_client_context(config.TLS_NODE_CERT, config.TLS_NODE_KEY, config.TLS_CA_CERT),
    )
    gossip_failure_timeout = config.GOSSIP_INTERVAL_SECONDS * config.GOSSIP_FAILURE_TIMEOUT_MULTIPLIER

    return Node(
        node_id=config.NODE_ID,
        storage=build_storage_from_config(),
        ring=ring,
        peers=peers,
        http_client=http_client,
        n=config.N,
        r=config.R,
        w=config.W,
        hint_storage=build_hint_storage_from_config(),
        gossip_failure_timeout=gossip_failure_timeout,
    )


async def main() -> None:
    node = _build_node()
    has_peers = len(cluster_nodes_from_config()) > 1
    gossip_worker = GossipWorker(node, config.GOSSIP_INTERVAL_SECONDS) if has_peers else None

    public_app = create_public_app(
        node,
        auth_tokens=load_auth_tokens(config.AUTH_TOKENS_FILE),
        audit_log_path=default_audit_log_path(),
    )
    internal_app = create_internal_app(node, gossip_worker, audit_log_path=default_internal_audit_log_path())

    public_config = uvicorn.Config(
        public_app,
        host="0.0.0.0",
        port=config.PORT,
        ssl_certfile=config.TLS_NODE_CERT,
        ssl_keyfile=config.TLS_NODE_KEY,
        ssl_cert_reqs=ssl.CERT_NONE,
    )
    internal_config = uvicorn.Config(
        internal_app,
        host="0.0.0.0",
        port=config.INTERNAL_PORT,
        ssl_certfile=config.TLS_NODE_CERT,
        ssl_keyfile=config.TLS_NODE_KEY,
        ssl_ca_certs=config.TLS_CA_CERT,
        ssl_cert_reqs=ssl.CERT_REQUIRED,
    )

    await asyncio.gather(
        uvicorn.Server(public_config).serve(),
        uvicorn.Server(internal_config).serve(),
    )


if __name__ == "__main__":
    asyncio.run(main())
