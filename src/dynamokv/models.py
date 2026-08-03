from typing import Any, Dict, List

from pydantic import BaseModel


class PutRequest(BaseModel):
    value: Any


class KeyValueResponse(BaseModel):
    key: str
    value: Any
    clock: Dict[str, int]


class VersionOut(BaseModel):
    value: Any
    clock: Dict[str, int]


class InternalPutRequest(BaseModel):
    value: Any
    clock: Dict[str, int]


class InternalVersionsResponse(BaseModel):
    key: str
    versions: List[VersionOut]


class DeleteResponse(BaseModel):
    key: str
    deleted: bool


class HealthResponse(BaseModel):
    node_id: str
    status: str


class GossipRequest(BaseModel):
    sender: str
    table: Dict[str, int]


class GossipResponse(BaseModel):
    table: Dict[str, int]


class InternalHintRequest(BaseModel):
    target: str
    value: Any
    clock: Dict[str, int]


class InternalHintResponse(BaseModel):
    status: str


class RingPointOut(BaseModel):
    position: float
    owner: str


class RingTopologyOut(BaseModel):
    virtual_nodes: int
    points: List[RingPointOut]


class RecentOpOut(BaseModel):
    timestamp: float
    node_id: str
    method: str
    path: str
    key: Any
    latency_ms: float
    status_code: int
    outcome: str
    conflict: bool


class ClusterStateResponse(BaseModel):
    """This node's own-perspective view of the cluster -- for the Phase 10
    dashboard. Unauthenticated (see routes.py's docstring on the route
    itself): never includes values or raw tokens, but recent_ops entries
    DO include key names, a genuinely new (if minor) disclosure to an
    unauthenticated caller relative to every other route in this app."""

    node_id: str
    public_cluster_urls: List[str]
    ring: RingTopologyOut
    peers: Dict[str, str]  # peer_id -> "up" | "down"
    pending_hints: Dict[str, int]
    recent_ops: List[RecentOpOut]
