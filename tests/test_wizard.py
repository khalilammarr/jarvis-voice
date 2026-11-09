"""`jarvis setup`: scripted answers in, config.toml + secrets.env out."""

import os
import stat

import pytest

from jarvis.config import load_config
from jarvis.setup.wizard import choose, run


def scripted(*answers):
    queue = list(answers)
    return lambda prompt: queue.pop(0)


def run_wizard(tmp_path, answers, secret="", **kw):
    said = []
    downloads = []
    run(config_dir=tmp_path, ask=scripted(*answers), say=said.append, secret=lambda prompt: secret,
        download_voice=lambda name: downloads.append(name) or True, check=lambda cfg: [],
        env_file=tmp_path / "none.env", **kw)
    return load_config(config_dir=tmp_path, env_file=tmp_path / "none.env", export=False), said, downloads


def test_choose_accepts_number_key_and_default():
    options = [("a", "Alpha"), ("b", "Beta")]
    say = lambda _: None
    assert choose(scripted("2"), say, "t", options, "a") == "b"
    assert choose(scripted("b"), say, "t", options, "a") == "b"
    assert choose(scripted(""), say, "t", options, "a") == "a"


def test_choose_reasks_on_garbage():
    said = []
    assert choose(scripted("9", "zzz", "1"), said.append, "t", [("a", "Alpha")], "a") == "a"
    assert sum("Please" in line for line in said) == 2


def test_local_path_writes_settings_and_downloads_the_voice(tmp_path):
    # backend, language, persona, stt size, llm model, wake word, briefing
    cfg, said, downloads = run_wizard(tmp_path, ["1", "fr", "friendly", "", "", "n", "n"])
    assert (cfg.backend, cfg.language, cfg.persona) == ("local", "fr", "friendly")
    assert cfg.wake_word is False and cfg.briefing is False
    assert cfg.local.llm_model == "llama3.2:3b" and cfg.local.stt_model == "small"
    assert downloads == ["fr_FR-siwis-medium"]
    assert not (tmp_path / "secrets.env").exists()


def test_gemini_path_stores_the_key_privately_and_warns(tmp_path):
    # backend, language, persona, voice, wake word, briefing
    cfg, said, _ = run_wizard(tmp_path, ["2", "en", "sidekick", "Kore", "y", "y"], secret="AIzaFAKEKEY")
    assert (cfg.backend, cfg.voice, cfg.wake_word) == ("gemini", "Kore", True)
    secrets = tmp_path / "secrets.env"
    assert "GEMINI_API_KEY=AIzaFAKEKEY" in secrets.read_text()
    assert stat.S_IMODE(os.stat(secrets).st_mode) == 0o600
    assert "AIzaFAKEKEY" not in (tmp_path / "config.toml").read_text()
    assert any("Google" in line and "non-sensitive" in line for line in said)


def test_gemini_rejects_unknown_voice_then_accepts(tmp_path):
    cfg, *_ = run_wizard(tmp_path, ["2", "en", "sidekick", "Nope", "Puck", "n", "n"], secret="k")
    assert cfg.voice == "Puck"


def test_gemini_without_a_key_is_allowed_but_explained(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    cfg, said, _ = run_wizard(tmp_path, ["2", "en", "sidekick", "", "n", "n"], secret="")
    assert cfg.backend == "gemini" and cfg.api_key == ""
    assert any("aistudio.google.com/apikey" in line for line in said)


def test_rerunning_keeps_previous_answers_as_defaults(tmp_path):
    run_wizard(tmp_path, ["1", "ar", "concise", "base", "qwen2.5:7b", "n", "n"])
    cfg, *_ = run_wizard(tmp_path, ["", "", "", "", "", "", ""])
    assert (cfg.backend, cfg.language, cfg.persona) == ("local", "ar", "concise")
    assert (cfg.local.stt_model, cfg.local.llm_model) == ("base", "qwen2.5:7b")


def test_failed_voice_download_is_reported_not_fatal(tmp_path):
    said = []
    run(config_dir=tmp_path, ask=scripted("1", "en", "sidekick", "", "", "n", "n"), say=said.append,
        secret=lambda p: "", download_voice=lambda name: False, check=lambda cfg: [],
        env_file=tmp_path / "none.env")
    assert any("download" in line.lower() and "jarvis setup" in line for line in said)
    assert (tmp_path / "config.toml").exists()
