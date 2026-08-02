from fastapi import APIRouter, Depends, HTTPException, Request

from dynamokv.models import DeleteResponse, HealthResponse, KeyValueResponse, PutRequest
from dynamokv.node import Node

router = APIRouter()


def get_node(request: Request) -> Node:
    return request.app.state.node


@router.put("/keys/{key}", response_model=KeyValueResponse)
def put_key(key: str, body: PutRequest, node: Node = Depends(get_node)) -> KeyValueResponse:
    node.put(key, body.value)
    return KeyValueResponse(key=key, value=body.value)


@router.get("/keys/{key}", response_model=KeyValueResponse)
def get_key(key: str, node: Node = Depends(get_node)) -> KeyValueResponse:
    return KeyValueResponse(key=key, value=node.get(key))


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
def put_key_local(key: str, body: PutRequest, node: Node = Depends(get_node)) -> KeyValueResponse:
    node.put_local(key, body.value)
    return KeyValueResponse(key=key, value=body.value)


@router.get("/internal/keys/{key}", response_model=KeyValueResponse, include_in_schema=False)
def get_key_local(key: str, node: Node = Depends(get_node)) -> KeyValueResponse:
    if not node.exists_local(key):
        raise HTTPException(status_code=404, detail=f"key '{key}' not found")
    return KeyValueResponse(key=key, value=node.get_local(key))


@router.delete("/internal/keys/{key}", response_model=DeleteResponse, include_in_schema=False)
def delete_key_local(key: str, node: Node = Depends(get_node)) -> DeleteResponse:
    deleted = node.delete_local(key)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"key '{key}' not found")
    return DeleteResponse(key=key, deleted=True)
