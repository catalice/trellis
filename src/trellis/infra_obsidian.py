"""
Obsidian vault projection — the visible window into the second brain.

Write-only (version 1): Trellis writes, the user reads. The DB is the source of
truth; edits made in Obsidian do not sync back.

Vault layout (see the path constants below):
    Calendar/Captures/<YYYY-MM-DD>.md   the day's log — captures + receipts, append-only
    Calendar/Tasks.md                   todos + parked — rewritten on every change
    Calendar/Seeds.md                   the exploration menu — rewritten on change
    Calendar/Tracking/Recent.md         last 14 days, every entry — rewritten on change
    Calendar/Tracking/History/<YYYY-MM>.md  full detail, month by month
    Calendar/Tracking/Overview.base     all-days table over daily-note properties
    Calendar/Training/Training.md       arc, baseline, this week with runs ticked
    Calendar/Training/Weeks/<YYYY-Www>.md   week archive: planned vs run
    Efforts/<Title>.md                  one page per effort; its captures accumulate

Capture notes hold inputs and one-line receipts of what each input produced —
records of events, never live data (data echoes go stale; receipts stay true).
Current truth lives only in the rewritten views. Capture notes and effort pages
are append-only: anything the user writes there is never touched.

Every public method swallows and logs failures — a vault write must never
break the bot.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from uuid import UUID

from trellis.domain_focus_models import (
    Capture,
    Effort,
    Task,
    TaskKind,
    TaskStatus,
)
from trellis.domain_sense_models import StateLog, TrackingEventType

_log = logging.getLogger(__name__)


def _range_or_value(scores: list) -> "str | int":
    """A day's 1-5 scores as a single value or an en-dash range: 3, or "2–4"."""
    lo, hi = min(scores), max(scores)
    return lo if lo == hi else f"{lo}–{hi}"


def _title_from_daily_path(path: Path) -> str | None:
    """Daily-note title from its YYYY-MM-DD filename; None for non-daily paths."""
    try:
        return date.fromisoformat(path.stem).strftime("%A %d %B %Y")
    except ValueError:
        return None

_TASKS_HEADER = """\
# Tasks

> Live view — managed by Trellis. Edits here won't sync back; tell Trellis instead.
"""

_SEEDS_HEADER = """\
# Seeds

> Things planted with zero obligation. Some grow into Efforts, some don't, \
none of them nag. Ask Trellis "what could I explore?" on a day with room in it.
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

# Vault layout — the user's chosen structure. Change here, nowhere else.
_DAILY_DIR = "Calendar/Captures"
_TASKS_PATH = "Calendar/Tasks.md"
_SEEDS_PATH = "Calendar/Seeds.md"
_TRACKING_PATH = "Calendar/Tracking/Recent.md"
_TRACKING_BASE_PATH = "Calendar/Tracking/Overview.base"
_TRAINING_PLAN_PATH = "Calendar/Training/Training.md"
_TRACKING_HISTORY_DIR = "Calendar/Tracking/History"
_TRAINING_WEEKS_DIR = "Calendar/Training/Weeks"
_WATCHER_PATH = "Atlas/Watcher.md"

# Written ONCE if absent, then never touched — it's the user's file to tweak in
# Obsidian's Bases UI. It turns the daily notes' frontmatter into a sortable
# table of every day ever (the long view; Tracking.md stays the 14-day glance).
_TRACKING_BASE_CONTENT = """\
filters:
  and:
    - file.inFolder("Calendar/Captures")
views:
  - type: table
    name: All days
    order:
      - file.name
      - energy
      - mood
      - sleep_hours
      - meds
      - cycle_day
      - entries
    sort:
      - property: file.name
        direction: DESC
