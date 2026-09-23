"""Removing captured data this server keeps on disk once it is old enough.

Everything here is local, but it is real traffic: saved capture snapshots,
the reverse-analysis database with request and response bodies, and copies of
the Charles config with the Web Interface credentials in them. None of it
expires on its own, and backup tools copy it along. This module deletes what is
older than a cut-off, and only that.

Two things are never removed, whatever their age:

- reset_environment's baseline (``<backup dir>/config`` and ``/profiles``) —
  it is the copy that tool restores from, and deleting it would disable the
  one tool that exists to undo damage;
- the newest backup in each other backup folder, so the most recent Charles
  config change can always be rolled back.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

PurgeScope = Literal["recordings", "reverse", "backups"]
ALL_SCOPES: tuple[PurgeScope, ...] = ("recordings", "reverse", "backups")

# reset_environment restores from these; see the module docstring.
_PROTECTED_BACKUP_FOLDERS = {"config", "profiles"}


@dataclass
class PurgeReport:
    dry_run: bool
    older_than_days: int
    recordings: list[str] = field(default_factory=list)
    backups: list[str] = field(default_factory=list)
    reverse_captures: int = 0
    reverse_undated: int = 0
    freed_bytes: int = 0

    def summary(self) -> str:
        verb = "would remove" if self.dry_run else "removed"
        parts = [
            f"{len(self.recordings)} saved capture(s)",
            f"{self.reverse_captures} reverse-analysis capture(s)",
            f"{len(self.backups)} Charles config backup(s)",
        ]
        text = (
            f"{verb} {', '.join(parts)} older than {self.older_than_days} day(s), "
            f"{self.freed_bytes / 1_048_576:.1f} MB of files"
        )
        if self.reverse_undated:
            text += (
                f"; {self.reverse_undated} reverse capture(s) have no timestamp and were kept"
            )
        return text


def purge_stored_data(
    *,
    package_dir: str | Path,
    backup_dir: str | Path,
    reverse_database: str | Path | None,
    older_than_days: int,
    scopes: tuple[PurgeScope, ...] | list[PurgeScope] = ALL_SCOPES,
    dry_run: bool = True,
    now: float | None = None,
) -> PurgeReport:
    """Delete stored captures and backups older than ``older_than_days``."""
    if older_than_days < 0:
        raise ValueError("older_than_days cannot be negative")
    cutoff = (now if now is not None else time.time()) - older_than_days * 86_400
    report = PurgeReport(dry_run=dry_run, older_than_days=older_than_days)

    if "recordings" in scopes:
        for path in _old_files(Path(package_dir), "*.chlsj", cutoff):
            report.recordings.append(path.name)
            report.freed_bytes += _remove(path, dry_run)

    if "backups" in scopes:
        root = Path(backup_dir)
        if root.is_dir():
            for folder in sorted(p for p in root.iterdir() if p.is_dir()):
                if folder.name in _PROTECTED_BACKUP_FOLDERS:
                    continue
                files = sorted(
                    (p for p in folder.rglob("*") if p.is_file()),
                    key=lambda p: p.stat().st_mtime,
                )
                # Keep the newest, so the latest config change stays reversible.
                for path in files[:-1]:
                    if path.stat().st_mtime < cutoff:
                        report.backups.append(f"{folder.name}/{path.name}")
                        report.freed_bytes += _remove(path, dry_run)

    if "reverse" in scopes and reverse_database and Path(reverse_database).is_file():
        removed, undated = _purge_reverse(Path(reverse_database), cutoff, dry_run)
        report.reverse_captures = removed
        report.reverse_undated = undated

    return report


def _old_files(root: Path, pattern: str, cutoff: float) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob(pattern) if p.is_file() and p.stat().st_mtime < cutoff)


def _remove(path: Path, dry_run: bool) -> int:
    size = path.stat().st_size
    if not dry_run:
        path.unlink()
    return size


def _purge_reverse(database: Path, cutoff: float, dry_run: bool) -> tuple[int, int]:
    """Drop reverse captures older than the cut-off, then the bodies they owned."""
    cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        # Timestamps are stored as ISO strings, which sort chronologically.
        stamp = "COALESCE(ended_at, started_at)"
        old = connection.execute(
            f"SELECT capture_id FROM captures WHERE {stamp} IS NOT NULL AND {stamp} < ?",
            (cutoff_iso,),
        ).fetchall()
        undated = connection.execute(
            f"SELECT COUNT(*) FROM captures WHERE {stamp} IS NULL"
        ).fetchone()[0]
        if dry_run or not old:
            return len(old), undated

        connection.executemany(
            "DELETE FROM captures WHERE capture_id = ?", [(row[0],) for row in old]
        )
        # Requests and responses cascade with their entries, but only null out
        # their body references: without this the bodies — the actual captured
        # payloads — would outlive the captures they belonged to.
        connection.execute(
            """
            DELETE FROM body_blobs WHERE body_blob_id NOT IN (
                SELECT body_blob_id FROM requests WHERE body_blob_id IS NOT NULL
                UNION
                SELECT body_blob_id FROM responses WHERE body_blob_id IS NOT NULL
            )
            """
        )
        connection.commit()
        # Deleted rows stay readable in free pages until the file is rebuilt,
        # which would defeat the point of purging captured data.
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("VACUUM")
        return len(old), undated
    finally:
        connection.close()


def retention_cutoff_description(days: int) -> str:
    """Human wording for logs: the date before which data is purged."""
    boundary = datetime.now(timezone.utc) - timedelta(days=days)
    return boundary.strftime("%Y-%m-%d")
