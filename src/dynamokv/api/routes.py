from fastapi import APIRouter, Depends, HTTPException, Request

from dynamokv.auth import AuthContext, get_auth_context
from dynamokv.models import (
    DeleteResponse,
    GossipRequest,
    GossipResponse,
    HealthResponse,
    InternalHintRequest,
    InternalHintResponse,
    InternalPutRequest,
    InternalVersionsResponse,
    KeyValueResponse,
    PutRequest,
    VersionOut,
)
from dynamokv.node import Node
from dynamokv.vector_clock import VectorClock, Version

# Three routers, matching the three trust/versioning domains this cluster
# now has: public_router is the versioned (/v1) client-facing data-plane API
# (token-gated, mounted with prefix="/v1" in main.py so a future /v2 can
# exist alongside it without touching this file); ops_router is small,
# deliberately unprefixed operational surface (/healthz, and /metrics lives
# on its own app/port entirely) -- liveness probes and scrape configs are
# not written against a business-API version and shouldn't need bumping
# when /v1 becomes /v2 someday. internal_router is node-to-node only, gated
# by mTLS at the transport layer instead (a separate port, requiring a
# certificate signed by the cluster CA) -- never by tokens, and never
# versioned, since it's a private wire protocol, not a public contract.
# create_app() includes all three on one app for tests; the production
# entrypoint (run.py) serves them across separate ports/apps.
public_router = APIRouter()
ops_router = APIRouter()
internal_router = APIRouter()


def get_node(request: Request) -> Node:
    return request.app.state.node


@public_router.put("/keys/{key}", response_model=KeyValueResponse)
def put_key(
    key: str,
    body: PutRequest,
    node: Node = Depends(get_node),
    auth: AuthContext = Depends(get_auth_context),
) -> KeyValueResponse:
    version = node.put(key, body.value)
    return KeyValueResponse(key=key, value=version.value, clock=version.clock.counters)


@public_router.get("/keys/{key}", response_model=KeyValueResponse)
def get_key(
    key: str,
    node: Node = Depends(get_node),
    auth: AuthContext = Depends(get_auth_context),
) -> KeyValueResponse:
    version = node.get(key)  # raises 404/409/503 internally
    return KeyValueResponse(key=key, value=version.value, clock=version.clock.counters)


@public_router.delete("/keys/{key}", response_model=DeleteResponse)
def delete_key(
    key: str,
    node: Node = Depends(get_node),
    auth: AuthContext = Depends(get_auth_context),
) -> DeleteResponse:
    deleted = node.delete(key)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"key '{key}' not found")
    return DeleteResponse(key=key, deleted=True)


@ops_router.get("/healthz", response_model=HealthResponse)
def healthz(node: Node = Depends(get_node)) -> HealthResponse:
    return HealthResponse(node_id=node.node_id, status="ok")


# Internal-only routes: no ring lookup, no fan-out, just this node's local
# storage. This is what a coordinator's quorum fan-out calls on peers --
# hitting the public /keys/{key} routes instead would make every replica
# re-derive the preference list on arrival and re-fan-out all over again.


@internal_router.put("/internal/keys/{key}", response_model=KeyValueResponse, include_in_schema=False)
def put_key_local(key: str, body: InternalPutRequest, node: Node = Depends(get_node)) -> KeyValueResponse:
    version = Version(value=body.value, clock=VectorClock(body.clock))
    node.put_local(key, version)
    return KeyValueResponse(key=key, value=version.value, clock=version.clock.counters)


@internal_router.get("/internal/keys/{key}", response_model=InternalVersionsResponse, include_in_schema=False)
def get_key_local(key: str, node: Node = Depends(get_node)) -> InternalVersionsResponse:
    versions = node.get_local(key)
    if not versions:
        raise HTTPException(status_code=404, detail=f"key '{key}' not found")
    return InternalVersionsResponse(key=key, versions=[VersionOut(**v.to_dict()) for v in versions])


@internal_router.delete("/internal/keys/{key}", response_model=DeleteResponse, include_in_schema=False)
def delete_key_local(key: str, node: Node = Depends(get_node)) -> DeleteResponse:
    deleted = node.delete_local(key)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"key '{key}' not found")
    return DeleteResponse(key=key, deleted=True)


# Gossip exchange and hint handoff -- also internal-only. A gossip round is
# a single push-pull: the caller sends its table, the callee merges it and
# hands back its own. Hint creation is how a coordinator gets a write to a
# neighbor node when the real target is unreachable -- delivery back to the
# real target, once it's live again, reuses the plain /internal/keys/{key}
# PUT above; from the target's side a handed-off write looks identical to
# an ordinary replica write.


@internal_router.post("/internal/gossip", response_model=GossipResponse, include_in_schema=False)
def gossip(body: GossipRequest, node: Node = Depends(get_node)) -> GossipResponse:
    table = node.handle_gossip(body.table)
    return GossipResponse(table=table)


@internal_router.put("/internal/hints/{key}", response_model=InternalHintResponse, include_in_schema=False)
def put_hint(key: str, body: InternalHintRequest, node: Node = Depends(get_node)) -> InternalHintResponse:
    version = Version(value=body.value, clock=VectorClock(body.clock))
    node.add_hint(body.target, key, version)
    return InternalHintResponse(status="ok")
