from __future__ import annotations

import time
from importlib import import_module
from threading import Lock

from shared.config import Settings


class BaseNonceStore:
    def has(self, nonce_key: str) -> bool:
        raise NotImplementedError

    def put(self, nonce_key: str, ttl_seconds: int) -> None:
        raise NotImplementedError

    def put_if_absent(self, nonce_key: str, ttl_seconds: int) -> bool:
        if self.has(nonce_key):
            return False
        self.put(nonce_key, ttl_seconds)
        return True


class InMemoryNonceStore(BaseNonceStore):
    def __init__(self) -> None:
        self._store: dict[str, float] = {}
        self._lock = Lock()

    def _cleanup(self) -> None:
        now = time.time()
        expired_keys = [key for key, expires_at in self._store.items() if expires_at < now]
        for key in expired_keys:
            self._store.pop(key, None)

    def has(self, nonce_key: str) -> bool:
        with self._lock:
            self._cleanup()
            return nonce_key in self._store

    def put(self, nonce_key: str, ttl_seconds: int) -> None:
        with self._lock:
            self._cleanup()
            self._store[nonce_key] = time.time() + ttl_seconds


class RedisNonceStore(BaseNonceStore):
    def __init__(self, redis_url: str) -> None:
        redis_module = import_module("redis")
        self._client = redis_module.from_url(redis_url, decode_responses=True)

    def has(self, nonce_key: str) -> bool:
        return bool(self._client.exists(nonce_key))

    def put(self, nonce_key: str, ttl_seconds: int) -> None:
        self._client.set(nonce_key, "1", ex=ttl_seconds, nx=True)

    def put_if_absent(self, nonce_key: str, ttl_seconds: int) -> bool:
        return bool(self._client.set(nonce_key, "1", ex=ttl_seconds, nx=True))


_MEMORY_NONCE_STORE = InMemoryNonceStore()
_REDIS_NONCE_STORES: dict[str, RedisNonceStore] = {}


def get_nonce_store(settings: Settings) -> BaseNonceStore:
    if settings.redis_url:
        try:
            store = _REDIS_NONCE_STORES.get(settings.redis_url)
            if store is None:
                store = RedisNonceStore(settings.redis_url)
                _REDIS_NONCE_STORES[settings.redis_url] = store
            return store
        except Exception:
            return _MEMORY_NONCE_STORE
    return _MEMORY_NONCE_STORE
