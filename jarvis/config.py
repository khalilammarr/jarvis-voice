"""Runtime configuration.

Precedence, highest first: process environment, ``~/.config/jarvis/secrets.env`` (API key,
mode 0600), the legacy project ``.env``, ``~/.config/jarvis/config.toml`` (written by
``jarvis setup``), then built-in defaults.
"""

from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values

from jarvis.fileio import write_atomic
from jarvis.languages import LANGUAGES, PERSONAS
from jarvis.shell import ShellPolicy

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "jarvis"

BACKENDS = ("gemini", "local")
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


class ConfigError(ValueError):
    """A setting is malformed or unsafe. The message says which file or variable to fix."""


def _xdg(var: str, fallback: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / fallback) / "jarvis"


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config")


def runtime_state_file() -> Path | None:
    """$XDG_RUNTIME_DIR/jarvis/state: 'listening' / 'talking' / 'stopped', read by the bar widget."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    return Path(runtime) / "jarvis" / "state" if runtime else None


MIC_RATE = 16_000     # Gemini Live input, openWakeWord and Whisper all expect 16 kHz mono int16
MIC_FRAME = 1_280     # 80 ms: openWakeWord's native frame size
SPEAKER_RATE = 24_000 # Gemini Live native-audio output rate; local TTS is resampled to match

# Locations the assistant may never name in a command. Extend with JARVIS_DENY_PATHS.
DEFAULT_DENY_PATHS = (
    "~/.claude", "~/.claude.json", "~/.ssh", "~/.gnupg", "~/.aws", "~/.azure", "~/.kube", "~/.docker",
    "~/.config/gcloud", "~/.config/gh", "~/.config/hub", "~/.netrc", "~/.git-credentials",
    "~/.password-store", "~/.local/share/keyrings", "~/.pypirc", "~/.npmrc",
    "~/.mozilla", "~/.zen", "~/.config/google-chrome", "~/.config/chromium",
    "~/.config/BraveSoftware", "~/.config/vivaldi",
    "/etc/shadow", "/etc/gshadow", "/etc/sudoers",
)

# Every setting that can live in config.toml, keyed by its environment variable.
_TOML_KEYS: dict[str, tuple[str, str]] = {
    "JARVIS_BACKEND": ("general", "backend"),
    "JARVIS_LANGUAGE": ("general", "language"),
    "JARVIS_PERSONA": ("general", "persona"),
    "JARVIS_WAKE_WORD": ("general", "wake_word"),
    "JARVIS_BRIEFING": ("general", "briefing"),
    "JARVIS_DAY_TRACKING": ("general", "day_tracking"),
    "JARVIS_INPUT_DEVICE": ("audio", "input_device"),
    "JARVIS_OUTPUT_DEVICE": ("audio", "output_device"),
    "JARVIS_BARGE_IN": ("audio", "barge_in"),
    "JARVIS_WAKE_THRESHOLD": ("audio", "wake_threshold"),
    "JARVIS_IDLE_TIMEOUT": ("audio", "idle_timeout"),
    "JARVIS_MODEL": ("gemini", "model"),
    "JARVIS_VOICE": ("gemini", "voice"),
    "JARVIS_GOOGLE_SEARCH": ("gemini", "google_search"),
    "JARVIS_LLM_URL": ("local", "llm_url"),
    "JARVIS_LLM_MODEL": ("local", "llm_model"),
    "JARVIS_STT_MODEL": ("local", "stt_model"),
    "JARVIS_STT_DEVICE": ("local", "stt_device"),
    "JARVIS_TTS_VOICE": ("local", "tts_voice"),
    "JARVIS_ALLOW_REMOTE_LLM": ("local", "allow_remote_llm"),
    "JARVIS_GUARD": ("safety", "guard"),
    "JARVIS_DENY_PATHS": ("safety", "deny_paths"),
    "JARVIS_SHELL_TIMEOUT": ("safety", "shell_timeout"),
    "JARVIS_MAX_OUTPUT": ("safety", "max_output"),
}
_FALSE = {"0", "false", "no", "off", ""}


class _Source:
    """Environment first, then config.toml, for one setting at a time."""

    def __init__(self, env: Mapping[str, str], toml: dict):
        self._env, self._toml = env, toml

    def raw(self, name: str):
        if name in self._env:
            return self._env[name]
        section, key = _TOML_KEYS[name]
        return self._toml.get(section, {}).get(key)

    def str(self, name: str, default: str = "") -> str:
        value = self.raw(name)
        return default if value is None or value == "" else str(value)

    def optional(self, name: str) -> str | None:
        return self.str(name) or None

    def bool(self, name: str, default: bool) -> bool:
        value = self.raw(name)
        if value is None:
            return default
        return value if isinstance(value, bool) else str(value).strip().lower() not in _FALSE

    def float(self, name: str, default: float) -> float:
        value = self.raw(name)
        return default if value in (None, "") else float(value)

    def list(self, name: str) -> tuple[str, ...]:
        value = self.raw(name)
        if isinstance(value, (list, tuple)):
            return tuple(str(v) for v in value)
        return tuple(p.strip() for p in (value or "").replace(",", ":").split(":") if p.strip())


@dataclass(frozen=True)
class LocalConfig:
    """The fully offline backend: Whisper (STT) -> an OpenAI-compatible LLM server -> Piper (TTS)."""

    llm_url: str = "http://127.0.0.1:11434/v1"  # Ollama's default; LM Studio / llama.cpp also work
    llm_model: str = "llama3.2:3b"
    stt_model: str = "small"
    stt_device: str = "auto"
    tts_voice: str = ""  # empty: the default Piper voice for the chosen language
    allow_remote_llm: bool = False


@dataclass(frozen=True)
class Config:
    api_key: str
    model: str | None
    voice: str
    wake_model: str
    wake_threshold: float
    idle_timeout_s: float
    barge_in: bool
    google_search: bool
    input_device: str | None
    output_device: str | None
    shell: ShellPolicy
    briefing: bool = True
    wake_word: bool = True
    day_tracking: bool = True
    profile_path: Path = _xdg("XDG_CONFIG_HOME", ".config") / "me.md"
    memory_path: Path = _xdg("XDG_DATA_HOME", ".local/share") / "memory.md"
    activity_dir: Path = STATE_DIR / "activity"
    backend: str = "gemini"
    language: str = "auto"
    persona: str = "sidekick"
    local: LocalConfig = field(default_factory=LocalConfig)


def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError:
        return {}
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise ConfigError(f"Cannot read {path}: {exc}. Fix it or re-run `jarvis setup`.") from exc


def _choice(value: str, allowed, label: str, where: str) -> str:
    if value not in allowed:
        raise ConfigError(f"Unknown {label} {value!r} ({where}). Choose one of: {', '.join(sorted(allowed))}.")
    return value


def _check_llm_url(url: str, allow_remote: bool) -> None:
    host = urlparse(url).hostname or ""
    if host not in LOOPBACK_HOSTS and not allow_remote:
        raise ConfigError(
            f"The local backend's llm_url points at {host or url!r}, which is not this machine, so your "
            "voice transcripts would leave it. Use localhost, or set allow_remote_llm = true "
            "(JARVIS_ALLOW_REMOTE_LLM=1) if you really mean it.")


def load_config(env_file: Path | None = None, config_dir: Path | None = None, export: bool = True) -> Config:
    """Read settings. ``export`` also puts dotenv values in os.environ (tools read a few directly)."""
    directory = config_dir or _xdg("XDG_CONFIG_HOME", ".config")
    env = {**dotenv_values(env_file or PROJECT_ROOT / ".env"), **dotenv_values(directory / "secrets.env"),
           **os.environ}
    env = {k: v for k, v in env.items() if v is not None}
    if export:
        for key, value in env.items():
            os.environ.setdefault(key, value)
    toml_path = directory / "config.toml"
    src = _Source(env, _read_toml(toml_path))

    api_key = env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY") or ""
    backend = src.str("JARVIS_BACKEND")
    if not backend and api_key:
        backend = "gemini"  # configured before `jarvis setup` existed: keep working
    if backend:
        _choice(backend, BACKENDS, "backend", toml_path.name)
    language = _choice(src.str("JARVIS_LANGUAGE", "auto"), LANGUAGES, "language", toml_path.name)
    persona = _choice(src.str("JARVIS_PERSONA", "sidekick"), PERSONAS, "persona", toml_path.name)

    local = LocalConfig(
        llm_url=src.str("JARVIS_LLM_URL", LocalConfig.llm_url),
        llm_model=src.str("JARVIS_LLM_MODEL", LocalConfig.llm_model),
        stt_model=src.str("JARVIS_STT_MODEL", LocalConfig.stt_model),
        stt_device=src.str("JARVIS_STT_DEVICE", LocalConfig.stt_device),
        tts_voice=src.str("JARVIS_TTS_VOICE"),
        allow_remote_llm=src.bool("JARVIS_ALLOW_REMOTE_LLM", False),
    )
    if backend == "local":
        _check_llm_url(local.llm_url, local.allow_remote_llm)

    return Config(
        api_key=api_key,
        model=src.optional("JARVIS_MODEL"),
        voice=src.str("JARVIS_VOICE", "Charon"),
        wake_model=env.get("JARVIS_WAKE_MODEL", "hey_jarvis"),
        wake_threshold=src.float("JARVIS_WAKE_THRESHOLD", 0.5),
        idle_timeout_s=src.float("JARVIS_IDLE_TIMEOUT", 20.0),
        barge_in=src.bool("JARVIS_BARGE_IN", False),
        google_search=src.bool("JARVIS_GOOGLE_SEARCH", True),
        input_device=src.optional("JARVIS_INPUT_DEVICE"),
        output_device=src.optional("JARVIS_OUTPUT_DEVICE"),
        shell=ShellPolicy(
            guard=src.bool("JARVIS_GUARD", True),
            deny_paths=DEFAULT_DENY_PATHS + src.list("JARVIS_DENY_PATHS"),
            timeout_s=src.float("JARVIS_SHELL_TIMEOUT", 60.0),
            max_output_chars=int(src.float("JARVIS_MAX_OUTPUT", 4_000)),
            log_path=STATE_DIR / "commands.log",
            home=Path.home(),
        ),
        briefing=src.bool("JARVIS_BRIEFING", True),
        wake_word=src.bool("JARVIS_WAKE_WORD", False),
        day_tracking=src.bool("JARVIS_DAY_TRACKING", bool(env.get("HYPRLAND_INSTANCE_SIGNATURE"))),
        profile_path=directory / "me.md",
        memory_path=_xdg("XDG_DATA_HOME", ".local/share") / "memory.md",
        activity_dir=STATE_DIR / "activity",
        backend=backend,
        language=language,
        persona=persona,
        local=local,
    )


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    return json.dumps(str(value))  # JSON string escapes are valid TOML basic-string escapes


def save_settings(path: Path, updates: Mapping[str, Mapping[str, object]]) -> None:
    """Merge ``{section: {key: value}}`` into config.toml, keeping everything already there."""
    merged = {section: dict(values) for section, values in _read_toml(path).items()}
    for section, values in updates.items():
        merged.setdefault(section, {}).update(values)
    lines = ["# Written by `jarvis setup`. Edit freely; environment variables (JARVIS_*) override it.", ""]
    for section, values in merged.items():
        lines.append(f"[{section}]")
        lines.extend(f"{key} = {_toml_value(value)}" for key, value in values.items())
        lines.append("")
    write_atomic(path, "\n".join(lines), mode=0o644)
