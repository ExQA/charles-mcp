"""Charles MCP server entrypoint and tool assembly."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from charles_mcp.agent_instructions import AGENT_GUIDE_URI, SERVER_INSTRUCTIONS, agent_guide
from charles_mcp.client import CharlesClient
from charles_mcp.config import Config, get_config
from charles_mcp.mocks.rule_service import RuleService
from charles_mcp.mocks.service import MockService
from charles_mcp.reverse.server import build_reverse_runtime, register_reverse_tools
from charles_mcp.services import (
    LiveCaptureService,
    RecordingHistoryService,
    TrafficAnalysisService,
    TrafficNormalizer,
    TrafficQueryService,
)
from charles_mcp.tools import (
    ToolDependencies,
    attach_tool_dependencies,
    register_history_tools,
    register_live_tools,
    register_reset_tools,
)
from charles_mcp.tools import (
    backup_config as _backup_config,
)
from charles_mcp.tools import (
    restore_config as _restore_config,
)
from charles_mcp.tools.mock_rules import register_mock_rule_tools
from charles_mcp.tools.mocks import register_mock_tools
from charles_mcp.tools.purge import register_purge_tools, run_purge

logger = logging.getLogger(__name__)


def backup_config(config: Config) -> bool:
    """Compatibility wrapper kept for lifecycle tests and monkeypatching."""
    return _backup_config(config)


async def restore_config(config: Config) -> bool:
    """Compatibility wrapper kept for lifecycle tests and monkeypatching."""
    return await _restore_config(config, client_factory=CharlesClient)


def create_server(config: Config | None = None) -> FastMCP[ToolDependencies]:
    """
    Create and configure the Charles MCP server.

    Args:
        config: Optional configuration. Defaults to the global config.

    Returns:
        Configured FastMCP server instance.
    """
    config = config or get_config()

    warnings = config.validate()
    for warning in warnings:
        logger.warning(warning)

    live_service = LiveCaptureService(config, client_factory=CharlesClient)
    history_service = RecordingHistoryService(config, client_factory=CharlesClient)
    traffic_normalizer = TrafficNormalizer(config)
    traffic_analysis_service = TrafficAnalysisService()
    traffic_query_service = TrafficQueryService(
        live_service=live_service,
        history_service=history_service,
        normalizer=traffic_normalizer,
        analysis_service=traffic_analysis_service,
    )
    reverse_runtime = build_reverse_runtime(config)
    deps = ToolDependencies(
        config=config,
        client_factory=CharlesClient,
        live_service=live_service,
        history_service=history_service,
        traffic_query_service=traffic_query_service,
        restore_config_fn=restore_config,
    )

    @asynccontextmanager
    async def lifespan(server: FastMCP[ToolDependencies]) -> AsyncIterator[ToolDependencies]:
        logger.info("MCP service lifespan started")

        if config.retention_days > 0:
            # Opt-in: only when CHARLES_RETENTION_DAYS is set. A failure here
            # must not keep the server from starting.
            try:
                purged = run_purge(
                    config,
                    reverse_runtime.config.database_path,
                    older_than_days=config.retention_days,
                    dry_run=False,
                )
                logger.info("retention: %s", purged["summary"])
            except Exception:
                logger.exception("retention purge failed; nothing further was removed")

        if config.manage_charles_lifecycle:
            backup_config(config)

        try:
            yield deps
        finally:
            if config.manage_charles_lifecycle:
                await restore_config(config)
            reverse_runtime.store.close()
            logger.info("MCP service lifespan finished")

    mcp = FastMCP(
        "CharlesMCP",
        instructions=SERVER_INSTRUCTIONS,
        json_response=True,
        lifespan=lifespan,
    )

    @mcp.resource(
        AGENT_GUIDE_URI,
        name="agent-guide",
        description="How to use charles-mcp: rules, every tool, workflows, Charles restarts.",
        mime_type="text/markdown",
    )
    def agent_guide_resource() -> str:
        return agent_guide()
    attach_tool_dependencies(mcp, deps)

    register_live_tools(mcp)
    register_history_tools(mcp)
    register_reset_tools(mcp)
    register_reverse_tools(mcp, reverse_runtime)
    register_mock_tools(
        mcp,
        MockService(
            config,
            traffic_query_service=traffic_query_service,
            client_factory=CharlesClient,
        ),
    )
    register_mock_rule_tools(
        mcp,
        RuleService(
            config,
            traffic_query_service=traffic_query_service,
            client_factory=CharlesClient,
        ),
    )
    register_purge_tools(mcp, config, reverse_runtime.config.database_path)

    return mcp
