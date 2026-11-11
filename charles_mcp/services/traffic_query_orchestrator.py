from __future__ import annotations

import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha1
from typing import TypeVar, cast

from charles_mcp.analyzers.resource_classifier import classify_entry
from charles_mcp.live_state import is_final_status
from charles_mcp.schemas.analysis import TrafficDetailResult, TrafficQueryResult
from charles_mcp.schemas.traffic import (
    CaptureSource,
    ResourceClass,
    ResourceClassification,
    TrafficEntry,
    TrafficMatch,
)
from charles_mcp.schemas.traffic_query import TrafficQuery
from charles_mcp.services.history_capture import RecordingHistoryService
from charles_mcp.services.live_capture import LiveCaptureService
from charles_mcp.services.traffic_analysis import TrafficAnalysisService
from charles_mcp.services.traffic_cache import SUMMARY_BODY_MODE, TrafficEntryCache
from charles_mcp.services.traffic_normalizer import TrafficNormalizer
from charles_mcp.services.traffic_query_models import PreparedTrafficEntries

# Cached classify/normalize result for one raw entry. entry=None means the
# entry has been classified but not normalized yet: it was outside the scan
# limit, ruled out by a raw filter, or excluded by the preset of that call.
# Whether an entry is excluded is decided on every call from the cached
# classification, never from the cached entry, so presets cannot leak into
# each other.
_NormalizeCacheValue = tuple[ResourceClassification, TrafficEntry | None]

# A live read with no upper bound. Analysis looks at the whole capture and
# decides itself which entries are worth normalizing.
READ_ALL = sys.maxsize

_T = TypeVar("_T")


