"""`jarvis doctor`: check the install end to end and say exactly how to fix what's wrong."""

from __future__ import annotations

import importlib.util
import json
import shutil
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from jarvis.config import Config
from jarvis.languages import local_voice_for

_SYMBOL = {"ok": "✓", "warn": "!", "fail": "✗"}


@dataclass(frozen=True)
class Check:
    status: str  # ok | warn | fail
    name: str
    detail: str = ""
    fix: str = ""


@dataclass(frozen=True)
class Probes:
    """Everything that touches the outside world, injectable for tests."""

    audio: Callable[[], tuple[bool, bool]]            # (has input, has output)
    importable: Callable[[str], bool]
    which: Callable[[str], str | None]
    llm_models: Callable[[str], list[str] | None]     # None = server unreachable
    voice_exists: Callable[[str], bool]


def _audio() -> tuple[bool, bool]:
    try:
        import sounddevice as sd

        devices = sd.query_devices()
        return (any(d["max_input_channels"] > 0 for d in devices),
                any(d["max_output_channels"] > 0 for d in devices))
    except Exception:
        return False, False


def _llm_models(base_url: str) -> list[str] | None:
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/models", timeout=2) as response:  # noqa: S310
            return [m["id"] for m in json.load(response).get("data", [])]
    except Exception:
        return None


def _voice_exists(name: str) -> bool:
    from jarvis.backends.local.tts import voice_path

    return voice_path(name).exists()


def real_probes() -> Probes:
    return Probes(audio=_audio, importable=lambda n: importlib.util.find_spec(n) is not None,
                  which=shutil.which, llm_models=_llm_models, voice_exists=_voice_exists)


def _audio_check(p: Probes) -> Check:
    has_in, has_out = p.audio()
    missing = [n for n, ok in (("microphone", has_in), ("speaker", has_out)) if not ok]
    if missing:
        return Check("fail", "audio", f"no {' or '.join(missing)} found",
                     "Check PipeWire/PulseAudio is running; list devices with `jarvis --devices`.")
    return Check("ok", "audio", "microphone and speaker found")


def _local_checks(cfg: Config, p: Probes) -> list[Check]:
    missing = [m for m in ("faster_whisper", "openai", "piper") if not p.importable(m)]
    checks = [Check("fail", "speech models", f"missing: {', '.join(missing)}", "uv sync --extra local")
              if missing else Check("ok", "speech models", "faster-whisper, openai and piper are installed")]
    models = p.llm_models(cfg.local.llm_url)
    wanted = cfg.local.llm_model
    if models is None:
        checks.append(Check("fail", "language model", f"nothing is answering at {cfg.local.llm_url}",
                            "Install Ollama (https://ollama.com) and start it: `ollama serve`"))
    elif not any(m == wanted or m.split(":")[0] == wanted for m in models):
        checks.append(Check("fail", "language model", f"{wanted} is not on the server", f"ollama pull {wanted}"))
    else:
        checks.append(Check("ok", "language model", f"{wanted} is ready at {cfg.local.llm_url}"))
    voice = cfg.local.tts_voice or local_voice_for(cfg.language)
    checks.append(Check("ok", "voice", voice) if p.voice_exists(voice)
                  else Check("fail", "voice", f"{voice} is not downloaded", "Run `jarvis setup` to download it."))
    return checks


def _gemini_checks(cfg: Config, p: Probes) -> list[Check]:
    return [
        Check("ok", "api key", "found") if cfg.api_key else
        Check("fail", "api key", "no GEMINI_API_KEY", "Get a free key at https://aistudio.google.com/apikey, "
                                                    "then run `jarvis setup`."),
        Check("ok", "gemini sdk", "google-genai is installed") if p.importable("google") else
        Check("fail", "gemini sdk", "google-genai is missing", "uv sync --extra gemini"),
        Check("warn", "privacy", "Gemini mode sends your voice and anything Jarvis reads to Google. "
                                 "Testing and non-sensitive use only.",
              "For private use, switch with `jarvis setup` and pick Local."),
    ]


def run_checks(cfg: Config, probes: Probes | None = None) -> list[Check]:
    p = probes or real_probes()
    if not cfg.backend:
        return [Check("fail", "backend", "no backend chosen yet", "Run `jarvis setup`.")]
    checks = [Check("ok", "backend", cfg.backend), _audio_check(p)]
    checks += _local_checks(cfg, p) if cfg.backend == "local" else _gemini_checks(cfg, p)
    optional = [t for t in ("notify-send", "playerctl", "mpv") if not p.which(t)]
    if optional:
        checks.append(Check("warn", "desktop tools", f"optional, not found: {', '.join(optional)}",
                            "Install them with your package manager for notifications and media control."))
    return checks


def format_checks(checks: list[Check]) -> list[str]:
    lines = []
    for c in checks:
        lines.append(f"{_SYMBOL[c.status]} {c.name}: {c.detail}".rstrip(": "))
        if c.fix and c.status != "ok":
            lines.append(f"    fix: {c.fix}")
    return lines


def run(cfg: Config) -> int:
    checks = run_checks(cfg)
    print("\n".join(format_checks(checks)))
    return 1 if any(c.status == "fail" for c in checks) else 0
