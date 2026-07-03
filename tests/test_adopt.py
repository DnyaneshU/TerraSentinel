import json

import anthropic

from terrasentinel.adopt import AdoptResult, check_import_consistency, generate_adoption
from terrasentinel.config import Settings


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


MAIN_TF = 'resource "aws_s3_bucket" "data" {\n  bucket = "my-company-customer-data"\n}\n'
IMPORTS_TF = 'import {\n  to = aws_s3_bucket.data\n  id = "my-company-customer-data"\n}\n'


def test_generate_adoption_parses(monkeypatch):
    payload = json.dumps({"main_tf": MAIN_TF, "imports_tf": IMPORTS_TF, "notes": "review me"})
    monkeypatch.setattr(anthropic, "Anthropic", lambda **_k: _Client([_Resp(payload)]))
    settings = Settings(api_key="sk-test", model="claude-opus-4-8", max_tokens=4000)
    result = generate_adoption(settings, [{"type": "aws_s3_bucket", "id": "x"}])
    assert "aws_s3_bucket" in result.main_tf
    assert "import {" in result.imports_tf
    assert result.notes == "review me"


def test_consistency_all_matched():
    result = AdoptResult(main_tf=MAIN_TF, imports_tf=IMPORTS_TF)
    matched, unmatched = check_import_consistency(result)
    assert matched == ["aws_s3_bucket.data"]
    assert unmatched == []


def test_consistency_detects_orphan_import():
    # import points at a resource that was NOT generated -> flagged
    imports = 'import {\n  to = aws_s3_bucket.missing\n  id = "x"\n}\n'
    result = AdoptResult(main_tf=MAIN_TF, imports_tf=imports)
    matched, unmatched = check_import_consistency(result)
    assert matched == []
    assert unmatched == ["aws_s3_bucket.missing"]
