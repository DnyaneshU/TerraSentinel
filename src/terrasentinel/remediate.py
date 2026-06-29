"""Verified fixes — the part that makes TerraSentinel's remediation trustworthy.

Most AI tools *suggest* fixes and hope they're right. We instead:
  1. Ask the model for corrected full file contents (propose_fixes).
  2. Write them to a scratch copy and re-run the scanner (verify_fixes).
  3. Report exactly which scanner checks the fix RESOLVED and whether it
     INTRODUCED anything new.

That turns "here's a suggested fix" into "here's a fix proven to clear N findings
with 0 new issues" — verified, not guessed.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .config import Settings
from .models import StaticFinding
from .reviewer import ReviewError, _extract_json, _text_of
from .scanner import run_scanner

REMEDIATION_SYSTEM_PROMPT = """\
You are a senior platform engineer fixing Infrastructure-as-Code. Given Terraform
files and a list of confirmed issues, produce corrected versions of the files that
resolve EVERY listed issue while preserving the original intent, resource names,
and addresses. Do not add unrelated resources or refactors.

Respond with ONLY a single JSON object (no prose, no markdown fences):
{"files": [{"path": "<repo-relative path>", "content": "<full corrected file>"}]}
Include only files you actually changed; return each one's COMPLETE new content.
"""


@dataclass
class VerificationResult:
    """The outcome of applying proposed fixes and re-scanning."""

    before: int
    after: int
    resolved_keys: set[tuple[str, str]] = field(default_factory=set)
    introduced_keys: set[tuple[str, str]] = field(default_factory=set)
    corrected_files: dict[str, str] = field(default_factory=dict)

    @property
    def resolved_count(self) -> int:
        return len(self.resolved_keys)

    @property
    def introduced_count(self) -> int:
        return len(self.introduced_keys)

    @property
    def is_clean(self) -> bool:
        """Fixes resolved something and introduced nothing new."""
        return self.resolved_count > 0 and self.introduced_count == 0

    def resolves(self, check_id: str | None, file: str) -> bool:
        """True if the re-scan confirmed this (check_id, file) finding is gone."""
        if not check_id:
            return False
        return (check_id, Path(file).name) in self.resolved_keys


def propose_fixes(
    settings: Settings,
    file_contents: dict[str, str],
    static_findings: list[StaticFinding],
) -> dict[str, str]:
    """Ask the model for corrected file contents. Returns {path: content}."""
    if not settings.has_api_key:
        raise ReviewError("No ANTHROPIC_API_KEY found — required to generate fixes.")
    try:
        import anthropic
    except ImportError as e:  # pragma: no cover
        raise ReviewError("The 'anthropic' package is not installed.") from e

    client = anthropic.Anthropic(api_key=settings.api_key)
    messages = [{"role": "user", "content": _build_fix_prompt(file_contents, static_findings)}]

    last_error: str | None = None
    for _ in range(2):
        if last_error:
            messages.append(
                {
                    "role": "user",
                    "content": f"Your previous response could not be parsed: {last_error}. "
                    "Respond again with ONLY the JSON object.",
                }
            )
        try:
            resp = client.messages.create(
                model=settings.model,
                max_tokens=settings.max_tokens,
                system=REMEDIATION_SYSTEM_PROMPT,
                messages=messages,
            )
        except anthropic.APIError as e:
            raise ReviewError(f"Claude API error: {e}") from e

        if resp.stop_reason == "refusal":
            raise ReviewError("Claude declined to generate fixes (safety refusal).")

        text = _text_of(resp)
        try:
            data = json.loads(_extract_json(text))
            files = {
                str(f["path"]): str(f["content"])
                for f in data.get("files", [])
                if isinstance(f, dict) and "path" in f and "content" in f
            }
            if not files:
                raise ValueError("no files returned")
            return files
        except (ValueError, json.JSONDecodeError, KeyError, TypeError) as e:
            last_error = str(e)
            messages.append({"role": "assistant", "content": text})

    raise ReviewError(f"Could not get valid fixes from Claude: {last_error}")


def verify_fixes(
    original_findings: list[StaticFinding],
    original_file_contents: dict[str, str],
    corrected_files: dict[str, str],
    scanner: str | None = None,
) -> VerificationResult:
    """Apply corrected files to a scratch copy, re-scan, and diff the findings.

    Findings are keyed by (check_id, file basename) so the comparison is robust to
    path differences between the original scan and the temp-dir re-scan.
    """
    before_keys = {(f.check_id, Path(f.file).name) for f in original_findings}

    merged = dict(original_file_contents)
    merged.update(corrected_files)  # corrected versions override originals

    with tempfile.TemporaryDirectory(prefix="terrasentinel-verify-") as td:
        root = Path(td)
        for rel, content in merged.items():
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
        _, after = run_scanner(root, scanner)

    after_keys = {(f.check_id, Path(f.file).name) for f in after}
    return VerificationResult(
        before=len(before_keys),
        after=len(after_keys),
        resolved_keys=before_keys - after_keys,
        introduced_keys=after_keys - before_keys,
        corrected_files=corrected_files,
    )


def _build_fix_prompt(file_contents: dict[str, str], findings: list[StaticFinding]) -> str:
    parts = ["Fix the following confirmed issues in these Terraform files.", "", "## Issues"]
    for f in findings:
        loc = f"{f.file}:{f.line}" if f.line else f.file
        parts.append(f"- [{f.check_id}] {f.title} ({loc})")
    parts.append("")
    parts.append("## Files (return COMPLETE corrected content for any you change)")
    for path, content in file_contents.items():
        parts.append(f"### {path}")
        parts.append("```hcl")
        parts.append(content)
        parts.append("```")
        parts.append("")
    parts.append('Return ONLY: {"files": [{"path": "...", "content": "..."}]}')
    return "\n".join(parts)