"""


class ObsidianVault:
    def __init__(
        self,
        vault: Path,
        tz: tzinfo,
        task_repo,
        reminder_repo,
        effort_repo,
        state_repo=None,
        move_repo=None,
    ) -> None:
        self._vault = vault
        self._tz = tz
        self._tasks = task_repo
        self._reminders = reminder_repo
        self._efforts = effort_repo
        self._states = state_repo
        self._move = move_repo

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
        todos = [t for t in tasks if t.kind == TaskKind.TODO]
        seeds = [t for t in tasks if t.kind == TaskKind.SEED]
        if todos:
            lines.append("\n**Added to tasks:**")
            for t in todos:
                due = f" — due {self._fmt(t.due_at)}" if t.due_at else ""
                lines.append(f"- {t.title}{due}")
            lines.append("")
        if seeds:
            lines.append("\n**Seeds planted:**")
            for t in seeds:
                lines.append(f"- {t.title}")
            lines.append("")
        return "\n".join(lines)

    # --- Task centre --------------------------------------------------------

    def tasks_changed(self, user_id: UUID) -> None:
        try:
            now = datetime.now(timezone.utc)
            all_open = self._tasks.list_open(user_id)
            open_tasks = [t for t in all_open if t.kind == TaskKind.TODO]
            seeds = [t for t in all_open if t.kind == TaskKind.SEED]
            parked = self._tasks.list_parked(user_id)
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
            if parked:
                parts.append("## Parked\n")
                parts.append("_Not now — consciously shelved. Say the word to bring one back._\n")
                parts.extend(f"- {t.title}" for t in parked)
                parts.append("")
            if upcoming_reminders:
                parts.append("## Reminders\n")
                for r in upcoming_reminders:
                    recur = f" ({r.recurrence})" if r.recurrence else ""
                    parts.append(f"- 🔔 {r.label} — {self._fmt(r.remind_at)}{recur}")
                parts.append("")
            if completed:
                parts.append("## Recently completed\n")
                parts.extend(
                    f"- [x] {t.title} — {self._fmt_date(t.completed_at)}" for t in completed
                )
                parts.append("")

            if not any([overdue, due_today, upcoming, anytime, parked, upcoming_reminders, completed]):
                parts.append("Nothing on the list. Enjoy the quiet.\n")

            path = self._vault / _TASKS_PATH
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(parts), encoding="utf-8")

            self._write_seeds(seeds)
        except Exception:
            _log.warning("obsidian: Tasks.md write failed", exc_info=True)

    def _write_seeds(self, seeds: list[Task]) -> None:
        parts = [_SEEDS_HEADER]
        parts.append(f"_Updated {datetime.now(self._tz).strftime('%a %d %b, %H:%M')}_\n")
        if seeds:
            for t in seeds:
                parts.append(f"- {t.title}")
            parts.append("")
        else:
            parts.append("Nothing planted yet. Dump an idea and see what takes root.\n")
        path = self._vault / _SEEDS_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(parts), encoding="utf-8")

    # --- Self-tracking ------------------------------------------------------

    def state_logged(self, log: StateLog) -> None:
        """Append a one-line receipt to the day's capture note.

        A receipt records the event ("a state was logged now") — it stays true
        even if the data is later corrected. Current truth lives in Tracking.md;
        the full note text lives there too, never echoed here."""
        try:
            local = log.logged_at.astimezone(self._tz)
            path = self._vault / _DAILY_DIR / f"{local.strftime('%Y-%m-%d')}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            scores = "/".join(
                s for s in (
                    f"e{log.energy}" if log.energy else "",
                    f"m{log.mood}" if log.mood else "",
                ) if s
            )
            felt = ""
            if log.felt_at.astimezone(self._tz).date() != local.date() or abs(
                (log.felt_at - log.logged_at).total_seconds()
            ) > 3600:
                felt = f" (felt {log.felt_at.astimezone(self._tz).strftime('%d %b %H:%M')})"
            line = f"\n- {local.strftime('%H:%M')} · tracking {scores or 'noted'}{felt}\n"
            if path.exists():
                with path.open("a", encoding="utf-8") as f:
                    f.write(line)
            else:
                title = f"# {local.strftime('%A %d %B %Y')}\n"
                path.write_text(title + line, encoding="utf-8")
            felt_day = log.felt_at.astimezone(self._tz).date()
            self._update_daily_properties(log.user_id, felt_day)
            if felt_day != local.date():
                self._update_daily_properties(log.user_id, local.date())
        except Exception:
            _log.warning("obsidian: state receipt write failed", exc_info=True)

    def tracking_changed(self, user_id: UUID) -> None:
        """Rewrite Tracking/Recent.md (last two weeks) and the current month's
        History file — one shared renderer, two windows."""
        if self._states is None:
            return
        try:
            now = datetime.now(self._tz)
            since = now - timedelta(days=_TRACKING_DAYS)
            states = self._states.list_states_since(user_id, since=since)
            events = self._states.list_events_since(user_id, since=since)
            period_start = self._states.last_period_start(user_id)

            parts = [_TRACKING_HEADER]
            parts.append(f"_Updated {now.strftime('%a %d %b, %H:%M')}_\n")
            day_parts = self._render_tracking_days(states, events, period_start)
            if day_parts:
                parts.extend(day_parts)
            else:
                parts.append("No entries yet. Just tell Trellis how you're doing.\n")

            path = self._vault / _TRACKING_PATH
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(parts), encoding="utf-8")
        except Exception:
            _log.warning("obsidian: Tracking Recent write failed", exc_info=True)
        try:
            today = datetime.now(self._tz).date()
            self.write_tracking_month(user_id, today.year, today.month)
        except Exception:
            _log.warning("obsidian: Tracking month write failed", exc_info=True)
        self._ensure_tracking_base()
        # Events (meds/sleep/period) don't say which day changed — refresh the
        # last few days' properties so the Base stays true without new wiring.
        try:
            today = datetime.now(self._tz).date()
            for offset in range(3):
                self._update_daily_properties(user_id, today - timedelta(days=offset))
        except Exception:
            _log.warning("obsidian: daily property refresh failed", exc_info=True)

    def write_tracking_month(self, user_id: UUID, year: int, month: int) -> None:
        """(Re)write one month's History file — every entry, full detail. The
        current month rewrites as entries land; past months are only touched by
        an explicit backfill."""
        if self._states is None:
            return
        try:
            month_start = datetime(year, month, 1, tzinfo=self._tz)
            next_month = datetime(year + (month == 12), (month % 12) + 1, 1, tzinfo=self._tz)
            states = [
                st for st in self._states.list_states_since(user_id, since=month_start)
                if st.felt_at.astimezone(self._tz) < next_month
            ]
            events = [
                e for e in self._states.list_events_since(user_id, since=month_start)
                if e.occurred_at.astimezone(self._tz) < next_month
            ]
            if not states and not events:
                return
            period_start = self._states.last_period_start(user_id)
            parts = [f"# Tracking — {month_start.strftime('%B %Y')}\n"]
            parts.extend(self._render_tracking_days(states, events, period_start))
            path = self._vault / _TRACKING_HISTORY_DIR / f"{year:04d}-{month:02d}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(parts), encoding="utf-8")
        except Exception:
            _log.warning("obsidian: Tracking month write failed", exc_info=True)

    def _render_tracking_days(self, states, events, period_start) -> list[str]:
        """Day-by-day tracking sections (newest first) — shared by Recent.md and
        the monthly History files so the two views can never drift."""
        by_day_states: dict = {}
        for st in states:
            by_day_states.setdefault(st.felt_at.astimezone(self._tz).date(), []).append(st)
        by_day_events: dict = {}
        for e in events:
            by_day_events.setdefault(e.occurred_at.astimezone(self._tz).date(), []).append(e)

        parts: list[str] = []
        days = sorted(set(by_day_states) | set(by_day_events), reverse=True)
        for day in days:
            day_states = sorted(by_day_states.get(day, []), key=lambda st: st.felt_at)
            day_events = by_day_events.get(day, [])
            parts.append(f"**{day.strftime('%a %d %b')}**")
            timeline = _timeline(day_states, self._tz)
            if timeline:
                parts.append(f"`{timeline}`")

            meds, sleeps, cycle_notes = [], [], []
            for e in day_events:
                if e.event_type == TrackingEventType.MEDS:
                    t = e.occurred_at.astimezone(self._tz).strftime("%H:%M")
                    meds.append(f"{e.detail or 'meds'} {t}")
                elif e.event_type == TrackingEventType.SLEEP:
                    bits = []
                    if e.value is not None:
                        bits.append(f"{e.value:g}h")
                    if e.detail:
                        bits.append(e.detail)
                    sleeps.append(" ".join(bits) if bits else "logged")
                elif e.event_type == TrackingEventType.PERIOD_START:
                    cycle_notes.append("period started")
                elif e.event_type == TrackingEventType.PERIOD_END:
                    cycle_notes.append("period ended")

            cycle_day = None
            if period_start is not None:
                delta = (day - period_start.occurred_at.astimezone(self._tz).date()).days
                if 0 <= delta < 60:
                    cycle_day = delta + 1

            if meds:
                parts.append(f"**Meds:** {', '.join(meds)}")
            if sleeps:
                parts.append(f"**Sleep:** {', '.join(sleeps)}")
            if cycle_day is not None or cycle_notes:
                cyc = ([f"day {cycle_day}"] if cycle_day is not None else []) + cycle_notes
                parts.append(f"**Cycle:** {' · '.join(cyc)}")

            for st in day_states:
                t = st.felt_at.astimezone(self._tz).strftime("%H:%M")
                retro = ""
                if st.felt_at.date() != st.logged_at.date():
                    retro = f" _(logged {st.logged_at.astimezone(self._tz).strftime('%d %b')})_"
                parts.append(f"  - {t} — {st.note}{retro}")
            parts.append("")
        return parts

    def _ensure_tracking_base(self) -> None:
        """Write Tracking.base ONCE if absent — never overwrite: after creation
        it's the user's file to reshape in Obsidian's Bases UI."""
        try:
            path = self._vault / _TRACKING_BASE_PATH
            if not path.exists() and self._vault.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(_TRACKING_BASE_CONTENT, encoding="utf-8")
        except Exception:
            _log.warning("obsidian: Tracking.base write failed", exc_info=True)

    def _update_daily_properties(self, user_id: UUID, day: date) -> None:
        """Upsert frontmatter properties on the day's note (energy, mood, sleep,
        meds, cycle day) so Bases can table the whole history. Body untouched —
        the note stays append-only; only the metadata block is rewritten."""
        if self._states is None:
            return
        try:
            day_start = datetime.combine(day, datetime.min.time(), tzinfo=self._tz)
            states = [
                s for s in self._states.list_states_since(user_id, since=day_start)
                if s.felt_at.astimezone(self._tz).date() == day
            ]
            events = [
                e for e in self._states.list_events_since(user_id, since=day_start)
                if e.occurred_at.astimezone(self._tz).date() == day
            ]
            props: dict = {}
            energies = [s.energy for s in states if s.energy]
            moods = [s.mood for s in states if s.mood]
            # Ranges, not averages — a spiky day must look spiky even at the
            # horizon zoom ("2–4", not a flattened 3.0).
            if energies:
                props["energy"] = _range_or_value(energies)
            if moods:
                props["mood"] = _range_or_value(moods)
            if states:
                props["entries"] = len(states)
            sleeps = [e.value for e in events
                      if e.event_type == TrackingEventType.SLEEP and e.value is not None]
            if sleeps:
                props["sleep_hours"] = round(float(sleeps[-1]), 1)
            meds = [e.detail for e in events
                    if e.event_type == TrackingEventType.MEDS and e.detail]
            if meds:
                props["meds"] = meds
            period_start = self._states.last_period_start(user_id)
            if period_start is not None:
                delta = (day - period_start.occurred_at.astimezone(self._tz).date()).days
                if 0 <= delta < 60:
                    props["cycle_day"] = delta + 1
            if not props:
                return
            path = self._vault / _DAILY_DIR / f"{day.strftime('%Y-%m-%d')}.md"
            self._upsert_frontmatter(path, props)
        except Exception:
            _log.warning("obsidian: daily properties update failed", exc_info=True)

    # The frontmatter keys Trellis owns. Everything else in a note's frontmatter
    # is the USER'S (added in Obsidian) and must survive a rewrite — the
    # append-only promise covers their metadata, not just their body text.
    _MANAGED_FRONTMATTER_KEYS = ("energy", "mood", "entries", "sleep_hours", "meds", "cycle_day")

    def _upsert_frontmatter(self, path: Path, props: dict) -> None:
        user_lines: list[str] = []
        body = ""
        if path.exists():
            text = path.read_text(encoding="utf-8")
            if text.startswith("---\n"):
                end = text.find("\n---\n", 4)
                if end != -1:
                    in_managed_list = False
                    for line in text[4:end].splitlines():
                        if line.startswith("  - ") and in_managed_list:
                            continue
                        in_managed_list = False
                        key = line.split(":", 1)[0].strip() if ":" in line else None
                        if key in self._MANAGED_FRONTMATTER_KEYS:
                            in_managed_list = line.rstrip().endswith(":")
                            continue
                        user_lines.append(line)
                    body = text[end + 5:]
                else:
                    body = text
            else:
                body = text

        lines = ["---", *user_lines]
        for key, val in props.items():
            if isinstance(val, list):
                lines.append(f"{key}:")
                lines.extend(f"  - {v}" for v in val)
            else:
                lines.append(f"{key}: {val}")
        lines.append("---")
        block = "\n".join(lines) + "\n"

        if path.exists():
            path.write_text(block + body, encoding="utf-8")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            title = f"# {day_title}\n" if (day_title := _title_from_daily_path(path)) else ""
            path.write_text(block + title, encoding="utf-8")

    # --- Training plan page --------------------------------------------------

    def plan_changed(self, user_id: UUID) -> None:
        """Rewrite Training/Plan.md — the arc, baseline, this week's sessions
        with completed runs matched by date, and recent runs. Write-only view,
        same contract as Tasks.md: edits there don't sync back."""
        if self._move is None:
            return
        try:
            plan = self._move.get(user_id)
            runs = self._move.recent_runs(user_id, limit=12)
            runs_by_date = {r.ran_on.isoformat(): r for r in runs}
            now = datetime.now(self._tz)

            parts = [
                "# Training Plan\n",
                "> Live view — managed by Trellis (your coach). Edits here won't "
                "sync back; tell Trellis instead.\n",
                f"_Updated {now.strftime('%d %b %Y, %H:%M')}_\n",
            ]
            plan_doc = plan.plan if plan is not None and plan.plan else {}
            arc = plan_doc.get("arc")
            if arc:
                parts.append(f"## Arc\n{arc}\n")
            if plan is not None and plan.baseline:
                parts.append(f"## Baseline\n{plan.baseline}\n")

            week = plan_doc.get("week") or []
            session_lines, matched_dates = self._session_lines(week, runs_by_date)
            if session_lines:
                parts.append("\n".join(["## This Week\n", *session_lines]) + "\n")

            extra_runs = [r for r in runs if r.ran_on.isoformat() not in matched_dates]
            if extra_runs:
                lines = ["## Recent Runs\n"]
                for r in extra_runs[:8]:
                    dist = f" — {r.distance_km}km" if r.distance_km is not None else ""
                    lines.append(f"- {r.ran_on.strftime('%a %d %b')}{dist}: {r.note}")
                parts.append("\n".join(lines) + "\n")

            if len(parts) == 3:
                parts.append("_No plan yet — ask Trellis to build one._\n")

            path = self._vault / _TRAINING_PLAN_PATH
            if not self._vault.exists():
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(parts), encoding="utf-8")
            self._write_training_week(week, runs_by_date, now)
        except Exception:
            _log.warning("obsidian: Training plan write failed", exc_info=True)

    def _session_lines(self, week: list, runs_by_date: dict) -> "tuple[list[str], set[str]]":
        """Checkbox lines for a week's sessions, runs ticked off by date —
        shared by the main page and the week archive so they can never drift."""
        lines: list[str] = []
        matched: set[str] = set()
        for sess in week:
            s_date = str(sess.get("date", ""))
            s_type = sess.get("type", "session")
            s_detail = sess.get("detail", "")
            run = runs_by_date.get(s_date)
            try:
                day_name = date.fromisoformat(s_date).strftime("%a %d %b")
            except ValueError:
                day_name = s_date
            if run is not None:
                matched.add(s_date)
                dist = f" — {run.distance_km}km done" if run.distance_km else " — done"
                lines.append(f"- [x] {day_name} — {s_type}: {s_detail}{dist}")
            else:
                lines.append(f"- [ ] {day_name} — {s_type}: {s_detail}")
        return lines, matched

    def _write_training_week(self, week: list, runs_by_date: dict, now: datetime) -> None:
        """Archive the plan's week to Weeks/<YYYY-Www>.md. Rewritten while it's
        the live week; once the plan moves on, the file is simply left behind —
        a frozen record of planned vs run."""
        if not week:
            return
        try:
            first = date.fromisoformat(str(week[0].get("date", "")))
        except ValueError:
            return
        iso_year, iso_week, _ = first.isocalendar()
        lines = [
            f"# Week {iso_week}, {iso_year}\n",
            f"_Updated {now.strftime('%d %b %Y, %H:%M')}_\n",
        ]
        session_lines, _matched = self._session_lines(week, runs_by_date)
        lines.extend(session_lines)
        path = self._vault / _TRAINING_WEEKS_DIR / f"{iso_year:04d}-W{iso_week:02d}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # --- The Watcher's window ------------------------------------------------

    def watcher_page(self, body: str) -> None:
        """The window into the slow mind — everything the Watcher is thinking,
        by status. Rewritten on every tick/verdict; same write-only contract."""
        try:
            if not self._vault.exists():
                return
            path = self._vault / _WATCHER_PATH
            path.parent.mkdir(parents=True, exist_ok=True)
            header = (
                "> Everything the slow mind is thinking. It speaks in chat only "
                "when something verifies — verdicts go to Trellis, not here.\n\n"
                f"*Updated {datetime.now(self._tz).strftime('%a %-d %b, %H:%M')}*\n\n"
            )
            path.write_text(header + body, encoding="utf-8")
        except Exception:
            _log.warning("obsidian: Watcher page write failed", exc_info=True)

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
            lines.append("## Research & notes\n")
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

    def research_saved(self, capture: Capture) -> None:
        """Research/notes kept onto an effort: full text on the effort page,
        a one-line receipt in the day's log (which links back to the effort)."""
        if capture.effort_id is None:
            return
        try:
            effort = self._efforts.get(capture.effort_id)
            if effort is None:
                return
            page = self._effort_path(effort)
            if page is None:
                return
            if not page.exists():
                self.effort_created(effort)
            local = capture.created_at.astimezone(self._tz)
            body = (capture.synthesis or capture.raw).strip()
            block = f"\n---\n_{local.strftime('%d %b %Y, %H:%M')}_\n\n{body}\n"
            with page.open("a", encoding="utf-8") as f:
                f.write(block)

            # Receipt in the day's log, linking to the effort page.
            daily = self._vault / _DAILY_DIR / f"{local.strftime('%Y-%m-%d')}.md"
            daily.parent.mkdir(parents=True, exist_ok=True)
            effort_link = effort.title
            receipt = f"\n- {local.strftime('%H:%M')} · research → [[{effort_link}]]: {capture.summary or ''}\n"
            if daily.exists():
                with daily.open("a", encoding="utf-8") as f:
                    f.write(receipt)
            else:
                daily.write_text(f"# {local.strftime('%A %d %B %Y')}\n{receipt}", encoding="utf-8")
        except Exception:
            _log.warning("obsidian: research write failed", exc_info=True)

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
