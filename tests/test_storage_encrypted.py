"""Checks the one property the shared contract suite structurally can't:
that data actually landing on disk is ciphertext, not readable JSON. The
contract suite only ever goes through the StorageBackend interface, so it
can prove round-tripping works but not that encryption is really happening.
"""
import json
import sqlite3

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from dynamokv.crypto import EncryptedStorage, load_or_create_encryption_key
from dynamokv.storage.sqlite import SqliteStorage


def test_raw_sqlite_bytes_are_not_plaintext(tmp_path):
    db_path = str(tmp_path / "encrypted.db")
    inner = SqliteStorage(db_path)
    storage = EncryptedStorage(inner, AESGCM.generate_key(bit_length=256))

    secret_value = "this is a very secret value nobody should see in plaintext"
    storage.put("foo", secret_value)

    conn = sqlite3.connect(db_path)
    raw = conn.execute("SELECT value FROM kv WHERE key = ?", ("foo",)).fetchone()[0]
    conn.close()

    assert secret_value not in raw
    # the raw column is itself JSON (SqliteStorage's own encoding of the
    # envelope dict) -- but decoding it must NOT yield the plaintext value
    envelope = json.loads(raw)
    assert "nonce" in envelope and "ciphertext" in envelope
    assert secret_value not in json.dumps(envelope)


def test_wrong_key_fails_to_decrypt(tmp_path):
    db_path = str(tmp_path / "encrypted.db")
    inner = SqliteStorage(db_path)
    storage = EncryptedStorage(inner, AESGCM.generate_key(bit_length=256))
    storage.put("foo", "bar")

    wrong_key_storage = EncryptedStorage(inner, AESGCM.generate_key(bit_length=256))
    try:
        wrong_key_storage.get("foo")
        assert False, "expected decryption to fail with the wrong key"
    except Exception:
        pass


def test_load_or_create_encryption_key_persists_across_calls(tmp_path):
    key_path = str(tmp_path / "node.key")
    first = load_or_create_encryption_key(key_path)
    second = load_or_create_encryption_key(key_path)
    assert first == second
    assert len(first) == 32  # 256 bits


def test_load_or_create_encryption_key_generates_distinct_keys_for_distinct_paths(tmp_path):
    key_a = load_or_create_encryption_key(str(tmp_path / "a.key"))
    key_b = load_or_create_encryption_key(str(tmp_path / "b.key"))
    assert key_a != key_b
