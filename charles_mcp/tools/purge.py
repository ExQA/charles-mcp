"""The purge tool: remove captured data this server stored, once it is old enough."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from charles_mcp.config import Config
from charles_mcp.services.purge import ALL_SCOPES, PurgeScope, purge_stored_data

logger = logging.getLogger(__name__)


def run_purge(
    config: Config,
    reverse_database: Path | None,
    *,
    older_than_days: int,
    scopes: list[PurgeScope] | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    report = purge_stored_data(
        package_dir=config.package_dir,
        backup_dir=config.backup_dir,
        reverse_database=reverse_database,
        older_than_days=older_than_days,
        scopes=tuple(scopes) if scopes else ALL_SCOPES,
        dry_run=dry_run,
    )
    return {
        "dry_run": report.dry_run,
        "older_than_days": report.older_than_days,
        "summary": report.summary(),
        "recordings": report.recordings,
        "backups": report.backups,
        "reverse_captures": report.reverse_captures,
        "reverse_undated": report.reverse_undated,
        "freed_bytes": report.freed_bytes,
    }


def register_purge_tools(mcp: FastMCP, config: Config, reverse_database: Path | None) -> None:
    # Named explicitly: the function name would shadow the service it calls.
    @mcp.tool(name="purge_stored_data")
    async def purge_stored_data_tool(
        older_than_days: int = 30,
        scopes: list[PurgeScope] | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Delete captured data this server stored on disk, older than a cut-off.

        Covers saved capture snapshots (`recordings`), the reverse-analysis
        database with request and response bodies (`reverse`), and Charles
        config backups with the Web Interface credentials (`backups`). Omit
        `scopes` for all three.

        `dry_run=true` (the default) only reports what would go; run it first,
        show the user the list, and repeat with `dry_run=false` only after they
        agree — deleted data cannot be recovered.

        Never removed, whatever their age: reset_environment's baseline backup
        and the newest backup in every other backup folder. Mocks and dispatcher
        rules are not touched; remove those with mock_remove / mock_rule_remove.
        """
        logger.info(
            "Tool called: purge_stored_data(older_than_days=%s, scopes=%s, dry_run=%s)",
            older_than_days,
            scopes,
            dry_run,
        )
        return run_purge(
            config,
            reverse_database,
            older_than_days=older_than_days,
            scopes=scopes,
            dry_run=dry_run,
        )
