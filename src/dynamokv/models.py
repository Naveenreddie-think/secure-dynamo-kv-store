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
