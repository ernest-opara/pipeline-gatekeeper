"""HTTP-level tests for the FastAPI endpoints."""

from fastapi.testclient import TestClient

import server
from server import DeployState, app, store

client = TestClient(app)


def test_deploy_status_404_unknown_id():
    resp = client.get("/deploy/status/does-not-exist")
    assert resp.status_code == 404


def test_deploy_status_returns_state():
    store.set("d1", {
        "state": DeployState.APPROVED, "chat_id": "c", "canary_percent": 25,
        "approver": "+15555551212", "forced": True,
    })
    resp = client.get("/deploy/status/d1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "approved"
    assert body["canary_percent"] == 25
    assert body["forced"] is True


def test_deploy_register_stores_pending(monkeypatch):
    monkeypatch.setattr(server, "send_deploy_alert", lambda **kw: "chat-xyz")
    monkeypatch.setattr(server, "summarize_risk", lambda **kw: "LOW RISK: doc-only.")
    payload = {
        "deploy_id": "d42",
        "repo": "acme/api",
        "branch": "main",
        "actor": "ernest",
        "notify_number": "+15555551212",
        "commit_sha": "abc123",
        "files_changed": ["README.md"],
    }
    resp = client.post("/deploy/register", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "pending"
    assert body["risk_summary"].startswith("LOW RISK")
    assert store.get("d42")["chat_id"] == "chat-xyz"


def test_pr_register_stores_pending(monkeypatch):
    monkeypatch.setattr(server, "create_chat", lambda to, msg: {"chat": {"id": "chat-pr"}})
    payload = {
        "owner": "acme",
        "repo": "api",
        "number": 42,
        "title": "Fix bug",
        "author": "dana",
        "notify_number": "+15555551212",
    }
    resp = client.post("/pr/register", json=payload)
    assert resp.status_code == 200
    entry = store.get(resp.json()["key"])
    assert entry["type"] == "pr"
    assert entry["number"] == 42
    assert entry["chat_id"] == "chat-pr"


def test_webhook_ignores_non_message_events():
    resp = client.post("/webhook/linq", json={"event_type": "message.sent"})
    assert resp.status_code == 200
    assert resp.json()["ignored"] is True


def test_webhook_ignores_outbound_messages():
    payload = {
        "event_type": "message.received",
        "data": {"direction": "outbound", "chat": {"id": "c1"}},
    }
    resp = client.post("/webhook/linq", json=payload)
    assert resp.json()["ignored"] is True
