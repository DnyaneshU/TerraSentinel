import json

from terrasentinel.github import _pr_number_from_env


def test_pr_number_explicit(monkeypatch):
    monkeypatch.setenv("GITHUB_PR_NUMBER", "123")
    assert _pr_number_from_env() == 123


def test_pr_number_from_event_file(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_PR_NUMBER", raising=False)
    monkeypatch.delenv("GITHUB_REF", raising=False)
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 7}}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    assert _pr_number_from_env() == 7


def test_pr_number_from_ref(monkeypatch):
    monkeypatch.delenv("GITHUB_PR_NUMBER", raising=False)
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    monkeypatch.setenv("GITHUB_REF", "refs/pull/55/merge")
    assert _pr_number_from_env() == 55


def test_pr_number_none(monkeypatch):
    for var in ("GITHUB_PR_NUMBER", "GITHUB_EVENT_PATH", "GITHUB_REF"):
        monkeypatch.delenv(var, raising=False)
    assert _pr_number_from_env() is None
