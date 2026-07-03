from terrasentinel.cli import _resolve_frameworks
from terrasentinel.config import DEFAULT_MODEL, load_settings


def _write_cfg(tmp_path, text):
    cfg = tmp_path / ".terrasentinel.yml"
    cfg.write_text(text, encoding="utf-8")
    return cfg


def test_config_file_loaded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TERRASENTINEL_MODEL", raising=False)
    _write_cfg(
        tmp_path,
        "model: claude-sonnet-4-6\n"
        "fail_on: medium\n"
        "frameworks: [terraform, kubernetes]\n"
        "ignore: [CKV_AWS_18]\n",
    )
    s = load_settings(target=tmp_path)
    assert s.model == "claude-sonnet-4-6"
    assert s.fail_on == "medium"
    assert s.frameworks == ["terraform", "kubernetes"]
    assert s.ignore == ["CKV_AWS_18"]
    assert s.config_path.endswith(".terrasentinel.yml")


def test_model_override_beats_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_cfg(tmp_path, "model: claude-sonnet-4-6\n")
    s = load_settings(target=tmp_path, model_override="claude-haiku-4-5")
    assert s.model == "claude-haiku-4-5"


def test_env_beats_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TERRASENTINEL_MODEL", "claude-haiku-4-5")
    _write_cfg(tmp_path, "model: claude-sonnet-4-6\n")
    s = load_settings(target=tmp_path)
    assert s.model == "claude-haiku-4-5"


def test_defaults_when_no_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TERRASENTINEL_MODEL", raising=False)
    s = load_settings(target=tmp_path)
    assert s.model == DEFAULT_MODEL
    assert s.frameworks == ["terraform"]
    assert s.ignore == []
    assert s.fail_on == "high"
    assert s.config_path is None


def test_resolve_frameworks():
    assert _resolve_frameworks(None) is None
    assert _resolve_frameworks(["terraform"]) == ["terraform"]
    assert _resolve_frameworks(["terraform,kubernetes"]) == ["terraform", "kubernetes"]
    assert _resolve_frameworks(["terraform", "kubernetes,cloudformation"]) == [
        "terraform",
        "kubernetes",
        "cloudformation",
    ]
