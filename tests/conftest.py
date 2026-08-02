import pytest
from fastapi.testclient import TestClient

from dynamokv.main import create_app
from dynamokv.storage.memory import MemoryStorage


@pytest.fixture
def client():
    app = create_app(storage=MemoryStorage(), node_id="test-node")
    return TestClient(app)
