"""Emit findings as SARIF 2.1.0 so they surface in GitHub's Security tab.

SARIF (Static Analysis Results Interchange Format) is the standard GitHub code
scanning ingests. Uploading a SARIF file (via github/codeql-action/upload-sarif)
puts each finding on the Security tab and as inline PR annotations.
"""

from __future__ import annotations

import re

from . import __version__
from .models import Finding, Review, StaticFinding

SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
INFO_URI = "https://github.com/DnyaneshU/TerraSentinel"

# SARIF levels are: error | warning | note.
_LEVEL = {
    "critical": "error",
    "high": "error",
    "medium": "warning",
    "low": "note",
    "info": "note",
}


def review_to_sarif(review: Review) -> dict:
    """SARIF document from the AI-enriched review findings."""
    rules: dict[str, dict] = {}
    results: list[dict] = []
    for f in review.findings:
        rule_id = f.related_check_id or _slug(f.title)
        rules.setdefault(
            rule_id,
            {
                "id": rule_id,
                "name": _slug(f.title),
                "shortDescription": {"text": f.title},
                "properties": {"category": f.category},
            },
        )
        results.append(
            _result(rule_id, _level(f.severity.value), _message(f), f.file, f.line)
        )
    return _document(list(rules.values()), results)


def static_findings_to_sarif(findings: list[StaticFinding]) -> dict:
    """SARIF document from raw scanner findings (scan-only mode)."""
    rules: dict[str, dict] = {}
    results: list[dict] = []
    for f in findings:
        rule = {"id": f.check_id, "name": _slug(f.title), "shortDescription": {"text": f.title}}
        if f.guideline:
            rule["helpUri"] = f.guideline
        rules.setdefault(f.check_id, rule)
        results.append(_result(f.check_id, _level(f.severity), f.title, f.file, f.line))
    return _document(list(rules.values()), results)


def _document(rules: list[dict], results: list[dict]) -> dict:
    return {
        "$schema": SARIF_SCHEMA,
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "TerraSentinel",
                        "informationUri": INFO_URI,
                        "version": __version__,
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }


def _result(rule_id: str, level: str, message: str, file: str, line: int | None) -> dict:
    start = line if (line and line > 0) else 1
    return {
        "ruleId": rule_id,
        "level": level,
        "message": {"text": message},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": file},
                    "region": {"startLine": start},
                }
            }
        ],
    }


def _message(f: Finding) -> str:
    parts = [f.explanation]
    if f.recommendation:
        parts.append(f"Recommendation: {f.recommendation}")
    if f.guardrail:
        parts.append(f"Violates guardrail: {f.guardrail}")
    return " ".join(p for p in parts if p)


def _level(severity: str | None) -> str:
    return _LEVEL.get((severity or "").lower(), "warning")


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")[:60] or "finding"
