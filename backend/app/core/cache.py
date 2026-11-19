"""Small cache abstraction.

* MemoryCache – thread-safe TTL + LRU cache, used in development.
* RedisCache  – shared cache for multiple API workers / containers.

Used for geocoding results, query embeddings, user profile vectors and
rate-limit counters.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Any

from app.core.config import get_settings

log = logging.getLogger(__name__)


class MemoryCache:
    def __init__(self, max_items: int = 5000):
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self._max = max_items

    def get(self, key: str) -> Any | None:
        with self._lock:
            item = self._data.get(key)
            if not item:
                return None
            expires, value = item
            if expires and expires < time.time():
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return value

    def set(self, key: str, value: Any, ttl: int = 300) -> None:
        with self._lock:
            self._data[key] = (time.time() + ttl if ttl else 0, value)
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def incr(self, key: str, ttl: int) -> int:
        with self._lock:
            now = time.time()
            item = self._data.get(key)
            if not item or (item[0] and item[0] < now):
                self._data[key] = (now + ttl, 1)
                return 1
            expires, value = item
            self._data[key] = (expires, value + 1)
            return value + 1

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


class RedisCache:
    def __init__(self, url: str):
        import redis  # imported lazily so Redis stays optional

        self._r = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2)
        self._r.ping()

    def get(self, key: str) -> Any | None:
        raw = self._r.get(key)
        return json.loads(raw) if raw is not None else None

    def set(self, key: str, value: Any, ttl: int = 300) -> None:
        self._r.set(key, json.dumps(value), ex=ttl or None)

    def delete(self, key: str) -> None:
        self._r.delete(key)

    def incr(self, key: str, ttl: int) -> int:
        pipe = self._r.pipeline()
        pipe.incr(key)
        pipe.expire(key, ttl, nx=True)
        value, _ = pipe.execute()
        return int(value)

    def clear(self) -> None:  # only used in tests
        self._r.flushdb()


_cache: MemoryCache | RedisCache | None = None
_cache_lock = threading.Lock()


def get_cache() -> MemoryCache | RedisCache:
    global _cache
    if _cache is None:
        with _cache_lock:
            if _cache is None:
                url = get_settings().redis_url
                if url:
                    try:
                        _cache = RedisCache(url)
                        log.info("Using Redis cache")
                    except Exception as exc:  # pragma: no cover - depends on infra
                        log.warning("Redis unavailable (%s); falling back to memory cache", exc)
                        _cache = MemoryCache()
                else:
                    _cache = MemoryCache()
    return _cache
