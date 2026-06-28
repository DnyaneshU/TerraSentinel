"""Post (and update in place) a review comment on a GitHub pull request.

Uses the GitHub REST API directly via `requests` — no extra SDK. The bot finds
its own previous comment by a hidden marker and edits it, so re-running on each
push updates one comment instead of spamming the PR.
"""

from __future__ import annotations

import json
import os

import requests

from .render import COMMENT_MARKER

API_ROOT = "https://api.github.com"
_TIMEOUT = 30


class GitHubError(RuntimeError):
    """Raised when a GitHub API call fails or required context is missing."""


def resolve_pr_context() -> tuple[str, int, str]:
    """Resolve (repo, pr_number, token) from the environment / event payload.

    Works both locally (set the env vars yourself) and inside GitHub Actions,
    where GITHUB_REPOSITORY / GITHUB_TOKEN / GITHUB_EVENT_PATH are provided.
    """
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")
    if not token:
        raise GitHubError("GITHUB_TOKEN is not set.")
    if not repo:
        raise GitHubError("GITHUB_REPOSITORY is not set (expected 'owner/repo').")

    pr_number = _pr_number_from_env()
    if pr_number is None:
        raise GitHubError(
            "Could not determine the pull-request number. Set GITHUB_PR_NUMBER, "
            "or run inside a pull_request GitHub Actions workflow."
        )
    return repo, pr_number, token


def post_or_update_comment(repo: str, pr_number: int, token: str, body: str) -> str:
    """Create the bot comment, or update its existing one. Returns the comment URL."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    existing = _find_existing_comment(repo, pr_number, headers)
    if existing is not None:
        url = f"{API_ROOT}/repos/{repo}/issues/comments/{existing}"
        resp = requests.patch(url, headers=headers, json={"body": body}, timeout=_TIMEOUT)
    else:
        url = f"{API_ROOT}/repos/{repo}/issues/{pr_number}/comments"
        resp = requests.post(url, headers=headers, json={"body": body}, timeout=_TIMEOUT)

    if resp.status_code >= 300:
        raise GitHubError(f"GitHub API {resp.status_code}: {resp.text[:300]}")
    return resp.json().get("html_url", "")


def _find_existing_comment(repo: str, pr_number: int, headers: dict[str, str]) -> int | None:
    url = f"{API_ROOT}/repos/{repo}/issues/{pr_number}/comments"
    page = 1
    while True:
        resp = requests.get(
            url, headers=headers, params={"per_page": 100, "page": page}, timeout=_TIMEOUT
        )
        if resp.status_code >= 300:
            raise GitHubError(f"GitHub API {resp.status_code}: {resp.text[:300]}")
        comments = resp.json()
        if not comments:
            return None
        for c in comments:
            if COMMENT_MARKER in (c.get("body") or ""):
                return c.get("id")
        if len(comments) < 100:
            return None
        page += 1


def _pr_number_from_env() -> int | None:
    explicit = os.getenv("GITHUB_PR_NUMBER")
    if explicit and explicit.strip().isdigit():
        return int(explicit.strip())

    # GitHub Actions writes the triggering event to this file.
    event_path = os.getenv("GITHUB_EVENT_PATH")
    if event_path and os.path.exists(event_path):
        try:
            with open(event_path, encoding="utf-8") as f:
                event = json.load(f)
            pr = event.get("pull_request") or {}
            if isinstance(pr.get("number"), int):
                return pr["number"]
            if isinstance(event.get("number"), int):
                return event["number"]
        except (OSError, json.JSONDecodeError):
            pass

    # Fallback: refs/pull/<n>/merge
    ref = os.getenv("GITHUB_REF", "")
    parts = ref.split("/")
    if len(parts) >= 3 and parts[1] == "pull" and parts[2].isdigit():
        return int(parts[2])
    return None
