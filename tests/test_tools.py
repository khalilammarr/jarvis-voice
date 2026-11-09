import signal
import subprocess
from pathlib import Path

import pytest

from jarvis import tools as tools_mod
from jarvis.shell import ShellPolicy
from jarvis.tools import DECLARATIONS, ToolRunner, _program, function_declarations


@pytest.fixture
def policy(tmp_path):
    return ShellPolicy(guard=True, deny_paths=("~/.ssh",), timeout_s=5.0,
                       max_output_chars=2_000, log_path=tmp_path / "log", home=tmp_path)


@pytest.fixture
def runner(policy):
    return ToolRunner(policy)


def test_declarations_are_valid_gemini_function_declarations():
    declarations = function_declarations()
    assert [d.name for d in declarations] == [d["name"] for d in DECLARATIONS]
    assert {"run_shell", "open_app", "play_music", "media_control", "remember", "recall", "forget",
            "type_text", "end_conversation"} == {
        d.name for d in declarations}


async def test_unknown_tool_returns_error(runner):
    assert "Unknown tool" in (await runner.call("format_disk", {}))["error"]


async def test_bad_arguments_return_error_instead_of_raising(runner):
    assert "Bad arguments" in (await runner.call("run_shell", {"cmd": "ls"}))["error"]


async def test_run_shell_tool_runs_command(runner):
    response = await runner.call("run_shell", {"command": "echo hi"})
    assert response == {"exit_code": 0, "output": "hi"}


async def test_run_shell_tool_enforces_policy(runner):
    response = await runner.call("run_shell", {"command": "cat ~/.ssh/id_rsa"})
    assert response["error"].startswith("Refused")


async def test_run_shell_timeout_is_clamped(runner):
    response = await runner.call("run_shell", {"command": "sleep 3", "timeout_seconds": 0.01})
    assert response["exit_code"] is None  # clamped up to 1 s, still shorter than sleep 3


@pytest.mark.parametrize("command, expected", [
    ("firefox https://x.y", "firefox"),
    ("GDK_BACKEND=wayland code ~/p", "code"),
    ("./AppImage.run --flag", "./AppImage.run"),
])
def test_program_extracts_executable(command, expected):
    assert _program(command) == expected


async def test_open_app_rejects_missing_program(runner):
    response = await runner.call("open_app", {"command": "definitely-not-a-real-binary-xyz"})
    assert "not installed" in response["error"]


async def test_open_app_enforces_guard(runner):
    response = await runner.call("open_app", {"command": "rm -rf ~"})
    assert response["error"].startswith("Refused")


