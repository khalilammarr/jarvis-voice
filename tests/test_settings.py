"""config.toml + env layering, and the language/persona catalogue."""

from pathlib import Path

import pytest

from jarvis import config as cfgmod
from jarvis.config import ConfigError, load_config
from jarvis.languages import LANGUAGES, PERSONAS, language_name, local_voice_for


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in list(__import__("os").environ):
        if name.startswith("JARVIS_") or name in {"GEMINI_API_KEY", "GOOGLE_API_KEY"}:
            monkeypatch.delenv(name)


def _load(tmp_path: Path, toml: str | None = None, env: str | None = None):
    if toml is not None:
        (tmp_path / "config.toml").write_text(toml)
    if env is not None:
        (tmp_path / "secrets.env").write_text(env)
    return load_config(config_dir=tmp_path, env_file=tmp_path / "none.env", export=False)


def test_defaults_without_any_files(tmp_path):
    cfg = _load(tmp_path)
    assert cfg.backend == "" and cfg.language == "auto" and cfg.persona == "sidekick"
    assert cfg.local.llm_url.startswith("http://127.0.0.1")


def test_toml_values_are_read(tmp_path):
    cfg = _load(tmp_path, '[general]\nbackend = "local"\nlanguage = "fr"\npersona = "concise"\n'
                          '[gemini]\nvoice = "Kore"\n[local]\nllm_model = "llama3.2:3b"\n')
    assert (cfg.backend, cfg.language, cfg.persona, cfg.voice) == ("local", "fr", "concise", "Kore")
    assert cfg.local.llm_model == "llama3.2:3b"


def test_env_overrides_toml(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_LANGUAGE", "de")
    cfg = _load(tmp_path, '[general]\nlanguage = "fr"\n')
    assert cfg.language == "de"


def test_api_key_comes_from_secrets_env(tmp_path):
    cfg = _load(tmp_path, '[general]\nbackend = "gemini"\n', env="GEMINI_API_KEY=abc123\n")
    assert cfg.api_key == "abc123"


def test_legacy_key_without_backend_infers_gemini(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert _load(tmp_path).backend == "gemini"


def test_bad_toml_is_a_clear_error(tmp_path):
    with pytest.raises(ConfigError, match="config.toml"):
        _load(tmp_path, "this is [not toml")


@pytest.mark.parametrize("toml, message", [
    ('[general]\nbackend = "chatgpt"\n', "backend"),
    ('[general]\npersona = "pirate"\n', "persona"),
    ('[general]\nlanguage = "xx"\n', "language"),
])
def test_unknown_choices_are_rejected(tmp_path, toml, message):
    with pytest.raises(ConfigError, match=message):
        _load(tmp_path, toml)


def test_day_tracking_defaults_off_outside_hyprland(tmp_path, monkeypatch):
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)
    assert _load(tmp_path).day_tracking is False
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "abc")
    assert _load(tmp_path).day_tracking is True


def test_remote_llm_url_is_refused_unless_allowed(tmp_path):
    toml = '[general]\nbackend = "local"\n[local]\nllm_url = "http://example.com:11434/v1"\n'
    with pytest.raises(ConfigError, match="allow_remote_llm"):
        _load(tmp_path, toml)
    ok = _load(tmp_path, toml + "allow_remote_llm = true\n")
    assert ok.local.llm_url.startswith("http://example.com")


def test_loopback_hosts_are_accepted(tmp_path):
    for host in ("localhost", "127.0.0.1", "[::1]"):
        cfg = _load(tmp_path, f'[general]\nbackend = "local"\n[local]\nllm_url = "http://{host}:8080/v1"\n')
        assert host in cfg.local.llm_url


def test_save_settings_round_trips(tmp_path):
    path = tmp_path / "config.toml"
    cfgmod.save_settings(path, {"general": {"backend": "local", "language": "ar", "wake_word": False},
                                "local": {"llm_model": 'qwen "3"'}})
    cfg = _load(tmp_path)
    assert (cfg.backend, cfg.language, cfg.wake_word, cfg.local.llm_model) == ("local", "ar", False, 'qwen "3"')


def test_save_settings_merges_into_existing_file(tmp_path):
    path = tmp_path / "config.toml"
    cfgmod.save_settings(path, {"general": {"language": "fr"}})
    cfgmod.save_settings(path, {"general": {"persona": "friendly"}})
    cfg = _load(tmp_path)
    assert (cfg.language, cfg.persona) == ("fr", "friendly")


def test_catalogue_is_consistent():
    assert "auto" in LANGUAGES and "en" in LANGUAGES
    assert language_name("fr") == "French" and language_name("auto") == "the language the user speaks"
    assert local_voice_for("fr").startswith("fr_") and local_voice_for("auto") == local_voice_for("en")
    assert {"sidekick", "friendly", "concise"} <= set(PERSONAS)
