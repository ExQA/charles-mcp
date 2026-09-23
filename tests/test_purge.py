"""Purging stored captures: what goes, and what must never go."""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from charles_mcp.reverse.storage.sqlite_store import SQLiteStore
from charles_mcp.services.purge import purge_stored_data

DAY = 86_400
NOW = time.time()


def _file(path: Path, age_days: float, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    stamp = NOW - age_days * DAY
    os.utime(path, (stamp, stamp))
    return path


def _purge(tmp_path: Path, **kwargs):
    defaults = dict(
        package_dir=tmp_path / "package",
        backup_dir=tmp_path / "backups",
        reverse_database=None,
        older_than_days=30,
        now=NOW,
    )
    defaults.update(kwargs)
    return purge_stored_data(**defaults)


def test_dry_run_is_the_default_and_deletes_nothing(tmp_path: Path) -> None:
    old = _file(tmp_path / "package" / "old.chlsj", 60)

    report = _purge(tmp_path)

    assert report.dry_run is True
    assert report.recordings == ["old.chlsj"]
    assert old.exists()
    assert "would remove" in report.summary()


def test_only_recordings_past_the_cutoff_are_removed(tmp_path: Path) -> None:
    old = _file(tmp_path / "package" / "old.chlsj", 60)
    fresh = _file(tmp_path / "package" / "fresh.chlsj", 5)
    other = _file(tmp_path / "package" / "notes.txt", 60)

    report = _purge(tmp_path, dry_run=False)

    assert report.recordings == ["old.chlsj"]
    assert not old.exists()
    assert fresh.exists()
    assert other.exists()  # not a capture snapshot


def test_reset_environment_baseline_is_never_removed(tmp_path: Path) -> None:
    baseline = _file(tmp_path / "backups" / "config" / "charles.config", 400)
    profile = _file(tmp_path / "backups" / "profiles" / "default.xml", 400)

    report = _purge(tmp_path, dry_run=False, scopes=["backups"])

    assert report.backups == []
    assert baseline.exists() and profile.exists()


def test_the_newest_backup_in_each_folder_survives(tmp_path: Path) -> None:
    oldest = _file(tmp_path / "backups" / "map-remote" / "a.config", 90)
    newest_but_old = _file(tmp_path / "backups" / "map-remote" / "b.config", 60)

    report = _purge(tmp_path, dry_run=False, scopes=["backups"])

    assert report.backups == ["map-remote/a.config"]
    assert not oldest.exists()
    # Older than the cut-off, but the only copy left to roll the change back.
    assert newest_but_old.exists()


def test_scopes_limit_what_is_touched(tmp_path: Path) -> None:
    recording = _file(tmp_path / "package" / "old.chlsj", 60)
    _file(tmp_path / "backups" / "map-remote" / "a.config", 90)
    _file(tmp_path / "backups" / "map-remote" / "b.config", 80)

    report = _purge(tmp_path, dry_run=False, scopes=["backups"])

    assert recording.exists()
    assert report.recordings == []
    assert report.backups == ["map-remote/a.config"]


def test_reverse_captures_and_their_bodies_are_removed_together(tmp_path: Path) -> None:
    database = tmp_path / "reverse.sqlite3"
    SQLiteStore(database).close()
    old = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(NOW - 60 * DAY))
    fresh = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(NOW - DAY))

    with sqlite3.connect(database) as connection:
        for capture_id, stamp in (("old", old), ("fresh", fresh), ("undated", None)):
            connection.execute(
                "INSERT INTO captures (capture_id, source_kind, source_format, started_at, "
                "ingest_status, entry_count, metadata_json) VALUES (?, 'live', 'json', ?, 'ok', 1, '{}')",
                (capture_id, stamp),
            )
            connection.execute(
                "INSERT INTO entries (entry_id, capture_id, sequence_no, method, host, path, "
                "timing_summary_json, size_summary_json, metadata_json) "
                "VALUES (?, ?, 1, 'GET', 'h', '/', '{}', '{}', '{}')",
                (f"e-{capture_id}", capture_id),
            )
            connection.execute(
                "INSERT INTO body_blobs (body_blob_id, storage_kind, is_binary, raw_text, "
                "preservation_level, metadata_json) VALUES (?, 'inline', 0, 'SECRET', 'full', '{}')",
                (f"b-{capture_id}",),
            )
            connection.execute(
                "INSERT INTO responses (response_id, entry_id, headers_json, set_cookies_json, "
                "body_blob_id, metadata_json) VALUES (?, ?, '[]', '[]', ?, '{}')",
                (f"r-{capture_id}", f"e-{capture_id}", f"b-{capture_id}"),
            )

    report = _purge(tmp_path, reverse_database=database, dry_run=False, scopes=["reverse"])

    assert report.reverse_captures == 1
    assert report.reverse_undated == 1
    with sqlite3.connect(database) as connection:
        captures = {row[0] for row in connection.execute("SELECT capture_id FROM captures")}
        blobs = {row[0] for row in connection.execute("SELECT body_blob_id FROM body_blobs")}
    assert captures == {"fresh", "undated"}
    # The body of the removed capture must not outlive it.
    assert blobs == {"b-fresh", "b-undated"}
