"""Charles settings the server needs and the Web Interface cannot change.

Proxy > Recording Settings is one of them. Charles records this server's own
session exports unless ``control.charles`` is excluded there, and the session
then grows with every tool call (see ``live_state.self_recording_warning``).
The Web Interface only toggles tools, so the setting is either made by hand in
the Charles UI or written into the config file while Charles is closed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from charles_mcp.config import Config
from charles_mcp.live_state import CONTROL_HOST
from charles_mcp.mocks.charles_config import (
    CharlesConfigError,
    apply_recording_exclude,
    recording_excludes,
)
from charles_mcp.mocks.rule_service import _port_open
from charles_mcp.mocks.store import MockPathError, normalize_host_pattern

logger = logging.getLogger(__name__)


def exclude_from_recording(config: Config, host: str, *, apply: bool) -> dict[str, Any]:
    try:
        host = normalize_host_pattern(host)
    except MockPathError as exc:
        raise ValueError(str(exc)) from exc
    if host == "*":
        raise ValueError("excluding every host would stop Charles recording anything")

    manual = [
        "In Charles open Proxy > Recording Settings > Exclude and press Add.",
        f"Set Host to `{host}`, leave the other fields empty, press OK, then OK again.",
        "It applies immediately; no restart is needed.",
        "Clear the session afterwards so the entries already recorded go too.",
    ]
    result: dict[str, Any] = {"host": host, "applied": False}

    if config.config_path:
        try:
            result["excluded_in_config_file"] = host in recording_excludes(config.config_path)
        except CharlesConfigError as exc:
            result["config_warning"] = str(exc)
    else:
        result["config_warning"] = "Charles config file not found; set CHARLES_CONFIG_PATH"

    if not apply:
        result["instructions"] = manual + [
            "Alternatively the user quits Charles (saving the session first) and this tool "
            "runs again with apply=true to write the exclusion into the config file.",
        ]
        result["note"] = (
            "excluded_in_config_file reflects the file, which Charles rewrites only when it "
            "quits: a change made in the UI this session is not in it yet."
        )
        return result

    if not config.config_path:
        raise ValueError(
            "Charles config file was not found; set CHARLES_CONFIG_PATH or follow the "
            "manual steps (apply=false)"
        )
    if _port_open(config.proxy_host, config.proxy_port):
        raise ValueError(
            "Charles is running. It rewrites its config file from memory on quit, so an edit "
            "made now would be lost. Either add the exclusion by hand in Proxy > Recording "
            "Settings (no restart needed), or ask the user to save the session and quit "
            "Charles, then call this tool with apply=true."
        )

    change = apply_recording_exclude(
        config.config_path,
        host=host,
        backup_dir=Path(config.backup_dir) / "recording",
    )
    result.update(
        applied=change.added,
        already_excluded=change.already_excluded,
        excluded_in_config_file=True,
        config_path=change.config_path,
        config_backup=change.backup_path,
        instructions=[
            "Start Charles; the exclusion is loaded at startup.",
            "Clear the session once, so the exports already recorded go too.",
            "Confirm with start_live_capture: its warnings must no longer include "
            "charles_records_own_exports.",
        ],
    )
    return result


def register_charles_settings_tools(mcp: FastMCP, config: Config) -> None:
    @mcp.tool()
    async def charles_recording_exclude(
        host: str = CONTROL_HOST, apply: bool = False
    ) -> dict[str, Any]:
        """Exclude a host from Charles recording (Proxy > Recording Settings > Exclude).

        Default host `control.charles`: without it Charles records every session
        export this server makes, with the whole session as its body, and the
        session grows on every tool call. Other hosts work too, for traffic that
        only adds noise.

        apply=false (default) returns the UI steps — they take effect at once with
        no restart — and whether the config file already has the exclusion.
        apply=true writes it into the Charles config: Charles must be closed (it
        rewrites the file on quit), the file is backed up first, and a host already
        excluded is left alone. Ask the user before they quit Charles: an unsaved
        session is lost.
        """
        logger.info("Tool called: charles_recording_exclude(host=%s, apply=%s)", host, apply)
        return exclude_from_recording(config, host, apply=apply)
