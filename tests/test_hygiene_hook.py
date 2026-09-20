"""The pre-push guard must see every commit being published, not only the tip:
a marker added in one commit and removed in the next still ships in history."""
from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "check_public_hygiene.sh"
ZERO = "0" * 40


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / ".gitignore").write_text(".hygiene-denylist\n")
    (repo / ".hygiene-denylist").write_text("zebra\n")
    (repo / "notes.md").write_text("plain\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def _push_check(repo: Path, local: str, remote: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SCRIPT)], cwd=repo, text=True, capture_output=True,
                          input=f"refs/heads/main {local} refs/heads/main {remote}\n")


def test_a_marker_added_then_removed_is_still_caught(tmp_path):
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "notes.md").write_text("the zebra detail\n")
    _git(repo, "commit", "-qam", "adds it")
    (repo / "notes.md").write_text("plain again\n")
    _git(repo, "commit", "-qam", "removes it")
    result = _push_check(repo, _git(repo, "rev-parse", "HEAD"), base)
    assert result.returncode == 1, result.stdout
    assert "zebra" in result.stdout


def test_clean_commits_pass(tmp_path):
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "notes.md").write_text("still plain\n")
    _git(repo, "commit", "-qam", "fine")
    assert _push_check(repo, _git(repo, "rev-parse", "HEAD"), base).returncode == 0


def test_a_first_push_checks_the_whole_branch(tmp_path):
    repo = _repo(tmp_path)
    (repo / "notes.md").write_text("zebra\n")
    _git(repo, "commit", "-qam", "adds it")
    (repo / "notes.md").write_text("gone\n")
    _git(repo, "commit", "-qam", "removes it")
    assert _push_check(repo, _git(repo, "rev-parse", "HEAD"), ZERO).returncode == 1
