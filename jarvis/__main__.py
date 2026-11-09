"""Entry point: `uv run jarvis` (or `python -m jarvis`)."""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import signal
import subprocess
import sys
import time
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# google-genai ships docstrings with invalid escapes; harmless, but noisy on first import.
warnings.filterwarnings("ignore", category=SyntaxWarning, module=r"google\.genai\..*")

from jarvis.backends import make_conversation
from jarvis.backends.local.errors import LocalBackendError
from jarvis.config import PROJECT_ROOT, STATE_DIR, Config, ConfigError, load_config, runtime_state_file
from jarvis.profile import BriefingGate, write_state
from jarvis.prompt import Context, briefing_day, briefing_request
from jarvis.ui import Transcript

log = logging.getLogger("jarvis")

NO_KEY_HELP = """\
No Gemini API key found.

  1. Get a free key at https://aistudio.google.com/apikey
  2. Run `jarvis setup` and paste it when asked (it is stored privately, mode 0600).
"""

FIRST_RUN_HELP = """\
Jarvis isn't set up yet. Run:

  jarvis setup

It asks which brain to use (local and private, or the Gemini API for testing), your language,
voice and personality, and writes ~/.config/jarvis/config.toml.
"""

SUBCOMMANDS = ("setup", "doctor")


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="jarvis", description=__doc__,
        epilog="Subcommands: `jarvis setup` (guided configuration), `jarvis doctor` (check your install).")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--now", action="store_true", help="start talking immediately, skip the first wake word")
    group.add_argument("--wake-test", action="store_true", help="show live wake-word scores (no API key needed)")
    group.add_argument("--list-models", action="store_true", help="list Live-capable models for your key")
    group.add_argument("--devices", action="store_true", help="list audio devices")
    group.add_argument("--voices", nargs="?", const="", metavar="NAMES",
                       help="audition Gemini voices (all, or a comma list like Puck,Kore) and pick one")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def _notify(body: str) -> None:
    if shutil.which("notify-send"):
        subprocess.Popen(["notify-send", "-a", "Jarvis", "-t", "2500", "Jarvis", body],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


_OFFLINE_MARKERS = ("name resolution", "network is unreachable", "getaddrinfo",
                    "errno -3", "errno 101", "connecterror", "connection refused")


def _is_offline(exc: BaseException) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return any(marker in text for marker in _OFFLINE_MARKERS)


def _explain(exc: BaseException) -> str:
    if isinstance(exc, LocalBackendError):
        return str(exc)
    text = str(exc) or type(exc).__name__
    lowered = text.lower()
    if _is_offline(exc):
        return "No internet connection. Jarvis keeps listening; say “hey jarvis” again once you're online."
    if "429" in text or "quota" in lowered or "resource_exhausted" in lowered:
        return (f"Gemini quota exceeded: {text}\n"
                "  On the free tier this usually means the model isn't available to free keys "
                "(try JARVIS_MODEL=gemini-2.5-flash-native-audio-latest), or the daily limit is used up.")
    if "1008" in text or "1007" in text or "not found" in lowered or "not supported" in lowered:
        return (f"Gemini rejected the session: {text}\n"
                "  Try `uv run jarvis --list-models` and set JARVIS_MODEL, "
                "or JARVIS_GOOGLE_SEARCH=0 if search isn't allowed on your tier.")
    return f"Conversation failed: {text}"


async def _wait_for_wake_word(mic, detector, poke: asyncio.Event | None = None) -> None:
    """Return on the wake word, or when ``poke`` is set (SIGUSR1 / the bar button)."""
    while True:
        frame = await mic.read()  # 80 ms, so a poke is noticed almost immediately
        if poke is not None and poke.is_set():
            poke.clear()
            return
        if detector.detected(frame):
            return


@dataclass
class Companions:
    """Optional extras around the conversation loop; all default to off for tests."""

    context: Callable[[], Context | None] = lambda: None
    briefing: BriefingGate | None = None
    now: Callable[[], datetime] = datetime.now
    state_path: Path | None = None
    poke: asyncio.Event | None = None


def _build_context(cfg: Config, memory, daylog) -> Context:
    from jarvis.profile import load_profile

    now = datetime.now().astimezone()  # aware, so the prompt can name the time zone
    try:
        return Context(now=now, profile=load_profile(cfg.profile_path), memories=memory.as_prompt(),
                       activity=daylog.summary() if daylog else "")
    except Exception:  # a broken profile or memory file must never block a conversation
        log.warning("could not build conversation context", exc_info=True)
        return Context(now=now)


async def _assistant(cfg: Config, client, model: str, start_now: bool) -> None:
    from jarvis import activity
    from jarvis.audio import Microphone, Speaker
    from jarvis.memory import MemoryStore
    from jarvis.profile import ensure_profile
    from jarvis.tools import ToolRunner

    if cfg.wake_word:
        from jarvis.wakeword import WakeWordDetector

    transcript = Transcript()
    detector = WakeWordDetector(cfg.wake_model, cfg.wake_threshold) if cfg.wake_word else None
    memory = MemoryStore(cfg.memory_path)
    tools = ToolRunner(cfg.shell, memory=memory)
    loop = asyncio.get_running_loop()
    if ensure_profile(cfg.profile_path):
        transcript.info(f"Wrote a draft profile to {cfg.profile_path}. Fill it in so Jarvis knows you.")

    daylog, tracker = None, None
    socket = activity.socket_path() if cfg.day_tracking else None
    if socket:
        daylog = activity.DayLog(cfg.activity_dir, monotonic=time.monotonic)
        tracker = asyncio.create_task(activity.track(daylog, socket, initial=activity.active_class))

    poke = asyncio.Event()
    loop.add_signal_handler(signal.SIGUSR1, poke.set)
    companions = Companions(context=lambda: _build_context(cfg, memory, daylog),
                            briefing=BriefingGate(STATE_DIR / "last-briefing") if cfg.briefing else None,
                            state_path=runtime_state_file(), poke=poke)

    with Microphone(loop, cfg.input_device) as mic, Speaker(cfg.output_device) as speaker:
        guard = "on" if cfg.shell.guard else "OFF"
        if cfg.backend == "local":
            transcript.info(f"local · llm {model} · language {cfg.language} · guard {guard} · "
                            f"audit log {cfg.shell.log_path}")
            transcript.info("Loading speech models…")
            await asyncio.to_thread(client.warm_up)
        else:
            transcript.info(f"gemini {model} · voice {cfg.voice} · language {cfg.language} · guard {guard} · "
                            f"audit log {cfg.shell.log_path}")
            transcript.info("Cloud mode: your voice goes to Google. Testing and non-sensitive use only.")
        try:
            if cfg.wake_word:
                await _conversation_loop(cfg, client, model, start_now, transcript, detector, tools, mic, speaker,
                                         companions)
            else:
                transcript.info("Always listening: just talk. Say goodbye to turn Jarvis off.")
                await _always_on_loop(cfg, client, model, transcript, tools, mic, speaker, companions)
        finally:
            tools.music.stop()  # don't leave music playing after Jarvis quits
            if tracker:
                tracker.cancel()
                await asyncio.gather(tracker, return_exceptions=True)
            write_state(companions.state_path, "stopped")


def _opening_for(cfg: Config, companions: Companions):
    """(briefing text, day) on the first wake of the day, else (None, day)."""
    day = briefing_day(companions.now())
    if companions.briefing and companions.briefing.due(day):
        return briefing_request(cfg.google_search and cfg.backend == "gemini"), day
    return None, day


BACKOFF_S = (1.0, 3.0, 10.0, 30.0, 60.0)
HEALTHY_SESSION_S = 30.0  # a session that lasted this long resets the failure count


async def _backoff_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def _is_rotation(exc: BaseException) -> bool:
    """Gemini closing a long session (GoAway, connection lifetime) rather than a real failure."""
    return isinstance(exc, ConnectionError) and not _is_offline(exc)


async def _always_on_loop(cfg, client, model, transcript, tools, mic, speaker, companions: Companions) -> str:
    """No wake word: keep one conversation open for as long as Jarvis runs.

    When Gemini rotates the connection, reconnect at once with the resumption handle so
    the conversation carries on. Real errors are announced once, then retried with backoff.
    Returns "goodbye" when the user says goodbye, which turns Jarvis off.
    """
    from jarvis.audio import CHIME_ERROR, CHIME_SLEEP, CHIME_WAKE
    from jarvis.session import ResumeState

    resume, failures, announced = ResumeState(), 0, False
    speaker.play(CHIME_WAKE)
    _notify("Listening: just talk")
    while True:
        write_state(companions.state_path, "listening")
        opening, day = _opening_for(cfg, companions)
        mic.drain()
        started = time.monotonic()
        try:
            await make_conversation(cfg, client, model, mic, speaker, tools, transcript,
                                    context=companions.context(), opening=opening, resume=resume).run()
            if opening:
                _mark_briefed(companions.briefing, day)
            speaker.play(CHIME_SLEEP)
            transcript.info("(goodbye: Jarvis is turning off)")
            return "goodbye"
        except Exception as exc:
            if time.monotonic() - started > HEALTHY_SESSION_S:
                failures, announced = 0, False
            failures += 1
            rotation = _is_rotation(exc)
            if rotation and opening:
                _mark_briefed(companions.briefing, day)
            if rotation and failures == 1:
                log.debug("session rotated; resuming")
                continue  # seamless: same conversation, new connection
            if not rotation:
                resume.handle = None  # the old session may be what is failing
            log.debug("always-on session error", exc_info=True)
            delay = BACKOFF_S[min(failures - 1, len(BACKOFF_S) - 1)]
            if not announced:  # say it once per outage, not on every retry
                transcript.error(_explain(exc))
                speaker.play(CHIME_ERROR)
                announced = True
            transcript.info(f"Reconnecting in {delay:.0f}s…")
            await _backoff_sleep(delay)


def _mark_briefed(briefing: BriefingGate, day) -> None:
    try:
        briefing.mark(day)
    except OSError as exc:  # read-only or full state dir: the briefing just repeats next wake
        log.warning("could not record today's briefing: %s", exc)


async def _conversation_loop(cfg, client, model, start_now, transcript, detector, tools, mic, speaker,
                             companions: Companions | None = None) -> None:
    """Wait for the wake word, hold one conversation, repeat. A failed conversation never ends the loop."""
    from jarvis.audio import CHIME_ERROR, CHIME_SLEEP, CHIME_WAKE

    companions = companions or Companions()
    skip_wake = start_now
    while True:
        if not skip_wake:
            write_state(companions.state_path, "listening")
            transcript.info("Listening for “hey jarvis”…  (Ctrl+C to quit)")
            await _wait_for_wake_word(mic, detector, companions.poke)
        skip_wake = False

        write_state(companions.state_path, "talking")
        speaker.play(CHIME_WAKE)
        _notify("Listening…")
        mic.drain()
        opening, day = _opening_for(cfg, companions)
        try:
            reason = await make_conversation(cfg, client, model, mic, speaker, tools, transcript,
                                             context=companions.context(), opening=opening).run()
            if opening:
                _mark_briefed(companions.briefing, day)
            transcript.info(f"(conversation ended: {reason})")
            speaker.play(CHIME_SLEEP)
        except Exception as exc:  # network drop, quota, bad model… keep the assistant alive
            log.debug("conversation error", exc_info=True)
            transcript.error(_explain(exc))
            speaker.play(CHIME_ERROR)
        detector.reset()
        mic.drain()
        if companions.poke is not None:
            companions.poke.clear()  # a "talk now" click during the conversation is stale now


async def _wake_test(cfg: Config) -> None:
    from jarvis.audio import Microphone
    from jarvis.wakeword import WakeWordDetector

    detector = WakeWordDetector(cfg.wake_model, cfg.wake_threshold)
    print(f"Say “hey jarvis”. Threshold {cfg.wake_threshold}. Ctrl+C to stop.")
    with Microphone(asyncio.get_running_loop(), cfg.input_device) as mic:
        while True:
            score = detector.score(await mic.read())
            bar = "█" * int(score * 40)
            hit = "  ← TRIGGER" if score >= cfg.wake_threshold else ""
            print(f"\r{score:5.2f} {bar:<40}{hit}", end="", flush=True)
            if hit:
                print()
                detector.reset()


def _resolve_model(cfg: Config, client, choose_model, fallback: str, errors) -> str:
    """Pinned model needs no network. Otherwise list models, but never crash at boot:
    under Hyprland exec-once, Jarvis often starts before Wi-Fi is up."""
    if cfg.model:
        return cfg.model
    try:
        return choose_model(None, client.models.list())
    except errors.APIError as exc:
        print(f"Gemini API error while listing models ({exc}); using {fallback}.", file=sys.stderr)
    except Exception as exc:
        reason = "offline" if _is_offline(exc) else f"{type(exc).__name__}: {exc}"
        print(f"Could not list Gemini models ({reason}); using {fallback}.", file=sys.stderr)
    return fallback


async def _audition(cfg: Config, client, model: str, voices: list[str]) -> None:
    from jarvis.audio import Speaker
    from jarvis.config import config_dir, save_settings
    from jarvis.voices import run_audition

    print(f"Auditioning {len(voices)} voice(s) with {model}. Ctrl+C to stop early.\n")
    with Speaker(cfg.output_device) as speaker:
        await run_audition(client, model, voices, env_file=PROJECT_ROOT / ".env", play=speaker.play,
                           ask=lambda prompt: input(prompt),
                           save=lambda voice: save_settings(config_dir() / "config.toml",
                                                            {"gemini": {"voice": voice}}))


def _install_signal_handlers() -> dict:
    """systemctl stop sends SIGTERM: unwind like Ctrl+C so music and the tracker are cleaned up.
    SIGUSR1 ("talk now") is ignored until the conversation loop installs its own handler,
    because its default action would kill the process."""
    def handler(signum, frame):
        raise KeyboardInterrupt
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGUSR1)}
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGUSR1, signal.SIG_IGN)
    return previous


