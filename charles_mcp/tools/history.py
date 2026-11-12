from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from charles_mcp.schemas.analysis import (
    CaptureAnalysisGroupsResult,
    CaptureAnalysisStatsResult,
    TrafficDetailResult,
    TrafficGroupBy,
    TrafficQueryResult,
)
from charles_mcp.schemas.history import (
    RecordedTrafficQueryResult,
    RecordingListResult,
    RecordingSnapshotResult,
)
from charles_mcp.schemas.traffic import CaptureSource
from charles_mcp.schemas.traffic_query import TrafficPreset
from charles_mcp.tools.tool_contract import (
    HostContains,
    HttpMethodFilter,
    KeywordRegex,
    ToolContext,
    build_tool_guidance_error,
    build_traffic_query,
    get_tool_dependencies,
    guidance_error_message,
    normalize_http_method,
    normalize_text_filter,
    raw_body_preview,
    summarize_raw_entry,
)

# A saved recording is a whole Charles session with every body in it. Returned
# as it is, one call put 2.4 million characters into the agent's context for a
# recording of 200 requests, so these tools answer with compact summaries and
# say how much they left out.
_MAX_RECORDING_ITEMS = 200


def _compact_recorded_entry(
    raw: dict[str, Any],
    *,
    entry_id: str,
    keep_request: bool = False,
    keep_response: bool = False,
) -> dict[str, Any]:
    """Routing fields of one recorded entry, its id, and short body previews on request."""
    summary = summarize_raw_entry(raw)
    summary["entry_id"] = entry_id
    truncated = False
    for wanted, side in ((keep_request, "request"), (keep_response, "response")):
        if not wanted:
            continue
        preview, cut = raw_body_preview(raw.get(side))
        if preview is not None:
            summary[f"{side}_body_preview"] = preview
            truncated = truncated or cut
    if truncated:
        summary["preview_truncated"] = True
    if "_match_location" in raw:
        summary["_match_location"] = raw["_match_location"]
    return summary


def _page_size(name: str, value: int) -> int:
    if value < 1:
        raise ValueError(f"`{name}` must be at least 1")
    return min(value, _MAX_RECORDING_ITEMS)


