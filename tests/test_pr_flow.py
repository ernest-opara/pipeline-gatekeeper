"""Tests for PR routing, diff parsing, and GitHub error mapping."""

import httpx
import pytest

import server
from pr_review import _commentable_lines


def test_find_pr_by_chat_returns_pending_pr():
    server.store.set("pr-a-b-1", {
        "type": "pr", "state": "pending", "chat_id": "c1",
        "owner": "a", "repo": "b", "number": 1,
    })
    found = server._find_pr_by_chat("c1")
    assert found is not None
    assert found["number"] == 1


def test_find_pr_by_chat_ignores_deploys():
    server.store.set("d1", {
        "state": server.DeployState.PENDING, "chat_id": "c1",
        "repo": "r", "branch": "b", "actor": "a",
    })
    assert server._find_pr_by_chat("c1") is None


def test_find_pr_by_chat_finds_reviewed_pr():
    server.store.set("pr-a-b-1", {
        "type": "pr", "state": "reviewed", "chat_id": "c1",
        "owner": "a", "repo": "b", "number": 1,
    })
    found = server._find_pr_by_chat("c1")
    assert found is not None
    assert found["number"] == 1


def test_pending_ids_excludes_prs():
    server.store.set("d1", {
        "state": server.DeployState.PENDING, "chat_id": "c1",
        "repo": "r", "branch": "b", "actor": "a",
    })
    server.store.set("pr-a-b-1", {
        "type": "pr", "state": "pending", "chat_id": "c2",
    })
    assert server._pending_ids() == ["d1"]


def _http_error(status: int, body: dict) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://api.github.com/x")
    resp = httpx.Response(status, request=req, json=body)
    return httpx.HTTPStatusError("boom", request=req, response=resp)


def test_friendly_error_self_approval():
    err = _http_error(422, {"message": "Can not approve your own pull request"})
    reply = server._friendly_github_error(err, "approve")
    assert "own PR" in reply
    assert "comment" in reply


def test_friendly_error_403_permissions():
    err = _http_error(403, {"message": "forbidden"})
    reply = server._friendly_github_error(err, "approve")
    assert "pull-requests: write" in reply


def test_friendly_error_404_not_found():
    err = _http_error(404, {"message": "not found"})
    reply = server._friendly_github_error(err, "approve")
    assert "couldn't find" in reply


def test_friendly_error_401_invalid_token():
    err = _http_error(401, {"message": "bad cred"})
    reply = server._friendly_github_error(err, "approve")
    assert "invalid or expired" in reply


def test_friendly_error_unknown_exception():
    reply = server._friendly_github_error(RuntimeError("kaboom"), "approve")
    assert "logs" in reply


def test_commentable_lines_multi_file():
    diff = """diff --git a/a.py b/a.py
+++ b/a.py
@@ -1,1 +1,2 @@
 x = 1
+y = 2
diff --git a/b.py b/b.py
+++ b/b.py
@@ -5,1 +5,2 @@
 z = 3
+w = 4
"""
    allowed = _commentable_lines(diff)
    assert 2 in allowed[("a.py", "RIGHT")]
    assert 6 in allowed[("b.py", "RIGHT")]


def test_commentable_lines_tracks_deletions():
    diff = """diff --git a/x.py b/x.py
+++ b/x.py
@@ -10,2 +10,1 @@
 keep
-remove_me
"""
    allowed = _commentable_lines(diff)
    assert 11 in allowed[("x.py", "LEFT")]


def test_commentable_lines_empty_diff():
    assert _commentable_lines("") == {}
