from fastapi import APIRouter, Depends, HTTPException, Request

from dynamokv.models import (
    DeleteResponse,
    GossipRequest,
    GossipResponse,
    HealthResponse,
    InternalHintRequest,
    InternalPutRequest,
    InternalVersionsResponse,
    KeyValueResponse,
    PutRequest,
    VersionOut,
)
from dynamokv.node import Node
from dynamokv.vector_clock import VectorClock, Version

router = APIRouter()


def get_node(request: Request) -> Node:
    return request.app.state.node


@router.put("/keys/{key}", response_model=KeyValueResponse)
def put_key(key: str, body: PutRequest, node: Node = Depends(get_node)) -> KeyValueResponse:
    version = node.put(key, body.value)
    return KeyValueResponse(key=key, value=version.value, clock=version.clock.counters)


@router.get("/keys/{key}", response_model=KeyValueResponse)
def get_key(key: str, node: Node = Depends(get_node)) -> KeyValueResponse:
    version = node.get(key)  # raises 404/409/503 internally
    return KeyValueResponse(key=key, value=version.value, clock=version.clock.counters)


@router.delete("/keys/{key}", response_model=DeleteResponse)
def delete_key(key: str, node: Node = Depends(get_node)) -> DeleteResponse:
    deleted = node.delete(key)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"key '{key}' not found")
    return DeleteResponse(key=key, deleted=True)


@router.get("/healthz", response_model=HealthResponse)
def healthz(node: Node = Depends(get_node)) -> HealthResponse:
    return HealthResponse(node_id=node.node_id, status="ok")


# Internal-only routes: no ring lookup, no fan-out, just this node's local
# storage. This is what a coordinator's quorum fan-out calls on peers --
# hitting the public /keys/{key} routes instead would make every replica
# re-derive the preference list on arrival and re-fan-out all over again.


@router.put("/internal/keys/{key}", response_model=KeyValueResponse, include_in_schema=False)
def put_key_local(key: str, body: InternalPutRequest, node: Node = Depends(get_node)) -> KeyValueResponse:
    version = Version(value=body.value, clock=VectorClock(body.clock))
    node.put_local(key, version)
    return KeyValueResponse(key=key, value=version.value, clock=version.clock.counters)


@router.get("/internal/keys/{key}", response_model=InternalVersionsResponse, include_in_schema=False)
def get_key_local(key: str, node: Node = Depends(get_node)) -> InternalVersionsResponse:
    versions = node.get_local(key)
    if not versions:
        raise HTTPException(status_code=404, detail=f"key '{key}' not found")
    return InternalVersionsResponse(key=key, versions=[VersionOut(**v.to_dict()) for v in versions])


@router.delete("/internal/keys/{key}", response_model=DeleteResponse, include_in_schema=False)
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


@router.post("/internal/gossip", response_model=GossipResponse, include_in_schema=False)
def gossip(body: GossipRequest, node: Node = Depends(get_node)) -> GossipResponse:
    table = node.handle_gossip(body.table)
    return GossipResponse(table=table)


@router.put("/internal/hints/{key}", include_in_schema=False)
def put_hint(key: str, body: InternalHintRequest, node: Node = Depends(get_node)) -> dict:
    version = Version(value=body.value, clock=VectorClock(body.clock))
    node.add_hint(body.target, key, version)
    return {"status": "ok"}
