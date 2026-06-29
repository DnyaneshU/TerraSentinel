import json
from pathlib import Path

import anthropic
import pytest

from terrasentinel.config import Settings
from terrasentinel.models import StaticFinding
from terrasentinel.remediate import propose_fixes, verify_fixes
from terrasentinel.scanner import detect_scanner, run_scanner

REPO = Path(__file__).resolve().parent.parent
INSECURE = REPO / "examples" / "insecure" / "main.tf"
SECURE = REPO / "examples" / "secure" / "main.tf"


# --------------------------------------------------------------------------- #
# propose_fixes — mocked Claude
# --------------------------------------------------------------------------- #
class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Resp:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [_Block(text)]
        self.stop_reason = stop_reason


class _Messages:
    def __init__(self, responses):
        self._responses = list(responses)

    def create(self, **_kwargs):
        return self._responses.pop(0)


class _Client:
    def __init__(self, responses):
        self.messages = _Messages(responses)


def test_propose_fixes_parses(monkeypatch):
    payload = json.dumps({"files": [{"path": "main.tf", "content": "# fixed\n"}]})
    monkeypatch.setattr(anthropic, "Anthropic", lambda **_k: _Client([_Resp(payload)]))
    settings = Settings(api_key="sk-test", model="claude-opus-4-8", max_tokens=2000)
    finding = StaticFinding(check_id="CKV_AWS_24", title="SSH open", file="main.tf")
    files = propose_fixes(settings, {"main.tf": "resource ..."}, [finding])
    assert files == {"main.tf": "# fixed\n"}


# --------------------------------------------------------------------------- #
# verify_fixes — REAL checkov re-scan (insecure -> hardened secure content)
# This is the end-to-end proof of the "verified, not guessed" mechanic, offline.
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(detect_scanner() is None, reason="no scanner (checkov/tfsec) available")
def test_verify_fixes_resolves_real_findings():
    name, findings = run_scanner(INSECURE)
    assert findings, "expected checkov to flag the insecure example"

    file_contents = {"main.tf": INSECURE.read_text(encoding="utf-8")}
    corrected = {"main.tf": SECURE.read_text(encoding="utf-8")}

    vr = verify_fixes(findings, file_contents, corrected, name)

    # The hardened version restricts SSH, so CKV_AWS_24 must be resolved by re-scan.
    assert ("CKV_AWS_24", "main.tf") in vr.resolved_keys
    assert vr.resolved_count > 0
    assert vr.resolves("CKV_AWS_24", "main.tf") is True
    assert vr.resolves("CKV_AWS_24", "other.tf") is False
    assert vr.resolves(None, "main.tf") is False
