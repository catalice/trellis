"""
Obsidian vault projection — the visible window into the second brain.

Write-only (version 1): Trellis writes, Cat reads. The DB is the source of
truth; edits made in Obsidian do not sync back.

Vault layout (see the _DAILY_DIR/_TASKS_PATH/_TRACKING_PATH constants):
    Calendar/Captures/<YYYY-MM-DD>.md   daily note — captures + state logs, journal-style
    Calendar/Tasks.md                   live task centre — rewritten on every change
    Calendar/Tracking.md                energy/mood/meds/sleep/cycle — rewritten on change
    Efforts/<Title>.md                  one page per effort; its captures accumulate

Daily notes and effort pages are append-only, so anything Cat writes in them
is never touched. Tasks.md and Tracking.md are Trellis-owned and rewritten wholesale.

Every public method swallows and logs failures — a vault write must never
break the bot.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from uuid import UUID

from trellis.domain_second_brain_models import (
    Capture,
    Effort,
    StateLog,
    Task,
    TaskStatus,
    TrackingEventType,
)

_log = logging.getLogger(__name__)

_TASKS_HEADER = """\
# Tasks

> Live view — managed by Trellis. Edits here won't sync back; tell Trellis instead.
"""

_TRACKING_HEADER = """\
# Tracking

