from terrasentinel.models import Finding, Review, Severity, StaticFinding, Verdict
from terrasentinel.render import (
    COMMENT_MARKER,
    review_to_markdown,
    static_findings_to_markdown,
)


def test_review_markdown_contains_marker_and_fields():
    review = Review(
        summary="Summary here",
        risk_score=80,
        verdict=Verdict.request_changes,
        cost_impact="adds ~$3,000/month",
        findings=[
            Finding(
                title="Oversized RDS instance",
                severity=Severity.high,
                category="cost",
                file="main.tf",
                line=42,
                explanation="db.m5.4xlarge is large.",
                blast_radius="High monthly bill.",
                recommendation="Downsize.",
                suggested_fix='instance_class = "db.t3.medium"',
            )
        ],
    )
    md = review_to_markdown(review, "checkov")
    assert COMMENT_MARKER in md
    assert "Oversized RDS instance" in md
    assert "adds ~$3,000/month" in md
    assert "instance_class" in md
    assert "main.tf:42" in md


def test_review_markdown_shows_guardrail_and_verified_fix():
    from types import SimpleNamespace

    review = Review(
        summary="Public DB exposed.",
        risk_score=90,
        verdict=Verdict.request_changes,
        cost_impact="negligible",
        findings=[
            Finding(
                title="RDS publicly accessible",
                severity=Severity.high,
                category="policy",
                file="main.tf",
                line=50,
                explanation="DB reachable from the internet.",
                blast_radius="Data exposure.",
                recommendation="Set publicly_accessible = false.",
                suggested_fix="publicly_accessible = false",
                related_check_id="CKV_AWS_17",
                guardrail="Databases must never be publicly accessible.",
                fix_verified=True,
            )
        ],
    )
    verification = SimpleNamespace(resolved_count=3, before=5, introduced_count=0)
    md = review_to_markdown(review, "checkov", verification)
    assert "Verified fixes" in md
    assert "resolved 3/5" in md
    assert "Violates guardrail" in md
    assert "verified" in md.lower()
    assert "📜" in md  # guardrail marker on the finding title


def test_static_markdown_handles_empty():
    md = static_findings_to_markdown([], "checkov")
    assert COMMENT_MARKER in md
    assert "No static-analysis findings" in md


def test_static_markdown_escapes_pipes():
    findings = [StaticFinding(check_id="C", title="a | b", severity="high", file="main.tf")]
    md = static_findings_to_markdown(findings, "checkov")
    assert "a \\| b" in md
