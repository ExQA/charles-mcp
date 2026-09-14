from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, cast

from mcp.server.fastmcp import Context
from pydantic import Field

from charles_mcp.client import CharlesClientError
from charles_mcp.config import Config
from charles_mcp.schemas.traffic import ResourceClass
from charles_mcp.schemas.traffic_query import TrafficPreset, TrafficQuery
from charles_mcp.services.history_capture import RecordingHistoryService
from charles_mcp.services.live_capture import LiveCaptureService
from charles_mcp.services.traffic_query_service import TrafficQueryService
from charles_mcp.utils import (
    ensure_directory,
    safe_copy_file,
    safe_copy_tree,
    safe_remove_tree,
)

logger = logging.getLogger(__name__)

HTTP_METHOD_CHOICES = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
THROTTLING_PRESET_CHOICES = (
    "3G",
    "4G",
    "5G",
    "fibre",
    "100mbps",
    "56k",
    "256k",
    "deactivate",
    "off",
    "on",
    "start",
)


@dataclass(frozen=True)
class ToolDependencies:
    config: Config
    client_factory: Any
    live_service: LiveCaptureService
    history_service: RecordingHistoryService
    traffic_query_service: TrafficQueryService
    restore_config_fn: Callable[[Config], Awaitable[bool]]


ToolContext = Context[Any, ToolDependencies, Any]
_TOOL_DEPENDENCIES_ATTR = "_charles_tool_dependencies"


def attach_tool_dependencies(server: Any, deps: ToolDependencies) -> None:
    setattr(server, _TOOL_DEPENDENCIES_ATTR, deps)


def get_tool_dependencies(ctx: ToolContext) -> ToolDependencies:
    try:
        return ctx.request_context.lifespan_context
    except ValueError:
        fastmcp = ctx.fastmcp
        deps = getattr(fastmcp, _TOOL_DEPENDENCIES_ATTR, None)
        if deps is None:
            raise ValueError("Tool dependencies are unavailable outside of a request") from None
        return cast(ToolDependencies, deps)


HostContains = Annotated[
    str | None,
    Field(
        description="Filter by host substring. Example: api.example.com",
        json_schema_extra={"examples": ["api.example.com", "gateway", "mmtls"]},
    ),
]

HttpMethodFilter = Annotated[
    str | None,
    Field(
        description=(
            "Filter by HTTP method. Only standard HTTP methods are allowed. "
            "It must be a method name, not a regular expression or a path."
        ),
        json_schema_extra={"enum": list(HTTP_METHOD_CHOICES)},
    ),
]

KeywordRegex = Annotated[
    str | None,
    Field(
        description=(
            "Python regular expression searched in request and response content. "
            "Keep it short to avoid catastrophic backtracking."
        ),
        json_schema_extra={"maxLength": 500, "examples": ["token", "session|csrf", "password"]},
    ),
]

ThrottlingPreset = Annotated[
    str,
    Field(
        description=(
            "Network throttling preset name. "
            "Allowed values: 3G/4G/5G/fibre/100mbps/56k/256k/deactivate/off/on/start."
        ),
        json_schema_extra={"enum": list(THROTTLING_PRESET_CHOICES)},
    ),
]


def build_tool_guidance_error(
    *,
    parameter: str,
    received: Any,
    reason: str,
    valid_input: str,
    retry_example: str,
) -> list[dict]:
    return [
        {
            "error": f"invalid `{parameter}`: {reason}",
            "received": received,
            "valid_input": valid_input,
            "retry_example": retry_example,
        }
    ]


def normalize_http_method(method: str | None) -> tuple[str | None, list[dict] | None]:
    if method is None:
        return (None, None)

    method_clean = method.strip().upper()
    if not method_clean:
        return (None, None)

    if method_clean not in HTTP_METHOD_CHOICES:
        return (
            None,
            build_tool_guidance_error(
                parameter="http_method",
                received=method,
                reason="not a supported HTTP method.",
                valid_input=f"one of {', '.join(HTTP_METHOD_CHOICES)}",
                retry_example='query_recorded_traffic(http_method="POST")',
            ),
        )

    return (method_clean, None)


