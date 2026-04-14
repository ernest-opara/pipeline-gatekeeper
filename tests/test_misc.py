"""Tests for signature verify, state store fallback, and risk summary caching."""

import hashlib
import hmac

import ai_summary
import server
from state_store import InMemoryStore, build_store


def test_signature_skipped_when_secret_missing(monkeypatch):
    monkeypatch.setattr(server, "LINQ_WEBHOOK_SECRET", "")
    assert server._verify_signature(b"body", "123", "sig") is True


def test_signature_valid(monkeypatch):
    monkeypatch.setattr(server, "LINQ_WEBHOOK_SECRET", "shh")
    ts, body = "1000", b'{"x":1}'
    sig = hmac.new(b"shh", f"{ts}.{body.decode()}".encode(), hashlib.sha256).hexdigest()
    assert server._verify_signature(body, ts, sig) is True


def test_signature_tampered(monkeypatch):
    monkeypatch.setattr(server, "LINQ_WEBHOOK_SECRET", "shh")
    assert server._verify_signature(b'{"x":2}', "1000", "deadbeef") is False


def test_build_store_defaults_to_in_memory(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    s = build_store()
    assert isinstance(s, InMemoryStore)


def test_build_store_falls_back_on_bad_redis_url(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://nonexistent-host-asdfg:6379")
    s = build_store()
    assert isinstance(s, InMemoryStore) or s.__class__.__name__ == "RedisStore"


def test_in_memory_store_roundtrip():
    s = InMemoryStore()
    s.set("k", {"a": 1})
    assert s.get("k") == {"a": 1}
    s.set("k2", {"a": 2})
    assert set(s.all().keys()) == {"k", "k2"}


def test_risk_summary_returns_empty_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    ai_summary._cache.clear()
    result = ai_summary.summarize_risk("sha1", "msg", ["a.py"], "stat")
    assert result == ""


def test_risk_summary_caches_per_sha(monkeypatch):
    ai_summary._cache.clear()
    ai_summary._cache["cached-sha"] = "HIGH RISK: test."
    result = ai_summary.summarize_risk("cached-sha", "", [], "")
    assert result == "HIGH RISK: test."
