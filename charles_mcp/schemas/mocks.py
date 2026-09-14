"""Schemas for Map Local mock tools."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_serializer

from charles_mcp.schemas.analysis import _strip_none


class _Compact(BaseModel):
    @model_serializer(mode="wrap")
    def _compact(self, handler: Any) -> Any:
        return _strip_none(handler(self))


class MockFile(_Compact):
    host: str
    path: str
    file: str
    size_bytes: int
    modified_at: str


class MockWriteResult(_Compact):
    mock: MockFile
    archived_previous: str | None = None
    source_entry_id: str | None = None
    patches_applied: int = 0
    warnings: list[str] = Field(default_factory=list)
    next_step: str


class MockListResult(_Compact):
    mock_dir: str
    total: int
    items: list[MockFile] = Field(default_factory=list)


class MockContentResult(_Compact):
    mock: MockFile
    content: str
    content_truncated: bool = False


class MockRemoveResult(_Compact):
    host: str
    path: str
    archived_to: str
    next_step: str


class MapLocalToggleResult(_Compact):
    enabled: bool
    success: bool
    message: str


class MapLocalRule(_Compact):
    protocol: str = "https"
    host: str
    port: int = 443
    path: str = "/*"
    local_path: str


class MockHostSetupResult(_Compact):
    host: str
    host_dir: str
    map_local_rule: MapLocalRule
    applied: bool = False
    config_path: str | None = None
    config_backup: str | None = None
    map_local_added: bool = False
    rewrite_added: bool = False
    warnings: list[str] = Field(default_factory=list)
    instructions: list[str] = Field(default_factory=list)


# ---- body-aware dispatcher rules ---------------------------------------------


class MapRemoteRoute(_Compact):
    protocol: str = "https"
    host: str
    port: int = 443
    path: str
    destination: str
    # True for wildcard paths: the destination has no path, so Charles keeps it.
    path_unchanged: bool = True
    preserve_host_header: bool = True


class RouteSetupResult(_Compact):
    host: str
    path: str
    route_file: str
    map_remote_rule: MapRemoteRoute
    applied: bool = False
    config_path: str | None = None
    config_backup: str | None = None
    mapping_added: bool = False
    warnings: list[str] = Field(default_factory=list)
    instructions: list[str] = Field(default_factory=list)


class RuleSummary(_Compact):
    host: str
    id: str
    enabled: bool
    priority: int
    method: str | None = None
    path: str
    # JSON text so that null / false match values survive serialization.
    body_match_json: str = "{}"
    response_mode: str
    status: int | None = None
    delay_ms: int | None = None
    response_patches: int = 0
    request_patches: int = 0
    request_header_edits: int = 0
    file: str


class RuleWriteResult(_Compact):
    rule: RuleSummary
    rule_json: str
    fixture_file: str | None = None
    archived_previous: str | None = None
    warnings: list[str] = Field(default_factory=list)
    next_step: str


class RuleListResult(_Compact):
    mock_dir: str
    routes: list[str] = Field(default_factory=list)
    total: int
    items: list[RuleSummary] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class RuleContentResult(_Compact):
    rule_json: str
    fixture: str | None = None
    fixture_truncated: bool = False


class RuleRemoveResult(_Compact):
    host: str
    id: str
    archived_to: str
    next_step: str


class VariantGroup(_Compact):
    method: str
    host: str
    path: str
    # JSON text of the body field value; "null" when the request has no such field.
    value_json: str
    count: int
    entry_ids: list[str] = Field(default_factory=list)
    response_statuses: list[int] = Field(default_factory=list)


class VariantDiscoveryResult(_Compact):
    host_contains: str | None = None
    path_contains: str | None = None
    # History only: pass this exact value as recording_path to mock_rule_create_from_entry.
    recording_path: str | None = None
    body_field: str
    scanned: int
    total_groups: int = 0
    groups: list[VariantGroup] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DispatcherStatusResult(_Compact):
    running: bool
    address: str
    mock_dir: str
    routes: list[str] = Field(default_factory=list)
    message: str