def register_history_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    async def analyze_recorded_traffic(
        ctx: ToolContext,
        recording_path: str | None = None,
        preset: TrafficPreset = "api_focus",
        host_contains: str | None = None,
        path_contains: str | None = None,
        method_in: list[str] | None = None,
        status_in: list[int] | None = None,
        resource_class_in: list[str] | None = None,
        min_priority_score: int | None = None,
        request_header_name: str | None = None,
        request_header_value_contains: str | None = None,
        response_header_name: str | None = None,
        response_header_value_contains: str | None = None,
        request_content_type: str | None = None,
        response_content_type: str | None = None,
        request_body_contains: str | None = None,
        response_body_contains: str | None = None,
        request_json_query: str | None = None,
        response_json_query: str | None = None,
        include_body_preview: bool = True,
        max_items: int = 10,
        max_preview_chars: int = 128,
        max_headers_per_side: int = 6,
        scan_limit: int = 500,
    ) -> TrafficQueryResult:
        """Analyze a saved recording snapshot with compact summaries.
        HISTORY-PLANE TOOL — only use when the user explicitly references a saved
        recording (.chlsj file). For ongoing / live traffic, prefer
        start_live_capture + query_live_capture_entries instead.
        Preserve recording_path for follow-up detail calls.
        Returns structured TrafficSummary items with matched_fields and match_reasons.
        Use get_traffic_entry_detail to drill down into a specific entry_id afterwards."""
        deps = get_tool_dependencies(ctx)
        query = build_traffic_query(
            preset=preset,
            host_contains=host_contains,
            path_contains=path_contains,
            method_in=method_in,
            status_in=status_in,
            resource_class_in=resource_class_in,
            min_priority_score=min_priority_score,
            request_header_name=request_header_name,
            request_header_value_contains=request_header_value_contains,
            response_header_name=response_header_name,
            response_header_value_contains=response_header_value_contains,
            request_content_type=request_content_type,
            response_content_type=response_content_type,
            request_body_contains=request_body_contains,
            response_body_contains=response_body_contains,
            request_json_query=request_json_query,
            response_json_query=response_json_query,
            include_body_preview=include_body_preview,
            max_items=max_items,
            max_preview_chars=max_preview_chars,
            max_headers_per_side=max_headers_per_side,
            scan_limit=scan_limit,
        )
        return await deps.traffic_query_service.analyze_recorded_traffic(
            recording_path=recording_path,
            query=query,
        )

    @mcp.tool()
    async def get_traffic_entry_detail(
        ctx: ToolContext,
        source: CaptureSource,
        entry_id: str,
        capture_id: str | None = None,
        recording_path: str | None = None,
        include_full_body: bool = False,
        max_body_chars: int = 2048,
    ) -> TrafficDetailResult:
        """Load one traffic entry detail view for drill-down inspection.
        Requires entry_id from a prior summary/query call.
        Use detail for one confirmed target, not bulk browsing.
        For history entries, pass recording_path from the summary.
        For live entries, pass capture_id from the summary.
        Keep include_full_body=false unless you specifically need the raw body text."""
        deps = get_tool_dependencies(ctx)
        return await deps.traffic_query_service.get_detail(
            source=source,
            entry_id=entry_id,
            capture_id=capture_id,
            recording_path=recording_path,
            include_full_body=include_full_body,
            max_body_chars=max_body_chars,
        )

    @mcp.tool()
    async def get_capture_analysis_stats(
        ctx: ToolContext,
        source: CaptureSource,
        capture_id: str | None = None,
        recording_path: str | None = None,
        preset: TrafficPreset = "api_focus",
        scan_limit: int = 500,
    ) -> CaptureAnalysisStatsResult:
        """Return coarse traffic class counts for a live capture or saved recording."""
        deps = get_tool_dependencies(ctx)
        return await deps.traffic_query_service.get_stats(
            source=source,
            capture_id=capture_id,
            recording_path=recording_path,
            preset=preset,
            scan_limit=scan_limit,
        )

    @mcp.tool()
    async def group_capture_analysis(
        ctx: ToolContext,
        source: CaptureSource,
        group_by: TrafficGroupBy,
        capture_id: str | None = None,
        recording_path: str | None = None,
        preset: TrafficPreset = "api_focus",
        host_contains: str | None = None,
        path_contains: str | None = None,
        method_in: list[str] | None = None,
        status_in: list[int] | None = None,
        resource_class_in: list[str] | None = None,
        min_priority_score: int | None = None,
        request_header_name: str | None = None,
        request_header_value_contains: str | None = None,
        response_header_name: str | None = None,
        response_header_value_contains: str | None = None,
        request_content_type: str | None = None,
        response_content_type: str | None = None,
        request_body_contains: str | None = None,
        response_body_contains: str | None = None,
        request_json_query: str | None = None,
        response_json_query: str | None = None,
        max_groups: int = 10,
        max_preview_chars: int = 128,
        max_headers_per_side: int = 6,
        scan_limit: int = 500,
    ) -> CaptureAnalysisGroupsResult:
        """Group analyzed traffic so the agent can inspect hot spots with lower token cost."""
        deps = get_tool_dependencies(ctx)
        query = build_traffic_query(
            preset=preset,
            host_contains=host_contains,
            path_contains=path_contains,
            method_in=method_in,
            status_in=status_in,
            resource_class_in=resource_class_in,
            min_priority_score=min_priority_score,
            request_header_name=request_header_name,
            request_header_value_contains=request_header_value_contains,
            response_header_name=response_header_name,
            response_header_value_contains=response_header_value_contains,
            request_content_type=request_content_type,
            response_content_type=response_content_type,
            request_body_contains=request_body_contains,
            response_body_contains=response_body_contains,
            request_json_query=request_json_query,
            response_json_query=response_json_query,
            include_body_preview=False,
            max_items=max_groups,
            max_preview_chars=max_preview_chars,
            max_headers_per_side=max_headers_per_side,
            scan_limit=scan_limit,
        )
        return await deps.traffic_query_service.group_capture(
            source=source,
            group_by=group_by,
            capture_id=capture_id,
            recording_path=recording_path,
            query=query,
            max_groups=max_groups,
        )

    @mcp.tool()
    async def query_recorded_traffic(
        ctx: ToolContext,
        host_contains: HostContains = None,
        http_method: HttpMethodFilter = None,
        keyword_regex: KeywordRegex = None,
        keep_request: bool = True,
        keep_response: bool = True,
        max_items: int = 20,
    ) -> RecordedTrafficQueryResult:
        """Query the latest saved recording. This tool never reads the live Charles session.
        HISTORY-PLANE TOOL — only use when the user explicitly references a saved
        recording (.chlsj file). For ongoing / live traffic, prefer
        start_live_capture + query_live_capture_entries instead.
        Returns compact summaries, at most max_items of them: route fields, an
        entry_id, and with keep_request / keep_response the first 256 characters of
        that body. total_items counts every match; truncated says more were left out.
        For one whole entry pass its entry_id and the returned path as recording_path
        to get_traffic_entry_detail(source="history")."""
        deps = get_tool_dependencies(ctx)
        max_items = _page_size("max_items", max_items)
        host_contains_normalized = normalize_text_filter(host_contains)
        method_normalized, method_error = normalize_http_method(http_method)
        if method_error:
            raise ValueError(guidance_error_message(method_error))

        if keyword_regex:
            valid, error_msg = deps.history_service.validate_keyword_regex(keyword_regex)
            if not valid:
                raise ValueError(
                    guidance_error_message(
                        build_tool_guidance_error(
                            parameter="keyword_regex",
                            received=keyword_regex,
                            reason=f"invalid regex: {error_msg}",
                            valid_input="Provide a valid Python regular expression.",
                            retry_example='query_recorded_traffic(keyword_regex="token|session")',
                        )
                    )
                )

        result = await deps.history_service.query_latest_result(
            host_contains=host_contains_normalized,
            method_normalized=method_normalized,
            keyword_regex=keyword_regex,
        )
        normalizer = deps.traffic_query_service.normalizer
        items = [
            _compact_recorded_entry(
                raw,
                entry_id=normalizer.entry_id_for(
                    raw, capture_source="history", recording_path=result.path
                ),
                keep_request=keep_request,
                keep_response=keep_response,
            )
            for raw in result.items[:max_items]
            if isinstance(raw, dict)
        ]
        return result.model_copy(
            update={"items": items, "truncated": result.total_items > len(items)}
        )

    @mcp.tool()
    async def list_recordings(ctx: ToolContext) -> RecordingListResult:
        """List saved recording files using an explicit history-oriented tool name.
        HISTORY-PLANE TOOL — only use when the user explicitly references saved
        recordings (.chlsj files). For ongoing / live traffic, prefer
        start_live_capture instead.
        Start history analysis here, then preserve recording_path for summary/detail calls."""
        deps = get_tool_dependencies(ctx)
        return deps.history_service.list_recordings_result()

    @mcp.tool()
    async def get_recording_snapshot(
        ctx: ToolContext,
        path: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> RecordingSnapshotResult:
        """List a saved recording snapshot. This tool never reads the live Charles session.
        HISTORY-PLANE TOOL — only use when the user explicitly references a saved
        recording (.chlsj file).
        Returns compact entry summaries (host/method/path/status and an entry_id),
        `limit` at a time from `offset`, never bodies: a recording is a whole session.
        total_items is the size of the recording; next_offset continues the listing.
        Use analyze_recorded_traffic to filter, and get_traffic_entry_detail
        (source="history", recording_path, entry_id) for one whole entry."""
        deps = get_tool_dependencies(ctx)
        if offset < 0:
            raise ValueError("`offset` must not be negative")
        limit = _page_size("limit", limit)
        try:
            result = await deps.history_service.get_snapshot_result(path)
        except Exception as exc:
            raise ValueError(str(exc)) from exc
        normalizer = deps.traffic_query_service.normalizer
        page = result.items[offset: offset + limit]
        items = [
            _compact_recorded_entry(
                raw,
                entry_id=normalizer.entry_id_for(
                    raw, capture_source="history", recording_path=result.path
                ),
            )
            for raw in page
            if isinstance(raw, dict)
        ]
        end = offset + len(page)
        more = end < result.total_items
        return result.model_copy(
            update={"items": items, "truncated": more, "next_offset": end if more else None}
        )
