from terrasentinel.cli import EXIT_GATE, EXIT_OK, _gate_review, _gate_static
from terrasentinel.models import Finding, Review, Severity, StaticFinding, Verdict


def _sf(sev):
    return StaticFinding(check_id="X", title="t", severity=sev, file="main.tf")


def test_gate_static_none_disables():
    assert _gate_static([_sf("critical")], "none") == EXIT_OK


def test_gate_static_threshold():
    assert _gate_static([_sf("high")], "high") == EXIT_GATE
    assert _gate_static([_sf("low")], "high") == EXIT_OK
    assert _gate_static([], "high") == EXIT_OK


def test_gate_static_unrated_treated_as_high():
    # checkov CE omits severity; an unrated finding counts as 'high'.
    assert _gate_static([_sf(None)], "high") == EXIT_GATE
    assert _gate_static([_sf(None)], "medium") == EXIT_GATE
    assert _gate_static([_sf(None)], "critical") == EXIT_OK


def _review(verdict, sev=None):
    findings = []
    if sev:
        findings = [
            Finding(
                title="t",
                severity=Severity(sev),
                category="security",
                file="main.tf",
                explanation="e",
                blast_radius="b",
                recommendation="r",
            )
        ]
    return Review(
        summary="s", risk_score=10, verdict=Verdict(verdict), cost_impact="n", findings=findings
    )


def test_gate_review_request_changes_always_gates():
    assert _gate_review(_review("request_changes"), "none") == EXIT_GATE


def test_gate_review_severity_threshold():
    assert _gate_review(_review("comment", "high"), "high") == EXIT_GATE
    assert _gate_review(_review("comment", "low"), "high") == EXIT_OK
    assert _gate_review(_review("approve"), "critical") == EXIT_OK
