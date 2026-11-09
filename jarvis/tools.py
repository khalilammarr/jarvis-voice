"""Tools exposed to Gemini, and the code that runs them."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
import shlex
import shutil
import signal
import subprocess

import urllib.parse

from jarvis.memory import MemoryStore
from jarvis.shell import ShellPolicy, ShellResult, audit, check_command, run_shell

log = logging.getLogger(__name__)

MEDIA_ACTIONS = ("play", "pause", "play-pause", "next", "previous", "stop", "status")
MAX_SHELL_TIMEOUT_S = 300.0

DECLARATIONS: tuple[dict, ...] = (
    {
        "name": "run_shell",
        "description": "Run a bash command as the user, in their home directory, and return "
                       "its exit code and combined output. Waits for the command to finish: "
                       "never use it for GUI apps or long-running programs (use open_app).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "command": {"type": "STRING", "description": "The bash command line."},
                "timeout_seconds": {"type": "NUMBER",
                                    "description": "Kill the command after this long. Default 60, max 300."},
            },
            "required": ["command"],
        },
    },
    {
        "name": "open_app",
        "description": "Launch a GUI application or long-running program in the background "
                       "and return immediately, e.g. 'firefox', 'kitty', 'code ~/project', "
                       "'firefox https://example.com'.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"command": {"type": "STRING", "description": "Command line to launch."}},
            "required": ["command"],
        },
    },
    {
        "name": "play_music",
        "description": "Search YouTube and play the best match in the background, audio only. "
                       "Replaces whatever play_music started before.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"query": {"type": "STRING",
                                     "description": "Song, artist, album or mood, e.g. 'lofi hip hop'."}},
            "required": ["query"],
        },
    },
    {
        "name": "media_control",
        "description": "Control the active media player (Spotify, browser, mpv) via MPRIS.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "enum": list(MEDIA_ACTIONS)},
                "player": {"type": "STRING",
                           "description": "Optional player name, e.g. 'spotify'. Omit for the active one."},
            },
            "required": ["action"],
        },
    },
    {
        "name": "remember",
        "description": "Save a lasting fact about the user (preference, plan, deadline, person). "
                       "Never secrets, credentials, bug-bounty targets or findings.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"fact": {"type": "STRING",
                                    "description": "One short self-contained fact in the user's "
                                                   "own words, e.g. '<thing> is on <date>'."}},
            "required": ["fact"],
        },
    },
    {
        "name": "recall",
        "description": "Search saved facts. Omit the query to list them all.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"query": {"type": "STRING", "description": "Words the fact must contain."}},
        },
    },
    {
        "name": "forget",
        "description": "Delete every saved fact containing the given text.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"query": {"type": "STRING", "description": "Text identifying the fact(s)."}},
            "required": ["query"],
        },
    },
    {
        "name": "end_conversation",
        "description": "End the conversation and go back to waiting for the wake word. Call it "
                       "after saying goodbye, when the user is done.",
    },
)


def function_declarations():
    """Gemini FunctionDeclarations. Imported lazily so local-only installs don't need google-genai."""
    from google.genai import types

    return [types.FunctionDeclaration(**d) for d in DECLARATIONS]


def _lower_types(node):
    if isinstance(node, dict):
        return {k: v.lower() if k == "type" and isinstance(v, str) else _lower_types(v) for k, v in node.items()}
    return [_lower_types(v) for v in node] if isinstance(node, list) else node


def openai_tools() -> list[dict]:
    """The same tools in OpenAI/Ollama function-calling format (JSON-schema types in lower case)."""
    empty = {"type": "OBJECT", "properties": {}}
    return [{"type": "function",
             "function": {"name": d["name"], "description": d["description"],
                          "parameters": _lower_types(d.get("parameters", empty))}}
            for d in DECLARATIONS]


def first_youtube_video(query: str) -> str:
    """Video id of the top YouTube search result (network lookup via yt-dlp)."""
    import yt_dlp

    options = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True}
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(f"ytsearch1:{query}", download=False)
    return info["entries"][0]["id"]


def _program(command: str) -> str | None:
    """First real word of a command line, skipping VAR=value prefixes."""
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    return next((w for w in words if "=" not in w or w.startswith(("/", "."))), None)


class MusicPlayer:
    """Owns the mpv process started by play_music. Pauses by signal, so mpv-mpris is optional."""

    def __init__(self, policy: ShellPolicy):
        self._policy = policy
        self._proc: subprocess.Popen | None = None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def play(self, query: str) -> dict:
        if not shutil.which("mpv"):
            return {"error": "mpv is not installed. Tell the user to install mpv (and mpv-mpris) with their package manager."}
        self.stop()
        argv = ["mpv", "--no-video", "--really-quiet", "--title=jarvis-music", f"ytdl://ytsearch1:{query}"]
        self._proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, start_new_session=True)
        audit(self._policy.log_path, "play_music", ShellResult(shlex.join(argv), 0, ""))
        return {"status": "playing", "query": query,
                "note": "Takes a few seconds to start while YouTube is searched."}

    def signal(self, sig: signal.Signals) -> bool:
        if not self.running:
            return False
        try:
            os.killpg(self._proc.pid, sig)
        except ProcessLookupError:  # exited between the check and the kill
            return False
        return True

    def stop(self) -> None:
        proc = self._proc
        if self.signal(signal.SIGCONT):  # a stopped process can't act on SIGTERM
            self.signal(signal.SIGTERM)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.signal(signal.SIGKILL)
                proc.wait()
        self._proc = None