> Live view — managed by Trellis. Each entry is energy/mood scored 1-5 at the \
time it was felt (e.g. `09:12 e2/m4`), so switches show as switches — no \
averaging. Meds, sleep, and cycle ride alongside.
"""

_UPCOMING_REMINDER_DAYS = 14
_RECENTLY_COMPLETED_LIMIT = 8
_TRACKING_DAYS = 14

# Vault layout — Cat's chosen structure. Change here, nowhere else.
_DAILY_DIR = "Calendar/Captures"
_TASKS_PATH = "Calendar/Tasks.md"
_TRACKING_PATH = "Calendar/Tracking.md"


class ObsidianVault:
    def __init__(
        self,
        vault: Path,
        tz: tzinfo,
        task_repo,
        reminder_repo,
        effort_repo,
        state_repo=None,
    ) -> None:
        self._vault = vault
        self._tz = tz
        self._tasks = task_repo
        self._reminders = reminder_repo
        self._efforts = effort_repo
        self._states = state_repo

    # --- Daily notes (journal) ---------------------------------------------

    def capture_saved(self, capture: Capture, tasks: tuple[Task, ...] = ()) -> None:
        try:
            local = capture.created_at.astimezone(self._tz)
            path = self._vault / _DAILY_DIR / f"{local.strftime('%Y-%m-%d')}.md"
            path.parent.mkdir(parents=True, exist_ok=True)

            entry = self._render_capture(capture, tasks, local)
            if path.exists():
                with path.open("a", encoding="utf-8") as f:
                    f.write(entry)
            else:
                title = f"# {local.strftime('%A %d %B %Y')}\n"
                path.write_text(title + entry, encoding="utf-8")
        except Exception:
            _log.warning("obsidian: daily note write failed", exc_info=True)

    def _render_capture(self, capture: Capture, tasks: tuple[Task, ...], local: datetime) -> str:
        heading = capture.summary or capture.capture_type.value.replace("_", " ")
        lines = [f"\n## {local.strftime('%H:%M')} — {heading}\n"]
        if capture.synthesis:
            lines.append(f"{capture.synthesis}\n")
        lines.append("\n> [!quote]- Raw")
        for raw_line in capture.raw.splitlines() or [""]:
            lines.append(f"> {raw_line}")
        lines.append("")
        if tasks:
            lines.append("\n**Tasks pulled out:**")
            for t in tasks:
                due = f" — due {self._fmt(t.due_at)}" if t.due_at else ""
                lines.append(f"- [ ] {t.title}{due}")
            lines.append("")
        return "\n".join(lines)

    # --- Task centre --------------------------------------------------------

    def tasks_changed(self, user_id: UUID) -> None:
        try:
            now = datetime.now(timezone.utc)
            open_tasks = self._tasks.list_open(user_id)
            recent = self._tasks.list_recent(user_id, limit=30)
            completed = [t for t in recent if t.status == TaskStatus.DONE][:_RECENTLY_COMPLETED_LIMIT]
            upcoming_reminders = self._reminders.list_upcoming(
                user_id, before=now + timedelta(days=_UPCOMING_REMINDER_DAYS)
            )

            today = now.astimezone(self._tz).date()
            overdue = [t for t in open_tasks if t.is_overdue(now)]
            due_today = [
                t for t in open_tasks
                if t.due_at and not t.is_overdue(now)
                and t.due_at.astimezone(self._tz).date() == today
            ]
            shown = {t.id for t in overdue} | {t.id for t in due_today}
            upcoming = [
                t for t in open_tasks
                if t.due_at and t.id not in shown
            ]
            anytime = [t for t in open_tasks if not t.due_at]

            parts = [_TASKS_HEADER]
            parts.append(f"_Updated {datetime.now(self._tz).strftime('%a %d %b, %H:%M')}_\n")

            if overdue:
                parts.append("## Overdue\n")
                parts.extend(f"- [ ] {t.title} — was due {self._fmt(t.due_at)}" for t in overdue)
                parts.append("")
            if due_today:
                parts.append("## Today\n")
                parts.extend(f"- [ ] {t.title} — {self._fmt_time(t.due_at)}" for t in due_today)
                parts.append("")
            if upcoming:
                parts.append("## Upcoming\n")
                parts.extend(f"- [ ] {t.title} — {self._fmt(t.due_at)}" for t in upcoming)
                parts.append("")
            if anytime:
                parts.append("## Anytime\n")
                parts.extend(f"- [ ] {t.title}" for t in anytime)
                parts.append("")
            if upcoming_reminders:
                parts.append("## Reminders\n")
                for r in upcoming_reminders:
                    recur = " (daily)" if r.recur_daily else ""
                    parts.append(f"- 🔔 {r.label} — {self._fmt(r.remind_at)}{recur}")
                parts.append("")
            if completed:
                parts.append("## Recently completed\n")
                parts.extend(
                    f"- [x] {t.title} — {self._fmt_date(t.completed_at)}" for t in completed
                )
                parts.append("")

            if not any([overdue, due_today, upcoming, anytime, upcoming_reminders, completed]):
                parts.append("Nothing on the list. Enjoy the quiet.\n")

            path = self._vault / _TASKS_PATH
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(parts), encoding="utf-8")
        except Exception:
            _log.warning("obsidian: Tasks.md write failed", exc_info=True)

    # --- Self-tracking ------------------------------------------------------

    def state_logged(self, log: StateLog) -> None:
        """Append a state line to the daily note (journal keeps the day's curve)."""
        try:
            local = log.logged_at.astimezone(self._tz)
            path = self._vault / _DAILY_DIR / f"{local.strftime('%Y-%m-%d')}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            scores = " · ".join(
                s for s in (
                    f"energy {log.energy}" if log.energy else "",
                    f"mood {log.mood}" if log.mood else "",
                ) if s
            )
            line = f"\n> [!tip] {local.strftime('%H:%M')} — {scores or 'state'}\n> {log.note}\n"
            if path.exists():
                with path.open("a", encoding="utf-8") as f:
                    f.write(line)
            else:
                title = f"# {local.strftime('%A %d %B %Y')}\n"
                path.write_text(title + line, encoding="utf-8")
        except Exception:
            _log.warning("obsidian: state daily-note write failed", exc_info=True)

    def tracking_changed(self, user_id: UUID) -> None:
        """Rewrite Tracking.md — the last two weeks at a glance."""
        if self._states is None:
            return
        try:
            now = datetime.now(self._tz)
            since = now - timedelta(days=_TRACKING_DAYS)
            states = self._states.list_states_since(user_id, since=since)
            events = self._states.list_events_since(user_id, since=since)
            period_start = self._states.last_period_start(user_id)

            by_day_states: dict = {}
            for s in states:
                by_day_states.setdefault(s.felt_at.astimezone(self._tz).date(), []).append(s)
            by_day_events: dict = {}
            for e in events:
                by_day_events.setdefault(e.occurred_at.astimezone(self._tz).date(), []).append(e)

            parts = [_TRACKING_HEADER]
            parts.append(f"_Updated {now.strftime('%a %d %b, %H:%M')}_\n")

            days = sorted(set(by_day_states) | set(by_day_events), reverse=True)
            if not days:
                parts.append("No entries yet. Just tell Trellis how you're doing.\n")
            for day in days:
                day_states = sorted(
                    by_day_states.get(day, []),
                    key=lambda s: s.felt_at,
                )
                day_events = by_day_events.get(day, [])
                line = f"**{day.strftime('%a %d %b')}**"
                timeline = _timeline(day_states, self._tz)
                if timeline:
                    line += f"  `{timeline}`"

                extras = []
                for e in day_events:
                    if e.event_type == TrackingEventType.MEDS:
                        t = e.occurred_at.astimezone(self._tz).strftime("%H:%M")
                        extras.append(f"{e.detail or 'meds'} {t}")
                    elif e.event_type == TrackingEventType.SLEEP:
                        bits = []
                        if e.value is not None:
                            bits.append(f"{e.value:g}h")
                        if e.detail:
                            bits.append(e.detail)
                        extras.append("slept " + " ".join(bits) if bits else "sleep logged")
                    elif e.event_type == TrackingEventType.PERIOD_START:
                        extras.append("period started")
                    elif e.event_type == TrackingEventType.PERIOD_END:
                        extras.append("period ended")
                if period_start is not None:
                    delta = (day - period_start.occurred_at.astimezone(self._tz).date()).days
                    if 0 <= delta < 60:
                        extras.append(f"cycle d{delta + 1}")
                if extras:
                    line += "  — " + ", ".join(extras)
                parts.append(line)

                for s in day_states:
                    t = s.felt_at.astimezone(self._tz).strftime("%H:%M")
                    retro = ""
                    if s.felt_at.date() != s.logged_at.date():
                        retro = f" _(logged {s.logged_at.astimezone(self._tz).strftime('%d %b')})_"
                    parts.append(f"  - {t} — {s.note}{retro}")
                parts.append("")

            path = self._vault / _TRACKING_PATH
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(parts), encoding="utf-8")
        except Exception:
            _log.warning("obsidian: Tracking.md write failed", exc_info=True)

    # --- Effort pages -------------------------------------------------------

    def effort_created(self, effort: Effort) -> None:
        try:
            path = self._effort_path(effort)
            if path is None or path.exists():
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            lines = [f"# {effort.title}\n", f"_Intensity: {effort.intensity.value}_\n"]
            if effort.notes:
                lines.append(f"{effort.notes}\n")
            lines.append("## Captures\n")
            path.write_text("\n".join(lines), encoding="utf-8")
        except Exception:
            _log.warning("obsidian: effort page write failed", exc_info=True)

    def capture_assigned(self, capture: Capture) -> None:
        if capture.effort_id is None:
            return
        try:
            effort = self._efforts.get(capture.effort_id)
            if effort is None:
                return
            path = self._effort_path(effort)
            if path is None:
                return
            if not path.exists():
                self.effort_created(effort)
            local = capture.created_at.astimezone(self._tz)
            summary = capture.summary or capture.raw[:80]
            date_link = local.strftime("%Y-%m-%d")
            line = f"- [[{date_link}]] — {summary}\n"
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            _log.warning("obsidian: effort capture append failed", exc_info=True)

    # --- Helpers ------------------------------------------------------------

    def _effort_path(self, effort: Effort) -> Path | None:
        if not effort.obsidian_path:
            return None
        return self._vault / effort.obsidian_path

    def _fmt(self, dt: datetime | None) -> str:
        if dt is None:
            return ""
        local = dt.astimezone(self._tz)
        if local.hour == 0 and local.minute == 0:
            return local.strftime("%a %d %b")
        return local.strftime("%a %d %b %H:%M")

    def _fmt_time(self, dt: datetime | None) -> str:
        return dt.astimezone(self._tz).strftime("%H:%M") if dt else ""

    def _fmt_date(self, dt: datetime | None) -> str:
        return dt.astimezone(self._tz).strftime("%a %d %b") if dt else ""


def _timeline(states: list, tz: tzinfo) -> str:
    """The day's actual curve: each scored entry at its felt time, in order,
    e.g. "09:12 e2/m4 → 12:54 e1/m1". No averaging — a switch shows as a switch."""
    points = []
    for s in states:
        scores = "/".join(
            p for p in (
                f"e{s.energy}" if s.energy else "",
                f"m{s.mood}" if s.mood else "",
            ) if p
        )
        if not scores:
            continue
        points.append(f"{s.felt_at.astimezone(tz).strftime('%H:%M')} {scores}")
    return " → ".join(points)
