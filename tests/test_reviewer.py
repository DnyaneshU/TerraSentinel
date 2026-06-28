import json

import anthropic
import pytest

from terrasentinel.config import Settings
from terrasentinel.reviewer import ReviewError, _extract_json, review

VALID = json.dumps(
    {
        "summary": "Opens SSH to the world.",
        "risk_score": 78,
        "verdict": "request_changes",
        "cost_impact": "negligible",
        "findings": [
            {
                "title": "SSH open to 0.0.0.0/0",
                "severity": "high",
                "category": "security",
                "file": "main.tf",
                "line": 3,
                "explanation": "Anyone can reach port 22.",
                "blast_radius": "Full host compromise.",
                "recommendation": "Restrict the CIDR.",
                "suggested_fix": 'cidr_blocks = ["10.0.0.0/8"]',
                "related_check_id": "CKV_AWS_24",
            }
        ],
    }
)


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


def _settings():
    return Settings(api_key="sk-test", model="claude-opus-4-8", max_tokens=8000)


def _patch(monkeypatch, responses):
    monkeypatch.setattr(anthropic, "Anthropic", lambda **_kw: _Client(responses))


def test_review_happy_path(monkeypatch):
    _patch(monkeypatch, [_Resp(VALID)])
    result = review(_settings(), diff_text=None, file_contents={"main.tf": "x"}, static_findings=[])
    assert result.clamped_risk() == 78
    assert result.verdict.value == "request_changes"
    assert result.findings[0].severity.value == "high"


def test_review_strips_markdown_fences(monkeypatch):
    _patch(monkeypatch, [_Resp(f"```json\n{VALID}\n```")])
    result = review(_settings(), diff_text=None, file_contents={}, static_findings=[])
    assert result.findings[0].related_check_id == "CKV_AWS_24"


def test_review_retries_on_bad_json(monkeypatch):
    _patch(monkeypatch, [_Resp("sorry, here you go:"), _Resp(VALID)])
    result = review(_settings(), diff_text=None, file_contents={}, static_findings=[])
    assert result.clamped_risk() == 78


def test_review_refusal_raises(monkeypatch):
    _patch(monkeypatch, [_Resp("", stop_reason="refusal")])
    with pytest.raises(ReviewError, match="refus"):
        review(_settings(), diff_text=None, file_contents={}, static_findings=[])


def test_review_requires_api_key():
    settings = Settings(api_key=None, model="m", max_tokens=10)
    with pytest.raises(ReviewError, match="ANTHROPIC_API_KEY"):
        review(settings, diff_text=None, file_contents={}, static_findings=[])


def test_extract_json_variants():
    assert _extract_json('{"a": 1}') == '{"a": 1}'
    assert _extract_json('prefix {"a": 1} suffix') == '{"a": 1}'
    assert _extract_json('```\n{"a": 1}\n```') == '{"a": 1}'
    with pytest.raises(ValueError):
        _extract_json("no json at all")
