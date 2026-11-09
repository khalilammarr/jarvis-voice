from pathlib import Path

from jarvis.config import Config, LocalConfig
from jarvis.setup.doctor import Probes, format_checks, run_checks
from jarvis.shell import ShellPolicy


def _cfg(**kw):
    base = dict(api_key="", model=None, voice="Charon", wake_model="hey_jarvis", wake_threshold=0.5,
                idle_timeout_s=20, barge_in=False, google_search=True, input_device=None, output_device=None,
                shell=ShellPolicy(True, (), 60, 4000, Path("/tmp/x"), Path("/tmp")), backend="local",
                local=LocalConfig())
    return Config(**{**base, **kw})


def probes(**kw):
    base = dict(audio=lambda: (True, True), importable=lambda name: True, which=lambda name: "/usr/bin/" + name,
                llm_models=lambda url: ["llama3.2:3b"], voice_exists=lambda name: True)
    return Probes(**{**base, **kw})


def by_name(checks):
    return {c.name: c for c in checks}


def test_everything_fine_for_local():
    checks = run_checks(_cfg(), probes())
    assert all(c.status == "ok" for c in checks if c.name != "desktop tools")


def test_no_backend_chosen_points_to_setup():
    [check, *_] = run_checks(_cfg(backend=""), probes())
    assert check.status == "fail" and "jarvis setup" in check.fix


def test_unreachable_llm_server_says_how_to_start_it():
    checks = by_name(run_checks(_cfg(), probes(llm_models=lambda url: None)))
    assert checks["language model"].status == "fail" and "ollama serve" in checks["language model"].fix


def test_missing_model_says_how_to_pull_it():
    checks = by_name(run_checks(_cfg(), probes(llm_models=lambda url: ["other:1b"])))
    assert "ollama pull llama3.2:3b" in checks["language model"].fix


def test_missing_extra_and_voice_are_reported():
    checks = by_name(run_checks(_cfg(), probes(importable=lambda name: name != "faster_whisper",
                                              voice_exists=lambda name: False)))
    assert checks["speech models"].status == "fail" and "uv sync --extra local" in checks["speech models"].fix
    assert checks["voice"].status == "fail" and "jarvis setup" in checks["voice"].fix


def test_gemini_needs_a_key_and_warns_about_privacy():
    checks = by_name(run_checks(_cfg(backend="gemini"), probes()))
    assert checks["api key"].status == "fail" and "aistudio.google.com/apikey" in checks["api key"].fix
    assert checks["privacy"].status == "warn" and "Google" in checks["privacy"].detail
    assert by_name(run_checks(_cfg(backend="gemini", api_key="k"), probes()))["api key"].status == "ok"


def test_missing_audio_devices_fail():
    checks = by_name(run_checks(_cfg(), probes(audio=lambda: (True, False))))
    assert checks["audio"].status == "fail" and "speaker" in checks["audio"].detail


def test_optional_tools_only_warn():
    checks = by_name(run_checks(_cfg(), probes(which=lambda name: None)))
    assert checks["desktop tools"].status == "warn"


def test_format_uses_symbols_and_shows_fixes():
    lines = format_checks(run_checks(_cfg(backend=""), probes()))
    assert lines[0].startswith("✗") and "jarvis setup" in "\n".join(lines)
