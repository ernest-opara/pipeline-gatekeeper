"""Minimal GitHub API client for submitting PR reviews."""

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


def _token() -> str:
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not tok:
        raise RuntimeError("GH_TOKEN must be set to submit PR reviews.")
    return tok


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_token()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def get_pr_diff(owner: str, repo: str, number: int) -> str:
    resp = httpx.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}",
        headers={**_headers(), "Accept": "application/vnd.github.v3.diff"},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.text


def get_pr(owner: str, repo: str, number: int) -> dict:
    resp = httpx.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}",
        headers=_headers(),
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def merge_pr(owner: str, repo: str, number: int, method: str = "squash") -> dict:
    """Merge a PR immediately if GitHub allows it. Raises on failure."""
    resp = httpx.put(
        f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}/merge",
        headers=_headers(),
        json={"merge_method": method},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def enable_auto_merge(owner: str, repo: str, number: int, method: str = "SQUASH") -> bool:
    """Enable GitHub's auto-merge on a PR via GraphQL. Returns True on success."""
    pr = get_pr(owner, repo, number)
    node_id = pr.get("node_id")
    if not node_id:
        return False
    query = """
    mutation($pr: ID!, $method: PullRequestMergeMethod!) {
      enablePullRequestAutoMerge(input: {pullRequestId: $pr, mergeMethod: $method}) {
        pullRequest { id }
      }
    }
    """
    resp = httpx.post(
        f"{GITHUB_API}/graphql",
        headers=_headers(),
        json={"query": query, "variables": {"pr": node_id, "method": method.upper()}},
        timeout=20,
    )
    resp.raise_for_status()
    payload = resp.json()
    errors = payload.get("errors") or []
    if errors:
        logger.warning("enablePullRequestAutoMerge errors: %s", errors)
        return False
    return True


def submit_review(
    owner: str,
    repo: str,
    number: int,
    decision: str,
    body: str,
    line_comments: Optional[list[dict]] = None,
) -> dict:
    """Submit a pull request review.

    decision: "approve" | "request_changes" | "comment"
    line_comments: [{"path": str, "line": int, "side": "RIGHT"|"LEFT", "body": str}]
    """
    event_map = {
        "approve": "APPROVE",
        "request_changes": "REQUEST_CHANGES",
        "comment": "COMMENT",
    }
    event = event_map.get(decision)
    if not event:
        raise ValueError(f"Unknown decision: {decision}")

    payload: dict = {"event": event, "body": body or ""}
    if line_comments:
        payload["comments"] = [
            {
                "path": c["path"],
                "line": c["line"],
                "side": c.get("side", "RIGHT"),
                "body": c["body"],
            }
            for c in line_comments
        ]

    resp = httpx.post(
        f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}/reviews",
        json=payload,
        headers=_headers(),
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()
