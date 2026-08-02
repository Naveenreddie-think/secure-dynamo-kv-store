import base64
import json
import os
from pathlib import Path
from typing import Any, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from dynamokv.storage.base import StorageBackend

_NONCE_SIZE_BYTES = 12  # 96 bits, the standard/recommended size for AES-GCM


def load_or_create_encryption_key(path: str) -> bytes:
    """Per-node, persisted AES-256 key. Generated once on first boot and
    reused thereafter -- a node must be able to decrypt what it wrote
    before a restart. Never shared across nodes: nothing in this system
    ever needs to decrypt another node's disk, only its own."""
    key_path = Path(path)
    if key_path.exists():
        return key_path.read_bytes()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key = AESGCM.generate_key(bit_length=256)
    key_path.write_bytes(key)
    try:
        os.chmod(key_path, 0o600)
    except (NotImplementedError, OSError):
        pass  # best-effort on platforms that don't support unix-style permissions
    return key


class EncryptedStorage:
    """Wraps another StorageBackend, encrypting every value with
    AES-256-GCM before it reaches the inner backend. Satisfies the same
    StorageBackend Protocol -- callers (Node, tests) never know or care
    that encryption is happening.

    GCM is authenticated encryption: it detects tampering as well as
    providing confidentiality, unlike unauthenticated modes like plain CBC.
    A fresh random nonce is generated per put() -- GCM nonces must never
    repeat under the same key.
    """

    def __init__(self, inner: StorageBackend, key: bytes) -> None:
        self._inner = inner
        self._aesgcm = AESGCM(key)

    def get(self, key: str) -> Optional[Any]:
        envelope = self._inner.get(key)
        if envelope is None:
            return None
        nonce = base64.b64decode(envelope["nonce"])
        ciphertext = base64.b64decode(envelope["ciphertext"])
        plaintext = self._aesgcm.decrypt(nonce, ciphertext, None)
        return json.loads(plaintext)

    def put(self, key: str, value: Any) -> None:
        nonce = os.urandom(_NONCE_SIZE_BYTES)
        plaintext = json.dumps(value).encode("utf-8")
        ciphertext = self._aesgcm.encrypt(nonce, plaintext, None)
        envelope = {
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }
        self._inner.put(key, envelope)

    def delete(self, key: str) -> bool:
        return self._inner.delete(key)

    def exists(self, key: str) -> bool:
        return self._inner.exists(key)