def entry_start_timestamp(raw_entry: dict) -> float | None:
    """When Charles started recording this entry, as a POSIX timestamp.

    None when ``times.start`` is missing or unparseable. A naive timestamp is
    read as UTC, which matches Charles' default export format.
    """
    times = raw_entry.get("times")
    start_value = times.get("start") if isinstance(times, dict) else None
    if not isinstance(start_value, str) or not start_value:
        return None
    try:
        parsed = datetime.fromisoformat(start_value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def keep_newest(items: list[_T], limit: int, *, raw_of: Callable[[_T], dict]) -> list[_T]:
    """The ``limit`` most recently started items, in their original order.

    Charles exports a session in start order, but a live capture appends
    entries as it discovers them, so position alone is not a safe proxy for
    age. Entries without a parseable start time count as newest: they are
    usually requests still in flight.
    """
    if len(items) <= limit:
        return items

    def rank(index: int) -> tuple[float, int]:
        started = entry_start_timestamp(raw_of(items[index]))
        return (float("inf") if started is None else started, index)

    keep = sorted(sorted(range(len(items)), key=rank)[-limit:])
    return [items[index] for index in keep]


@dataclass(frozen=True)
class _RawFilter:
    """The host, path and method filters of a query, applied to raw Charles entries.

    They mirror TrafficAnalysisService.match_entry exactly, so an entry dropped
    here could never have matched. Checking them before normalization lets the
    scan limit go to entries that still can.
    """

    host: str | None
    path: str | None
    methods: frozenset[str]

    @classmethod
    def from_query(cls, query: TrafficQuery) -> _RawFilter:
        return cls(
            host=query.host_contains.lower() if query.host_contains else None,
            path=query.path_contains.lower() if query.path_contains else None,
            methods=frozenset(item.upper() for item in query.method_in),
        )

    def matches(self, raw_entry: dict) -> bool:
        if self.host is not None and self.host not in str(raw_entry.get("host") or "").lower():
            return False
        if self.path is not None and self.path not in str(raw_entry.get("path") or "").lower():
            return False
        if self.methods and str(raw_entry.get("method") or "").upper() not in self.methods:
            return False
        return True


class TrafficQueryOrchestrator:
    """Coordinate loading, caching, and summarizing live/history traffic."""

    def __init__(
        self,
        *,
        live_service: LiveCaptureService,
        history_service: RecordingHistoryService,
        normalizer: TrafficNormalizer,
        analysis_service: TrafficAnalysisService,
        entry_cache: TrafficEntryCache,
    ) -> None:
        self.live_service = live_service
        self.history_service = history_service
        self.normalizer = normalizer
        self.analysis_service = analysis_service
        self.entry_cache = entry_cache
        # Diff cache keyed by (source, identity, query_shape) -> raw_hash -> value.
        # Lets repeated live polls reuse normalize/classify output for entries
        # whose stable routing fields are unchanged since the previous call.
        self._normalize_cache: dict[
            tuple[str, str, bool, int, int],
            dict[str, _NormalizeCacheValue],
        ] = {}
        self.normalize_cache_stats: dict[str, int] = {"hits": 0, "misses": 0}

    def reset_normalize_cache(self) -> None:
        """Drop all cached normalize/classify results (used in tests)."""
        self._normalize_cache.clear()
        self.normalize_cache_stats = {"hits": 0, "misses": 0}

    async def analyze_live_capture(
        self,
        *,
        capture_id: str,
        query: TrafficQuery,
        cursor: int | None = None,
    ) -> TrafficQueryResult:
        prepared = await self.prepare_capture(
            source="live",
            query=query,
            capture_id=capture_id,
            cursor=cursor,
            advance=False,
        )
        return self.build_query_result(prepared=prepared, query=query, include_items=True)

    async def analyze_recorded_traffic(
        self,
        *,
        recording_path: str | None,
        query: TrafficQuery,
    ) -> TrafficQueryResult:
        try:
            prepared = await self.prepare_capture(
                source="history",
                query=query,
                recording_path=recording_path,
                advance=False,
            )
        except FileNotFoundError:
            return TrafficQueryResult(
                source="history",
                items=[],
                total_items=0,
                scanned_count=0,
                matched_count=0,
                filtered_out_count=0,
                filtered_out_by_class={},
                warnings=["no_saved_recordings"],
            )
        return self.build_query_result(prepared=prepared, query=query, include_items=True)

    async def prepare_capture(
        self,
        *,
        source: CaptureSource,
        query: TrafficQuery,
        capture_id: str | None = None,
        recording_path: str | None = None,
        cursor: int | None = None,
        advance: bool = False,
    ) -> PreparedTrafficEntries:
        if source == "live":
            if not capture_id:
                raise ValueError("capture_id is required for live analysis")
            # Read every entry from the cursor on. The scan limit is applied in
            # _prepare_entries, after the cheap filters, so it keeps the newest
            # candidates instead of whatever the session happened to start with.
            live_result = await self.live_service.read(
                capture_id,
                cursor=cursor if cursor is not None else 0,
                limit=READ_ALL,
                advance=advance,
            )
            return self._prepare_entries(
                source="live",
                raw_entries=live_result.items,
                query=query,
                capture_id=capture_id,
                next_cursor=live_result.next_cursor,
                total_items=live_result.total_new_items,
                initial_warnings=list(live_result.warnings),
            )

        if source == "history":
            source_path, raw_entries = await self._load_history_entries(recording_path)
            return self._prepare_entries(
                source="history",
                raw_entries=raw_entries,
                query=query,
                recording_path=source_path,
            )

        raise ValueError("source must be `live` or `history`")

    def build_query_result(
        self,
        *,
        prepared: PreparedTrafficEntries,
        query: TrafficQuery,
        include_items: bool,
    ) -> TrafficQueryResult:
        matched_entries = list(prepared.matched_entries)
        matched_entries.sort(key=self._summary_sort_key, reverse=True)

        matched_summaries = []
        if include_items:
            matched_summaries = [
                self.analysis_service.summarize_entry(
                    entry,
                    match,
                    max_headers_per_side=query.max_headers_per_side,
                    include_body_preview=query.include_body_preview,
                )
                for entry, match in matched_entries[: query.max_items]
            ]

        truncated = prepared.truncated
        if include_items and len(matched_entries) > query.max_items:
            truncated = True

        return TrafficQueryResult(
            source=prepared.source,
            items=matched_summaries if include_items else [],
            total_items=prepared.total_items,
            scanned_count=prepared.scanned_count,
            matched_count=prepared.matched_count,
            filtered_out_count=prepared.filtered_out_count,
            filtered_out_by_class=prepared.filtered_out_by_class,
            next_cursor=prepared.next_cursor,
            truncated=truncated,
            warnings=prepared.warnings,
        )

    async def get_detail(
        self,
        *,
        source: CaptureSource,
        entry_id: str,
        capture_id: str | None = None,
        recording_path: str | None = None,
        include_full_body: bool = False,
        max_body_chars: int = 4096,
    ) -> TrafficDetailResult:
        if source not in {"live", "history"}:
            raise ValueError("source must be `live` or `history`")

        identity = self._resolve_identity(
            source=source,
            entry_id=entry_id,
            capture_id=capture_id,
            recording_path=recording_path,
        )
        body_mode = self._body_mode(include_full_body=include_full_body, max_body_chars=max_body_chars)
        entry = self.entry_cache.get_entry(
            source=source,
            identity=identity,
            entry_id=entry_id,
            body_mode=body_mode,
        )
        if entry is not None and not is_final_status(entry.status):
            # Cached while the request was still in flight: its response may
            # have arrived since, so look again instead of serving half of it.
            entry = None

        if entry is None:
            entry = await self._hydrate_and_cache_entry(
                source=source,
                identity=identity,
                entry_id=entry_id,
                include_full_body=include_full_body,
                max_body_chars=max_body_chars,
            )

        if entry is None:
            raise ValueError(
                f"traffic entry `{entry_id}` was not found in `{identity}`. An entry_id changes "
                "when a request that was still in flight completes; query the capture again "
                "and use the id it returns now."
            )

        detail = self.analysis_service.build_detail(entry)
        warnings: list[str] = []
        estimated_chars = len(detail.model_dump_json(exclude_none=True))
        if estimated_chars > 12_000:
            warnings.append(
                f"large_response:{estimated_chars}_chars. "
                "Consider using a smaller max_body_chars or include_full_body=false."
            )
        return TrafficDetailResult(
            source=source,
            entry_id=entry_id,
            detail=detail,
            warnings=warnings,
        )

    async def _hydrate_and_cache_entry(
        self,
        *,
        source: CaptureSource,
        identity: str,
        entry_id: str,
        include_full_body: bool,
        max_body_chars: int,
    ) -> TrafficEntry | None:
        """Find one entry by id anywhere in the capture or recording.

        The id is computed from routing fields alone, so only the entry that
        matches is normalized; the bodies of all the others are never parsed.
        """
        capture_id: str | None
        recording_path: str | None
        if source == "history":
            capture_id = None
            recording_path = identity
            raw = self._find_raw_entry(
                await self.history_service.get_snapshot(identity),
                source=source,
                capture_id=capture_id,
                recording_path=recording_path,
                entry_id=entry_id,
            )
        else:
            capture_id = identity
            recording_path = None
            # A finished request does not change any more, so the copy from the
            # last export is as good as a new one — and a new one costs a whole
            # session export, tens of seconds on a long session.
            raw = self._find_raw_entry(
                self._known_live_entries(identity),
                source=source,
                capture_id=capture_id,
                recording_path=recording_path,
                entry_id=entry_id,
            )
            if raw is None or not is_final_status(raw.get("status")):
                live_result = await self.live_service.read(
                    identity,
                    cursor=0,
                    limit=READ_ALL,
                    advance=False,
                )
                raw = self._find_raw_entry(
                    live_result.items,
                    source=source,
                    capture_id=capture_id,
                    recording_path=recording_path,
                    entry_id=entry_id,
                )

        if raw is not None:
            classification = classify_entry(raw)
            if include_full_body:
                hydrated = self.normalizer.normalize_entry(
                    raw,
                    capture_source=source,
                    capture_id=capture_id,
                    recording_path=recording_path,
                    include_full_body=True,
                    max_preview_chars=min(max_body_chars, 1024),
                    max_headers_per_side=16,
                    max_full_body_chars=max_body_chars,
                    classification=classification,
                )
                # One entry per full-body scope: a hydrated body can be large,
                # and the next lookup with this body size replaces it.
                self.entry_cache.put(
                    source=source,
                    identity=identity,
                    body_mode=self._body_mode(include_full_body=True, max_body_chars=max_body_chars),
                    entries={entry_id: hydrated},
                )
            else:
                hydrated = self.normalizer.normalize_entry(
                    raw,
                    capture_source=source,
                    capture_id=capture_id,
                    recording_path=recording_path,
                    classification=classification,
                )
                self.entry_cache.add_entry(
                    source=source,
                    identity=identity,
                    body_mode=SUMMARY_BODY_MODE,
                    entry=hydrated,
                )
            return hydrated

        return None

    def _known_live_entries(self, capture_id: str) -> list[dict]:
        """What the live service already holds for the capture; empty if it cannot say."""
        known = getattr(self.live_service, "known_entries", None)
        if known is None:
            return []
        try:
            return list(known(capture_id))
        except Exception:
            # An unknown capture is reported by the read that follows, with its own error.
            return []

    def _find_raw_entry(
        self,
        raw_entries: list[dict],
        *,
        source: CaptureSource,
        capture_id: str | None,
        recording_path: str | None,
        entry_id: str,
    ) -> dict | None:
        for raw in raw_entries:
            if not isinstance(raw, dict):
                continue
            candidate_id = self.normalizer.entry_id_for(
                raw,
                capture_source=source,
                capture_id=capture_id,
                recording_path=recording_path,
            )
            if candidate_id == entry_id:
                return raw
        return None

    async def _load_history_entries(self, recording_path: str | None) -> tuple[str, list[dict]]:
        if recording_path:
            return recording_path, await self.history_service.get_snapshot(recording_path)
        return await self.history_service.load_latest_with_path()

    def _resolve_identity(
        self,
        *,
        source: CaptureSource,
        entry_id: str,
        capture_id: str | None,
        recording_path: str | None,
    ) -> str:
        if source == "live":
            if not capture_id:
                raise ValueError("capture_id is required for live detail lookup")
            return capture_id

        if recording_path:
            return recording_path

        resolved = self.entry_cache.resolve_identity(source=source, entry_id=entry_id)
        if resolved is not None:
            return resolved

        raise ValueError(
            "recording_path is required for history detail lookup when the entry is not already cached"
        )

    def _prepare_entries(
        self,
        *,
        source: CaptureSource,
        raw_entries: list[dict],
        query: TrafficQuery,
        capture_id: str | None = None,
        recording_path: str | None = None,
        next_cursor: int | None = None,
        total_items: int | None = None,
        initial_warnings: list[str] | None = None,
    ) -> PreparedTrafficEntries:
        warnings = list(initial_warnings or [])
        total_items = total_items if total_items is not None else len(raw_entries)

        # errors_only preset: inject has_error filter so only error traffic matches
        effective_query = query
        if query.preset == "errors_only" and query.has_error is None:
            effective_query = query.model_copy(update={"has_error": True})
        needs_body_search = bool(
            effective_query.request_body_contains or effective_query.response_body_contains
        )

        identity = capture_id if source == "live" else recording_path
        scope_key = self._normalize_scope_key(
            source=source,
            identity=identity,
            needs_body_search=needs_body_search,
            max_preview_chars=effective_query.max_preview_chars,
            max_headers_per_side=effective_query.max_headers_per_side,
        )
        scope_cache: dict[str, _NormalizeCacheValue] | None = (
            self._normalize_cache.setdefault(scope_key, {}) if scope_key is not None else None
        )
        seen_hashes: set[str] = set()

        filtered_out_by_class: Counter[ResourceClass] = Counter()
        classified_counts: Counter[ResourceClass] = Counter()

        # Pass 1, cheap, over every entry. The time window, the preset's noise
        # classes and the host/path/method filters need only raw routing
        # fields. Deciding them here, before the scan limit, spends the limit
        # on entries that can still match instead of on whatever came first:
        # a long session used to hide its newest traffic behind the limit.
        window_start = (
            datetime.now(timezone.utc).timestamp() - query.since_seconds
            if query.since_seconds is not None
            else None
        )
        raw_filter = _RawFilter.from_query(effective_query)
        scanned_count = 0
        candidates: list[tuple[dict, str, ResourceClassification]] = []

        for raw in raw_entries:
            if not isinstance(raw, dict):
                continue

            raw_hash = self._stable_raw_entry_hash(raw)
            if scope_cache is not None:
                seen_hashes.add(raw_hash)

            if window_start is not None and not self._started_since(raw, window_start):
                continue
            scanned_count += 1

            cached = scope_cache.get(raw_hash) if scope_cache is not None else None
            if cached is not None:
                classification = cached[0]
            else:
                classification = classify_entry(raw)
                if scope_cache is not None:
                    scope_cache[raw_hash] = (classification, None)
            classified_counts[classification.resource_class] += 1

            if self._excluded_by_preset(classification.resource_class, effective_query):
                filtered_out_by_class[classification.resource_class] += 1
                continue
            if not raw_filter.matches(raw):
                continue
            candidates.append((raw, raw_hash, classification))

        truncated = len(candidates) > query.scan_limit
        if truncated:
            warnings.append("scan_limit_reached")
            warnings.append(
                f"analysed the newest {query.scan_limit} of {len(candidates)} candidate entries; "
                "narrow with since_seconds, host_contains or path_contains, or raise scan_limit"
            )
            candidates = keep_newest(candidates, query.scan_limit, raw_of=lambda item: item[0])

        # Pass 2: normalize and match what is left.
        matched_entries: list[tuple[TrafficEntry, TrafficMatch]] = []
        detail_entries: dict[str, TrafficEntry] = {}

        for raw, raw_hash, classification in candidates:
            cached = scope_cache.get(raw_hash) if scope_cache is not None else None
            entry = cached[1] if cached is not None else None
            if entry is not None:
                self.normalize_cache_stats["hits"] += 1
            else:
                entry = self.normalizer.normalize_entry(
                    raw,
                    capture_source=source,
                    capture_id=capture_id,
                    recording_path=recording_path,
                    include_full_body=needs_body_search,
                    max_preview_chars=effective_query.max_preview_chars,
                    max_headers_per_side=effective_query.max_headers_per_side,
                    max_full_body_chars=(
                        self._body_match_chars(raw) if needs_body_search else 4096
                    ),
                    classification=classification,
                )
                if scope_cache is not None:
                    # Cache the entry with full body intact so subsequent
                    # body_contains matches can still see it. The compact
                    # form is only used for downstream storage, not matching.
                    scope_cache[raw_hash] = (classification, entry)
                self.normalize_cache_stats["misses"] += 1

            match = self.analysis_service.match_entry(entry, effective_query)
            storage_entry = (
                self._compact_entry_for_cache(entry) if needs_body_search else entry
            )
            detail_entries[storage_entry.entry_id] = storage_entry
            if match.matched:
                matched_entries.append((storage_entry, match))

        if scope_cache is not None:
            # Only entries that left the capture are dropped; an entry outside
            # this call's time window is still there and keeps its cached value.
            stale_hashes = set(scope_cache.keys()) - seen_hashes
            for stale_hash in stale_hashes:
                scope_cache.pop(stale_hash, None)

        if identity:
            self.entry_cache.put(
                source=source,
                identity=identity,
                body_mode=SUMMARY_BODY_MODE,
                entries=detail_entries,
                classified_counts=cast(dict[str, int], dict(classified_counts)),
            )

        return PreparedTrafficEntries(
            source=source,
            identity=identity,
            total_items=total_items,
            scanned_count=scanned_count,
            matched_count=len(matched_entries),
            filtered_out_count=sum(filtered_out_by_class.values()),
            filtered_out_by_class=dict(filtered_out_by_class),
            matched_entries=matched_entries,
            next_cursor=next_cursor,
            truncated=truncated,
            warnings=warnings,
        )

    @staticmethod
    def _body_mode(*, include_full_body: bool, max_body_chars: int) -> str:
        if not include_full_body:
            return SUMMARY_BODY_MODE
        return f"full:{max_body_chars}"

    @staticmethod
    def _started_since(raw_entry: dict, window_start: float) -> bool:
        """Whether the entry started inside the trailing time window.

        Entries with an unparseable or missing times.start are kept so
        in-flight items are not silently dropped.
        """
        started = entry_start_timestamp(raw_entry)
        return started is None or started >= window_start

    @staticmethod
    def _normalize_scope_key(
        *,
        source: CaptureSource,
        identity: str | None,
        needs_body_search: bool,
        max_preview_chars: int,
        max_headers_per_side: int,
    ) -> tuple[str, str, bool, int, int] | None:
        # Without an identity we cannot safely correlate entries across calls;
        # disable the cache rather than risk cross-capture contamination.
        if not identity:
            return None
        return (source, identity, needs_body_search, max_preview_chars, max_headers_per_side)

    @staticmethod
    def _stable_raw_entry_hash(raw_entry: dict) -> str:
        """Stable fingerprint of a raw Charles entry's routing fields.

        Excludes capture identity because the normalize cache is already
        partitioned by (source, identity). The fields chosen match what
        Charles fills in once a response is fully captured, so the hash
        only stays stable for completed entries.
        """
        times_raw = raw_entry.get("times")
        times = times_raw if isinstance(times_raw, dict) else {}
        response_raw = raw_entry.get("response")
        response = response_raw if isinstance(response_raw, dict) else {}
        components = [
            str(raw_entry.get("host") or ""),
            str(raw_entry.get("method") or ""),
            str(raw_entry.get("path") or ""),
            str(raw_entry.get("query") or ""),
            str(raw_entry.get("status") or ""),
            str(response.get("status") or ""),
            str(times.get("start") or ""),
            str(times.get("end") or ""),
            str(raw_entry.get("totalSize") or ""),
        ]
        return sha1("|".join(components).encode("utf-8")).hexdigest()

    @staticmethod
    def _body_match_chars(raw_entry: dict) -> int:
        lengths = []
        for side in ("request", "response"):
            message = raw_entry.get(side) or {}
            body = message.get("body") or {}
            text = body.get("text")
            if text not in (None, ""):
                lengths.append(len(str(text)))
        return max(lengths, default=4096)

    @staticmethod
    def _compact_entry_for_cache(entry: TrafficEntry) -> TrafficEntry:
        updates: dict[str, object] = {}
        for side in ("request", "response"):
            message = getattr(entry, side)
            body = message.body
            if body.full_text is None and not body.full_text_truncated:
                continue
            updates[side] = message.model_copy(
                update={
                    "body": body.model_copy(
                        update={"full_text": None, "full_text_truncated": False}
                    )
                }
            )
        if updates:
            return entry.model_copy(update=updates)
        return entry

    @staticmethod
    def _excluded_by_preset(resource_class: str, query: TrafficQuery) -> bool:
        if query.preset == "all_http":
            return resource_class == "control"
        # api_focus, errors_only, page_bootstrap all exclude the same noise classes
        return resource_class in {"control", "static_asset", "font", "media", "connect_tunnel"}

    @staticmethod
    def _summary_sort_key(item: tuple[TrafficEntry, TrafficMatch]) -> tuple[int, int, str]:
        entry, _ = item
        start_time = str((entry.times or {}).get("start") or "")
        error_rank = 1 if (entry.response_status or 0) >= 400 or entry.error_message else 0
        return (entry.priority_score, error_rank, start_time)
