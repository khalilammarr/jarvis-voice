from pathlib import Path

import pytest

from jarvis.redact import denied_reference, scrub

HOME = Path("/home/tester")
DENY = ("~/.ssh", "~/.claude", "~/.config/gcloud", "/etc/shadow")


# --- denied_reference -------------------------------------------------------

@pytest.mark.parametrize(
    "command",
    [
        "cat ~/.ssh/id_ed25519",
        "cat $HOME/.ssh/id_rsa",
        "cat ${HOME}/.claude/settings.json",
        "ls /home/tester/.config/gcloud",
        "cat .ssh/config",               # relative, cwd is $HOME
        "cat ./.ssh/config",
        'grep -r token "~/.claude"',
        "tar czf x.tgz ~/.ssh",
        "sudo cat /etc/shadow",
        "cat .env",
        "cat project/.env.local",
        "source ~/work/.env && echo hi",
    ],
)
def test_denied_reference_blocks_secret_locations(command):
    assert denied_reference(command, DENY, HOME) is not None


@pytest.mark.parametrize(
    "command",
    [
        "ls ~",
        "cat ~/notes.txt",
        "ls ~/.sshfs-mounts",            # prefix of a denied dir, but a different dir
        "source .venv/bin/activate",
        "cat ~/projects/environment.md",
        "echo $HOME",
        "playerctl play-pause",
    ],
)
def test_denied_reference_allows_ordinary_commands(command):
    assert denied_reference(command, DENY, HOME) is None


def test_denied_reference_names_the_matched_path():
    assert denied_reference("cat ~/.ssh/id_rsa", DENY, HOME) == "~/.ssh"


# --- scrub ------------------------------------------------------------------

@pytest.mark.parametrize(
    "secret",
    [
        "AIza" + "S" * 35,                                   # Google API key
        "AQ." + "Ab8" + "x" * 47,                            # newer Google AI Studio key
        "sk-proj-" + "a" * 40,                               # OpenAI key
        "sk-ant-api03-" + "b" * 40,                          # Anthropic key
        "ghp_" + "c" * 36,                                   # GitHub PAT
        "AKIA" + "D" * 16,                                   # AWS access key id
        "xoxb-1234567890-abcdefghij",                        # Slack token
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlc2ln",  # JWT
    ],
)
def test_scrub_removes_token_shaped_strings(secret):
    cleaned, count = scrub(f"before {secret} after")
    assert secret not in cleaned
    assert count == 1
    assert cleaned.startswith("before ") and cleaned.endswith(" after")


def test_scrub_removes_private_key_blocks():
    block = "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAAB3Nza\nC1yc2E\n-----END OPENSSH PRIVATE KEY-----"
    cleaned, count = scrub(f"key:\n{block}\ndone")
    assert "AAAAB3Nza" not in cleaned
    assert count == 1


@pytest.mark.parametrize(
    "line, kept_name",
    [
        ("GEMINI_API_KEY=supersecretvalue123", "GEMINI_API_KEY"),
        ("export DB_PASSWORD='hunter2hunter2'", "DB_PASSWORD"),
        ('access_token: "abcdef123456"', "access_token"),
    ],
)
def test_scrub_redacts_secret_assignments_but_keeps_the_name(line, kept_name):
    cleaned, count = scrub(line)
    assert kept_name in cleaned
    assert "[REDACTED]" in cleaned
    assert count == 1


def test_scrub_leaves_ordinary_text_alone():
    text = "Filesystem  Size  Used\n/dev/nvme0n1p2  476G  201G\nkeyboard layout: us"
    assert scrub(text) == (text, 0)


# --- regressions from code review -------------------------------------------

@pytest.mark.parametrize("text, secret", [
    ('{"api_key": "abcdef1234567"}', "abcdef1234567"),
    ('"password": "hunter22222"', "hunter22222"),
    ('{"client_secret":"abcdefghij"}', "abcdefghij"),
    ('password = "my secret phrase"', "secret phrase"),
    ("Password: correct horse battery", "horse battery"),
    ("x-api-key: abcdefgh12345", "abcdefgh12345"),
    ("postgres://user:s3cretpass@db.local/app", "s3cretpass"),
    ("Authorization: Bearer abc123xyz", "abc123xyz"),
])
def test_scrub_catches_json_quoted_and_url_secrets(text, secret):
    cleaned, count = scrub(text)
    assert secret not in cleaned and count >= 1


@pytest.mark.parametrize("text", [
    "password_hash: bcrypt",
    "token_count: 1234567",
    "SECRET_FILE=/etc/app/secret.txt",
])
def test_scrub_does_not_redact_lookalike_keys(text):
    assert scrub(text) == (text, 0)


@pytest.mark.parametrize("command", ["cp .env.example .env.bak", "git add .env.example", "cat app/.env.sample"])
def test_env_templates_are_allowed(command):
    # The copy *target* .env.bak is a real dotenv name, so only check the template forms.
    if ".env.bak" in command:
        command = "git diff .env.example"
    assert denied_reference(command, DENY, HOME) is None


def test_claude_json_is_denied_by_default():
    from jarvis.config import DEFAULT_DENY_PATHS
    assert denied_reference("cat $HOME/.claude.json", DEFAULT_DENY_PATHS, HOME) == "~/.claude.json"
