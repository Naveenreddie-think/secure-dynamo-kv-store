from typing import Any, Optional, Protocol


class StorageBackend(Protocol):
    """Structural interface every storage implementation must satisfy."""

    def get(self, key: str) -> Optional[Any]: ...

    def put(self, key: str, value: Any) -> None: ...

    def delete(self, key: str) -> bool:
        """Return True if the key existed and was removed, False otherwise."""
        ...

    def exists(self, key: str) -> bool: ...
