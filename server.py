"""
Pipeline Gatekeeper — Webhook Server

Receives inbound Linq messages, parses approve/rollback/canary commands, and
exposes /deploy/register and /deploy/status endpoints for GitHub Actions.

Features:
  - Approver phone-number allowlist
  - AI pre-flight risk summary (Anthropic Claude)
  - Business-hours deploy window with `force approve` override
  - Canary stages: `approve 10`, `approve 50`, `approve 100`
  - `status` command to list pending deploys
  - Redis-backed state (falls back to in-memory)
"""

import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from ai_summary import summarize_risk
from github_client import get_pr_diff, submit_review
from linq_client import (
    create_chat,
    mark_as_read,
    reply_to_chat,
    send_deploy_alert,
    send_reaction,
    start_typing,
    stop_typing,
)
from pr_review import parse_review
from state_store import build_store

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Pipeline Gatekeeper")

LINQ_WEBHOOK_SECRET = os.environ.get("LINQ_WEBHOOK_SECRET", "")

APPROVER_NUMBERS = [
    n.strip() for n in os.environ.get("APPROVER_NUMBERS", "").split(",") if n.strip()
]

DEPLOY_WINDOW_START = int(os.environ.get("DEPLOY_WINDOW_START_HOUR", "-1"))
DEPLOY_WINDOW_END = int(os.environ.get("DEPLOY_WINDOW_END_HOUR", "-1"))
DEPLOY_WINDOW_TZ = os.environ.get("DEPLOY_WINDOW_TZ", "UTC")

store = build_store()


