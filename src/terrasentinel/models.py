"""Typed data models shared across the pipeline.

Two layers:
  * StaticFinding — the normalized output of an external scanner (checkov / tfsec).
    These are deterministic facts we feed to the LLM as grounding.
  * Finding / Review — the AI-enriched result: plain-English explanations, blast
    radius, cost impact, prioritized risk, and concrete fixes.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Severity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    info = "info"


# Higher number = more severe. Used for sorting and threshold comparisons.
SEVERITY_RANK: dict[str, int] = {
    Severity.critical.value: 4,
    Severity.high.value: 3,
    Severity.medium.value: 2,
    Severity.low.value: 1,
    Severity.info.value: 0,
}


def severity_rank(value: str | None) -> int:
    if value is None:
        return 0
    return SEVERITY_RANK.get(str(value).lower(), 0)


class Verdict(str, Enum):
    approve = "approve"
    comment = "comment"
    request_changes = "request_changes"


class StaticFinding(BaseModel):
    """A single failed check from an external IaC scanner, normalized."""

    check_id: str
    title: str
    severity: str | None = None
    file: str
    line: int | None = None
    resource: str | None = None
    guideline: str | None = None
    scanner: str = "unknown"


class Finding(BaseModel):
    """An AI-enriched finding: what tools report, plus the reasoning they can't."""

    title: str
    severity: Severity
    category: str = Field(description="e.g. security, cost, reliability, best-practice")
    file: str
    line: int | None = None
    explanation: str = Field(description="Plain-English why this matters for THIS change")
    blast_radius: str = Field(description="What could go wrong / who is affected")
    recommendation: str
    suggested_fix: str | None = Field(
        default=None, description="Corrected Terraform snippet, if applicable"
    )
    related_check_id: str | None = Field(
        default=None, description="The scanner check_id this maps to, if any"
    )
    guardrail: str | None = Field(
        default=None,
        description="The plain-English team guardrail this finding violates, if any",
    )
    # Set by the verification step, not the model: True if a re-scan confirmed the
    # fix resolves the related scanner check; False if it didn't; None if not checked.
    fix_verified: bool | None = None


class Review(BaseModel):
    """The full review the bot posts back."""

    summary: str
    risk_score: int = Field(description="Overall risk 0-100 (0 = safe, 100 = critical)")
    verdict: Verdict
    cost_impact: str = Field(description="Plain-English estimate of cost delta")
    findings: list[Finding] = Field(default_factory=list)

    def clamped_risk(self) -> int:
        return max(0, min(100, int(self.risk_score)))

    def max_severity(self) -> Severity | None:
        if not self.findings:
            return None
        return max(self.findings, key=lambda f: severity_rank(f.severity.value)).severity
