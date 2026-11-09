import json
from pathlib import Path

import pytest

from jarvis.shell import ShellPolicy, catastrophic_reason, run_shell

HOME = Path("/home/tester")


# --- catastrophic_reason ----------------------------------------------------

@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "rm -rf /*",
        "rm -fr ~",
        "rm -r -f ~/",
        "rm -rf $HOME",
        'rm -rf "$HOME"/*',
        "sudo rm -rf --no-preserve-root /",
        "cd /tmp && rm -Rf /usr",
        "mkfs.ext4 /dev/sda1",
        "sudo mkfs -t btrfs /dev/nvme0n1p3",
        "dd if=/dev/zero of=/dev/sda bs=1M",
        "dd if=arch.iso of=/dev/nvme0n1",
        "cat /dev/urandom > /dev/sda",
        "wipefs -a /dev/sdb",
        ":(){ :|:& };:",
    ],
)
def test_guard_blocks_unrecoverable_operations(command):
    assert catastrophic_reason(command, HOME) is not None


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf ~/Downloads/old-build",
        "rm -rf ./node_modules",
        "rm /tmp/file.txt",
        "rm -rf /tmp/scratch",
        "dd if=/dev/zero of=./disk.img bs=1M count=10",
        "dd if=/dev/urandom of=/dev/null count=1",
        "echo hi > /dev/null",
        "ls -la /",
        "grep -r 'rm -rf /' notes.md",
    ],
)
def test_guard_allows_ordinary_commands(command):
    assert catastrophic_reason(command, HOME) is None


# --- run_shell --------------------------------------------------------------

def _policy(tmp_path, **overrides):
    defaults = dict(
        guard=True,
        deny_paths=("~/.ssh",),
        timeout_s=5.0,
        max_output_chars=2_000,
        log_path=tmp_path / "commands.log",
        home=tmp_path,
    )
    return ShellPolicy(**{**defaults, **overrides})


def _log_events(policy):
    return [json.loads(line) for line in policy.log_path.read_text().splitlines()]


def test_run_shell_returns_output_and_exit_code(tmp_path):
    result = run_shell("echo hello; echo oops >&2; exit 3", _policy(tmp_path))
    assert result.exit_code == 3
    assert "hello" in result.output and "oops" in result.output
    assert result.refused is None


def test_run_shell_runs_in_home_directory(tmp_path):
    result = run_shell("pwd", _policy(tmp_path))
    assert result.output.strip() == str(tmp_path)


def test_run_shell_refuses_guarded_command_without_running_it(tmp_path):
    marker = tmp_path / "ran"
    result = run_shell(f"touch {marker}; rm -rf /", _policy(tmp_path))
    assert result.refused and "guard" in result.refused
    assert not marker.exists()


def test_guard_can_be_disabled(tmp_path):
    # `false &&` short-circuits, so mkfs never actually executes.
    command = "false && mkfs.ext4 /dev/sdz"
    assert run_shell(command, _policy(tmp_path)).refused
    result = run_shell(command, _policy(tmp_path, guard=False))
    assert result.refused is None


def test_run_shell_refuses_denied_path(tmp_path):
    result = run_shell("cat ~/.ssh/id_rsa", _policy(tmp_path))
    assert result.refused and "~/.ssh" in result.refused


def test_run_shell_scrubs_secrets_from_output(tmp_path):
    key = "AIza" + "Q" * 35
    result = run_shell(f"echo {key}", _policy(tmp_path))
    assert key not in result.output
    assert result.redactions == 1


def test_run_shell_truncates_long_output(tmp_path):
    result = run_shell("seq 1 100000", _policy(tmp_path, max_output_chars=500))
    assert len(result.output) < 700
    assert "truncated" in result.output
    assert result.output.lstrip().startswith("1\n")      # keeps the head…
    assert result.output.rstrip().endswith("100000")    # …and the tail


def test_run_shell_times_out_and_kills_the_process_group(tmp_path):
    marker = tmp_path / "survived"
    result = run_shell(f"(sleep 3; touch {marker}) & sleep 30", _policy(tmp_path, timeout_s=0.5))
    assert result.exit_code is None
    assert "timed out" in result.output
    import time; time.sleep(3.5)
    assert not marker.exists(), "background child outlived the timeout"


def test_every_command_is_audit_logged_without_its_output(tmp_path):
    policy = _policy(tmp_path)
    run_shell("echo visible-output", policy)
    run_shell("cat ~/.ssh/id_rsa", policy)
    events = _log_events(policy)
    assert [e["command"] for e in events] == ["echo visible-output", "cat ~/.ssh/id_rsa"]
    assert events[0]["exit_code"] == 0
    assert events[1]["refused"]
    assert all("output" not in event for event in events)


def test_log_directory_is_private(tmp_path):
    policy = _policy(tmp_path, log_path=tmp_path / "state" / "jarvis" / "commands.log")
    run_shell("true", policy)
    assert (policy.log_path.parent.stat().st_mode & 0o077) == 0


# --- regressions from code review -------------------------------------------

@pytest.mark.parametrize("command", [
    "echo hi\nrm -rf ~",                      # multi-line scripts
    "if true; then rm -rf ~; fi",
    "for i in 1; do rm -rf ~; done",
    "{ rm -rf ~; }",
    "! rm -rf ~",
    "sudo -u root rm -rf /",                  # option that takes an argument
    "env -u FOO rm -rf ~",
    "rm -rf ~/.*",
    "rm -rf /./",
    "rm -rf /usr/../",
    "rm -rf ..",                              # cwd is $HOME, so .. is /home
    "sudo rm -rf /var/lib",
    "bash -c 'rm -rf ~'",
    'sh -c "rm -rf /"',
])
def test_guard_blocks_review_bypasses(command):
    assert catastrophic_reason(command, HOME) is not None


@pytest.mark.parametrize("command", [
    "cd /tmp/build && rm -rf .",              # . after cd is not $HOME
    "cd ~/project; rm -rf ./dist ..",         # .. of ~/project is $HOME… blocked below
][:1])
def test_guard_tracks_cd_for_relative_targets(command):
    assert catastrophic_reason(command, HOME) is None


def test_guard_blocks_rm_dot_in_home():
    assert catastrophic_reason("rm -rf .", HOME) is not None
    assert catastrophic_reason("cd ~/project && rm -rf ..", HOME) is not None
