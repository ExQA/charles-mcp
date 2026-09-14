"""File-backed mock store served by a Charles Map Local directory mapping.

One Map Local rule per host maps ``https://<host>/*`` to ``<root>/<host>/``.
Charles serves ``<root>/<host>/<request path>`` when that file exists and
passes the request through to the real server otherwise, so every mock is
a plain file on disk and no Charles rule has to change at runtime.

Nothing is ever deleted: overwritten and removed mocks are moved under
``<root>/_archive/`` with a timestamp suffix.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ARCHIVE_DIR_NAME = "_archive"

_HOST_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOST_RE = re.compile(rf"^{_HOST_LABEL}(?:\.{_HOST_LABEL})*$")
_FORBIDDEN_SEGMENTS = {".", ".."}


class MockPathError(ValueError):
    """The host or path cannot be mapped to a mock file."""


class MockPathConflictError(MockPathError):
    """The mock file would collide with an existing file or directory."""


@dataclass(frozen=True)
class MockLocation:
    host: str
    path: str
    file: Path
    query_ignored: str | None = None


@dataclass(frozen=True)
class MockRecord:
    host: str
    path: str
    file: str
    size_bytes: int
    modified_at: str


def normalize_host(host: str) -> str:
    value = (host or "").strip().lower().rstrip(".")
    if value.startswith(("http://", "https://")):
        raise MockPathError("host must be a bare hostname, without scheme: api.example.com")
    if ":" in value:
        raise MockPathError(
            "host must not include a port; Map Local rules in this store are per hostname"
        )
    if not _HOST_RE.match(value):
        raise MockPathError(f"invalid host `{host}`")
    return value


ANY_HOST = "*"
_WILDCARD_LABEL = r"(?:\*|[a-z0-9*](?:[a-z0-9*-]{0,61}[a-z0-9*])?)"
_HOST_PATTERN_RE = re.compile(rf"^{_WILDCARD_LABEL}(?:\.{_WILDCARD_LABEL})*$")


def is_host_pattern(value: str) -> bool:
    return "*" in value


def normalize_host_pattern(host: str) -> str:
    """Accept an exact hostname, ``*`` (any host) or a glob such as ``*.example.com``."""
    value = (host or "").strip().lower().rstrip(".")
    if not is_host_pattern(value):
        return normalize_host(value)
    if value.startswith(("http://", "https://")) or ":" in value:
        raise MockPathError("host pattern must be a bare hostname glob, e.g. *.example.com")
    if value != ANY_HOST and not _HOST_PATTERN_RE.match(value):
        raise MockPathError(f"invalid host pattern `{host}`")
    return value


def split_request_path(path: str) -> tuple[list[str], str | None]:
    """Split a request path into file-system segments.

    Returns the segments and the query string that was stripped, if any.
    Charles Map Local ignores the query string, so ``/items?page=1`` and
    ``/items?page=2`` are served by the same file.
    """
    raw = (path or "").strip()
    query: str | None = None
    if "?" in raw:
        raw, query = raw.split("?", 1)
    if "#" in raw:
        raw = raw.split("#", 1)[0]
    if not raw.startswith("/"):
        raise MockPathError(f"path must start with `/`: `{path}`")
    if raw.endswith("/"):
        raise MockPathError(
            f"path `{raw}` ends with `/`; a mock must name a file, e.g. /api/v1/items"
        )

    segments = raw[1:].split("/")
    for segment in segments:
        if not segment:
            raise MockPathError(f"path `{raw}` contains an empty segment (`//`)")
        if segment in _FORBIDDEN_SEGMENTS:
            raise MockPathError(f"path `{raw}` must not contain `.` or `..` segments")
        if "\\" in segment or any(ord(ch) < 32 for ch in segment):
            raise MockPathError(f"path `{raw}` contains a forbidden character")
    return segments, query or None


class MockStore:
    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root).expanduser()

    @property
    def archive_root(self) -> Path:
        return self.root / ARCHIVE_DIR_NAME

    def host_dir(self, host: str) -> Path:
        return self.root / normalize_host(host)

    def locate(self, host: str, path: str) -> MockLocation:
        normalized_host = normalize_host(host)
        segments, query = split_request_path(path)
        host_dir = self.root / normalized_host
        target = host_dir.joinpath(*segments)

        resolved_root = self.root.resolve()
        if not target.resolve().is_relative_to(resolved_root / normalized_host):
            raise MockPathError(f"path `{path}` escapes the mock directory")

        return MockLocation(
            host=normalized_host,
            path="/" + "/".join(segments),
            file=target,
            query_ignored=query,
        )

    def ensure_host_dir(self, host: str) -> Path:
        directory = self.host_dir(host)
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def write(self, host: str, path: str, content: str) -> tuple[MockRecord, str | None]:
        """Write a mock atomically. Returns the record and the archived previous file."""
        location = self.locate(host, path)
        self._check_conflicts(location)

        archived: str | None = None
        if location.file.is_file():
            archived = str(self._archive(location))

        location.file.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{location.file.name}.", suffix=".tmp", dir=location.file.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(content)
            os.replace(tmp_name, location.file)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        return self._record(location.host, location.path, location.file), archived

    def read(self, host: str, path: str) -> tuple[MockRecord, str]:
        location = self.locate(host, path)
        if not location.file.is_file():
            raise FileNotFoundError(f"no mock for {location.host}{location.path}")
        content = location.file.read_text(encoding="utf-8")
        return self._record(location.host, location.path, location.file), content

    def remove(self, host: str, path: str) -> str:
        """Move a mock to the archive so Charles passes the request through again."""
        location = self.locate(host, path)
        if not location.file.is_file():
            raise FileNotFoundError(f"no mock for {location.host}{location.path}")
        archived = self._archive(location)
        self._prune_empty_dirs(location.file.parent, stop_at=self.root / location.host)
        return str(archived)

    def list_mocks(self, host: str | None = None) -> list[MockRecord]:
        if not self.root.is_dir():
            return []
        if host is not None:
            host_dirs = [self.host_dir(host)]
        else:
            host_dirs = [
                child
                for child in self.root.iterdir()
                if child.is_dir() and child.name != ARCHIVE_DIR_NAME
                and not child.name.startswith(".")
            ]

        records: list[MockRecord] = []
        for host_dir in sorted(host_dirs):
            if not host_dir.is_dir():
                continue
            for file in sorted(host_dir.rglob("*")):
                relative = file.relative_to(host_dir)
                if not file.is_file() or any(part.startswith(".") for part in relative.parts):
                    continue
                records.append(
                    self._record(host_dir.name, "/" + "/".join(relative.parts), file)
                )
        return records

    def _check_conflicts(self, location: MockLocation) -> None:
        if location.file.is_dir():
            raise MockPathConflictError(
                f"{location.host}{location.path} is already a directory of other mocks; "
                "Map Local cannot serve a file and a directory at the same path"
            )
        host_dir = self.root / location.host
        parent = location.file.parent
        while parent != host_dir and parent != parent.parent:
            if parent.is_file():
                blocking = "/" + "/".join(parent.relative_to(host_dir).parts)
                raise MockPathConflictError(
                    f"mock {location.host}{blocking} is a file, so {location.path} "
                    "cannot be created under it; remove that mock first"
                )
            parent = parent.parent

    def _archive(self, location: MockLocation) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        relative = location.file.relative_to(self.root)
        destination = self.archive_root / relative.parent / f"{relative.name}@{stamp}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(location.file), str(destination))
        return destination

    @staticmethod
    def _prune_empty_dirs(directory: Path, *, stop_at: Path) -> None:
        while directory != stop_at and directory.is_relative_to(stop_at):
            try:
                directory.rmdir()
            except OSError:
                return
            directory = directory.parent

    @staticmethod
    def _record(host: str, path: str, file: Path) -> MockRecord:
        stat = file.stat()
        return MockRecord(
            host=host,
            path=path,
            file=str(file),
            size_bytes=stat.st_size,
            modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        )
