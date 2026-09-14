"""Data-driven mock rules for the body-aware dispatcher.

Charles Map Local picks a file by URL only, so it cannot tell apart two POST
requests to the same URL whose bodies differ (for example by an ``action``
field). Routed hosts go through Charles Map Remote to the local dispatcher,
which selects a rule by host, method, path, query, headers and body fields,
then either serves a fixture or forwards the request upstream and edits the
real request and response.

Rules and fixtures are data, never code. They live outside the repository:

    <mock_dir>/_rules/routes.json            which hosts/paths Charles sends here
    <mock_dir>/_rules/_any/<rule_id>.json    rules for any routed host or a host glob
    <mock_dir>/_rules/<host>/<rule_id>.json  rules for one exact host
    <mock_dir>/_rules/.../<rule_id>.body     fixture body for fixture rules

The same API usually runs on several hosts (dev, stage, ...), so rules default
to ``host: "*"``; an exact-host rule wins over a pattern rule. Likewise a rule
path may use ``*`` for one segment or part of one (``/api/*/payoneer``) when a
segment varies by platform or version; an exact path wins over a pattern.

Fixtures are usually captured from real traffic and may hold personal or
payment data, so the mock directory must never be committed or shared.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import cached_property
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs

from pydantic import BaseModel, Field, field_validator

from charles_mcp.mocks.json_patch import get_pointer, validate_patches
from charles_mcp.mocks.store import (
    ARCHIVE_DIR_NAME,
    is_host_pattern,
    normalize_host,
    normalize_host_pattern,
    split_request_path,
)

logger = logging.getLogger(__name__)

RULES_DIR_NAME = "_rules"
ANY_DIR_NAME = "_any"
ROUTES_FILE_NAME = "routes.json"
_RULE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")


def check_rule_id(value: str) -> str:
    if not _RULE_ID_RE.match(value):
        raise ValueError(
            f"rule id `{value}` must match {_RULE_ID_RE.pattern} "
            "(lowercase letters, digits, dot, dash, underscore)"
        )
    return value


def _validate_path(value: str) -> str:
    segments, query = split_request_path(value)
    if query:
        raise ValueError("path must not contain a query string; use `query` to match it")
    return "/" + "/".join(segments)


def normalize_path_pattern(value: str) -> str:
    """A route path: ``/*`` for the whole host, ``/api/*`` for a prefix, or one path."""
    if value in ("/*", "*"):
        return "/*"
    if value.endswith("/*"):
        return _validate_path(value[:-2]) + "/*"
    return _validate_path(value)


def host_matches(pattern: str, host: str) -> bool:
    return pattern == host or (is_host_pattern(pattern) and fnmatch.fnmatchcase(host, pattern))


def path_matches(pattern: str, path: str) -> bool:
    if pattern == "/*":
        return True
    if pattern.endswith("/*"):
        return path == pattern[:-2] or path.startswith(pattern[:-1])
    return path == pattern


def rule_path_matches(pattern: str, path: str) -> bool:
    """A rule path: exact, or ``*`` standing for one segment or part of one."""
    if "*" not in pattern:
        return path == pattern
    pattern_segments = pattern.split("/")
    path_segments = path.split("/")
    if len(pattern_segments) != len(path_segments):
        return False
    return all(
        re.fullmatch(re.escape(expected).replace(r"\*", "[^/]*"), actual) is not None
        for expected, actual in zip(pattern_segments, path_segments)
    )


def route_covers_rule_path(route_pattern: str, rule_path: str) -> bool:
    """Does a route pattern send every concrete path a rule pattern can match?

    ``rule_path`` may itself contain ``*`` (``/api/*/payoneer``), so treat its
    literal head (before the first ``*``) as the family of paths it produces.
    Routes only ever use ``/*`` or a ``/prefix/*`` suffix, never a mid-segment
    wildcard, so a prefix route covers the rule when that head stays inside it.
    """
    if route_pattern == "/*":
        return True
    if route_pattern.endswith("/*"):
        base = route_pattern[:-2]
        head = rule_path.split("*", 1)[0]
        return rule_path == base or head.startswith(base + "/")
    return route_pattern == rule_path


class RuleMatch(BaseModel):
    """Conditions a request must meet. Every condition present must match."""

    method: str | None = None
    path: str
    query: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    # JSON Pointer into the request body (JSON or form) -> expected value.
    body: dict[str, Any] = Field(default_factory=dict)

    @field_validator("method")
    @classmethod
    def _upper_method(cls, value: str | None) -> str | None:
        return value.upper() if value else None

    @field_validator("path")
    @classmethod
    def _check_path(cls, value: str) -> str:
        if "**" in value:
            raise ValueError(
                "`**` is not special in a rule path; a single `*` matches one segment or "
                "part of one and never crosses `/`. Use `*` per segment (e.g. /api/*/v*/x)."
            )
        return _validate_path(value)

    @field_validator("headers")
    @classmethod
    def _lower_headers(cls, value: dict[str, str]) -> dict[str, str]:
        return {name.lower(): header for name, header in value.items()}

    @field_validator("body")
    @classmethod
    def _check_pointers(cls, value: dict[str, Any]) -> dict[str, Any]:
        for pointer in value:
            if not pointer.startswith("/"):
                raise ValueError(f"body match key must be a JSON Pointer like /action: `{pointer}`")
        return value

    def specificity(self) -> int:
        return int(self.method is not None) + len(self.query) + len(self.headers) + len(self.body)


