"""The nightly backup must never make things worse: a failed run leaves earlier
dumps intact, and rotation survives a vault path with spaces. Runs the real
script against a stand-in `docker` executable."""
from __future__ import annotations

import gzip
import os
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "backup_db.sh"
GOOD_DUMP = "-- PostgreSQL database dump\n" + ("CREATE TABLE t (id int);\n" * 200) + "-- PostgreSQL database dump complete\n"


def _run(tmp_path: Path, vault: Path, *, docker_ok: bool) -> subprocess.CompletedProcess:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    dump = tmp_path / "dump.sql"
    dump.write_text(GOOD_DUMP)
    fake = bin_dir / "docker"
    fake.write_text(f"#!/usr/bin/env bash\n" + (f'cat "{dump}"\n' if docker_ok else "echo 'connection refused' >&2\nexit 1\n"))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "OBSIDIAN_VAULT": str(vault)}
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)


def _dumps(vault: Path) -> list[Path]:
    return sorted((vault / ".backups").glob("trellis-*.sql.gz"))


def test_failed_retry_keeps_the_days_good_backup(tmp_path):
    vault = tmp_path / "vault"
    assert _run(tmp_path, vault, docker_ok=True).returncode == 0
    (good,) = _dumps(vault)
    before = good.read_bytes()
    failed = _run(tmp_path, vault, docker_ok=False)
    assert failed.returncode != 0
    assert good.read_bytes() == before
    assert gzip.decompress(good.read_bytes()).decode().rstrip().endswith("dump complete")
    assert not list((vault / ".backups").glob("*.partial"))


def test_rotation_keeps_newest_fourteen_with_spaces_in_the_path(tmp_path):
    vault = tmp_path / "my vault with spaces"
    backups = vault / ".backups"
    backups.mkdir(parents=True)
    for day in range(1, 21):
        (backups / f"trellis-2026-01-{day:02d}.sql.gz").write_bytes(gzip.compress(GOOD_DUMP.encode()))
    assert _run(tmp_path, vault, docker_ok=True).returncode == 0
    kept = _dumps(vault)
    assert len(kept) == 14
    assert not (backups / "trellis-2026-01-01.sql.gz").exists()   # oldest went
    assert kept[-1].name > "trellis-2026-01-20.sql.gz"             # today's is there


def test_truncated_dump_is_rejected(tmp_path):
    vault = tmp_path / "vault"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    fake.write_text("#!/usr/bin/env bash\nprintf -- '-- PostgreSQL database dump\\n%.0s' {1..400}\n")   # no completion marker
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "OBSIDIAN_VAULT": str(vault)}
    result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert _dumps(vault) == []
