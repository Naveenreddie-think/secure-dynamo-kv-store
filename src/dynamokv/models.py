from typing import Any

from pydantic import BaseModel


class PutRequest(BaseModel):
    value: Any


class KeyValueResponse(BaseModel):
    key: str
    value: Any


class DeleteResponse(BaseModel):
    key: str
    deleted: bool


class HealthResponse(BaseModel):
    node_id: str
    status: str
