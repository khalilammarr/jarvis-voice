from datetime import date, datetime

from jarvis.profile import DRAFT_PROFILE, BriefingGate, ensure_profile, load_profile, write_state


def test_ensure_profile_writes_a_draft_once(tmp_path):
    path = tmp_path / "jarvis" / "me.md"
    assert ensure_profile(path) is True
    assert "About me" in path.read_text() and "Browser:" in path.read_text()
    path.write_text("my own words")
    assert ensure_profile(path) is False
    assert path.read_text() == "my own words"


def test_load_profile_drops_html_comments_and_blank_runs(tmp_path):
    path = tmp_path / "me.md"
    path.write_text("# Me\n<!-- hint for the user\nspanning lines -->\n\n\n\nName: K\n")
    assert load_profile(path) == "# Me\n\nName: K"


def test_load_profile_is_capped_and_tolerates_missing_file(tmp_path):
    path = tmp_path / "me.md"
    assert load_profile(path) == ""
    path.write_text("x" * 10_000)
    assert len(load_profile(path, max_chars=100)) == 100


def test_load_profile_scrubs_secrets(tmp_path):
    path = tmp_path / "me.md"
    path.write_text("token: ghp_" + "a" * 40)
    assert "ghp_" not in load_profile(path)


def test_draft_profile_never_contains_placeholders_for_secrets():
    assert "password" not in DRAFT_PROFILE.lower()


def test_briefing_gate_fires_once_per_day(tmp_path):
    gate = BriefingGate(tmp_path / "state" / "last-briefing")
    today = date(2026, 11, 7)
    assert gate.due(today)
    gate.mark(today)
    assert not gate.due(today)
    assert gate.due(date(2026, 11, 8))


def test_briefing_gate_ignores_garbage_state(tmp_path):
    path = tmp_path / "last-briefing"
    path.write_text("yesterday-ish")
    assert BriefingGate(path).due(date(2026, 11, 7))


def test_write_state_is_atomic_and_tolerates_missing_runtime_dir(tmp_path):
    path = tmp_path / "run" / "jarvis" / "state"
    write_state(path, "listening")
    assert path.read_text() == "listening\n"
    write_state(path, "talking")
    assert path.read_text() == "talking\n"
    write_state(None, "talking")  # no XDG_RUNTIME_DIR: silently skipped


def test_write_atomic_follows_symlinks_instead_of_replacing_them(tmp_path):
    from jarvis.fileio import write_atomic
    real = tmp_path / "dotfiles" / "me.md"
    real.parent.mkdir()
    real.write_text("old")
    link = tmp_path / "me.md"
    link.symlink_to(real)
    write_atomic(link, "new")
    assert link.is_symlink() and real.read_text() == "new"
