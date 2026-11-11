from __future__ import annotations

import logging
import os
from datetime import datetime

from mcp.server.fastmcp import FastMCP

from charles_mcp.client import CharlesClientError
from charles_mcp.schemas.status import (
    ActiveCaptureStatus,
    CharlesStatusConfig,
    CharlesStatusResult,
    LiveCaptureRuntimeStatus,
)
from charles_mcp.tools.tool_contract import (
    THROTTLING_PRESET_CHOICES,
    ThrottlingPreset,
    ToolContext,
    get_tool_dependencies,
    safe_ctx_log,
)
from charles_mcp.utils import list_files_with_extension

logger = logging.getLogger(__name__)


def register_reset_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    async def throttling(ctx: ToolContext, preset: ThrottlingPreset) -> str:
        """Set a network throttling preset in Charles."""
        logger.info("Tool called: throttling(preset=%s)", preset)
        deps = get_tool_dependencies(ctx)

        preset_clean = preset.strip()
        if not preset_clean:
            return (
                "Error: parameter `preset` cannot be empty. "
                "Use one of the supported presets, for example: 3G / 4G / 5G / off / deactivate."
            )

        preset_lower = preset_clean.lower()
        supported = {value.lower() for value in THROTTLING_PRESET_CHOICES}
        if preset_lower not in supported:
            return (
                "Error: parameter `preset` is invalid. "
                f"Supported values: {', '.join(THROTTLING_PRESET_CHOICES)}. "
                'Retry with something like throttling("3G") or throttling("off").'
            )

        normalized_preset = "3G" if preset_lower in ("start", "on") else preset_clean

        try:
            async with deps.client_factory(deps.config) as client:
                success, message = await client.set_throttling(normalized_preset)
                return f"{'Success' if success else 'Error'}: {message}"
        except CharlesClientError as exc:
            logger.error("Throttling error: %s", exc)
            return f"Error: {exc}"

    @mcp.tool()
    async def reset_environment(ctx: ToolContext) -> str:
        """Quit Charles, put back the Charles config saved when this server started,
        and delete every saved recording.
        DESTRUCTIVE and not undoable: the unsaved Charles session is lost when Charles
        quits, the current Charles settings are overwritten by the saved copy, and all
        .chlsj recordings this server stored are removed. Call it only when the user
        asks for exactly this reset; it does not start Charles again.
        The saved copy exists only when the server manages Charles
        (CHARLES_MANAGE_LIFECYCLE=true). Without one there is nothing to restore, and
        the tool changes nothing and says so. To stop mocks use mock_dispatcher(stop)
        or mock_set_enabled(false); to remove old data use purge_stored_data."""
        deps = get_tool_dependencies(ctx)
        config = deps.config
        baseline = os.path.join(config.backup_dir, "config", "charles.config")
        recordings = len(list_files_with_extension(config.package_dir, ".chlsj"))
        if not os.path.isfile(baseline):
            # Without a baseline the "reset" is only its side effects: Charles
            # quits, the session is lost and the recordings are deleted, and
            # nothing is restored. That is never what was asked for.
            return (
                "Nothing was changed: there is no saved Charles configuration to restore. "
                "The server saves one at start only when it manages Charles "
                "(CHARLES_MANAGE_LIFECYCLE=true). A reset now would only quit Charles, lose "
                f"its unsaved session and delete {recordings} saved recording(s). To stop "
                "mocks use mock_dispatcher(action=\"stop\") or mock_set_enabled(false); to "
                "remove stored data use purge_stored_data."
            )

        saved_at = datetime.fromtimestamp(os.path.getmtime(baseline)).strftime("%Y-%m-%d %H:%M")
        await safe_ctx_log(ctx, "info", "Running manual environment reset...")
        try:
            await deps.restore_config_fn(config)
        except Exception as exc:
            logger.error("Reset environment failed: %s", exc)
            return f"Reset failed: {exc}"
        return (
            "Environment reset completed: Charles was asked to quit, its config was restored "
            f"from the copy saved {saved_at}, and {recordings} saved recording(s) were deleted. "
            "Charles is not started again; ask the user to start it."
        )

    @mcp.tool()
    async def charles_status(ctx: ToolContext) -> CharlesStatusResult:
        """Check Charles connectivity and active live-capture state.
        Returns recommended_next_action to nudge agents toward the live plane:
        when no active capture exists, agents should start_live_capture before
        falling back to history-plane tools. `warnings` lists what is broken
        right now, e.g. Map Remote left on with no dispatcher running: tell the
        user and fix it before anything else."""
        logger.info("Tool called: charles_status()")
        deps = get_tool_dependencies(ctx)

        active_capture = deps.live_service.get_active_capture()
        result = CharlesStatusResult(
            config=CharlesStatusConfig(
                proxy_url=deps.config.proxy_url,
                base_url=deps.config.charles_base_url,
                config_path=deps.config.config_path or "not_detected",
                manage_charles_lifecycle=deps.config.manage_charles_lifecycle,
            ),
            live_capture=LiveCaptureRuntimeStatus(
                active_capture=ActiveCaptureStatus(**active_capture) if active_capture else None
            ),
            connected=False,
        )

        rejected_status: int | None = None
        try:
            async with deps.client_factory(deps.config) as client:
                info = await client.get_info()
                result.connected = info is not None
                if info:
                    result.charles_info = info
        except CharlesClientError as exc:
            result.connected = False
            result.error = str(exc)
            status_code = getattr(exc, "status_code", None)
            if status_code in (401, 403):
                rejected_status = status_code

        if result.connected and deps.routing_warnings_fn is not None:
            try:
                result.warnings = await deps.routing_warnings_fn()
            except Exception as exc:  # never let a side check break the status call
                logger.debug("routing check failed: %s", exc)

        if not result.connected and rejected_status is not None:
            # Charles is up and answered; only the credentials are wrong. Saying
            # "unreachable" here sent people restarting a Charles that was fine.
            result.recommended_next_action = (
                "Charles is running but rejected the Web Interface credentials "
                f"(HTTP {rejected_status}). Make CHARLES_USER and CHARLES_PASS in the MCP "
                "client config match Proxy → Web Interface Settings in Charles, then "
                "restart the MCP server."
            )
        elif not result.connected:
            result.recommended_next_action = (
                "Charles is unreachable. Confirm Charles Proxy is running and the "
                "Web Interface is enabled before retrying."
            )
        elif active_capture is None:
            result.recommended_next_action = (
                "No active live capture. For ongoing / live traffic, call "
                "start_live_capture first and reuse its capture_id. Only call "
                "list_recordings / query_recorded_traffic when the user "
                "explicitly references a saved recording (.chlsj)."
            )
        else:
            result.recommended_next_action = (
                f"Active capture detected (capture_id={active_capture['capture_id']}). "
                "Use query_live_capture_entries for structured filtering, then "
                "get_traffic_entry_detail to drill down into one confirmed entry_id."
            )

        return result