def _run_subcommand(name: str) -> int:
    try:
        if name == "setup":
            from jarvis.setup import doctor, wizard

            try:
                wizard.run(check=lambda cfg: doctor.format_checks(doctor.run_checks(cfg)))
            except (KeyboardInterrupt, EOFError):
                print("\nSetup cancelled; nothing was changed after the last saved step.")
                return 130
            return 0
        from jarvis.setup import doctor

        return doctor.run(load_config())
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2


def _run_local(cfg: Config, start_now: bool) -> int:
    from jarvis.backends.local.runtime import create_runtime

    try:
        runtime = create_runtime(cfg)
    except LocalBackendError as exc:
        print(exc, file=sys.stderr)
        return 2
    write_state(runtime_state_file(), "starting")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    asyncio.run(_assistant(cfg, runtime, cfg.local.llm_model, start_now=start_now))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in SUBCOMMANDS:
        return _run_subcommand(argv[0])
    args = _parse(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        cfg = load_config()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    # First thing: as a service, Jarvis is "active" before model lookup finishes, and a
    # bar right-click (SIGUSR1) during that window must not kill it.
    previous = _install_signal_handlers()
    try:
        if args.devices:
            import sounddevice as sd
            print(sd.query_devices())
            return 0
        if args.wake_test:
            asyncio.run(_wake_test(cfg))
            return 0

        voices = None
        if args.voices is not None:
            from jarvis.voices import parse_voices
            try:
                voices = parse_voices(args.voices)
            except ValueError as exc:
                print(exc, file=sys.stderr)
                return 2

        if not cfg.backend:
            print(FIRST_RUN_HELP, file=sys.stderr)
            return 2
        if cfg.backend == "local":
            if args.list_models or args.voices is not None:
                print("--list-models and --voices are for the Gemini backend. "
                      "Run `jarvis setup` to switch.", file=sys.stderr)
                return 2
            return _run_local(cfg, args.now)

        if not cfg.api_key:
            print(NO_KEY_HELP, file=sys.stderr)
            return 2

        if not (args.list_models or args.voices is not None):
            write_state(runtime_state_file(), "starting")  # the bar shows progress during the slow imports
        try:
            from google import genai
            from google.genai import errors
        except ImportError:
            print("The Gemini backend needs the google-genai package:\n  uv sync --extra gemini", file=sys.stderr)
            return 2

        from jarvis.models import FALLBACK_LIVE_MODEL, LIVE_API_VERSION, choose_model, live_models

        client = genai.Client(api_key=cfg.api_key, http_options={"api_version": LIVE_API_VERSION})

        if args.list_models:
            try:
                models = list(client.models.list())
            except Exception as exc:
                print(f"Could not reach Gemini: {exc}", file=sys.stderr)
                return 1
            advertised = live_models(models)
            print("\n".join(advertised) if advertised else
                  f"Google lists no Live models for this key; Jarvis will try {FALLBACK_LIVE_MODEL}.")
            return 0

        model = _resolve_model(cfg, client, choose_model, FALLBACK_LIVE_MODEL, errors)

        if voices is not None:
            asyncio.run(_audition(cfg, client, model, voices))
            return 0

        STATE_DIR.mkdir(parents=True, exist_ok=True)
        asyncio.run(_assistant(cfg, client, model, start_now=args.now))
    except KeyboardInterrupt:
        print("\nBye.")
    finally:
        for sig, old in previous.items():
            signal.signal(sig, old)
    return 0


if __name__ == "__main__":
    sys.exit(main())