def normalize_text_filter(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned if cleaned else None


def guidance_error_message(error_payload: list[dict]) -> str:
    if not error_payload:
        return "invalid tool input"
    first = error_payload[0]
    parts = [str(first.get("error", "invalid tool input"))]
    if first.get("valid_input"):
        parts.append(f"valid_input={first['valid_input']}")
    if first.get("retry_example"):
        parts.append(f"retry_example={first['retry_example']}")
    return "; ".join(parts)


async def safe_ctx_log(ctx: Context, level: str, message: str) -> None:
    try:
        log_fn = getattr(ctx, level)
        await log_fn(message)
    except ValueError:
        logger.debug("Skipping ctx.%s log outside request context: %s", level, message)


def backup_config(config: Config) -> bool:
    if not config.config_path or not os.path.exists(config.config_path):
        logger.warning("Charles config file not found: %s", config.config_path)
        return False

    cfg_back_dir = os.path.join(config.backup_dir, "config")
    ensure_directory(cfg_back_dir)

    success = safe_copy_file(
        config.config_path,
        os.path.join(cfg_back_dir, "charles.config"),
    )

    if config.profiles_dir and os.path.exists(config.profiles_dir):
        prf_back_dir = os.path.join(config.backup_dir, "profiles")
        safe_copy_tree(config.profiles_dir, prf_back_dir, remove_existing=True)

    if success:
        logger.info("Charles config backed up")
    return success


async def restore_config(config: Config, *, client_factory: Any) -> bool:
    logger.info("Resetting the environment...")

    try:
        async with client_factory(config) as client:
            await client.quit_charles()
            await asyncio.sleep(2)
    except CharlesClientError as exc:
        logger.warning("failed to send the quit command (ignored): %s", exc)

    cfg_source = os.path.join(config.backup_dir, "config", "charles.config")
    if os.path.exists(cfg_source) and config.config_path:
        safe_copy_file(cfg_source, config.config_path)

    prf_source = os.path.join(config.backup_dir, "profiles")
    if os.path.exists(prf_source) and config.profiles_dir:
        safe_copy_tree(prf_source, config.profiles_dir, remove_existing=True)

    safe_remove_tree(config.package_dir)
    ensure_directory(config.package_dir)

    logger.info("environment reset done")
    return True


def build_traffic_query(
    *,
    preset: TrafficPreset = "api_focus",
    host_contains: str | None = None,
    path_contains: str | None = None,
    method_in: list[str] | None = None,
    status_in: list[int] | None = None,
    resource_class_in: list[str] | None = None,
    min_priority_score: int | None = None,
    request_body_contains: str | None = None,
    response_body_contains: str | None = None,
    request_header_name: str | None = None,
    request_header_value_contains: str | None = None,
    response_header_name: str | None = None,
    response_header_value_contains: str | None = None,
    request_content_type: str | None = None,
    response_content_type: str | None = None,
    request_json_query: str | None = None,
    response_json_query: str | None = None,
    include_body_preview: bool = True,
    max_items: int = 20,
    max_preview_chars: int = 256,
    max_headers_per_side: int = 8,
    scan_limit: int = 500,
    since_seconds: int | None = None,
) -> TrafficQuery:
    valid_resource_classes: set[ResourceClass] = {
        "api_candidate",
        "document",
        "script",
        "static_asset",
        "media",
        "font",
        "connect_tunnel",
        "control",
        "unknown",
    }
    resource_classes = [
        cast(ResourceClass, item)
        for item in (resource_class_in or [])
        if item in valid_resource_classes
    ]
    return TrafficQuery(
        preset=preset,
        host_contains=normalize_text_filter(host_contains),
        path_contains=normalize_text_filter(path_contains),
        method_in=[item.strip().upper() for item in (method_in or []) if item and item.strip()],
        status_in=[item for item in (status_in or []) if isinstance(item, int)],
        resource_class_in=resource_classes,
        min_priority_score=min_priority_score,
        request_body_contains=normalize_text_filter(request_body_contains),
        response_body_contains=normalize_text_filter(response_body_contains),
        request_header_name=normalize_text_filter(request_header_name),
        request_header_value_contains=normalize_text_filter(request_header_value_contains),
        response_header_name=normalize_text_filter(response_header_name),
        response_header_value_contains=normalize_text_filter(response_header_value_contains),
        request_content_type=normalize_text_filter(request_content_type),
        response_content_type=normalize_text_filter(response_content_type),
        request_json_query=normalize_text_filter(request_json_query),
        response_json_query=normalize_text_filter(response_json_query),
        include_body_preview=include_body_preview,
        max_items=max_items,
        max_preview_chars=max_preview_chars,
        max_headers_per_side=max_headers_per_side,
        scan_limit=scan_limit,
        since_seconds=since_seconds,
    )