class DeployState(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    ROLLED_BACK = "rolled_back"


def _in_deploy_window() -> bool:
    """True if deploys are currently allowed without a force override."""
    if DEPLOY_WINDOW_START < 0 or DEPLOY_WINDOW_END < 0:
        return True
    try:
        now = datetime.now(ZoneInfo(DEPLOY_WINDOW_TZ))
    except Exception:
        now = datetime.now()
    hour = now.hour
    if DEPLOY_WINDOW_START <= DEPLOY_WINDOW_END:
        return DEPLOY_WINDOW_START <= hour < DEPLOY_WINDOW_END
    return hour >= DEPLOY_WINDOW_START or hour < DEPLOY_WINDOW_END


def _approver_allowed(sender: str) -> bool:
    if not APPROVER_NUMBERS:
        return True
    return sender in APPROVER_NUMBERS


def _verify_signature(raw_body: bytes, timestamp: str, signature: str) -> bool:
    if not LINQ_WEBHOOK_SECRET:
        logger.warning("LINQ_WEBHOOK_SECRET not set — skipping signature check")
        return True
    message = f"{timestamp}.{raw_body.decode('utf-8')}"
    expected = hmac.new(
        LINQ_WEBHOOK_SECRET.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@app.post("/webhook/linq")
async def linq_webhook(
    request: Request,
    x_webhook_timestamp: str = Header(default=""),
    x_webhook_signature: str = Header(default=""),
    x_webhook_event: str = Header(default=""),
):
    raw_body = await request.body()

    if x_webhook_timestamp:
        age = time.time() - float(x_webhook_timestamp)
        if age > 300:
            raise HTTPException(status_code=400, detail="Webhook timestamp too old")

    if x_webhook_signature and not _verify_signature(raw_body, x_webhook_timestamp, x_webhook_signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload = json.loads(raw_body)
    event_type = payload.get("event_type") or x_webhook_event

    if event_type != "message.received":
        return {"ok": True, "ignored": True}

    data = payload.get("data", {})
    if data.get("direction") != "inbound":
        return {"ok": True, "ignored": True}

    chat_id = data.get("chat", {}).get("id", "")
    message_id = data.get("id", "")
    sender = data.get("sender_handle", {}).get("handle", "")
    parts = data.get("parts", [])
    body = ""
    for part in parts:
        if part.get("type") == "text":
            body = part.get("value", "").strip().lower()
            break

    raw_body_text = ""
    for part in parts:
        if part.get("type") == "text":
            raw_body_text = part.get("value", "").strip()
            break

    logger.info("Inbound from %s: '%s'", sender, body)

    _safe(mark_as_read, chat_id)
    if message_id:
        _safe(send_reaction, message_id, "like")
    _safe(start_typing, chat_id)

    pr_entry = _find_pr_by_chat(chat_id)

    try:
        if pr_entry:
            reply_text = _handle_pr_reply(pr_entry, raw_body_text, sender, message_id)
        else:
            reply_text = _handle_command(body, sender, message_id)
    finally:
        _safe(stop_typing, chat_id)

    if reply_text:
        _safe(reply_to_chat, chat_id, reply_text)

    return {"ok": True}


def _friendly_github_error(err: Exception, decision: str) -> str:
    import httpx
    if isinstance(err, httpx.HTTPStatusError):
        status = err.response.status_code
        try:
            msg = (err.response.json().get("message") or "").lower()
        except Exception:
            msg = ""
        if status == 422 and "own pull request" in msg:
            if decision == "approve":
                return (
                    "GitHub won't let you approve your own PR. "
                    "Reply 'comment ...' instead, or use a PAT under a different account."
                )
            return "GitHub rejected the review on your own PR. Try 'comment' instead."
        if status == 403:
            return "GitHub denied the request — the token is missing 'pull-requests: write'."
        if status == 404:
            return "GitHub couldn't find the PR. Check the token has access to this repo."
        if status == 401:
            return "GitHub token is invalid or expired."
        return f"GitHub error {status}. Check server logs."
    return "Could not submit the review. Check server logs."


def _find_pr_by_chat(chat_id: str) -> Optional[dict]:
    for key, entry in store.all().items():
        if entry.get("type") == "pr" and entry.get("chat_id") == chat_id and entry.get("state") == "pending":
            return {"key": key, **entry}
    return None


def _handle_pr_reply(pr: dict, reply_text: str, sender: str, message_id: str) -> str:
    if not _approver_allowed(sender):
        return f"Sender {sender} is not on the approver allowlist."

    owner = pr["owner"]
    repo = pr["repo"]
    number = pr["number"]

    try:
        diff = get_pr_diff(owner, repo, number)
    except Exception as e:
        logger.error("Failed to fetch PR diff: %s", e)
        return "Could not fetch the PR diff from GitHub."

    review = parse_review(reply_text, diff)
    if not review:
        return "Could not parse your review. Try again with explicit 'approve', 'request changes', or 'comment'."

    decision = review["decision"]
    try:
        submit_review(
            owner=owner,
            repo=repo,
            number=number,
            decision=decision,
            body=review.get("body", ""),
            line_comments=review.get("line_comments", []),
        )
    except Exception as e:
        logger.error("GitHub review submit failed: %s", e)
        return _friendly_github_error(e, decision)

    entry = store.get(pr["key"]) or {}
    entry["state"] = "reviewed"
    entry["decision"] = decision
    entry["reviewer"] = sender
    store.set(pr["key"], entry)

    if message_id:
        emoji = {"approve": "✅", "request_changes": "❌", "comment": "💬"}[decision]
        _safe(send_reaction, message_id, "custom", emoji)

    n_comments = len(review.get("line_comments", []))
    suffix = f" ({n_comments} line comment{'s' if n_comments != 1 else ''})" if n_comments else ""
    label = {"approve": "Approved", "request_changes": "Changes requested", "comment": "Commented"}[decision]
    return f"{label} on {owner}/{repo}#{number}{suffix}."


def _pending_ids() -> list[str]:
    return [
        k for k, v in store.all().items()
        if v.get("state") == DeployState.PENDING and v.get("type") != "pr"
    ]


def _handle_command(body: str, sender: str, message_id: str) -> Optional[str]:
    """Parse the command. Returns the reply text."""
    words = body.split()
    if not words:
        return "Reply 'approve', 'rollback', or 'status'."

    if words[0] == "status":
        pending = _pending_ids()
        if not pending:
            return "No pending deploys."
        lines = ["Pending deploys:"]
        for did in pending:
            v = store.get(did) or {}
            lines.append(f"  {did} — {v.get('repo', '?')} ({v.get('branch', '?')})")
        return "\n".join(lines)

    force = False
    if words[0] == "force":
        force = True
        words = words[1:]
        if not words:
            return "Usage: 'force approve' or 'force approve 10'."

    percent = 100
    if len(words) >= 2 and words[-1].isdigit():
        p = int(words[-1])
        if p in (10, 25, 50, 100):
            percent = p
            words = words[:-1]
        else:
            return "Canary must be one of: 10, 25, 50, 100."

    if len(words) == 2:
        deploy_id, command = words[0], words[1]
    elif len(words) == 1:
        command = words[0]
        pending = _pending_ids()
        if len(pending) == 1:
            deploy_id = pending[0]
        elif not pending:
            return "No pending deploys."
        else:
            return f"Multiple pending: {', '.join(pending)}\nReply '<id> approve'."
    else:
        return "Reply 'approve', 'rollback', or 'status'."

    entry = store.get(deploy_id)
    if not entry:
        return f"Unknown deploy ID: {deploy_id}"
    if entry["state"] != DeployState.PENDING:
        return f"Deploy {deploy_id} is already {entry['state']}."

    if not _approver_allowed(sender):
        return f"Sender {sender} is not on the approver allowlist."

    if command == "approve":
        if not _in_deploy_window() and not force:
            return (
                "Outside the deploy window. Reply 'force approve' to override."
            )
        entry["state"] = DeployState.APPROVED
        entry["canary_percent"] = percent
        entry["approver"] = sender
        entry["forced"] = force
        store.set(deploy_id, entry)
        if message_id:
            _safe(send_reaction, message_id, "custom", "✅")
        logger.info("Deploy %s APPROVED by %s at %d%%", deploy_id, sender, percent)
        scope = "full" if percent == 100 else f"canary {percent}%"
        return f"Approved ({scope}). Deploying {deploy_id}..."

    if command == "rollback":
        entry["state"] = DeployState.ROLLED_BACK
        entry["approver"] = sender
        store.set(deploy_id, entry)
        if message_id:
            _safe(send_reaction, message_id, "custom", "❌")
        logger.info("Deploy %s ROLLED BACK by %s", deploy_id, sender)
        return f"Cancelled. Deploy {deploy_id} rolled back."

    return "Unknown command. Reply 'approve', 'rollback', or 'status'."


def _safe(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except Exception as e:
        logger.error("Non-fatal error calling %s: %s", fn.__name__, e)


class RegisterDeploy(BaseModel):
    deploy_id: str
    repo: str
    branch: str
    actor: str
    notify_number: str
    commit_sha: str = ""
    commit_message: str = ""
    pr_title: str = ""
    files_changed: list[str] = []
    diff_stat: str = ""
    run_url: str = ""


@app.post("/deploy/register")
async def register_deploy(body: RegisterDeploy):
    risk = ""
    if body.commit_sha:
        risk = summarize_risk(
            sha=body.commit_sha,
            commit_message=body.commit_message,
            files_changed=body.files_changed,
            diff_stat=body.diff_stat,
        )

    outside_window = not _in_deploy_window()

    chat_id = send_deploy_alert(
        to=body.notify_number,
        deploy_id=body.deploy_id,
        repo=body.repo,
        branch=body.branch,
        actor=body.actor,
        commit_sha=body.commit_sha,
        commit_message=body.commit_message,
        pr_title=body.pr_title,
        files_changed_count=len(body.files_changed),
        run_url=body.run_url,
        risk_summary=risk,
        outside_window=outside_window,
    )

    store.set(
        body.deploy_id,
        {
            "state": DeployState.PENDING,
            "chat_id": chat_id,
            "repo": body.repo,
            "branch": body.branch,
            "actor": body.actor,
            "commit_sha": body.commit_sha,
            "risk_summary": risk,
        },
    )
    logger.info("Registered deploy %s (chat: %s)", body.deploy_id, chat_id)
    return {
        "ok": True,
        "deploy_id": body.deploy_id,
        "state": DeployState.PENDING,
        "risk_summary": risk,
    }


class RegisterPR(BaseModel):
    owner: str
    repo: str
    number: int
    title: str
    author: str
    notify_number: str
    url: str = ""


@app.post("/pr/register")
async def register_pr(body: RegisterPR):
    msg = (
        f"PR ready for review — {body.owner}/{body.repo}#{body.number}\n"
        f"Title: {body.title}\n"
        f"By: {body.author}\n"
    )
    if body.url:
        msg += f"\n{body.url}\n"
    msg += "\nReply with your review (e.g. 'approve', 'request changes — auth.py:42 needs a timeout')."

    chat = create_chat(body.notify_number, msg)
    chat_id = chat["chat"]["id"]

    key = f"pr-{body.owner}-{body.repo}-{body.number}"
    store.set(
        key,
        {
            "type": "pr",
            "state": "pending",
            "chat_id": chat_id,
            "owner": body.owner,
            "repo": body.repo,
            "number": body.number,
            "title": body.title,
            "author": body.author,
        },
    )
    logger.info("Registered PR %s/%s#%d (chat: %s)", body.owner, body.repo, body.number, chat_id)
    return {"ok": True, "key": key, "chat_id": chat_id}


@app.get("/deploy/status/{deploy_id}")
async def get_status(deploy_id: str):
    entry = store.get(deploy_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Unknown deploy ID")
    return {
        "deploy_id": deploy_id,
        "state": entry["state"],
        "canary_percent": entry.get("canary_percent", 100),
        "approver": entry.get("approver", ""),
        "forced": entry.get("forced", False),
    }
