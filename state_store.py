"""State store with an in-memory default and an optional Redis backend.

Set REDIS_URL to use Redis; otherwise state lives in process memory and is lost
on restart.
"""

import json
import os
from typing import Optional


class InMemoryStore:
    def __init__(self):
        self._data: dict[str, dict] = {}

    def get(self, key: str) -> Optional[dict]:
        return self._data.get(key)

    def set(self, key: str, value: dict) -> None:
        self._data[key] = value

    def all(self) -> dict[str, dict]:
        return dict(self._data)


class RedisStore:
    def __init__(self, url: str):
        import redis  # type: ignore
        self._r = redis.from_url(url, decode_responses=True)
        self._prefix = "gatekeeper:deploy:"

    def _k(self, key: str) -> str:
        return f"{self._prefix}{key}"

    def get(self, key: str) -> Optional[dict]:
        raw = self._r.get(self._k(key))
        return json.loads(raw) if raw else None

    def set(self, key: str, value: dict) -> None:
        self._r.set(self._k(key), json.dumps(value), ex=60 * 60 * 24 * 7)

    def all(self) -> dict[str, dict]:
        keys = self._r.keys(f"{self._prefix}*")
        out = {}
        for k in keys:
            raw = self._r.get(k)
            if raw:
                out[k[len(self._prefix):]] = json.loads(raw)
        return out


def build_store():
    url = os.environ.get("REDIS_URL")
    if url:
        try:
            return RedisStore(url)
        except Exception:
            pass
    return InMemoryStore()
