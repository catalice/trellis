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


def test_a_second_backup_while_one_is_running_steps_aside(tmp_path):
    vault = tmp_path / "vault"
    assert _run(tmp_path, vault, docker_ok=True).returncode == 0
    (good,) = _dumps(vault)
    before = good.read_bytes()
    (vault / ".backups" / ".backup.lock").mkdir()                   # another run holds the lock
    second = _run(tmp_path, vault, docker_ok=False)
    assert second.returncode != 0 and "already running" in second.stderr
    assert good.read_bytes() == before
    assert (vault / ".backups" / ".backup.lock").is_dir()           # it didn't steal or clear the lock


def test_staging_files_are_private_to_each_run(tmp_path):
    script = SCRIPT.read_text()
    assert "mktemp" in script and '"$OUT.partial"' not in script


def test_a_stale_lock_from_a_crashed_run_is_taken_over(tmp_path):
    import time
    vault = tmp_path / "vault"
    lock = vault / ".backups" / ".backup.lock"
    lock.mkdir(parents=True)
    old = time.time() - 3 * 3600
    os.utime(lock, (old, old))
    assert _run(tmp_path, vault, docker_ok=True).returncode == 0
    assert len(_dumps(vault)) == 1 and not lock.exists()


def _lock(vault: Path, *, pid: int | None, hours_old: float = 0) -> Path:
    import time
    lock = vault / ".backups" / ".backup.lock"
    lock.mkdir(parents=True)
    if pid is not None:
        (lock / "pid").write_text(f"{pid}\n")
    if hours_old:
        old = time.time() - hours_old * 3600
        os.utime(lock, (old, old))
    return lock


def _dead_pid() -> int:
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def test_a_lock_held_by_a_living_process_is_respected_however_old(tmp_path):
    vault = tmp_path / "vault"
    lock = _lock(vault, pid=os.getpid(), hours_old=30)             # old, but its owner is alive
    result = _run(tmp_path, vault, docker_ok=True)
    assert result.returncode != 0 and "already running" in result.stderr
    assert (lock / "pid").read_text().strip() == str(os.getpid())   # untouched
    assert _dumps(vault) == []


def test_a_dead_owners_lock_is_reclaimed_and_then_owned(tmp_path):
    vault = tmp_path / "vault"
    lock = _lock(vault, pid=_dead_pid())
    assert _run(tmp_path, vault, docker_ok=True).returncode == 0
    assert len(_dumps(vault)) == 1 and not lock.exists()
    assert not list((vault / ".backups").glob(".backup.lock.*"))   # the reclaimed husk is gone too


def test_a_run_never_removes_a_lock_it_does_not_own(tmp_path):
    script = SCRIPT.read_text()
    assert 'cat "$LOCK/pid"' in script and '"$$"' in script      # ownership is checked before release


SNAPSHOT = Path(__file__).parent.parent / "scripts" / "predeploy_snapshot.sh"


def _snapshot(tmp_path: Path, vault: Path, *, docker_ok: bool = True):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    dump = tmp_path / "dump.sql"
    dump.write_text(GOOD_DUMP)
    fake = bin_dir / "docker"
    fake.write_text("#!/usr/bin/env bash\n" + (f'cat "{dump}"\n' if docker_ok else "exit 1\n"))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "OBSIDIAN_VAULT": str(vault),
           "SNAPSHOT_ROOT": str(tmp_path / "snapshots")}
    return subprocess.run(["bash", str(SNAPSHOT)], env=env, capture_output=True, text=True)


def test_a_release_snapshot_is_timestamped_separate_and_holds_the_vault(tmp_path):
    import tarfile
    vault = tmp_path / "my vault"
    (vault / "Atlas/Maps").mkdir(parents=True)
    (vault / "Atlas/Maps/Rome.md").write_text("hand-written\n")
    (vault / ".backups").mkdir()
    (vault / ".backups/trellis-2026-01-01.sql.gz").write_bytes(b"old nightly dump")
    result = _snapshot(tmp_path, vault)
    assert result.returncode == 0, result.stderr
    (folder,) = (tmp_path / "snapshots").iterdir()
    names = sorted(p.name for p in folder.iterdir())
    assert [n.split("-")[0] for n in names] == ["README.txt", "trellis", "vault"]
    assert gzip.decompress(next(folder.glob("trellis-*.sql.gz")).read_bytes()).decode().rstrip().endswith("dump complete")
    with tarfile.open(next(folder.glob("vault-*.tar.gz"))) as archive:
        members = archive.getnames()
    assert "my vault/Atlas/Maps/Rome.md" in members
    assert not any(".backups" in m for m in members)                 # the dumps aren't copied into the copy
    assert not os.access(folder / names[1], os.W_OK)                 # read-only: nothing replaces it by accident
    assert list((vault / ".backups").iterdir()) == [vault / ".backups/trellis-2026-01-01.sql.gz"]   # nightly untouched
    os.system(f'chmod -R u+w "{tmp_path / "snapshots"}"')            # let pytest clean up


def test_a_snapshot_whose_dump_failed_is_not_left_looking_complete(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    result = _snapshot(tmp_path, vault, docker_ok=False)
    assert result.returncode != 0
    assert not list((tmp_path / "snapshots").rglob("*.sql.gz"))
    os.system(f'chmod -R u+w "{tmp_path / "snapshots"}" 2>/dev/null')
