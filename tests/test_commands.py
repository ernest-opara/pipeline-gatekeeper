"""Tests for deploy reply commands: approve, rollback, canary, force, status."""

import server
from server import DeployState, _handle_command, store


def _register(did="d1"):
    store.set(did, {
        "state": DeployState.PENDING,
        "chat_id": f"chat-{did}",
        "repo": "acme/api",
        "branch": "main",
        "actor": "ernest",
    })


def test_empty_reply_prompts_for_command():
    reply = _handle_command("", "+1", "m1")
    assert "approve" in reply.lower()


def test_unknown_deploy_id():
    reply = _handle_command("deploy-xyz approve", "+1", "m1")
    assert "Unknown" in reply


def test_no_pending_deploys():
    reply = _handle_command("approve", "+1", "m1")
    assert "No pending" in reply


def test_multiple_pending_requires_id():
    _register("d1")
    _register("d2")
    reply = _handle_command("approve", "+1", "m1")
    assert "Multiple pending" in reply


def test_explicit_id_disambiguates():
    _register("d1")
    _register("d2")
    _handle_command("d1 approve", "+1", "m1")
    assert store.get("d1")["state"] == DeployState.APPROVED
    assert store.get("d2")["state"] == DeployState.PENDING


def test_already_decided_deploy_rejects_second_command():
    _register("d1")
    _handle_command("approve", "+1", "m1")
    reply = _handle_command("d1 rollback", "+1", "m1")
    assert "already" in reply


def test_canary_valid_percents():
    for p in (10, 25, 50, 100):
        store.set(f"d{p}", {
            "state": DeployState.PENDING, "chat_id": "c",
            "repo": "r", "branch": "b", "actor": "a",
        })
        _handle_command(f"d{p} approve {p}", "+1", "m1")
        assert store.get(f"d{p}")["canary_percent"] == p


def test_status_with_no_pending():
    reply = _handle_command("status", "+1", "m1")
    assert "No pending" in reply


def test_approver_allowlist_blocks_outsiders(monkeypatch):
    monkeypatch.setattr(server, "APPROVER_NUMBERS", ["+15555550001"])
    _register("d1")
    reply = _handle_command("approve", "+15550000000", "m1")
    assert "allowlist" in reply
    assert store.get("d1")["state"] == DeployState.PENDING


def test_approver_allowlist_permits_listed(monkeypatch):
    monkeypatch.setattr(server, "APPROVER_NUMBERS", ["+15555550001"])
    _register("d1")
    _handle_command("approve", "+15555550001", "m1")
    assert store.get("d1")["state"] == DeployState.APPROVED


def test_force_approve_overrides_closed_window(monkeypatch):
    monkeypatch.setattr(server, "_in_deploy_window", lambda: False)
    _register("d1")
    reply = _handle_command("approve", "+1", "m1")
    assert "Outside" in reply
    assert store.get("d1")["state"] == DeployState.PENDING

    _handle_command("force approve", "+1", "m1")
    assert store.get("d1")["state"] == DeployState.APPROVED
    assert store.get("d1")["forced"] is True


def test_deploy_window_open_allows_normal_approve(monkeypatch):
    monkeypatch.setattr(server, "_in_deploy_window", lambda: True)
    _register("d1")
    _handle_command("approve", "+1", "m1")
    assert store.get("d1")["state"] == DeployState.APPROVED
    assert store.get("d1")["forced"] is False
