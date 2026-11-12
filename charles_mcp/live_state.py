"""Runtime state management for live Charles capture polling."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha1
from typing import Any, Literal
from uuid import uuid4

from charles_mcp.schemas.live_capture import LiveCaptureReadResult


class LiveCaptureError(Exception):
    """Base error for live capture state problems."""


class LiveCaptureConflictError(LiveCaptureError):
    """Raised when a new capture starts while another is still active."""


class LiveCaptureNotFoundError(LiveCaptureError):
    """Raised when a capture id does not exist."""


# Charles statuses of an entry that will not change any more. Anything else —
# RECEIVING_REQUEST_BODY, RECEIVING_RESPONSE_BODY, an open CONNECT tunnel — is
# still in flight, and Charles keeps updating its sizes, timings and status.
_FINAL_STATUSES = frozenset({"COMPLETE", "EXCEPTION"})


def is_final_status(status: object) -> bool:
    """Whether a Charles entry status means the entry is finished.

    A missing status counts as finished: saved recordings and synthetic entries
    do not always carry one, and nothing is going to update them.
    """
    if status is None or status == "":
        return True
    return str(status).upper() in _FINAL_STATUSES


@dataclass
class LiveCaptureState:
    """Mutable runtime state for an active or stopped live capture.

    ``entries`` holds every request of the capture once, in its latest state.
    ``feed`` is the append-only list of things worth telling a reader about: a
    request seen for the first time, and the same request again once it has
    finished. Cursors are positions in the feed, so they stay valid for as long
    as the Charles session is not cleared.
    """

    capture_id: str
    managed: bool
    # Whether traffic recorded before the capture started is visible. The running
    # server always sets this True (LiveCaptureService.start); the manager keeps the
    # toggle so its unit tests can exercise the False path directly.
    include_existing: bool
    status: Literal["active", "stopped"] = "active"
    cursor: int = 0
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    finished: set[str] = field(default_factory=set)
    feed: list[str] = field(default_factory=list)
    # Requests that were already in the session when a capture with
    # include_existing=False started: never shown, however they change later.
    hidden: set[str] = field(default_factory=set)
    last_export_count: int = 0
    # A cleared Charles session was noticed, and the reader has not been told yet.
    reset_pending: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def items(self) -> list[dict[str, Any]]:
        """Every request of the capture, once, in the order it was first seen."""
        return list(self.entries.values())


CONTROL_HOST = "control.charles"


def self_recording_warning(raw_items: list[dict[str, Any]]) -> str | None:
    """Warn when Charles has recorded this server's own requests to its Web Interface.

    Every tool call exports the session through the Charles proxy, and Charles
    records that request like any other — with the whole export as its response
    body. The next export then contains the previous one, so the session grows
    with every call: in one QA run it went from 100 KB to 2 GB and each call
    from 0.03 s to 25 s. The entries are filtered out of every result, which is
    exactly why nobody notices; this puts the cost back in view.
    """
    count = 0
    body_bytes = 0
    for entry in raw_items:
        if not isinstance(entry, dict) or entry.get("host") != CONTROL_HOST:
            continue
        count += 1
        sizes = (entry.get("response") or {}).get("sizes") or {}
        if isinstance(sizes.get("body"), int):
            body_bytes += sizes["body"]
    if not count:
        return None
    return (
        f"charles_records_own_exports: the session holds {count} request(s) to "
        f"{CONTROL_HOST} ({body_bytes / 1_048_576:.1f} MB) — this server's own session "
        "exports, recorded by Charles. Each tool call adds another, so the session keeps "
        "growing and every call gets slower. Fix once: exclude host "
        f"`{CONTROL_HOST}` in Proxy → Recording Settings → Exclude (by hand, no restart), "
        "or with charles_recording_exclude(apply=true) while Charles is closed; then clear "
        "the session."
    )


class LiveCaptureManager:
    """Track one active live capture and compute incremental diffs."""

    def __init__(self) -> None:
        self.active: LiveCaptureState | None = None

    def start(
        self,
        *,
        managed: bool,
        include_existing: bool,
        baseline_items: list[dict[str, Any]] | None = None,
    ) -> LiveCaptureState:
        if self.active and self.active.status == "active":
            raise LiveCaptureConflictError(
                f"capture `{self.active.capture_id}` is already active"
            )

        capture = LiveCaptureState(
            capture_id=str(uuid4()),
            managed=managed,
            include_existing=include_existing,
        )

        if baseline_items:
            prepared = self._iter_unique_entries(baseline_items)
            capture.last_export_count = len(prepared)
            if include_existing:
                # baseline_items come straight from a fresh session export that the
                # caller discards, so entries are stored by reference; a deepcopy
                # here doubles peak memory on a large session for no benefit.
                self._absorb(capture, prepared)
            else:
                capture.hidden.update(key for key, _ in prepared)
            if warning := self_recording_warning(baseline_items):
                capture.warnings.append(warning)

        self.active = capture
        return capture

    def require(self, capture_id: str) -> LiveCaptureState:
        if not self.active or self.active.capture_id != capture_id:
            raise LiveCaptureNotFoundError(f"live capture `{capture_id}` was not found")
        return self.active

    def close(self, capture_id: str) -> LiveCaptureState:
        capture = self.require(capture_id)
        capture.status = "stopped"
        self.active = None
        return capture

    def read(
        self,
        capture_id: str,
        raw_items: list[dict[str, Any]],
        *,
        cursor: int | None = None,
        limit: int = 50,
        advance: bool = True,
    ) -> LiveCaptureReadResult:
        """Take in a fresh session export and return what is new since ``cursor``.

        What the capture knows about the session is updated on every read. Only
        the cursor is the reader's own: ``advance=False`` looks without moving it.
        """
        capture = self.require(capture_id)

        if limit <= 0:
            raise ValueError("limit must be greater than 0")

        prepared = self._iter_unique_entries(raw_items)
        warnings: list[str] = []

        detected = len(prepared) < capture.last_export_count
        if detected:
            # The Charles session was cleared: everything known so far is gone,
            # and so is the meaning of every cursor handed out before.
            capture.entries.clear()
            capture.finished.clear()
            capture.feed.clear()
            capture.hidden.clear()
            capture.cursor = 0
            capture.reset_pending = True
        # The read that notices the reset reports it, and so does the next read
        # that moves the cursor: that reader's position is the one that was lost.
        reset = detected or (advance and capture.reset_pending)
        if reset:
            warnings.append("session_reset_detected")
        if warning := self_recording_warning(raw_items):
            warnings.append(warning)

        self._absorb(capture, prepared)
        capture.last_export_count = len(prepared)

        if reset:
            base_cursor = 0
        elif cursor is None:
            base_cursor = capture.cursor
        else:
            base_cursor = min(max(cursor, 0), len(capture.feed))
        status: Literal["active", "reset_detected", "stopped"] = (
            "reset_detected" if reset else capture.status
        )

        # A request can be in the feed twice (seen, then finished). A read returns
        # it once, in its latest state.
        pending = capture.feed[base_cursor:]
        total_new_items = len(set(pending))
        items: list[dict[str, Any]] = []
        taken: set[str] = set()
        consumed = 0
        for key in pending:
            if key not in taken:
                if len(items) >= limit:
                    break
                taken.add(key)
                items.append(capture.entries[key])
            consumed += 1
        next_cursor = base_cursor + consumed
        truncated = total_new_items > len(items)

        if advance:
            capture.cursor = next_cursor
            capture.reset_pending = False
            capture.warnings = warnings
            if capture.status != "stopped":
                capture.status = "active"

        return LiveCaptureReadResult(
            capture_id=capture.capture_id,
            status=status,
            items=items,
            next_cursor=next_cursor,
            total_new_items=total_new_items,
            truncated=truncated,
            warnings=warnings,
        )

    @staticmethod
    def _absorb(capture: LiveCaptureState, prepared: list[tuple[str, dict[str, Any]]]) -> None:
        """Store the latest state of every entry and queue what a reader should see.

        Charles keeps updating an entry while it is in flight: an open CONNECT
        tunnel changes its sizes and timings on every export. The entry is one
        request all along, so it is replaced in place. It goes to the feed when
        it is first seen and once more when it has finished, which is when its
        status and response become final.
        """
        for key, entry in prepared:
            if key in capture.hidden:
                continue
            is_new = key not in capture.entries
            capture.entries[key] = entry
            is_final = is_final_status(entry.get("status"))
            if is_new or (is_final and key not in capture.finished):
                capture.feed.append(key)
            if is_final:
                capture.finished.add(key)

    def _iter_unique_entries(
        self,
        raw_items: list[dict[str, Any]],
    ) -> list[tuple[str, dict[str, Any]]]:
        """Pair every entry of an export with a key that names the request itself."""
        counts: dict[str, int] = {}
        prepared: list[tuple[str, dict[str, Any]]] = []

        for entry in raw_items:
            if not isinstance(entry, dict):
                continue
            if entry.get("host") == CONTROL_HOST:
                continue

            fingerprint = self._fingerprint(entry)
            # Two requests can share every identifying field (synthetic entries,
            # exports of older Charles versions without client ports). Charles
            # keeps the export order, so the position among equals tells them apart.
            counts[fingerprint] = counts.get(fingerprint, 0) + 1
            prepared.append((f"{fingerprint}:{counts[fingerprint]}", entry))

        return prepared

    def _fingerprint(self, entry: dict[str, Any]) -> str:
        payload = self._fingerprint_components(entry)
        return sha1(payload.encode("utf-8")).hexdigest()

    def _fingerprint_components(self, entry: dict[str, Any]) -> str:
        """The fields that identify a request and never change while it runs.

        Status, sizes, timings and the response are deliberately left out: they
        change as the request proceeds, and a fingerprint built on them made
        every in-flight entry look like a new one on each export — the capture
        then listed the same request several times. Bodies are never part of it.
        """
        times = entry.get("times") or {}
        components = [
            str(entry.get("host") or ""),
            str(entry.get("method") or ""),
            str(entry.get("path") or ""),
            str(entry.get("query") or ""),
            str(times.get("start") or ""),
            str(entry.get("clientAddress") or ""),
            str(entry.get("clientPort") or ""),
        ]
        return "|".join(components)
