"""
Obsidian vault projection — the visible window into the second brain.

Write-only (version 1): Trellis writes, Cat reads. The DB is the source of
truth; edits made in Obsidian do not sync back.

Vault layout:
    Calendar/<YYYY-MM-DD>.md   daily note — captures append here, journal-style
    Tasks.md                   live task centre — fully rewritten on every change
    Efforts/<Title>.md         one page per effort; its captures accumulate

Daily notes and effort pages are append-only, so anything Cat writes in them
is never touched. Tasks.md is Trellis-owned and rewritten wholesale.

Every public method swallows and logs failures — a vault write must never
break the bot.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from uuid import UUID

from trellis.domain_second_brain_models import Capture, Effort, Task, TaskStatus

_log = logging.getLogger(__name__)

_TASKS_HEADER = """\
# Tasks

> Live view — managed by Trellis. Edits here won't sync back; tell Trellis instead.
"""

_UPCOMING_REMINDER_DAYS = 14
_RECENTLY_COMPLETED_LIMIT = 8


class ObsidianVault:
    def __init__(
        self,
        vault: Path,
        tz: tzinfo,
        task_repo,
        reminder_repo,
        effort_repo,
    ) -> None:
        self._vault = vault
        self._tz = tz
        self._tasks = task_repo
        self._reminders = reminder_repo
        self._efforts = effort_repo

    # --- Daily notes (journal) ---------------------------------------------

    def capture_saved(self, capture: Capture, tasks: tuple[Task, ...] = ()) -> None:
        try:
            local = capture.created_at.astimezone(self._tz)
            path = self._vault / "Calendar" / f"{local.strftime('%Y-%m-%d')}.md"
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

            path = self._vault / "Tasks.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(parts), encoding="utf-8")
        except Exception:
            _log.warning("obsidian: Tasks.md write failed", exc_info=True)

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
