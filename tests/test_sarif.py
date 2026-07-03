from terrasentinel.models import Finding, Review, Severity, StaticFinding, Verdict
from terrasentinel.sarif import _level, _slug, review_to_sarif, static_findings_to_sarif


def test_static_findings_to_sarif_structure():
    findings = [
        StaticFinding(
            check_id="CKV_AWS_24",
            title="SSH open",
            severity="high",
            file="main.tf",
            line=29,
            guideline="https://example.com/ckv24",
        )
    ]
    doc = static_findings_to_sarif(findings)
    assert doc["version"] == "2.1.0"
    driver = doc["runs"][0]["tool"]["driver"]
    assert driver["name"] == "TerraSentinel"
    assert driver["rules"][0]["id"] == "CKV_AWS_24"
    assert driver["rules"][0]["helpUri"] == "https://example.com/ckv24"

    res = doc["runs"][0]["results"][0]
    assert res["ruleId"] == "CKV_AWS_24"
    assert res["level"] == "error"
    loc = res["locations"][0]["physicalLocation"]
    assert loc["artifactLocation"]["uri"] == "main.tf"
    assert loc["region"]["startLine"] == 29


def test_static_sarif_defaults_line_to_1_and_warning():
    findings = [StaticFinding(check_id="X", title="t", severity=None, file="main.tf", line=None)]
    res = static_findings_to_sarif(findings)["runs"][0]["results"][0]
    assert res["level"] == "warning"  # unrated -> warning
    assert res["locations"][0]["physicalLocation"]["region"]["startLine"] == 1


def test_review_to_sarif_message_and_level():
    review = Review(
        summary="s",
        risk_score=80,
        verdict=Verdict.request_changes,
        cost_impact="n",
        findings=[
            Finding(
                title="SSH open",
                severity=Severity.high,
                category="security",
                file="main.tf",
                line=3,
                explanation="anyone can reach port 22",
                blast_radius="b",
                recommendation="restrict the CIDR",
                related_check_id="CKV_AWS_24",
            )
        ],
    )
    res = review_to_sarif(review)["runs"][0]["results"][0]
    assert res["ruleId"] == "CKV_AWS_24"
    assert res["level"] == "error"
    assert "anyone can reach port 22" in res["message"]["text"]
    assert "restrict the CIDR" in res["message"]["text"]


def test_level_and_slug():
    assert _level("critical") == "error"
    assert _level("high") == "error"
    assert _level("medium") == "warning"
    assert _level("low") == "note"
    assert _level(None) == "warning"
    assert _slug("SSH open to 0.0.0.0/0!") == "SSH_open_to_0_0_0_0_0"
