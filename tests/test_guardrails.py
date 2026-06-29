from terrasentinel.collect import load_guardrails
from terrasentinel.reviewer import _build_prompt


def test_load_guardrails_explicit(tmp_path):
    g = tmp_path / "rules.md"
    g.write_text("No public databases.")
    path, text = load_guardrails(tmp_path, str(g))
    assert text == "No public databases."
    assert path.endswith("rules.md")


def test_load_guardrails_autodetect(tmp_path):
    (tmp_path / "guardrails.md").write_text("No wildcards in IAM policies.")
    path, text = load_guardrails(tmp_path, None)
    assert "No wildcards" in text
    assert path.endswith("guardrails.md")


def test_load_guardrails_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # ensure no guardrails.md in cwd either
    path, text = load_guardrails(tmp_path, None)
    assert path is None
    assert text is None


def test_build_prompt_includes_guardrails():
    prompt = _build_prompt(None, {"main.tf": "x"}, [], guardrails="No public DBs allowed.")
    assert "Team guardrails" in prompt
    assert "No public DBs allowed." in prompt


def test_build_prompt_without_guardrails_omits_section():
    prompt = _build_prompt(None, {"main.tf": "x"}, [], guardrails=None)
    assert "Team guardrails" not in prompt
