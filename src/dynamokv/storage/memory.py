from typing import Any, Optional


class MemoryStorage:
    """In-process dict-backed storage. Data does not survive a restart."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    def get(self, key: str) -> Optional[Any]:
        return self._data.get(key)

    def put(self, key: str, value: Any) -> None:
        self._data[key] = value

    def delete(self, key: str) -> bool:
        existed = key in self._data
        self._data.pop(key, None)
        return existed

    def exists(self, key: str) -> bool:
        return key in self._data
