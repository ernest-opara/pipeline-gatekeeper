"""AI pre-flight risk summary for deploys.

Calls Claude to produce a one-line risk assessment from a diff. Cached per
commit SHA so repeated registrations of the same SHA don't re-bill.
"""

import logging
import os

logger = logging.getLogger(__name__)

_cache: dict[str, str] = {}

_SYSTEM = """You are a deployment risk reviewer. You will be given a git diff
summary and must respond with a single short sentence (max 20 words) describing
the risk level and reason. Start with one of: LOW RISK, MEDIUM RISK, HIGH RISK.

Examples:
  LOW RISK: doc-only changes.
  MEDIUM RISK: refactor of payment module with partial test coverage.
  HIGH RISK: touches auth middleware and secrets handling — review carefully.

No preamble, no markdown, no trailing punctuation beyond one period."""


def summarize_risk(sha: str, commit_message: str, files_changed: list[str], diff_stat: str) -> str:
    """Return a one-line risk summary. Returns empty string if unavailable."""
    if sha in _cache:
        return _cache[sha]

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return ""

    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic SDK not installed — skipping risk summary")
        return ""

    client = anthropic.Anthropic()
    user_content = (
        f"Commit: {commit_message}\n"
        f"Files changed ({len(files_changed)}):\n"
        + "\n".join(f"  - {f}" for f in files_changed[:50])
        + f"\n\nDiff stat:\n{diff_stat[:4000]}"
    )

    try:
        resp = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=200,
            system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_content}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "").strip()
        _cache[sha] = text
        return text
    except Exception as e:
        logger.error("Risk summary failed: %s", e)
        return ""
