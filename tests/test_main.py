import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import jarvis.__main__ as cli
from jarvis.config import Config
from jarvis.shell import ShellPolicy


def _cfg(api_key="key", model=None):
    return Config(api_key=api_key, model=model, voice="Charon", wake_model="hey_jarvis",
                  wake_threshold=0.5, idle_timeout_s=20, barge_in=False, google_search=True,
                  input_device=None, output_device=None,
                  shell=ShellPolicy(True, (), 60, 4000, Path("/tmp/x"), Path("/tmp")))


@pytest.mark.parametrize("error, expected", [
    (RuntimeError("429 RESOURCE_EXHAUSTED"), "quota"),
    (RuntimeError("1011 You exceeded your current quota"), "JARVIS_MODEL=gemini-2.5-flash-native-audio-latest"),
    (RuntimeError("1008 policy violation: model not found"), "--list-models"),
    (ValueError("boom"), "Conversation failed: boom"),
])
def test_explain_turns_errors_into_actionable_messages(error, expected):
    assert expected in cli._explain(error)


def test_main_without_api_key_prints_setup_help(monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_config", lambda: _cfg(api_key=""))
    assert cli.main([]) == 2
    assert "aistudio.google.com/apikey" in capsys.readouterr().err


class FakeGenaiClient:
    models_listing = [
        SimpleNamespace(name="models/gemini-3.8-live", supported_actions=["bidiGenerateContent"]),
        SimpleNamespace(name="models/gemini-3.8-pro", supported_actions=["generateContent"]),
    ]

    def __init__(self, api_key, http_options=None):
        FakeGenaiClient.http_options = http_options
        self.models = SimpleNamespace(list=lambda: iter(self.models_listing))


def test_list_models_prints_only_live_models(monkeypatch, capsys):
    import google.genai
    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(google.genai, "Client", FakeGenaiClient)
    assert cli.main(["--list-models"]) == 0
    assert capsys.readouterr().out.strip() == "models/gemini-3.8-live"


def test_main_auto_selects_a_model_and_starts_the_assistant(monkeypatch):
    import google.genai
    started = {}

    async def fake_assistant(cfg, client, model, start_now):
        started.update(model=model, start_now=start_now)

    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(google.genai, "Client", FakeGenaiClient)
    monkeypatch.setattr(cli, "_assistant", fake_assistant)
    assert cli.main(["--now"]) == 0
    assert started == {"model": "models/gemini-3.8-live", "start_now": True}
    assert FakeGenaiClient.http_options == {"api_version": "v1alpha"}  # v1beta Live 404s for AI Studio keys


def test_explicit_model_overrides_auto_selection(monkeypatch):
    import google.genai
    started = {}

    async def fake_assistant(cfg, client, model, start_now):
        started["model"] = model

    monkeypatch.setattr(cli, "load_config", lambda: _cfg(model="models/my-pick"))
    monkeypatch.setattr(google.genai, "Client", FakeGenaiClient)
    monkeypatch.setattr(cli, "_assistant", fake_assistant)
    cli.main([])
    assert started["model"] == "models/my-pick"


def test_config_reads_env(monkeypatch, tmp_path):
    from jarvis.config import load_config
    monkeypatch.setenv("GEMINI_API_KEY", "abc")
    monkeypatch.setenv("JARVIS_GUARD", "0")
    monkeypatch.setenv("JARVIS_BARGE_IN", "yes")
    monkeypatch.setenv("JARVIS_IDLE_TIMEOUT", "12.5")
    monkeypatch.setenv("JARVIS_DENY_PATHS", "~/bugbounty:~/recon")
    cfg = load_config(env_file=tmp_path / "missing.env")
    assert cfg.api_key == "abc" and cfg.shell.guard is False and cfg.barge_in is True
    assert cfg.idle_timeout_s == 12.5
    assert cfg.shell.deny_paths[-2:] == ("~/bugbounty", "~/recon")


def test_installed_console_script_runs(tmp_path):
    """Guards against a broken editable install: pytest's own sys.path would hide it."""
    import shutil
    import subprocess
    import sys
    script = Path(sys.executable).parent / "jarvis"
    assert script.exists(), "console script not installed; run `uv sync`"
    proc = subprocess.run([str(script), "--help"], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert "--wake-test" in proc.stdout



class _Stop(Exception):
    """Ends the otherwise endless conversation loop in tests."""


def _loop_fakes(monkeypatch, outcome):
    """Fake detector/mic/speaker/Conversation; the 2nd wake-word wait raises _Stop."""
    import io
    import jarvis.audio
    import jarvis.session
    from jarvis.ui import Transcript

    events, waits = [], {"n": 0}

    async def wait_for_wake(mic, detector, poke=None):
        waits["n"] += 1
        if waits["n"] > 1:
            raise _Stop
    monkeypatch.setattr(cli, "_wait_for_wake_word", wait_for_wake)
    monkeypatch.setattr(cli, "_notify", lambda body: events.append("notify"))

    class FakeConversation:
        def __init__(self, *args, **kwargs):
            events.append({"opening": kwargs.get("opening")})
        async def run(self):
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
    monkeypatch.setattr(jarvis.session, "Conversation", FakeConversation)

    speaker = SimpleNamespace(play=lambda pcm: events.append(
        {jarvis.audio.CHIME_WAKE: "wake", jarvis.audio.CHIME_SLEEP: "sleep",
         jarvis.audio.CHIME_ERROR: "error"}[pcm]))
    detector = SimpleNamespace(reset=lambda: events.append("reset"))
    mic = SimpleNamespace(drain=lambda: None)
    out = io.StringIO()
    return events, speaker, detector, mic, Transcript(out), out


async def test_conversation_loop_runs_a_conversation_then_waits_again(monkeypatch):
    events, speaker, detector, mic, transcript, out = _loop_fakes(monkeypatch, "goodbye")
    with pytest.raises(_Stop):
        await cli._conversation_loop(_cfg(), None, "m", False, transcript, detector, None, mic, speaker)
    assert [e for e in events if not isinstance(e, dict)] == ["wake", "notify", "sleep", "reset"]
    assert "conversation ended: goodbye" in out.getvalue()


async def test_conversation_loop_survives_a_failed_conversation(monkeypatch):
    events, speaker, detector, mic, transcript, out = _loop_fakes(
        monkeypatch, RuntimeError("1011 You exceeded your current quota"))
    with pytest.raises(_Stop):  # reaching the 2nd wake-word wait proves the loop kept going
        await cli._conversation_loop(_cfg(), None, "m", False, transcript, detector, None, mic, speaker)
    assert [e for e in events if not isinstance(e, dict)] == ["wake", "notify", "error", "reset"]
    assert "quota exceeded" in out.getvalue()


async def test_now_flag_skips_only_the_first_wake_word(monkeypatch):
    events, speaker, detector, mic, transcript, _ = _loop_fakes(monkeypatch, "idle")
    with pytest.raises(_Stop):
        await cli._conversation_loop(_cfg(), None, "m", True, transcript, detector, None, mic, speaker)
    # first conversation started without waiting; the 1st wait came after it and still passed,
    # so a second conversation ran before the 2nd wait stopped the loop
    assert events.count("wake") == 2



class OfflineGenaiClient:
    def __init__(self, api_key, http_options=None):
        def offline():
            raise ConnectionError("[Errno -3] Temporary failure in name resolution")
        self.models = SimpleNamespace(list=offline)


def _capture_start(monkeypatch):
    started = {}
    async def fake_assistant(cfg, client, model, start_now):
        started["model"] = model
    monkeypatch.setattr(cli, "_assistant", fake_assistant)
    return started


def test_pinned_model_starts_without_listing_models(monkeypatch):
    import google.genai
    monkeypatch.setattr(cli, "load_config", lambda: _cfg(model="gemini-2.5-flash-native-audio-latest"))
    monkeypatch.setattr(google.genai, "Client", OfflineGenaiClient)  # listing would raise
    started = _capture_start(monkeypatch)
    assert cli.main([]) == 0
    assert started["model"] == "gemini-2.5-flash-native-audio-latest"


def test_offline_at_boot_falls_back_instead_of_crashing(monkeypatch, capsys):
    import google.genai
    from jarvis.models import FALLBACK_LIVE_MODEL
    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(google.genai, "Client", OfflineGenaiClient)
    started = _capture_start(monkeypatch)
    assert cli.main([]) == 0
    assert started["model"] == FALLBACK_LIVE_MODEL
    assert "offline" in capsys.readouterr().err.lower()


def test_list_models_offline_fails_cleanly(monkeypatch, capsys):
    import google.genai
    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(google.genai, "Client", OfflineGenaiClient)
    assert cli.main(["--list-models"]) == 1
    assert "Could not reach Gemini" in capsys.readouterr().err


@pytest.mark.parametrize("error", [
    OSError("[Errno -3] Temporary failure in name resolution"),
    OSError("[Errno 101] Network is unreachable"),
])
def test_explain_recognises_being_offline(error):
    assert "No internet connection" in cli._explain(error)


# -- wake-on-signal, morning briefing, state file, voices ---------------------

async def test_poke_event_wakes_jarvis_without_the_wake_word():
    import numpy as np
    poke = asyncio.Event()
    reads = {"n": 0}

    class Mic:
        async def read(self):
            reads["n"] += 1
            if reads["n"] == 3:
                poke.set()
            return np.zeros(1280, dtype=np.int16)

    detector = SimpleNamespace(detected=lambda frame: False)
    await asyncio.wait_for(cli._wait_for_wake_word(Mic(), detector, poke), 2)
    assert not poke.is_set()  # consumed, so the next wait listens again


async def test_first_conversation_of_the_day_gets_the_briefing_once(monkeypatch, tmp_path):
    from datetime import datetime
    from jarvis.profile import BriefingGate
    events, speaker, detector, mic, transcript, _ = _loop_fakes(monkeypatch, "goodbye")
    gate = BriefingGate(tmp_path / "last-briefing")
    companions = cli.Companions(briefing=gate, now=lambda: datetime(2026, 11, 7, 9, 0),
                                state_path=tmp_path / "state")
    with pytest.raises(_Stop):
        await cli._conversation_loop(_cfg(), None, "m", True, transcript, detector, None, mic, speaker,
                                     companions)
    openings = [e["opening"] for e in events if isinstance(e, dict)]
    assert openings[0] and "briefing" in openings[0].lower()
    assert openings[1] is None  # second conversation, same day
    assert (tmp_path / "state").read_text() == "listening\n"


async def test_failed_conversation_does_not_use_up_the_briefing(monkeypatch, tmp_path):
    from datetime import datetime
    from jarvis.profile import BriefingGate
    events, speaker, detector, mic, transcript, _ = _loop_fakes(monkeypatch, RuntimeError("offline"))
    gate = BriefingGate(tmp_path / "last-briefing")
    companions = cli.Companions(briefing=gate, now=lambda: datetime(2026, 11, 7, 9, 0))
    with pytest.raises(_Stop):
        await cli._conversation_loop(_cfg(), None, "m", False, transcript, detector, None, mic, speaker,
                                     companions)
    assert gate.due(datetime(2026, 11, 7).date())


def test_voices_flag_runs_the_audition(monkeypatch):
    import google.genai
    seen = {}

    async def fake_audition(cfg, client, model, voices):
        seen.update(model=model, voices=voices)

    monkeypatch.setattr(cli, "load_config", lambda: _cfg(model="m1"))
    monkeypatch.setattr(google.genai, "Client", FakeGenaiClient)
    monkeypatch.setattr(cli, "_audition", fake_audition)
    assert cli.main(["--voices", "puck"]) == 0
    assert seen == {"model": "m1", "voices": ["Puck"]}


def test_voices_flag_rejects_unknown_voice(monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_config", lambda: _cfg(model="m1"))
    assert cli.main(["--voices", "Bob"]) == 2
    assert "Bob" in capsys.readouterr().err


def test_config_reads_know_me_settings(monkeypatch, tmp_path):
    from jarvis.config import load_config
    monkeypatch.setenv("JARVIS_BRIEFING", "0")
    monkeypatch.setenv("JARVIS_DAY_TRACKING", "0")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    cfg = load_config(tmp_path / "missing.env")
    assert cfg.briefing is False and cfg.day_tracking is False
    assert cfg.profile_path == tmp_path / "cfg" / "jarvis" / "me.md"
    assert cfg.memory_path == tmp_path / "data" / "jarvis" / "memory.md"


def test_build_context_includes_profile_memories_and_activity(tmp_path):
    from dataclasses import replace
    from jarvis.memory import MemoryStore
    profile = tmp_path / "me.md"
    profile.write_text("Handle: sam")
    memory = MemoryStore(tmp_path / "memory.md")
    memory.remember("drinks tea")
    cfg = replace(_cfg(), profile_path=profile)
    ctx = cli._build_context(cfg, memory, SimpleNamespace(summary=lambda: "kitty 1h"))
    assert ctx.profile == "Handle: sam" and "drinks tea" in ctx.memories and ctx.activity == "kitty 1h"


def test_build_context_survives_a_broken_memory_store(tmp_path):
    from dataclasses import replace
    def boom():
        raise OSError("disk on fire")
    ctx = cli._build_context(replace(_cfg(), profile_path=tmp_path / "none"), SimpleNamespace(as_prompt=boom), None)
    assert ctx.profile == "" and ctx.memories == ""


def test_signal_handlers_are_installed_then_restorable():
    import signal
    before = signal.getsignal(signal.SIGUSR1)
    previous = cli._install_signal_handlers()
    try:
        assert signal.getsignal(signal.SIGUSR1) is signal.SIG_IGN  # a stray USR1 must not kill Jarvis
        with pytest.raises(KeyboardInterrupt):
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
    finally:
        for sig, old in previous.items():
            signal.signal(sig, old)
    assert signal.getsignal(signal.SIGUSR1) == before


async def test_poke_during_a_conversation_does_not_start_a_phantom_one(monkeypatch, tmp_path):
    events, speaker, detector, mic, transcript, _ = _loop_fakes(monkeypatch, "goodbye")
    poke = asyncio.Event()
    import jarvis.session
    class PokedConversation:
        def __init__(self, *a, **k):
            pass
        async def run(self):
            poke.set()  # right-click on the bar while already talking
            return "goodbye"
    monkeypatch.setattr(jarvis.session, "Conversation", PokedConversation)
    with pytest.raises(_Stop):
        await cli._conversation_loop(_cfg(), None, "m", True, transcript, detector, None, mic, speaker,
                                     cli.Companions(poke=poke))
    assert not poke.is_set()


async def test_briefing_mark_failure_does_not_turn_a_good_conversation_into_an_error(monkeypatch):
    from datetime import datetime
    events, speaker, detector, mic, transcript, _ = _loop_fakes(monkeypatch, "goodbye")
    gate = SimpleNamespace(due=lambda day: True, mark=lambda day: (_ for _ in ()).throw(OSError("ro fs")))
    with pytest.raises(_Stop):
        await cli._conversation_loop(_cfg(), None, "m", True, transcript, detector, None, mic, speaker,
                                     cli.Companions(briefing=gate, now=lambda: datetime(2026, 11, 7, 9)))
    assert "error" not in events and "sleep" in events


def test_usr1_is_ignored_before_the_model_lookup(monkeypatch):
    import signal
    seen = {}

    def resolve(cfg, client, choose, fallback, errors):
        seen["usr1"] = signal.getsignal(signal.SIGUSR1)
        return "m"

    async def fake_assistant(cfg, client, model, start_now):
        pass

    import google.genai
    monkeypatch.setattr(cli, "load_config", lambda: _cfg())
    monkeypatch.setattr(google.genai, "Client", FakeGenaiClient)
    monkeypatch.setattr(cli, "_resolve_model", resolve)
    monkeypatch.setattr(cli, "_assistant", fake_assistant)
    before = signal.getsignal(signal.SIGUSR1)
    cli.main([])
    assert seen["usr1"] is signal.SIG_IGN
    assert signal.getsignal(signal.SIGUSR1) == before  # restored for the test process


def test_main_announces_starting_before_slow_startup(monkeypatch, tmp_path):
    import google.genai
    state = tmp_path / "state"
    seen = {}

    async def fake_assistant(cfg, client, model, start_now):
        seen["state"] = state.read_text()

    monkeypatch.setattr(cli, "runtime_state_file", lambda: state)
    monkeypatch.setattr(cli, "load_config", lambda: _cfg(model="m"))
    monkeypatch.setattr(google.genai, "Client", FakeGenaiClient)
    monkeypatch.setattr(cli, "_assistant", fake_assistant)
    cli.main([])
    assert seen["state"] == "starting\n"


# -- always-on mode -------------------------------------------------------------

def _always_fakes(monkeypatch, outcomes):
    """Conversation.run() returns/raises each outcome in turn; the list running out stops the loop."""
    import io
    import jarvis.audio
    import jarvis.session
    from jarvis.ui import Transcript

    events, sleeps, outcomes = [], [], list(outcomes)

    class FakeConversation:
        def __init__(self, *args, **kwargs):
            events.append({"opening": kwargs.get("opening"), "resume": kwargs.get("resume")})
        async def run(self):
            if not outcomes:
                raise _Stop
            outcome = outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(jarvis.session, "Conversation", FakeConversation)
    monkeypatch.setattr(cli, "_notify", lambda body: events.append("notify"))
    monkeypatch.setattr(cli, "_backoff_sleep", fake_sleep)
    speaker = SimpleNamespace(play=lambda pcm: events.append(
        {jarvis.audio.CHIME_WAKE: "wake", jarvis.audio.CHIME_SLEEP: "sleep",
         jarvis.audio.CHIME_ERROR: "error"}[pcm]))
    mic = SimpleNamespace(drain=lambda: None)
    out = io.StringIO()
    return events, sleeps, speaker, mic, Transcript(out), out


async def test_always_on_goodbye_ends_the_assistant(monkeypatch, tmp_path):
    events, sleeps, speaker, mic, transcript, _ = _always_fakes(monkeypatch, ["goodbye"])
    companions = cli.Companions(state_path=tmp_path / "state")
    result = await cli._always_on_loop(_cfg(), None, "m", transcript, None, mic, speaker, companions)
    assert result == "goodbye"
    assert events.count("wake") == 1 and "sleep" in events
    assert (tmp_path / "state").read_text() == "listening\n"


async def test_always_on_reconnects_quietly_when_the_server_rotates_the_session(monkeypatch):
    closed = ConnectionError("The Live session was closed by the server.")
    events, sleeps, speaker, mic, transcript, out = _always_fakes(monkeypatch, [closed, "goodbye"])
    assert await cli._always_on_loop(_cfg(), None, "m", transcript, None, mic, speaker, cli.Companions()) == "goodbye"
    assert "error" not in events and events.count("wake") == 1  # seamless: no chimes on reconnect
    resumes = [e["resume"] for e in events if isinstance(e, dict)]
    assert resumes[0] is resumes[1]  # same handle holder, so the conversation continues


async def test_always_on_backs_off_on_repeated_errors_and_forgets_a_bad_handle(monkeypatch):
    err = RuntimeError("1011 Internal error")
    events, sleeps, speaker, mic, transcript, out = _always_fakes(monkeypatch, [err, err, err, "goodbye"])
    await cli._always_on_loop(_cfg(), None, "m", transcript, None, mic, speaker, cli.Companions())
    assert sleeps == sorted(sleeps) and sleeps[0] < sleeps[-1]
    assert events.count("error") == 1  # one error chime, not one per retry
    assert "Internal error" in out.getvalue()
    assert [e["resume"].handle for e in events if isinstance(e, dict)][-1] is None


async def test_always_on_gives_the_briefing_on_the_first_session_only(monkeypatch, tmp_path):
    from datetime import datetime
    from jarvis.profile import BriefingGate
    closed = ConnectionError("closed")
    events, sleeps, speaker, mic, transcript, _ = _always_fakes(monkeypatch, [closed, "goodbye"])
    gate = BriefingGate(tmp_path / "b")
    await cli._always_on_loop(_cfg(), None, "m", transcript, None, mic, speaker,
                              cli.Companions(briefing=gate, now=lambda: datetime(2026, 11, 9, 9)))
    openings = [e["opening"] for e in events if isinstance(e, dict)]
    assert openings[0] and openings[1] is None


def test_config_defaults_to_no_wake_word(monkeypatch, tmp_path):
    from jarvis.config import load_config
    monkeypatch.delenv("JARVIS_WAKE_WORD", raising=False)
    assert load_config(tmp_path / "missing.env").wake_word is False
    monkeypatch.setenv("JARVIS_WAKE_WORD", "1")
    assert load_config(tmp_path / "missing.env").wake_word is True


def test_toggle_script_starts_a_stopped_service_and_stops_a_running_one(tmp_path):
    import os
    import subprocess
    script = Path(cli.__file__).resolve().parent.parent / "contrib" / "jarvis-toggle"
    log = tmp_path / "calls"
    for name, body in {"systemctl": 'echo "$*" >> "$LOG"; [ "$2" = is-active ] && exit "$ACTIVE"; exit 0',
                       "notify-send": "exit 0"}.items():
        stub = tmp_path / name
        stub.write_text(f"#!/bin/bash\n{body}\n")
        stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:/usr/bin:/bin", "LOG": str(log)}
    subprocess.run([str(script)], env={**env, "ACTIVE": "3"}, check=True)
    subprocess.run([str(script)], env={**env, "ACTIVE": "0"}, check=True)
    calls = log.read_text().splitlines()
    assert "--user start jarvis.service" in calls and "--user stop jarvis.service" in calls


def _local_cfg(**kw):
    from dataclasses import replace
    return replace(_cfg(api_key=""), backend="local", **kw)


def test_first_run_without_backend_points_to_setup(monkeypatch, capsys):
    from dataclasses import replace
    monkeypatch.setattr(cli, "load_config", lambda: replace(_cfg(api_key=""), backend=""))
    assert cli.main([]) == 2
    assert "jarvis setup" in capsys.readouterr().err


def test_bad_config_file_exits_with_its_message(monkeypatch, capsys):
    def boom():
        raise cli.ConfigError("Cannot read config.toml: oops")
    monkeypatch.setattr(cli, "load_config", boom)
    assert cli.main([]) == 2
    assert "config.toml" in capsys.readouterr().err


def test_local_backend_needs_no_api_key_and_starts_the_local_runtime(monkeypatch):
    started = {}
    monkeypatch.setattr(cli, "load_config", lambda: _local_cfg())
    monkeypatch.setattr(cli, "_run_local", lambda cfg, start_now: started.update(backend=cfg.backend) or 0)
    assert cli.main([]) == 0
    assert started == {"backend": "local"}


def test_gemini_only_flags_are_refused_on_the_local_backend(monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_config", lambda: _local_cfg())
    assert cli.main(["--list-models"]) == 2
    assert "Gemini backend" in capsys.readouterr().err


def test_missing_local_extras_print_install_help(monkeypatch, capsys):
    import jarvis.backends.local.runtime as runtime
    from jarvis.backends.local.errors import LocalBackendError

    def missing(cfg):
        raise LocalBackendError("needs extras: uv sync --extra local")
    monkeypatch.setattr(runtime, "create_runtime", missing)
    assert cli._run_local(_local_cfg(), False) == 2
    assert "uv sync --extra local" in capsys.readouterr().err


def test_subcommands_are_dispatched_before_argparse(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "_run_subcommand", lambda name: seen.append(name) or 0)
    assert cli.main(["doctor"]) == 0 and cli.main(["setup"]) == 0
    assert seen == ["doctor", "setup"]


def test_doctor_subcommand_returns_the_checks_exit_code(monkeypatch):
    import jarvis.setup.doctor as doctor
    monkeypatch.setattr(cli, "load_config", lambda: _local_cfg())
    monkeypatch.setattr(doctor, "run", lambda cfg: 1)
    assert cli.main(["doctor"]) == 1


def test_local_errors_are_explained_verbatim():
    from jarvis.backends.local.errors import LocalBackendError
    assert cli._explain(LocalBackendError("Can't reach the server")) == "Can't reach the server"


def test_cancelling_setup_is_not_a_crash(monkeypatch, capsys):
    import jarvis.setup.wizard as wizard

    def interrupted(**kw):
        raise KeyboardInterrupt
    monkeypatch.setattr(wizard, "run", interrupted)
    assert cli.main(["setup"]) == 130
    assert "cancelled" in capsys.readouterr().out
