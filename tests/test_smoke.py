from server import app, DeployState, _handle_command, store
from pr_review import _commentable_lines


def test_app_boots():
    assert app.title == "Pipeline Gatekeeper"


def test_deploy_states_defined():
    assert DeployState.PENDING == "pending"
    assert DeployState.APPROVED == "approved"
    assert DeployState.ROLLED_BACK == "rolled_back"


def _reset_store(deploy_id="d1"):
    store.set(deploy_id, {
        "state": DeployState.PENDING,
        "chat_id": "c1",
        "repo": "acme/api",
        "branch": "main",
        "actor": "ernest",
    })


def test_status_command_lists_pending():
    _reset_store("d1")
    reply = _handle_command("status", "+15551234567", "m1")
    assert "d1" in reply


def test_approve_single_pending():
    _reset_store("d1")
    reply = _handle_command("approve", "+15551234567", "m1")
    assert "Approved" in reply
    assert store.get("d1")["state"] == DeployState.APPROVED


def test_canary_percent_parsed():
    _reset_store("d1")
    _handle_command("approve 10", "+15551234567", "m1")
    assert store.get("d1")["canary_percent"] == 10


def test_invalid_canary_rejected():
    _reset_store("d1")
    reply = _handle_command("approve 77", "+15551234567", "m1")
    assert "Canary" in reply
    assert store.get("d1")["state"] == DeployState.PENDING


def test_rollback():
    _reset_store("d1")
    _handle_command("rollback", "+15551234567", "m1")
    assert store.get("d1")["state"] == DeployState.ROLLED_BACK


def test_commentable_lines_parses_additions():
    diff = """diff --git a/auth.py b/auth.py
--- a/auth.py
+++ b/auth.py
@@ -10,3 +10,4 @@
 def login():
-    pass
+    timeout = 30
+    return timeout
 # end
"""
    allowed = _commentable_lines(diff)
    right = allowed.get(("auth.py", "RIGHT"), set())
    assert 11 in right
    assert 12 in right
