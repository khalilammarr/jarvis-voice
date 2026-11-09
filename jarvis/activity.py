"""Day awareness: how long each app had focus today, from Hyprland's event socket.

Only the window *class* (kitty, brave-browser, code) is kept. Titles and URLs are
discarded the moment an event is parsed: they can name bug-bounty targets, and this
summary is sent to Gemini. Durations use the monotonic clock, which stops while the
laptop is suspended, so a night asleep isn't counted as time in the last app.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

from jarvis.fileio import write_atomic

log = logging.getLogger(__name__)

KEEP_DAYS = 14
SUMMARY_APPS = 6
FLUSH_EVERY_S = 60.0
RETRY_S = 30.0
_EVENT = "activewindow>>"
_UNSAFE = re.compile(r"[^\w.\-]")
_DAY_FILE = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")


def parse_event(line: str) -> str | None:
    """App class from an ``activewindow>>class,title`` event; None for other events."""
    if not line.startswith(_EVENT):
        return None
    app = _UNSAFE.sub("", line[len(_EVENT):].split(",", 1)[0])[:64]
    return app or "desktop"


def format_duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return "<1m"
    hours, minutes = divmod(minutes, 60)
    if not hours:
        return f"{minutes}m"
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


def socket_path() -> Path | None:
    runtime, signature = os.environ.get("XDG_RUNTIME_DIR"), os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not runtime or not signature:
        return None
    return Path(runtime) / "hypr" / signature / ".socket2.sock"


class DayLog:
    """Per-app focus totals for today, persisted as ``<folder>/YYYY-MM-DD.json``."""

    def __init__(self, folder: Path, monotonic: Callable[[], float],
                 today: Callable[[], date] = date.today):
        self._folder, self._monotonic, self._today = folder, monotonic, today
        self._day = today()
        self._totals = self._load(self._day)
        self.current: str | None = None
        self._since = monotonic()

    def _file(self, day: date) -> Path:
        return self._folder / f"{day.isoformat()}.json"

    def _load(self, day: date) -> dict[str, float]:
        try:
            data = json.loads(self._file(day).read_text())
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k): float(v) for k, v in data.items() if isinstance(v, (int, float))}

    def _close_segment(self) -> None:
        now = self._monotonic()
        if self.current is not None:
            elapsed = max(0.0, now - self._since)
            self._totals = {**self._totals, self.current: self._totals.get(self.current, 0.0) + elapsed}
        self._since = now
        today = self._today()
        if today > self._day:  # (never on a clock jump backwards) the segment that crossed midnight stays with yesterday
            self._write()
            self._day, self._totals = today, {}

    def focus(self, app: str | None) -> None:
        self._close_segment()
        self.current = app

    def totals(self) -> dict[str, float]:
        self._close_segment()
        return dict(self._totals)

    def summary(self) -> str:
        ranked = sorted(self.totals().items(), key=lambda item: item[1], reverse=True)
        return ", ".join(f"{app} {format_duration(s)}" for app, s in ranked[:SUMMARY_APPS] if s >= 60)

    def flush(self) -> None:
        self._close_segment()
        self._write()

    def _write(self) -> None:
        write_atomic(self._file(self._day), json.dumps(
            {app: round(seconds) for app, seconds in self._totals.items()}, sort_keys=True))
        self._prune()

    def _prune(self) -> None:
        cutoff = self._day - timedelta(days=KEEP_DAYS)
        for path in self._folder.glob("*.json"):
            if _DAY_FILE.match(path.name) and path.stem < cutoff.isoformat():
                path.unlink(missing_ok=True)


async def _flush_periodically(daylog: DayLog, every_s: float) -> None:
    while True:
        await asyncio.sleep(every_s)
        try:
            daylog.flush()
        except OSError as exc:  # full disk etc.: keep tracking in memory, try again later
            log.warning("could not save activity log: %s", exc)


async def _follow(daylog: DayLog, path: Path) -> None:
    reader, writer = await asyncio.open_unix_connection(str(path))
    try:
        while line := await reader.readline():
            app = parse_event(line.decode("utf-8", "replace").rstrip("\n"))
            if app is not None:
                daylog.focus(app)
    finally:
        writer.close()


async def track(daylog: DayLog, path: Path, initial: Callable[[], str | None],
                flush_every_s: float = FLUSH_EVERY_S, retry_s: float = RETRY_S) -> None:
    """Follow focus changes forever. Survives Hyprland restarts and a missing socket."""
    flusher = asyncio.create_task(_flush_periodically(daylog, flush_every_s))
    try:
        while True:
            try:
                daylog.focus(await asyncio.to_thread(initial))
                await _follow(daylog, path)
            except (OSError, ValueError) as exc:  # no socket, Hyprland restart, over-long line
                log.debug("Hyprland event socket unavailable (%s); retrying", exc)
            try:
                daylog.focus(None)
            except OSError as exc:
                log.warning("activity log: %s", exc)
            await asyncio.sleep(retry_s)
    finally:
        flusher.cancel()
        try:
            daylog.flush()
        except OSError as exc:
            log.warning("could not save activity log: %s", exc)


def active_class() -> str | None:
    """Class of the focused window right now, via hyprctl (titles are never read)."""
    import subprocess

    try:
        proc = subprocess.run(["hyprctl", "activewindow", "-j"], capture_output=True, text=True, timeout=3)
        data = json.loads(proc.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    app = data.get("class", "") if isinstance(data, dict) else ""
    if not app:
        return None
    return parse_event(_EVENT + str(app))
