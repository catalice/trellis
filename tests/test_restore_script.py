"""A live restore must never leave the install worse off: the running database
is replaced only after the dump has restored and checked out somewhere else, and
the database it replaces is kept. Runs the real script against a stand-in
`docker` that records every command."""
from __future__ import annotations

import gzip
import os
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "restore_db.sh"

FAKE_DOCKER = r'''#!/usr/bin/env bash
echo "$*" >> "$FAKE_LOG"
case "$*" in
  *"-d trellis_restore_staging"*)
    if [[ "$*" != *" -c "* ]]; then          # the import: SQL arrives on stdin
      cat > /dev/null
      [[ -n "${FAIL_IMPORT:-}" ]] && { echo "ERROR: syntax error" >&2; exit 3; }
      exit 0
    fi
    [[ -n "${EMPTY_STAGING:-}" ]] && { echo "0"; exit 0; }
    echo "25"; exit 0 ;;
esac
exit 0
'''


def _run(tmp_path: Path, *, answer: str = "restore\n", **env) -> tuple[subprocess.CompletedProcess, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "docker"
    fake.write_text(FAKE_DOCKER)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    dump = tmp_path / "trellis-2026-01-01.sql.gz"
    dump.write_bytes(gzip.compress(b"-- PostgreSQL database dump\nSELECT 1;\n-- PostgreSQL database dump complete\n"))
    log = tmp_path / "docker.log"
    log.write_text("")
    result = subprocess.run(
        ["bash", str(SCRIPT), str(dump), "--live"], input=answer, text=True, capture_output=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_LOG": str(log), **env})
    return result, log.read_text()


def _touched_live(log: str) -> bool:
    return any(marker in log for marker in ("RENAME", "DROP DATABASE IF EXISTS trellis;", "stop trellis"))


def test_a_dump_that_fails_to_import_never_touches_the_live_database(tmp_path):
    result, log = _run(tmp_path, FAIL_IMPORT="1")
    assert result.returncode != 0
    assert not _touched_live(log), log


def test_a_dump_that_restores_to_nothing_is_refused(tmp_path):
    result, log = _run(tmp_path, EMPTY_STAGING="1")
    assert result.returncode != 0
    assert not _touched_live(log), log


def test_declining_the_prompt_changes_nothing(tmp_path):
    result, log = _run(tmp_path, answer="no\n")
    assert result.returncode != 0
    assert not _touched_live(log), log


def test_a_good_restore_swaps_by_rename_and_keeps_the_old_database(tmp_path):
    result, log = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "ALTER DATABASE trellis RENAME TO trellis_before_restore_" in log
    assert "ALTER DATABASE trellis_restore_staging RENAME TO trellis" in log
    assert "DROP DATABASE IF EXISTS trellis;" not in log          # the old one is never dropped
    assert log.index("stop trellis") < log.index("RENAME TO trellis_before_restore_") < log.index("start trellis")
    assert "trellis_before_restore_" in result.stdout               # and the person is told where it is
