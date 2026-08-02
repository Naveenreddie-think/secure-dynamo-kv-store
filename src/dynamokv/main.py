from typing import Optional

from fastapi import FastAPI

from dynamokv import config
from dynamokv.api.routes import router
from dynamokv.node import Node
from dynamokv.storage.base import StorageBackend
from dynamokv.storage.memory import MemoryStorage
from dynamokv.storage.sqlite import SqliteStorage


def _build_storage_from_config() -> StorageBackend:
    if config.STORAGE_BACKEND == "memory":
        return MemoryStorage()
    return SqliteStorage(config.DB_PATH)


def create_app(storage: Optional[StorageBackend] = None, node_id: Optional[str] = None) -> FastAPI:
    """Build the app. Production wires storage from env vars (config.py);
    tests inject a MemoryStorage directly for isolation and speed."""
    app = FastAPI(title="dynamokv")

    resolved_storage = storage if storage is not None else _build_storage_from_config()
    resolved_node_id = node_id if node_id is not None else config.NODE_ID

    app.state.node = Node(node_id=resolved_node_id, storage=resolved_storage)
    app.include_router(router)

    return app


app = create_app()
