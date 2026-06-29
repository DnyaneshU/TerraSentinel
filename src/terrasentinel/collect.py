"""Gather the material to review: Terraform files, their contents, and git diffs."""

from __future__ import annotations

import subprocess
from pathlib import Path

MAX_FILE_CHARS = 20_000  # guard against pasting a giant generated file into the prompt


def find_tf_files(path: str | Path) -> list[Path]:
    """Return all *.tf files under a directory, or the file itself if a file is given."""
    p = Path(path)
    if p.is_file():
        return [p] if p.suffix == ".tf" else []
    return sorted(f for f in p.rglob("*.tf") if ".terraform" not in f.parts)


def read_files(files: list[Path]) -> dict[str, str]:
    """Read file contents keyed by forward-slash relative path, truncated if huge."""
    out: dict[str, str] = {}
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(text) > MAX_FILE_CHARS:
            text = text[:MAX_FILE_CHARS] + "\n# ...[truncated by TerraSentinel]...\n"
        out[str(f).replace("\\", "/")] = text
    return out


def git_changed_tf_files(base: str, head: str = "HEAD") -> list[str]:
    """List *.tf files that changed between base and head (forward-slash paths)."""
    rng = f"{base}...{head}"
    proc = _git(["diff", "--name-only", "--diff-filter=d", rng, "--", "*.tf"])
    files = [line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()]
    return files


def git_diff(base: str, head: str = "HEAD", files: list[str] | None = None) -> str:
    """Unified diff for the given files (or all *.tf) between base and head."""
    rng = f"{base}...{head}"
    args = ["diff", rng, "--"]
    args += files if files else ["*.tf"]
    return _git(args).stdout


GUARDRAILS_FILENAMES = ("guardrails.md", ".terrasentinel/guardrails.md")


def load_guardrails(target: str | Path, explicit: str | None = None) -> tuple[str | None, str | None]:
    """Find and read the plain-English guardrails file.

    Returns (path, text). Looks at an explicit path first, then conventional
    locations relative to the target and the current directory.
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    base = Path(target)
    search_dirs = [base if base.is_dir() else base.parent, Path(".")]
    for d in search_dirs:
        for name in GUARDRAILS_FILENAMES:
            candidates.append(d / name)

    seen: set[Path] = set()
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        if c.is_file():
            text = c.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                return str(c).replace("\\", "/"), text
    return None, None


def resolve_base_ref(explicit: str | None) -> str:
    """Pick a sensible base ref: explicit > origin/main > origin/master > main > HEAD~1."""
    if explicit:
        return explicit
    for candidate in ("origin/main", "origin/master", "main", "master"):
        if _ref_exists(candidate):
            return candidate
    return "HEAD~1"


def matches_changed(finding_file: str, changed: list[str]) -> bool:
    """True if a scanner finding's path corresponds to one of the changed files."""
    ff = finding_file.replace("\\", "/").lstrip("/")
    for c in changed:
        cc = c.replace("\\", "/").lstrip("/")
        if ff == cc or ff.endswith("/" + cc) or cc.endswith("/" + ff):
            return True
        if Path(ff).name == Path(cc).name:
            return True
    return False


def _git(args: list[str]) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0 and "diff" not in args[0]:
        raise GitError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc


def _ref_exists(ref: str) -> bool:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", ref],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


class GitError(RuntimeError):
    """Raised when a git command fails (e.g. not a repository)."""