async def test_open_app_launches_detached_with_setsid_even_on_hyprland(runner, monkeypatch):
    calls = []
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "abc")  # Hyprland 0.56 broke `hyprctl dispatch exec`
    monkeypatch.setattr(tools_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(tools_mod.subprocess, "run", lambda argv, **kw: calls.append(argv)
                        or subprocess.CompletedProcess(argv, 0, "", ""))
    await runner.call("open_app", {"command": "kitty"})
    assert calls == [["setsid", "-f", "bash", "-c", "kitty"]]


async def test_play_music_opens_the_top_youtube_result_in_the_browser(runner, monkeypatch):
    launched = []
    monkeypatch.setattr(tools_mod, "first_youtube_video", lambda q: "dQw4w9WgXcQ")
    monkeypatch.setattr(runner, "open_app", lambda command: launched.append(command) or {"status": "launched"})
    response = await runner.call("play_music", {"query": "daft punk"})
    assert response["status"] == "playing"
    assert launched == ["xdg-open 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'"]


async def test_play_music_falls_back_to_search_page_when_lookup_fails(runner, monkeypatch):
    launched = []
    def offline(query):
        raise OSError("no network")
    monkeypatch.setattr(tools_mod, "first_youtube_video", offline)
    monkeypatch.setattr(runner, "open_app", lambda command: launched.append(command) or {"status": "launched"})
    await runner.call("play_music", {"query": "lofi; rm -rf ~"})
    assert launched == ["xdg-open 'https://www.youtube.com/results?search_query=lofi%3B+rm+-rf+~'"]


async def test_play_music_can_use_mpv_when_configured(runner, monkeypatch):
    monkeypatch.setenv("JARVIS_MUSIC_PLAYER", "mpv")
    monkeypatch.setattr(tools_mod.shutil, "which", lambda name: None)
    response = await runner.call("play_music", {"query": "daft punk"})
    assert "mpv is not installed" in response["error"]


async def test_play_music_passes_query_as_argv_not_through_a_shell(runner, monkeypatch):
    monkeypatch.setenv("JARVIS_MUSIC_PLAYER", "mpv")
    launched = []

    class FakePopen:
        pid = 4242
        def __init__(self, argv, **kwargs):
            launched.append((argv, kwargs))
        def poll(self):
            return None

    monkeypatch.setattr(tools_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(tools_mod.subprocess, "Popen", FakePopen)
    response = await runner.call("play_music", {"query": "lofi; rm -rf ~"})
    argv, kwargs = launched[0]
    assert response["status"] == "playing"
    assert argv[-1] == "ytdl://ytsearch1:lofi; rm -rf ~"
    assert kwargs.get("shell") is not True and kwargs["start_new_session"] is True


async def test_media_control_rejects_unknown_action(runner):
    assert "action must be" in (await runner.call("media_control", {"action": "explode"}))["error"]


async def test_media_control_falls_back_to_signals_without_playerctl(runner, monkeypatch):
    sent = []
    monkeypatch.setattr(tools_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(type(runner.music), "running", property(lambda self: True))
    monkeypatch.setattr(runner.music, "signal", lambda sig: sent.append(sig) or True)
    assert (await runner.call("media_control", {"action": "pause"}))["status"] == "pause"
    assert (await runner.call("media_control", {"action": "play"}))["status"] == "play"
    assert sent == [signal.SIGSTOP, signal.SIGCONT]


async def test_open_app_fallback_does_not_capture_child_output(runner, monkeypatch):
    seen = {}
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)
    monkeypatch.setattr(tools_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    def fake_run(argv, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(argv, 0, None, None)
    monkeypatch.setattr(tools_mod.subprocess, "run", fake_run)
    assert (await runner.call("open_app", {"command": "kitty"}))["status"] == "launched"
    assert "capture_output" not in seen and seen["stdout"] is subprocess.DEVNULL


def test_music_signal_tolerates_process_that_already_exited(runner, monkeypatch):
    class Gone:
        pid = 999999
        def poll(self): return None
    runner.music._proc = Gone()
    monkeypatch.setattr(tools_mod.os, "killpg", lambda *a: (_ for _ in ()).throw(ProcessLookupError()))
    assert runner.music.signal(signal.SIGSTOP) is False


# -- memory tools -------------------------------------------------------------

@pytest.fixture
def memory_runner(policy, tmp_path):
    from jarvis.memory import MemoryStore
    return ToolRunner(policy, memory=MemoryStore(tmp_path / "memory.md"))


def test_memory_tools_are_declared():
    assert {"remember", "recall", "forget"} <= {d.name for d in function_declarations()}


async def test_remember_recall_forget_through_the_tool_runner(memory_runner):
    assert (await memory_runner.call("remember", {"fact": "drinks tea"}))["status"] == "remembered"
    assert (await memory_runner.call("recall", {"query": "tea"}))["facts"][0].endswith("drinks tea")
    assert (await memory_runner.call("recall", {}))["facts"]
    assert (await memory_runner.call("forget", {"query": "tea"}))["forgot"]


async def test_memory_tools_without_a_store_explain_themselves(runner):
    assert "memory is disabled" in (await runner.call("remember", {"fact": "x"}))["error"]


def test_openai_tools_mirror_the_declarations_with_json_schema_types():
    from jarvis.tools import DECLARATIONS, openai_tools

    tools = openai_tools()
    assert [t["function"]["name"] for t in tools] == [d["name"] for d in DECLARATIONS]
    shell = next(t for t in tools if t["function"]["name"] == "run_shell")["function"]["parameters"]
    assert shell["type"] == "object" and shell["properties"]["command"]["type"] == "string"
    assert shell["required"] == ["command"]
    ending = next(t for t in tools if t["function"]["name"] == "end_conversation")["function"]["parameters"]
    assert ending == {"type": "object", "properties": {}}
    media = next(t for t in tools if t["function"]["name"] == "media_control")["function"]["parameters"]
    assert "play-pause" in media["properties"]["action"]["enum"]


# -- type_text: dictation into whatever window has focus ---------------------------

@pytest.fixture
def typing(monkeypatch):
    """Fake subprocess.run + which; returns the list of argv that were executed."""
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(tools_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(tools_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    return calls


async def test_type_text_uses_wtype_on_wayland_without_a_shell(runner, typing):
    result = await runner.call("type_text", {"text": "- hello; rm -rf ~ $(x)"})
    assert result["status"] == "typed" and result["characters"] == 22
    assert typing == [["wtype", "--", "- hello; rm -rf ~ $(x)"]]  # one argv element: never shell-parsed


async def test_type_text_uses_xdotool_on_x11(runner, typing, monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    await runner.call("type_text", {"text": "salut"})
    assert typing[0][:2] == ["xdotool", "type"] and typing[0][-1] == "salut"


async def test_type_text_falls_back_to_ydotool(runner, typing, monkeypatch):
    monkeypatch.setattr(tools_mod.shutil, "which", lambda name: "/usr/bin/ydotool" if name == "ydotool" else None)
    await runner.call("type_text", {"text": "hi"})
    assert typing == [["ydotool", "type", "--", "hi"]]


async def test_type_text_can_press_enter_afterwards(runner, typing):
    await runner.call("type_text", {"text": "ls", "press_enter": True})
    assert typing == [["wtype", "--", "ls"], ["wtype", "-k", "Return"]]


async def test_type_text_does_not_press_enter_by_default(runner, typing):
    await runner.call("type_text", {"text": "ls"})
    assert len(typing) == 1


async def test_type_text_without_a_typing_tool_says_what_to_install(runner, typing, monkeypatch):
    monkeypatch.setattr(tools_mod.shutil, "which", lambda name: None)
    assert "wtype" in (await runner.call("type_text", {"text": "x"}))["error"]
    assert typing == []


@pytest.mark.parametrize("text", ["", "   ", "x" * 2001])
async def test_type_text_rejects_empty_or_huge_input(runner, typing, text):
    assert "error" in await runner.call("type_text", {"text": text})
    assert typing == []


async def test_type_text_reports_a_failing_typer(runner, monkeypatch):
    monkeypatch.setattr(tools_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setattr(tools_mod.subprocess, "run",
                        lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "compositor does not support virtual keyboard"))
    assert "virtual keyboard" in (await runner.call("type_text", {"text": "x"}))["error"]


async def test_type_text_audit_log_records_length_never_the_text(runner, typing, policy):
    await runner.call("type_text", {"text": "my secret passphrase"})
    log = policy.log_path.read_text()
    assert "type_text" in log and "20 chars" in log and "passphrase" not in log