class RuleRequest(BaseModel):
    """Edits applied to the request before it is forwarded upstream."""

    patches: list[dict[str, Any]] = Field(default_factory=list)
    # Header name -> value to set, or None to remove it. Host and Content-Length
    # are managed by the dispatcher and cannot be set here.
    headers: dict[str, str | None] = Field(default_factory=dict)

    @field_validator("patches")
    @classmethod
    def _check_patches(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        validate_patches(value)
        return value


class RuleResponse(BaseModel):
    """``fixture`` serves the stored body; ``patch`` edits the real upstream response."""

    mode: Literal["fixture", "patch"]
    status: int | None = Field(default=None, ge=100, le=599)
    headers: dict[str, str] = Field(default_factory=dict)
    # Delay before the response is sent, to test loaders and client timeouts.
    delay_ms: int | None = Field(default=None, ge=0, le=60000)
    patches: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("patches")
    @classmethod
    def _check_patches(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        validate_patches(value)
        return value


class MockRule(BaseModel):
    version: Literal[1] = 1
    id: str
    # Exact hostname, a glob such as "*.example.com", or "*" for any routed host.
    host: str = "*"
    enabled: bool = True
    priority: int = 0
    description: str | None = None
    match: RuleMatch
    request: RuleRequest = Field(default_factory=RuleRequest)
    response: RuleResponse
    source_entry_id: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        return check_rule_id(value)

    @field_validator("host")
    @classmethod
    def _check_host(cls, value: str) -> str:
        return normalize_host_pattern(value)


class RouteConfig(BaseModel):
    """Which requests Charles sends to the dispatcher and how to reach upstream.

    A request whose host matches no route is never forwarded.
    """

    version: Literal[1] = 1
    host: str
    upstream_scheme: Literal["https", "http"] = "https"
    upstream_port: int = Field(default=443, ge=1, le=65535)
    verify_tls: bool = True
    paths: list[str] = Field(default_factory=lambda: ["/*"])

    @field_validator("host")
    @classmethod
    def _check_host(cls, value: str) -> str:
        return normalize_host_pattern(value)

    @field_validator("paths")
    @classmethod
    def _check_paths(cls, value: list[str]) -> list[str]:
        return sorted({normalize_path_pattern(path) for path in value})


class RouteTable(BaseModel):
    version: Literal[1] = 1
    routes: list[RouteConfig] = Field(default_factory=list)


def make_rule_id(method: str | None, path: str, body_match: Mapping[str, Any]) -> str:
    """Readable, stable id: ``post-api-v1-wallet-action-init-1a2b3c``."""
    parts = [method or "any", path]
    parts.extend(f"{pointer}-{value}" for pointer, value in sorted(body_match.items()))
    slug = re.sub(r"[^a-z0-9]+", "-", " ".join(str(part) for part in parts).lower()).strip("-")
    digest = hashlib.sha256(
        json.dumps([method, path, dict(body_match)], sort_keys=True, default=str).encode()
    ).hexdigest()[:6]
    return f"{slug[:72].rstrip('-')}-{digest}"


@dataclass
class IncomingRequest:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: Mapping[str, str]  # lower-case names
    body: bytes
    content_type: str = ""
    host: str = ""

    @cached_property
    def body_document(self) -> Any:
        """Request body as JSON, or a form body as a dict; ``None`` otherwise."""
        return parse_body_document(self.body, self.content_type)


def parse_body_document(body: bytes, content_type: str) -> Any:
    if not body:
        return None
    text = body.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except ValueError:
        pass
    if "application/x-www-form-urlencoded" in content_type.lower() or (
        "=" in text and not text.lstrip().startswith(("{", "["))
    ):
        parsed = parse_qs(text, keep_blank_values=True)
        if parsed:
            return {key: values[0] if len(values) == 1 else values for key, values in parsed.items()}
    return None


def rule_matches(rule: MockRule, request: IncomingRequest) -> bool:
    if request.host and not host_matches(rule.host, request.host):
        return False
    match = rule.match
    if match.method and match.method != request.method.upper():
        return False
    if not rule_path_matches(match.path, request.path):
        return False
    for name, expected in match.query.items():
        if expected not in request.query.get(name, []):
            return False
    for name, expected in match.headers.items():
        if request.headers.get(name) != expected:
            return False
    if match.body:
        document = request.body_document
        if document is None:
            return False
        for pointer, expected in match.body.items():
            try:
                actual = get_pointer(document, pointer)
            except KeyError:
                return False
            if actual != expected and str(actual) != str(expected):
                return False
    return True


def select_rule(rules: list[MockRule], request: IncomingRequest) -> MockRule | None:
    """Pick by priority, then exact host over host patterns, then exact path over
    path patterns, then match specificity."""
    candidates = [rule for rule in rules if rule.enabled and rule_matches(rule, request)]
    if not candidates:
        return None
    candidates.sort(
        key=lambda rule: (
            -rule.priority,
            is_host_pattern(rule.host),
            "*" in rule.match.path,
            -rule.match.specificity(),
            rule.id,
        )
    )
    return candidates[0]


class RuleStore:
    def __init__(self, mock_dir: str | os.PathLike[str]) -> None:
        self.root = Path(mock_dir).expanduser()

    @property
    def rules_root(self) -> Path:
        return self.root / RULES_DIR_NAME

    def rules_dir(self, host: str) -> Path:
        """``_rules/_any`` for patterns (including ``*``), ``_rules/<host>`` otherwise."""
        normalized = normalize_host_pattern(host)
        return self.rules_root / (ANY_DIR_NAME if is_host_pattern(normalized) else normalized)

    def rule_path(self, host: str, rule_id: str) -> Path:
        return self.rules_dir(host) / f"{check_rule_id(rule_id)}.json"

    def fixture_path(self, host: str, rule_id: str) -> Path:
        return self.rule_path(host, rule_id).with_suffix(".body")

    # ---- routes -------------------------------------------------------------

    def _routes_file(self) -> Path:
        return self.rules_root / ROUTES_FILE_NAME

    def list_routes(self) -> list[RouteConfig]:
        path = self._routes_file()
        if not path.is_file():
            return []
        return RouteTable.model_validate_json(path.read_text(encoding="utf-8")).routes

    def save_route(self, route: RouteConfig) -> Path:
        """Insert or replace the route for ``route.host``."""
        routes = [item for item in self.list_routes() if item.host != route.host]
        routes.append(route)
        routes.sort(key=lambda item: item.host)
        path = self._routes_file()
        _atomic_write(path, RouteTable(routes=routes).model_dump_json(indent=2) + "\n")
        return path

    def get_route(self, host: str) -> RouteConfig | None:
        normalized = normalize_host_pattern(host)
        return next((route for route in self.list_routes() if route.host == normalized), None)

    def find_route(self, host: str, path: str | None = None) -> RouteConfig | None:
        """The route serving a concrete host: exact host first, then the narrowest glob."""
        candidates = [
            route
            for route in self.list_routes()
            if host_matches(route.host, host)
            and (path is None or any(path_matches(pattern, path) for pattern in route.paths))
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda route: (is_host_pattern(route.host), -len(route.host.replace("*", ""))))
        return candidates[0]

    # ---- rules --------------------------------------------------------------

    def save_rule(
        self,
        rule: MockRule,
        fixture: bytes | None = None,
        *,
        archive_previous: bool = True,
    ) -> tuple[Path, str | None]:
        """Write a rule (and fixture) atomically; archive the previous version."""
        path = self.rule_path(rule.host, rule.id)
        archived = None
        if archive_previous and path.exists():
            archived = self._archive(rule.host, rule.id, keep_fixture=fixture is None)
        if fixture is not None:
            _atomic_write(self.fixture_path(rule.host, rule.id), fixture)
        _atomic_write(path, rule.model_dump_json(indent=2) + "\n")
        return path, archived

    def rule_dirs(self) -> list[Path]:
        if not self.rules_root.is_dir():
            return []
        return sorted(child for child in self.rules_root.iterdir() if child.is_dir())

    def load_dir(self, directory: Path) -> tuple[list[MockRule], list[str]]:
        """Return valid rules and one error string per unreadable rule file."""
        if not directory.is_dir():
            return [], []
        is_any = directory.name == ANY_DIR_NAME
        rules: list[MockRule] = []
        errors: list[str] = []
        for path in sorted(directory.glob("*.json")):
            try:
                rule = MockRule.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                errors.append(f"{directory.name}/{path.name}: {exc}")
                continue
            in_place = (
                is_host_pattern(rule.host) if is_any else rule.host == directory.name
            ) and f"{rule.id}.json" == path.name
            if not in_place:
                errors.append(f"{directory.name}/{path.name}: id/host do not match the file location")
                continue
            rules.append(rule)
        return rules, errors

    def rules_for_host(self, host: str) -> tuple[list[MockRule], list[str]]:
        """Rules that can apply to a concrete host: its own plus matching pattern rules."""
        exact, errors = self.load_dir(self.rules_root / normalize_host(host))
        patterns, pattern_errors = self.load_dir(self.rules_root / ANY_DIR_NAME)
        return exact + [rule for rule in patterns if host_matches(rule.host, host)], [
            *errors,
            *pattern_errors,
        ]

    def all_rules(self) -> tuple[list[MockRule], list[str]]:
        rules: list[MockRule] = []
        errors: list[str] = []
        for directory in self.rule_dirs():
            found, problems = self.load_dir(directory)
            rules.extend(found)
            errors.extend(problems)
        return rules, errors

    def get_rule(self, host: str, rule_id: str) -> MockRule:
        path = self.rule_path(host, rule_id)
        if not path.is_file():
            raise FileNotFoundError(f"no rule `{rule_id}` for host `{normalize_host_pattern(host)}`")
        return MockRule.model_validate_json(path.read_text(encoding="utf-8"))

    def read_fixture(self, host: str, rule_id: str) -> bytes:
        path = self.fixture_path(host, rule_id)
        if not path.is_file():
            raise FileNotFoundError(f"fixture for rule `{rule_id}` is missing: {path}")
        return path.read_bytes()

    def remove_rule(self, host: str, rule_id: str) -> str:
        if not self.rule_path(host, rule_id).is_file():
            raise FileNotFoundError(f"no rule `{rule_id}` for host `{normalize_host_pattern(host)}`")
        return self._archive(host, rule_id)

    def _archive(self, host: str, rule_id: str, *, keep_fixture: bool = False) -> str:
        """Move the rule (and its fixture) under ``_archive``; never deletes.

        ``keep_fixture`` copies the fixture instead of moving it, for rule-only
        updates that must keep serving the same body.
        """
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        destination = (
            self.root / ARCHIVE_DIR_NAME / RULES_DIR_NAME / self.rules_dir(host).name / stamp
        )
        destination.mkdir(parents=True, exist_ok=True)
        rule_file = self.rule_path(host, rule_id)
        fixture_file = self.fixture_path(host, rule_id)
        if rule_file.exists():
            shutil.move(str(rule_file), str(destination / rule_file.name))
        if fixture_file.exists():
            if keep_fixture:
                shutil.copy2(fixture_file, destination / fixture_file.name)
            else:
                shutil.move(str(fixture_file), str(destination / fixture_file.name))
        return str(destination)


def _atomic_write(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = content.encode("utf-8") if isinstance(content, str) else content
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
