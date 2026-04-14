import os
import httpx

LINQ_API_BASE = "https://api.linqapp.com/api/partner/v3"
LINQ_API_TOKEN = os.environ.get("LINQ_API_TOKEN")
LINQ_PHONE_NUMBER = os.environ.get("LINQ_PHONE_NUMBER")  # your Linq line, e.g. +12052960153


def _require_credentials():
    if not LINQ_API_TOKEN or not LINQ_PHONE_NUMBER:
        raise RuntimeError(
            "LINQ_API_TOKEN and LINQ_PHONE_NUMBER must be set to call the Linq API."
        )


def _headers():
    _require_credentials()
    return {
        "Authorization": f"Bearer {LINQ_API_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def create_chat(to: str, body: str) -> dict:
    """Start a new chat (used for the initial deploy notification)."""
    payload = {
        "from": LINQ_PHONE_NUMBER,
        "to": [to],
        "message": {
            "parts": [{"type": "text", "value": body}]
        },
    }
    resp = httpx.post(f"{LINQ_API_BASE}/chats", json=payload, headers=_headers())
    resp.raise_for_status()
    return resp.json()


def reply_to_chat(chat_id: str, body: str) -> dict:
    """Send a message to an existing chat (used for approve/rollback confirmation)."""
    payload = {
        "message": {
            "parts": [{"type": "text", "value": body}]
        }
    }
    resp = httpx.post(
        f"{LINQ_API_BASE}/chats/{chat_id}/messages",
        json=payload,
        headers=_headers(),
    )
    resp.raise_for_status()
    return resp.json()


def mark_as_read(chat_id: str) -> None:
    """Mark all messages in a chat as read. Clears the unread badge on Linq's side."""
    resp = httpx.post(
        f"{LINQ_API_BASE}/chats/{chat_id}/read",
        headers=_headers(),
    )
    resp.raise_for_status()


def start_typing(chat_id: str) -> None:
    """Show the typing indicator in the chat."""
    resp = httpx.post(
        f"{LINQ_API_BASE}/chats/{chat_id}/typing",
        headers=_headers(),
    )
    resp.raise_for_status()


def stop_typing(chat_id: str) -> None:
    """Stop the typing indicator. Called after the reply is sent."""
    resp = httpx.delete(
        f"{LINQ_API_BASE}/chats/{chat_id}/typing",
        headers=_headers(),
    )
    resp.raise_for_status()


def send_reaction(message_id: str, reaction: str, custom_emoji: str | None = None) -> None:
    """
    React to a specific message.

    reaction: "love", "like", "dislike", "laugh", "emphasize", "question", or "custom"
    custom_emoji: required when reaction == "custom", e.g. "🚀"

    Examples:
        send_reaction(message_id, "like")
        send_reaction(message_id, "custom", "✅")
        send_reaction(message_id, "custom", "🚀")
    """
    payload: dict = {"operation": "add", "type": reaction}
    if custom_emoji:
        payload["custom_emoji"] = custom_emoji
    resp = httpx.post(
        f"{LINQ_API_BASE}/messages/{message_id}/reactions",
        json=payload,
        headers=_headers(),
    )
    resp.raise_for_status()


def send_deploy_alert(
    to: str,
    deploy_id: str,
    repo: str,
    branch: str,
    actor: str,
    commit_sha: str = "",
    commit_message: str = "",
    pr_title: str = "",
    files_changed_count: int = 0,
    run_url: str = "",
    risk_summary: str = "",
    outside_window: bool = False,
) -> str:
    """Send the deploy gate notification and return the chat_id for future replies."""
    lines = [f"Deploy ready — {repo} ({branch})"]
    if pr_title:
        lines.append(f"PR: {pr_title}")
    if commit_message:
        first_line = commit_message.splitlines()[0][:120]
        lines.append(f"Commit: {first_line}")
    if commit_sha:
        lines.append(f"SHA: {commit_sha[:7]}")
    if files_changed_count:
        lines.append(f"Files changed: {files_changed_count}")
    lines.append(f"By: {actor}")
    lines.append(f"ID: {deploy_id}")
    if risk_summary:
        lines.append("")
        lines.append(risk_summary)
    if outside_window:
        lines.append("")
        lines.append("⚠︎ Outside deploy window — reply 'force approve' to override.")
    if run_url:
        lines.append("")
        lines.append(f"Run: {run_url}")
    lines.append("")
    lines.append("Reply 'approve', 'approve 10' (canary 10%), or 'rollback'.")
    body = "\n".join(lines)
    result = create_chat(to, body)
    return result["chat"]["id"]
