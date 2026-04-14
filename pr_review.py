"""Parse a free-form iMessage reply into a structured GitHub review.

Uses Claude with JSON-schema structured output. Validates line references
against the actual PR diff so hallucinated line numbers are dropped.
"""

import json
import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)

_SYSTEM = """You convert a reviewer's free-form message into a structured GitHub PR review.

Rules:
- decision is exactly one of: "approve", "request_changes", "comment".
- body is a short overall review comment (may be empty).
- line_comments reference ONLY lines actually shown in the provided diff.
  - path: the file path exactly as it appears in the diff
  - line: the line number on the RIGHT side of the diff (the new version)
  - side: "RIGHT" for added/context lines, "LEFT" only for lines shown as deleted
  - body: the inline comment text
- If the reviewer is vague about approval, prefer "comment" over "approve".
- Do not invent line numbers or file paths. If unsure, omit the line_comment."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["approve", "request_changes", "comment"]},
        "body": {"type": "string"},
        "line_comments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "line": {"type": "integer"},
                    "side": {"type": "string", "enum": ["RIGHT", "LEFT"]},
                    "body": {"type": "string"},
                },
                "required": ["path", "line", "side", "body"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["decision", "body", "line_comments"],
    "additionalProperties": False,
}


def _commentable_lines(diff: str) -> dict[tuple[str, str], set[int]]:
    """Parse a unified diff into {(path, side): {line_numbers}} where comments are allowed."""
    allowed: dict[tuple[str, str], set[int]] = {}
    current: Optional[str] = None
    right_line = 0
    left_line = 0

    for raw in diff.splitlines():
        if raw.startswith("diff --git"):
            m = re.match(r"diff --git a/(.+) b/(.+)", raw)
            current = m.group(2) if m else None
            continue
        if raw.startswith("+++ b/"):
            current = raw[6:]
            continue
        if raw.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
            if m:
                left_line = int(m.group(1))
                right_line = int(m.group(2))
            continue
        if current is None:
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            allowed.setdefault((current, "RIGHT"), set()).add(right_line)
            right_line += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            allowed.setdefault((current, "LEFT"), set()).add(left_line)
            left_line += 1
        elif raw.startswith(" "):
            allowed.setdefault((current, "RIGHT"), set()).add(right_line)
            right_line += 1
            left_line += 1

    return allowed


def parse_review(reply_text: str, diff: str) -> Optional[dict]:
    """Turn `reply_text` into {decision, body, line_comments}. Returns None on failure."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.warning("ANTHROPIC_API_KEY not set — cannot parse review")
        return None

    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic SDK not installed — cannot parse review")
        return None

    client = anthropic.Anthropic()
    truncated_diff = diff[:60000]
    user_content = (
        f"Reviewer's message:\n{reply_text}\n\n"
        f"PR diff:\n{truncated_diff}"
    )

    try:
        resp = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=2000,
            system=_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": _SCHEMA,
                }
            },
        )
    except Exception as e:
        logger.error("Claude review parse failed: %s", e)
        return None

    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        review = json.loads(text)
    except json.JSONDecodeError:
        logger.error("Claude returned invalid JSON: %s", text[:200])
        return None

    allowed = _commentable_lines(diff)
    valid_comments = []
    dropped = 0
    for c in review.get("line_comments", []):
        key = (c["path"], c["side"])
        if c["line"] in allowed.get(key, set()):
            valid_comments.append(c)
        else:
            dropped += 1
    if dropped:
        logger.info("Dropped %d line comment(s) referencing lines not in diff", dropped)
    review["line_comments"] = valid_comments
    return review
