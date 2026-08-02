"""Behavioral contract every StorageBackend implementation must satisfy.

Parametrized so MemoryStorage, SqliteStorage, and EncryptedStorage are held
to identical behavior -- if a test only passes for one backend, the
interface is leaking implementation details.
"""
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from dynamokv.crypto import EncryptedStorage
from dynamokv.storage.memory import MemoryStorage
from dynamokv.storage.sqlite import SqliteStorage


def make_memory_storage(tmp_path):
    return MemoryStorage()


def make_sqlite_storage(tmp_path):
    return SqliteStorage(str(tmp_path / "test.db"))


def make_encrypted_storage(tmp_path):
    inner = SqliteStorage(str(tmp_path / "encrypted.db"))
    return EncryptedStorage(inner, AESGCM.generate_key(bit_length=256))


BACKEND_FACTORIES = [make_memory_storage, make_sqlite_storage, make_encrypted_storage]


@pytest.fixture(params=BACKEND_FACTORIES)
def storage(request, tmp_path):
    return request.param(tmp_path)


def test_get_missing_returns_none(storage):
    assert storage.get("missing") is None


def test_put_then_get_round_trips(storage):
    storage.put("foo", "bar")
    assert storage.get("foo") == "bar"


def test_put_overwrites(storage):
    storage.put("foo", "bar")
    storage.put("foo", "baz")
    assert storage.get("foo") == "baz"


def test_delete_existing_returns_true_and_removes(storage):
    storage.put("foo", "bar")
    assert storage.delete("foo") is True
    assert storage.get("foo") is None


def test_delete_missing_returns_false(storage):
    assert storage.delete("missing") is False


def test_exists_reflects_state(storage):
    assert storage.exists("foo") is False
    storage.put("foo", "bar")
    assert storage.exists("foo") is True
    storage.delete("foo")
    assert storage.exists("foo") is False


def test_stores_none_value_distinctly_from_missing(storage):
    storage.put("foo", None)
    assert storage.exists("foo") is True
    assert storage.delete("foo") is True
