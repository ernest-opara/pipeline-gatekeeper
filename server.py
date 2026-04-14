"""
Pipeline Gatekeeper — Webhook Server

Receives inbound Linq messages (v2026-02-03 webhook format),
parses approve/rollback commands, and exposes a /deploy/status/{deploy_id}
endpoint that GitHub Actions polls every 10s.

On each inbound message:
  1. Mark chat as read (clears unread badge)
  2. React to the message to acknowledge receipt
  3. Show typing indicator while processing
  4. Send the reply
  5. Stop typing indicator
"""

import os
import hmac
import hashlib
import logging
import time
import json
from enum import Enum
from fastapi import FastAPI, Request, HTTPException, Header
from pydantic import BaseModel
from linq_client import (
    reply_to_chat,
    send_deploy_alert,
    mark_as_read,
    start_typing,
    stop_typing,
    send_reaction,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Pipeline Gatekeeper")

LINQ_WEBHOOK_SECRET = os.environ.get("LINQ_WEBHOOK_SECRET", "")

# In-memory state store.
# Key: deploy_id  Value: {"state": DeployState, "chat_id": str, ...}
# Swap for Redis if you need persistence across restarts.
deploy_states: dict[str, dict] = {}


class DeployState(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    ROLLED_BACK = "rolled_back"


# ---------------------------------------------------------------------------
# Webhook signature verification
# ---------------------------------------------------------------------------

def _verify_signature(raw_body: bytes, timestamp: str, signature: str) -> bool:
    """Verify Linq HMAC-SHA256 webhook signature.
    Signed over: "{timestamp}.{raw_body}"
    """
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


# ---------------------------------------------------------------------------
# Linq webhook endpoint
# ---------------------------------------------------------------------------

@app.post("/webhook/linq")
async def linq_webhook(
    request: Request,
    x_webhook_timestamp: str = Header(default=""),
    x_webhook_signature: str = Header(default=""),
    x_webhook_event: str = Header(default=""),
):
    """
    Receives v2026-02-03 Linq webhook events.

    For message.received, the data shape is:
    {
      "event_type": "message.received",
      "data": {
        "chat": { "id": "<uuid>", "is_group": false, "owner_handle": {...} },
        "id": "<message-uuid>",
        "direction": "inbound",
        "sender_handle": { "handle": "+1...", "is_me": false, ... },
        "parts": [ { "type": "text", "value": "approve" } ],
        "sent_at": "...",
        "service": "iMessage"
      }
    }
    """
    raw_body = await request.body()

    # Reject replayed webhooks older than 5 minutes
    if x_webhook_timestamp:
        age = time.time() - float(x_webhook_timestamp)
        if age > 300:
            raise HTTPException(status_code=400, detail="Webhook timestamp too old")

    # Verify HMAC signature
    if x_webhook_signature and not _verify_signature(raw_body, x_webhook_timestamp, x_webhook_signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload = json.loads(raw_body)

    event_type = payload.get("event_type") or x_webhook_event
    logger.info("Webhook event: %s", event_type)

    if event_type != "message.received":
        return {"ok": True, "ignored": True}

    data = payload.get("data", {})

    # v2026-02-03: direction field instead of is_from_me
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

    logger.info("Inbound from %s in chat %s: '%s'", sender, chat_id, body)

    # 1. Mark as read immediately — clears the unread badge
    _safe(mark_as_read, chat_id)

    # 2. React to the message to acknowledge we received it
    #    (thumbs up while we figure out what they said)
    if message_id:
        _safe(send_reaction, message_id, "like")

    # 3. Show typing indicator while we process
    _safe(start_typing, chat_id)

    try:
        reply_text = _handle_command(body, chat_id, sender, message_id)
    finally:
        # 4. Always stop typing, even if something crashes
        _safe(stop_typing, chat_id)

    # 5. Send the reply in-thread
    if reply_text:
        await _reply(chat_id, reply_text)

    return {"ok": True}


def _handle_command(body: str, chat_id: str, sender: str, message_id: str) -> str | None:
    """Parse the command and update deploy state. Returns the reply text."""
    words = body.split()

    if len(words) == 2:
        deploy_id, command = words[0], words[1]
    elif len(words) == 1:
        command = words[0]
        pending = [k for k, v in deploy_states.items() if v["state"] == DeployState.PENDING]
        if len(pending) == 1:
            deploy_id = pending[0]
        elif len(pending) == 0:
            return "No pending deploys right now."
        else:
            ids = ", ".join(pending)
            return f"Multiple pending deploys: {ids}\nReply '<id> approve' or '<id> rollback'."
    else:
        return "Reply 'approve' or 'rollback' (optionally prefix with the deploy ID)."

    if deploy_id not in deploy_states:
        return f"Unknown deploy ID: {deploy_id}"

    current = deploy_states[deploy_id]["state"]
    if current != DeployState.PENDING:
        return f"Deploy {deploy_id} is already {current}."

    if command == "approve":
        deploy_states[deploy_id]["state"] = DeployState.APPROVED
        # Upgrade the earlier thumbs-up to a checkmark on approve
        if message_id:
            _safe(send_reaction, message_id, "custom", "✅")
        logger.info("Deploy %s APPROVED by %s", deploy_id, sender)
        return f"Approved. Deploying {deploy_id}..."

    elif command == "rollback":
        deploy_states[deploy_id]["state"] = DeployState.ROLLED_BACK
        # Red X reaction on rollback
        if message_id:
            _safe(send_reaction, message_id, "custom", "❌")
        logger.info("Deploy %s ROLLED BACK by %s", deploy_id, sender)
        return f"Cancelled. Deploy {deploy_id} rolled back."

    else:
        return "Unknown command. Reply 'approve' or 'rollback'."


def _safe(fn, *args, **kwargs):
    """Call a Linq API function and log errors instead of crashing."""
    try:
        fn(*args, **kwargs)
    except Exception as e:
        logger.error("Non-fatal error calling %s: %s", fn.__name__, e)


async def _reply(chat_id: str, body: str):
    try:
        reply_to_chat(chat_id, body)
    except Exception as e:
        logger.error("Failed to reply to chat %s: %s", chat_id, e)


# ---------------------------------------------------------------------------
# State management endpoints (called by GitHub Actions)
# ---------------------------------------------------------------------------

class RegisterDeploy(BaseModel):
    deploy_id: str
    repo: str
    branch: str
    actor: str
    notify_number: str


@app.post("/deploy/register")
async def register_deploy(body: RegisterDeploy):
    """Called by GitHub Actions at the start of the gate step."""
    chat_id = send_deploy_alert(
        to=body.notify_number,
        deploy_id=body.deploy_id,
        repo=body.repo,
        branch=body.branch,
        actor=body.actor,
    )

    deploy_states[body.deploy_id] = {
        "state": DeployState.PENDING,
        "chat_id": chat_id,
        "repo": body.repo,
        "branch": body.branch,
        "actor": body.actor,
    }
    logger.info("Registered deploy %s (chat: %s)", body.deploy_id, chat_id)
    return {"ok": True, "deploy_id": body.deploy_id, "state": DeployState.PENDING}


@app.get("/deploy/status/{deploy_id}")
async def get_status(deploy_id: str):
    """Polled by GitHub Actions every 10s."""
    if deploy_id not in deploy_states:
        raise HTTPException(status_code=404, detail="Unknown deploy ID")
    return {
        "deploy_id": deploy_id,
        "state": deploy_states[deploy_id]["state"],
    }
