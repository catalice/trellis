from __future__ import annotations

import os
import tempfile
from datetime import datetime, tzinfo
from pathlib import Path

from trellis.captures import Capture
from trellis.tasks import Task
from trellis.training import WeeklyPlan, date_for_day


START_MARKER = "<!-- trellis:tasks:start -->"
END_MARKER = "<!-- trellis:tasks:end -->"
TRAINING_START_MARKER = "<!-- trellis:training:start -->"
TRAINING_END_MARKER = "<!-- trellis:training:end -->"


class ObsidianTaskProjection:
    """Writes only the Trellis-owned region and preserves all other content."""

    def __init__(self, vault: Path):
        self.path = vault / "Calendar" / "Trellis" / "Tasks.md"

    def write(self, tasks: list[Task]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing = self.path.read_text(encoding="utf-8") if self.path.exists() else "# Tasks\n"
        block = self._render(tasks)

        if START_MARKER in existing or END_MARKER in existing:
            if existing.count(START_MARKER) != 1 or existing.count(END_MARKER) != 1:
                raise ValueError("Ambiguous Trellis task markers in Calendar/Tasks.md")
            before, remainder = existing.split(START_MARKER, 1)
            _, after = remainder.split(END_MARKER, 1)
            updated = f"{before}{block}{after}"
        else:
            separator = "" if existing.endswith("\n\n") else "\n"
            updated = f"{existing}{separator}{block}\n"

        self._atomic_write(updated)

    def _render(self, tasks: list[Task]) -> str:
        lines = [
            START_MARKER,
            "## Trellis Tasks",
            "",
            "_This section is managed by Trellis. Content outside it is preserved._",
            "",
        ]
        if not tasks:
            lines.append("- No open tasks.")
        else:
            for task in tasks:
                metadata = [
                    f"id:{task.id}",
                    f"priority:{task.priority.value}",
                    f"energy:{task.energy.value}",
                ]
                if task.due_at:
                    metadata.append(f"due:{task.due_at.isoformat()}")
                lines.append(f"- [ ] {task.title} <!-- {' '.join(metadata)} -->")
        lines.append(END_MARKER)
        return "\n".join(lines)

    def _atomic_write(self, content: str) -> None:
        fd, temporary_name = tempfile.mkstemp(
            dir=self.path.parent,
            prefix=".trellis-tasks-",
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, self.path)
        except Exception:
            Path(temporary_name).unlink(missing_ok=True)
            raise


class ObsidianCaptureProjection:
    def __init__(self, vault: Path, timezone: tzinfo):
        self.directory = vault / "Calendar" / "Trellis" / "Captures"
        self.timezone = timezone

    def write(
        self,
        capture: Capture,
        *,
        task_titles: list[str],
        idea_titles: list[str],
    ) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        local_time = capture.created_at.astimezone(self.timezone)
        path = self.directory / f"{local_time.date().isoformat()}.md"
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        section = self._render(capture, local_time, task_titles, idea_titles)
        separator = "\n" if existing and not existing.endswith("\n\n") else ""
        _atomic_write(path, f"{existing}{separator}{section}\n")

    @staticmethod
    def _render(
        capture: Capture,
        local_time,
        task_titles: list[str],
        idea_titles: list[str],
    ) -> str:
        lines = [
            f"## {local_time.strftime('%H:%M')} - Telegram capture",
            f"<!-- trellis:capture:{capture.id} -->",
            "",
            "### Synthesis",
            capture.synthesis or "Processing failed before synthesis.",
            "",
            "### Processed Into",
        ]
        if task_titles:
            lines.append(f"- {len(task_titles)} tasks -> [[Tasks]]")
            lines.extend(f"  - {title}" for title in task_titles)
        if idea_titles:
            lines.append(
                f"- {len(idea_titles)} ideas -> [[trellis-idea-inbox]]"
            )
            lines.extend(f"  - {title}" for title in idea_titles)
        if not task_titles and not idea_titles:
            lines.append("- No tasks or ideas created.")

        for heading, values in (
            ("Questions", capture.questions),
            ("Decisions", capture.decisions),
            ("Observations", capture.observations),
        ):
            if values:
                lines.extend(["", f"### {heading}"])
                lines.extend(f"- {value}" for value in values)

        lines.extend(["", "### Original"])
        lines.extend(f"> {line}" if line else ">" for line in capture.content.splitlines())
        return "\n".join(lines)


class ObsidianTrainingProjection:
    def __init__(self, vault: Path):
        self.directory = vault / "Calendar" / "Trellis" / "Training"

    def write(self, plan: WeeklyPlan) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        filename = plan.week_start.strftime("%d%b%Y") + ".md"
        path = self.directory / filename
        existing = (
            path.read_text(encoding="utf-8")
            if path.exists()
            else f"# Training — w/c {plan.week_start.strftime('%d %b %Y')}\n"
        )
        block = self._render(plan)
        if TRAINING_START_MARKER in existing or TRAINING_END_MARKER in existing:
            if (
                existing.count(TRAINING_START_MARKER) != 1
                or existing.count(TRAINING_END_MARKER) != 1
            ):
                raise ValueError(f"Ambiguous Trellis training markers in {filename}")
            before, remainder = existing.split(TRAINING_START_MARKER, 1)
            _, after = remainder.split(TRAINING_END_MARKER, 1)
            updated = f"{before}{block}{after}"
        else:
            separator = "" if existing.endswith("\n\n") else "\n"
            updated = f"{existing}{separator}{block}\n"
        _atomic_write(path, updated)

    @staticmethod
    def _render(plan: WeeklyPlan) -> str:
        lines = [
            TRAINING_START_MARKER,
            "## Trellis Training Plan",
            "",
            "_This section is managed by Trellis. Content outside it is preserved._",
            "",
            f"- Week: {plan.week_start.isoformat()}",
            f"- Mode: {plan.mode.value}",
            f"- Revision: {plan.revision}",
            f"- Total planned time: {plan.total_minutes} minutes",
            "",
            "### Sessions",
        ]
        for session in plan.sessions:
            session_date = date_for_day(plan.week_start, session.day)
            time_text = (
                f" at {session.start_time.strftime('%H:%M')}"
                if session.start_time
                else ""
            )
            lines.extend(
                [
                    "",
                    (
                        f"#### {session_date.strftime('%A')} - {session.title}"
                        f"{time_text}"
                    ),
                    (
                        f"- Type: {session.kind.value}; intensity: "
                        f"{session.intensity.value}; total: "
                        f"{session.total_minutes} minutes"
                    ),
                ]
            )
            for block in session.blocks:
                lines.append(f"- {block.name} ({block.duration_minutes} min)")
                lines.extend(f"  - {instruction}" for instruction in block.instructions)
            if session.notes:
                lines.append("- Notes")
                lines.extend(f"  - {note}" for note in session.notes)

        if plan.rationale:
            lines.extend(["", "### Rationale"])
            lines.extend(f"- {item}" for item in plan.rationale)
        lines.append(TRAINING_END_MARKER)
        return "\n".join(lines)


class ObsidianRawCaptureProjection:
    """Appends raw (unprocessed) captures to a daily captures file."""

    def __init__(self, vault: Path, timezone: tzinfo):
        self.directory = vault / "Calendar" / "Trellis" / "Captures"
        self.timezone = timezone

    def append(self, text: str, now) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        local_time = now.astimezone(self.timezone)
        path = self.directory / f"{local_time.date().isoformat()}.md"
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        entry = f"\n## {local_time.strftime('%H:%M')} — capture\n\n{text}\n"
        _atomic_write(path, existing + entry)


def _atomic_write(path: Path, content: str) -> None:
    fd, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.stem}-",
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise
