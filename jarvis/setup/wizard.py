"""`jarvis setup`: a short guided questionnaire that writes config.toml (and the API key, privately)."""

from __future__ import annotations

import getpass
import importlib.util
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from jarvis.config import Config, config_dir as default_config_dir, load_config, save_settings
from jarvis.fileio import write_atomic
from jarvis.languages import LANGUAGES, PERSONAS, local_voice_for

Ask = Callable[[str], str]
Say = Callable[[str], None]

BACKEND_OPTIONS = [
    ("local", "Local (recommended). Whisper + a language model on this machine (Ollama) + Piper voice.\n"
              "      Nothing you say ever leaves your computer. Needs Ollama and a ~2 GB model download."),
    ("gemini", "Gemini API. Quick start with a free Google key, but your microphone audio and everything\n"
               "      Jarvis can read go to Google. For testing and non-sensitive conversations only."),
]
STT_SIZES = ["tiny", "base", "small", "medium"]
GEMINI_WARNING = ("Heads up: with the Gemini backend your voice and any command output, memories and\n"
                  "profile text are sent to Google (free-tier keys may be used for training).\n"
                  "Use it for testing and non-sensitive conversations only.")


def choose(ask: Ask, say: Say, title: str, options: list[tuple[str, str]], default: str) -> str:
    """Numbered menu. Accepts the number, the key, a unique key prefix, or Enter for the default."""
    say(f"\n{title}")
    for number, (key, label) in enumerate(options, 1):
        say(f"  {number}) {label}" + ("   [default]" if key == default else ""))
    while True:
        answer = ask(f"Choose [{default}]: ").strip().lower()
        if not answer:
            return default
        keys = [key for key, _ in options]
        if answer.isdigit() and 1 <= int(answer) <= len(keys):
            return keys[int(answer) - 1]
        matches = [k for k in keys if k.lower() == answer] or [k for k in keys if k.lower().startswith(answer)]
        if len(matches) == 1:
            return matches[0]
        say(f"Please enter a number from 1 to {len(keys)}, or one of: {', '.join(keys)}.")


def yes_no(ask: Ask, question: str, default: bool) -> bool:
    answer = ask(f"{question} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    return default if not answer else answer.startswith("y")


def text(ask: Ask, question: str, default: str) -> str:
    return ask(f"{question} [{default}]: ").strip() or default


def download_piper_voice(name: str) -> bool:
    """Fetch a Piper voice into Jarvis's data dir (one-time download from the Piper voice index)."""
    from jarvis.backends.local.tts import voices_dir

    target = voices_dir()
    target.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([sys.executable, "-m", "piper.download_voices", "--download-dir", str(target), name])
    return result.returncode == 0


def _gemini_voice(ask: Ask, say: Say, current: str) -> str:
    from jarvis.voices import parse_voices

    while True:
        entered = text(ask, "Gemini voice (hear them all later with `jarvis --voices`)", current)
        try:
            return parse_voices(entered)[0]
        except ValueError as exc:
            say(str(exc))


def run(config_dir: Path | None = None, ask: Ask = input, say: Say = print, secret: Ask = getpass.getpass,
        download_voice: Callable[[str], bool] = download_piper_voice,
        check: Callable[[Config], list] | None = None, env_file: Path | None = None) -> Config:
    directory = config_dir or default_config_dir()
    before = load_config(config_dir=directory, env_file=env_file, export=False)
    say("Jarvis setup. Press Enter to accept the [default] in brackets. Re-run any time.")

    backend = choose(ask, say, "How should Jarvis think?", BACKEND_OPTIONS, before.backend or "local")
    languages = [(code, f"{lang.name} ({code})" if code != "auto" else "Auto: reply in whatever I speak")
                 for code, lang in LANGUAGES.items()]
    language = choose(ask, say, "Which language should Jarvis speak?", languages, before.language)
    persona = choose(ask, say, "Personality?", [(k, f"{k}: {v}") for k, v in PERSONAS.items()], before.persona)

    updates: dict[str, dict] = {"general": {"backend": backend, "language": language, "persona": persona}}
    voice_to_download = None
    if backend == "gemini":
        say("\n" + GEMINI_WARNING)
        key = secret("Gemini API key (hidden; Enter to skip): ").strip()
        if key:
            write_atomic(directory / "secrets.env", f"GEMINI_API_KEY={key}\n", mode=0o600)
        elif not before.api_key:
            say("No key yet. Get a free one at https://aistudio.google.com/apikey and re-run `jarvis setup`.")
        updates["gemini"] = {"voice": _gemini_voice(ask, say, before.voice)}
    else:
        stt = choose(ask, say, "Speech recognition size (bigger = more accurate, slower)?",
                     [(s, s) for s in STT_SIZES], before.local.stt_model)
        llm = text(ask, "Ollama model to chat with (must support tools)", before.local.llm_model)
        updates["local"] = {"stt_model": stt, "llm_model": llm}
        voice_to_download = before.local.tts_voice or local_voice_for(language)

    updates["general"]["wake_word"] = yes_no(ask, "\nWait for the wake word 'hey jarvis' (otherwise always listening)?",
                                            before.wake_word)
    if updates["general"]["wake_word"] and importlib.util.find_spec("openwakeword") is None:
        say("The wake word needs one more package: uv sync --extra wakeword")
    updates["general"]["briefing"] = yes_no(ask, "Give a short briefing the first time you talk each day?",
                                            before.briefing)
    save_settings(directory / "config.toml", updates)
    say(f"\nSaved {directory / 'config.toml'}")

    if voice_to_download and not download_voice(voice_to_download):
        say(f"Could not download the voice {voice_to_download!r}. Check your connection and re-run `jarvis setup`.")

    cfg = load_config(config_dir=directory, env_file=env_file, export=False)
    for line in (check(cfg) if check else []):
        say(str(line))
    say("\nAll set. Start it with `jarvis`. Check your install any time with `jarvis doctor`.")
    return cfg
