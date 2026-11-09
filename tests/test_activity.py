import asyncio
import json
from datetime import date, datetime

import pytest

from jarvis.activity import DayLog, format_duration, parse_event, track


class Clock:
    def __init__(self):
        self.mono = 1000.0
        self.day = date(2026, 11, 7)

    def advance(self, seconds):
        self.mono += seconds


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def log(tmp_path, clock):
    return DayLog(tmp_path / "activity", monotonic=lambda: clock.mono, today=lambda: clock.day)


@pytest.mark.parametrize("line, expected", [
    ("activewindow>>kitty,~/chatgt — nvim", "kitty"),
    ("activewindow>>brave-browser,Example Portal | private-project.internal report", "brave-browser"),
    ("activewindow>>,", "desktop"),
    ("activewindowv2>>0x55d", None),
    ("workspace>>2", None),
    ("activewindow>>we\x1bird$(cls),t", "weirdcls"),
])
def test_parse_event_keeps_only_a_sanitised_class_never_the_title(line, expected):
    assert parse_event(line) == expected


@pytest.mark.parametrize("seconds, text", [(30, "<1m"), (60, "1m"), (3_900, "1h 5m"), (7_200, "2h")])
def test_format_duration(seconds, text):
    assert format_duration(seconds) == text


def test_time_is_attributed_to_the_focused_app(log, clock):
    log.focus("kitty")
    clock.advance(600)
    log.focus("brave-browser")
    clock.advance(120)
    totals = log.totals()
    assert totals == {"kitty": 600, "brave-browser": 120}  # includes the still-open segment


def test_summary_ranks_apps_by_time(log, clock):
    for app, seconds in [("code", 300), ("kitty", 3_900), ("brave-browser", 1_200)]:
        log.focus(app)
        clock.advance(seconds)
    log.focus("desktop")
    assert log.summary() == "kitty 1h 5m, brave-browser 20m, code 5m"


def test_summary_is_empty_before_any_activity(log):
    assert log.summary() == ""


def test_flush_persists_today_and_a_new_daylog_resumes_it(tmp_path, log, clock):
    log.focus("kitty")
    clock.advance(300)
    log.flush()
    saved = json.loads((tmp_path / "activity" / "2026-11-07.json").read_text())
    assert saved == {"kitty": 300}
    resumed = DayLog(tmp_path / "activity", monotonic=lambda: clock.mono, today=lambda: clock.day)
    assert resumed.totals() == {"kitty": 300}


def test_flush_never_writes_window_titles(tmp_path, log, clock):
    log.focus(parse_event("activewindow>>brave-browser,victim.example.com admin panel"))
    clock.advance(60)
    log.flush()
    assert "victim" not in (tmp_path / "activity" / "2026-11-07.json").read_text()


def test_a_new_day_starts_fresh_totals(log, clock):
    log.focus("kitty")
    clock.advance(300)
    clock.day = date(2026, 11, 8)
    log.focus("code")
    clock.advance(60)
    assert log.totals() == {"code": 60}


def test_old_day_files_are_pruned(tmp_path, clock):
    folder = tmp_path / "activity"
    folder.mkdir()
    (folder / "2026-10-01.json").write_text("{}")
    (folder / "2026-11-06.json").write_text("{}")
    (folder / "notes.txt").write_text("keep me")
    DayLog(folder, monotonic=lambda: clock.mono, today=lambda: clock.day).flush()
    assert sorted(p.name for p in folder.iterdir()) == ["2026-11-06.json", "2026-11-07.json", "notes.txt"]


def test_corrupt_day_file_is_ignored(tmp_path, clock):
    folder = tmp_path / "activity"
    folder.mkdir()
    (folder / "2026-11-07.json").write_text("not json")
    assert DayLog(folder, monotonic=lambda: clock.mono, today=lambda: clock.day).totals() == {}


async def test_track_reads_focus_events_from_the_hyprland_socket(tmp_path, log, clock):
    path = tmp_path / "s.sock"

    async def serve(reader, writer):
        writer.write(b"workspace>>1\nactivewindow>>kitty,secret title\n")
        await writer.drain()
        await asyncio.sleep(3600)

    server = await asyncio.start_unix_server(serve, path=str(path))
    task = asyncio.create_task(track(log, path, initial=lambda: "code", flush_every_s=0.05))
    for _ in range(100):
        await asyncio.sleep(0.01)
        if log.current == "kitty":
            break
    task.cancel()
    server.close()
    assert log.current == "kitty"
    assert "code" in log.totals()


async def test_track_retries_when_the_socket_is_missing(tmp_path, log):
    task = asyncio.create_task(track(log, tmp_path / "missing.sock", initial=lambda: None,
                                     flush_every_s=0.05, retry_s=0.01))
    await asyncio.sleep(0.1)
    assert not task.done()  # keeps retrying instead of crashing Jarvis
    task.cancel()


def test_clock_going_backwards_does_not_reset_today(log, clock):
    log.focus("kitty")
    clock.advance(300)
    clock.day = date(2026, 11, 6)
    log.focus("code")
    assert log.totals()["kitty"] == 300


async def test_tracker_survives_disk_errors(tmp_path, log, monkeypatch):
    calls = {"n": 0}
    def broken_flush():
        calls["n"] += 1
        raise OSError("disk full")
    monkeypatch.setattr(log, "flush", broken_flush)
    task = asyncio.create_task(track(log, tmp_path / "missing.sock", initial=lambda: None,
                                     flush_every_s=0.01, retry_s=0.01))
    await asyncio.sleep(0.1)
    assert not task.done() and calls["n"] >= 2  # flusher keeps going
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def test_active_class_tolerates_odd_hyprctl_output(monkeypatch):
    import subprocess
    from jarvis import activity
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "null", ""))
    assert activity.active_class() is None