class ToolRunner:
    def __init__(self, policy: ShellPolicy, memory: MemoryStore | None = None):
        self._policy = policy
        self._memory = memory
        self.music = MusicPlayer(policy)
        self._handlers = {
            "run_shell": self.run_shell,
            "open_app": self.open_app,
            "play_music": self.play_music,
            "media_control": self.media_control,
            "remember": self.remember,
            "recall": self.recall,
            "forget": self.forget,
        }

    async def call(self, name: str, args: dict | None) -> dict:
        handler = self._handlers.get(name)
        if handler is None:
            return {"error": f"Unknown tool: {name}"}
        try:
            return await asyncio.to_thread(handler, **(args or {}))
        except TypeError as exc:
            return {"error": f"Bad arguments for {name}: {exc}"}
        except Exception as exc:  # a tool failure must never kill the conversation
            log.exception("tool %s failed", name)
            return {"error": f"{type(exc).__name__}: {exc}"}

    # -- tools -------------------------------------------------------------

    _NO_MEMORY = {"error": "Long-term memory is disabled (no memory file configured)."}

    def remember(self, fact: str) -> dict:
        return self._memory.remember(fact) if self._memory else self._NO_MEMORY

    def recall(self, query: str | None = None) -> dict:
        return self._memory.recall(query) if self._memory else self._NO_MEMORY

    def forget(self, query: str) -> dict:
        return self._memory.forget(query) if self._memory else self._NO_MEMORY

    def play_music(self, query: str) -> dict:
        """YouTube in the default browser (set JARVIS_MUSIC_BROWSER to pick one); JARVIS_MUSIC_PLAYER=mpv for headless."""
        if os.environ.get("JARVIS_MUSIC_PLAYER", "browser") == "mpv":
            return self.music.play(query)
        browser = os.environ.get("JARVIS_MUSIC_BROWSER", "xdg-open")
        try:
            url = f"https://www.youtube.com/watch?v={first_youtube_video(query)}"
        except Exception as exc:  # offline, YouTube changed, no results: show the search instead
            log.warning("YouTube lookup failed (%s); opening search results", exc)
            url = "https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": query})
        result = self.open_app(f"{browser} {shlex.quote(url)}")
        if "error" in result:
            return result
        return {"status": "playing", "query": query, "where": f"YouTube in {browser}"}

    def run_shell(self, command: str, timeout_seconds: float | None = None) -> dict:
        policy = self._policy
        if timeout_seconds:
            timeout = min(max(float(timeout_seconds), 1.0), MAX_SHELL_TIMEOUT_S)
            policy = dataclasses.replace(policy, timeout_s=timeout)
        return run_shell(command, policy).as_tool_response()

    def open_app(self, command: str) -> dict:
        refusal = check_command(command, self._policy)
        if refusal:
            audit(self._policy.log_path, "open_app", ShellResult(command, None, "", refused=refusal))
            return {"error": f"Refused: {refusal}"}
        program = _program(command)
        if not program or not shutil.which(os.path.expanduser(program)):
            return {"error": f"'{program}' is not installed or not on PATH. "
                             "Search for the right name with run_shell first."}

        # setsid fully detaches the app from Jarvis. (Not `hyprctl dispatch exec`: Hyprland 0.56
        # moved dispatch to a Lua API and the old syntax fails with exit 7.) Never capture output:
        # the app would inherit the pipes and we'd wait on it.
        argv = ["setsid", "-f", "bash", "-c", command]
        proc = subprocess.run(argv, cwd=self._policy.home, stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        failed = proc.returncode != 0
        audit(self._policy.log_path, "open_app", ShellResult(command, proc.returncode, ""))
        if failed:
            detail = ((proc.stdout or "") + (proc.stderr or "")).strip()
            return {"error": detail or f"exit code {proc.returncode}"}
        return {"status": "launched", "command": command}

    def media_control(self, action: str, player: str | None = None) -> dict:
        if action not in MEDIA_ACTIONS:
            return {"error": f"action must be one of {', '.join(MEDIA_ACTIONS)}"}

        if action == "stop" and self.music.running:
            self.music.stop()
            return {"status": "stopped"}

        if shutil.which("playerctl"):
            argv = ["playerctl"] + (["--player", player] if player else []) + [action]
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=5)
            if proc.returncode == 0:
                return {"status": action, "output": proc.stdout.strip() or "ok"}
            if not self.music.running:
                return {"error": proc.stderr.strip() or "No media player is running."}

        # No MPRIS player (or no playerctl): fall back to the mpv that play_music owns.
        if not self.music.running:
            return {"error": "Nothing is playing, and playerctl is not installed "
                             "(tell the user to install playerctl)."}
        fallback = {"pause": signal.SIGSTOP, "play": signal.SIGCONT}
        if action == "play-pause":
            return {"error": "Say pause or play explicitly; play-pause needs playerctl."}
        if action in fallback:
            self.music.signal(fallback[action])
            return {"status": action}
        return {"error": f"'{action}' needs playerctl and mpv-mpris (tell the user to install them)."}
