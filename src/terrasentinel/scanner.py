"""Static-analysis layer: run an external IaC scanner and normalize its output.

The scanner is deliberately *external and optional*:
  * If `checkov` is importable (same venv) or on PATH, we use it.
  * Else if `tfsec` is on PATH, we use it.
  * Else we return nothing and the reviewer runs in AI-only mode.

This keeps the TerraSentinel package itself pure-Python (installs everywhere) and
lets the deterministic findings *ground* the LLM so it cites real issues instead
of hallucinating.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from .models import StaticFinding

SCANNER_TIMEOUT = 300  # seconds


def detect_scanner() -> str | None:
    """Return the name of an available scanner, or None."""
    if shutil.which("checkov"):
        return "checkov"
    # checkov installed in the same environment but not on PATH (common on Windows venvs)
    try:
        import checkov  # noqa: F401

        return "checkov-module"
    except Exception:
        pass
    if shutil.which("tfsec"):
        return "tfsec"
    return None


def run_scanner(
    path: str | Path,
    scanner: str | None = None,
    frameworks: list[str] | None = None,
) -> tuple[str | None, list[StaticFinding]]:
    """Run the chosen (or auto-detected) scanner against *path*.

    Returns (scanner_name_used, findings). scanner_name_used is None when no
    scanner is available. `frameworks` selects checkov frameworks (terraform,
    kubernetes, cloudformation, …); defaults to Terraform only.
    """
    scanner = scanner or detect_scanner()
    if scanner is None:
        return None, []

    frameworks = frameworks or ["terraform"]
    target = Path(path)
    if scanner in ("checkov", "checkov-module"):
        # Use the module runner if explicitly requested, or if the console script
        # isn't on PATH (common on Windows venvs) but the package is importable.
        use_module = scanner == "checkov-module" or shutil.which("checkov") is None
        return ("checkov", _run_checkov(target, use_module=use_module, frameworks=frameworks))
    if scanner == "tfsec":
        return ("tfsec", _run_tfsec(target))  # tfsec is Terraform-only
    raise ValueError(f"Unknown scanner: {scanner}")


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=SCANNER_TIMEOUT,
        encoding="utf-8",
        errors="replace",
    )


def _run_checkov(target: Path, use_module: bool, frameworks: list[str]) -> list[StaticFinding]:
    base = [sys.executable, "-m", "checkov.main"] if use_module else ["checkov"]
    mode = ["-f", str(target)] if target.is_file() else ["-d", str(target)]
    fw_args: list[str] = []
    for fw in frameworks:
        fw_args += ["--framework", fw]
    cmd = [*base, *mode, "-o", "json", "--compact", "--quiet", *fw_args]

    # checkov exits non-zero when it finds failed checks — that is success for us.
    proc = _run(cmd)
    stdout = proc.stdout.strip()
    if not stdout:
        # Nothing parseable (e.g. no terraform files found); surface stderr on real errors.
        if proc.returncode not in (0, 1) and proc.stderr.strip():
            raise ScannerError(f"checkov failed: {proc.stderr.strip()[:500]}")
        return []

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        # Some checkov versions print a leading line; try to recover the JSON body.
        data = _salvage_json(stdout)
        if data is None:
            raise ScannerError("Could not parse checkov JSON output")

    return _parse_checkov(data)


def _parse_checkov(data: object) -> list[StaticFinding]:
    # checkov returns a dict for a single framework, or a list of dicts for several.
    blocks = data if isinstance(data, list) else [data]
    findings: list[StaticFinding] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        results = block.get("results") or {}
        for check in results.get("failed_checks", []) or []:
            line_range = check.get("file_line_range") or [None]
            findings.append(
                StaticFinding(
                    check_id=str(check.get("check_id", "UNKNOWN")),
                    title=str(check.get("check_name", "Unnamed check")),
                    severity=_norm_sev(check.get("severity")),
                    file=_norm_path(check.get("file_path", "")),
                    line=line_range[0] if line_range else None,
                    resource=check.get("resource"),
                    guideline=check.get("guideline"),
                    scanner="checkov",
                )
            )
    return findings


def _run_tfsec(target: Path) -> list[StaticFinding]:
    cmd = ["tfsec", str(target), "-f", "json", "--no-color"]
    proc = _run(cmd)
    stdout = proc.stdout.strip()
    if not stdout:
        return []
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        raise ScannerError("Could not parse tfsec JSON output")

    findings: list[StaticFinding] = []
    for r in data.get("results", []) or []:
        loc = r.get("location") or {}
        findings.append(
            StaticFinding(
                check_id=str(r.get("rule_id", "UNKNOWN")),
                title=str(r.get("rule_description") or r.get("description") or "Unnamed rule"),
                severity=_norm_sev(r.get("severity")),
                file=_norm_path(loc.get("filename", "")),
                line=loc.get("start_line"),
                resource=r.get("resource"),
                guideline=r.get("links", [None])[0] if r.get("links") else None,
                scanner="tfsec",
            )
        )
    return findings


def _norm_sev(value: object) -> str | None:
    if not value:
        return None
    s = str(value).lower()
    # tfsec uses CRITICAL/HIGH/MEDIUM/LOW; checkov sometimes null.
    return s if s in {"critical", "high", "medium", "low", "info"} else None


def _norm_path(value: str) -> str:
    return str(value).replace("\\", "/").lstrip("/")


def _salvage_json(text: str) -> object | None:
    start = text.find("{")
    bracket = text.find("[")
    if bracket != -1 and (start == -1 or bracket < start):
        start = bracket
    if start == -1:
        return None
    try:
        return json.loads(text[start:])
    except json.JSONDecodeError:
        return None


class ScannerError(RuntimeError):
    """Raised when a scanner is present but fails unexpectedly."""
