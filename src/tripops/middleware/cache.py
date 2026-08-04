import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock


@dataclass(slots=True)
class _CacheEntry[T]:
    value: T
    expires_at: float


class TTLCache[T]:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._entries: dict[str, _CacheEntry[T]] = {}
        self._lock = Lock()

    def get(self, key: str) -> T | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.expires_at <= self.clock():
                self._entries.pop(key, None)
                return None
            return entry.value

    def put(self, key: str, value: T, *, ttl_seconds: float) -> None:
        if ttl_seconds <= 0:
            raise ValueError("cache ttl must be positive")
        with self._lock:
            self._entries[key] = _CacheEntry(value, self.clock() + ttl_seconds)
