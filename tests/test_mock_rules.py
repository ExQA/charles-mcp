import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from charles_mcp.mocks.json_patch import JsonPatchError, get_pointer
from charles_mcp.mocks.rules import (
    IncomingRequest,
    MockRule,
    RouteConfig,
    RuleStore,
    make_rule_id,
    route_covers_rule_path,
    select_rule,
)


def test_route_covers_rule_path_treats_the_rule_path_as_a_pattern() -> None:
    # A prefix route covers a rule whose wildcard sits inside that prefix.
    assert route_covers_rule_path("/api/*", "/api/*/payoneer")
    assert route_covers_rule_path("/api/*", "/api/p24-aos2/payoneer")
    assert route_covers_rule_path("/*", "/api/*/payoneer")
    # A narrower route does not cover a rule that also matches other segments.
    assert not route_covers_rule_path("/api/p24-aos2/*", "/api/*/payoneer")
    assert not route_covers_rule_path("/other/*", "/api/*/payoneer")
    # An exact route covers only the identical exact path.
    assert route_covers_rule_path("/api/x", "/api/x")
    assert not route_covers_rule_path("/api/x", "/api/*")


def _rule(rule_id: str, **match: object) -> MockRule:
    return MockRule.model_validate(
        {
            "id": rule_id,
            "host": "api.example.com",
            "match": {"method": "POST", "path": "/api/v1/wallet", **match},
            "response": {"mode": "patch"},
        }
    )


def _request(body: object, *, content_type: str = "application/json", **extra: object) -> IncomingRequest:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return IncomingRequest(
        method=str(extra.get("method", "POST")),
        path=str(extra.get("path", "/api/v1/wallet")),
        query=extra.get("query", {}),  # type: ignore[arg-type]
        headers=extra.get("headers", {}),  # type: ignore[arg-type]
        body=raw,
        content_type=content_type,
    )


def test_same_url_is_routed_by_body_action() -> None:
    rules = [
        _rule("init", body={"/action": "init"}),
        _rule("transactions", body={"/action": "transactions"}),
    ]

    assert select_rule(rules, _request({"action": "init"})).id == "init"  # type: ignore[union-attr]
    assert select_rule(rules, _request({"action": "transactions"})).id == "transactions"  # type: ignore[union-attr]
    assert select_rule(rules, _request({"action": "payout"})) is None
    assert select_rule(rules, _request({"no_action": 1})) is None


def test_nested_pointer_and_non_string_values() -> None:
    rules = [_rule("nested", body={"/meta/flags/0": True, "/meta/version": 2})]

    assert select_rule(rules, _request({"meta": {"flags": [True], "version": 2}})) is not None
    assert select_rule(rules, _request({"meta": {"flags": [False], "version": 2}})) is None


def test_form_bodies_match_too() -> None:
    rules = [_rule("form", body={"/action": "init"})]
    request = _request(
        b"action=init&lang=uk", content_type="application/x-www-form-urlencoded"
    )

    assert select_rule(rules, request) is not None


def test_non_matching_method_path_query_and_headers() -> None:
    rule = _rule("strict", query={"lang": "uk"}, headers={"X-App": "ios"}, body={"/action": "init"})
    good = {"query": {"lang": ["uk"]}, "headers": {"x-app": "ios"}}

    assert select_rule([rule], _request({"action": "init"}, **good)) is not None
    assert select_rule([rule], _request({"action": "init"}, method="GET", **good)) is None
    assert select_rule([rule], _request({"action": "init"}, path="/other", **good)) is None
    assert select_rule([rule], _request({"action": "init"}, query={"lang": ["en"]},
                                        headers={"x-app": "ios"})) is None
    assert select_rule([rule], _request({"action": "init"}, query={"lang": ["uk"]},
                                        headers={"x-app": "android"})) is None


def test_priority_then_specificity_decides() -> None:
    generic = _rule("generic")
    specific = _rule("specific", body={"/action": "init"})
    urgent = _rule("urgent").model_copy(update={"priority": 10})

    assert select_rule([generic, specific], _request({"action": "init"})).id == "specific"  # type: ignore[union-attr]
    assert select_rule([generic, specific, urgent], _request({"action": "init"})).id == "urgent"  # type: ignore[union-attr]


def test_path_patterns_cover_segments_that_vary_by_platform() -> None:
    any_platform = _rule("any", path="/api/*/payoneer", body={"/action": "init"})
    p24_only = _rule("p24", path="/api/p24-*/payoneer", body={"/action": "init"})

    def picked(rule: MockRule, path: str) -> bool:
        return select_rule([rule], _request({"action": "init"}, path=path)) is not None

    assert picked(any_platform, "/api/p24-aos2/payoneer")
    assert picked(any_platform, "/api/p24-ios2/payoneer")
    assert not picked(any_platform, "/api/payoneer")
    assert not picked(any_platform, "/api/p24-aos2/v2/payoneer")
    assert picked(p24_only, "/api/p24-aos2/payoneer")
    assert not picked(p24_only, "/api/web-aos2/payoneer")


def test_exact_path_wins_over_a_path_pattern() -> None:
    pattern = _rule("pattern", path="/api/*/payoneer", body={"/action": "init"})
    exact = _rule("exact", path="/api/p24-aos2/payoneer", body={"/action": "init"})

    chosen = select_rule([pattern, exact], _request({"action": "init"}, path="/api/p24-aos2/payoneer"))
    other = select_rule([pattern, exact], _request({"action": "init"}, path="/api/p24-ios2/payoneer"))

    assert chosen is not None and chosen.id == "exact"
    assert other is not None and other.id == "pattern"


def test_priority_lifts_an_action_pattern_over_a_bare_exact_path() -> None:
    # A conditionless exact-path rule outranks an action pattern by default...
    bare = _rule("bare", path="/api/p24-aos2/payoneer")
    action = _rule("action", path="/api/*/payoneer", body={"/action": "init"})
    request = _request({"action": "init"}, path="/api/p24-aos2/payoneer")
    assert select_rule([bare, action], request).id == "bare"  # type: ignore[union-attr]

    # ...until the action rule is given a higher priority.
    action = action.model_copy(update={"priority": 5})
    assert select_rule([bare, action], request).id == "action"  # type: ignore[union-attr]


def test_double_star_in_a_rule_path_is_rejected() -> None:
    with pytest.raises(ValidationError, match=r"`\*\*` is not special"):
        _rule("bad", path="/api/**/payoneer")


def test_disabled_rules_are_skipped() -> None:
    rule = _rule("off", body={"/action": "init"}).model_copy(update={"enabled": False})

    assert select_rule([rule], _request({"action": "init"})) is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"id": "Bad ID"},
        {"id": "../escape"},
        {"host": "https://api.example.com"},
        {"match": {"path": "/items?page=1"}},
        {"match": {"path": "/items", "body": {"action": "init"}}},
        {"response": {"mode": "patch", "patches": [{"op": "add", "path": "/x"}]}},
        {"response": {"mode": "fixture", "status": 999}},
    ],
)
def test_invalid_rules_are_rejected(overrides: dict) -> None:
    document = {
        "id": "ok",
        "host": "api.example.com",
        "match": {"path": "/items"},
        "response": {"mode": "patch"},
        **overrides,
    }
    with pytest.raises((ValidationError, JsonPatchError, ValueError)):
        MockRule.model_validate(document)


def test_make_rule_id_is_valid_and_stable() -> None:
    first = make_rule_id("POST", "/api/p/wallet", {"/action": "init"})
    second = make_rule_id("POST", "/api/p/wallet", {"/action": "init"})
    other = make_rule_id("POST", "/api/p/wallet", {"/action": "payout"})

    assert first == second != other
    assert first.startswith("post-api-p-wallet-action-init-")
    MockRule.model_validate(
        {"id": first, "host": "a.example.com", "match": {"path": "/x"}, "response": {"mode": "patch"}}
    )


def test_get_pointer() -> None:
    document = {"a": [{"b": 1}], "c/d": 2}

    assert get_pointer(document, "/a/0/b") == 1
    assert get_pointer(document, "/c~1d") == 2
    with pytest.raises(KeyError):
        get_pointer(document, "/a/5")


def test_store_round_trip_archive_and_invalid_files(tmp_path: Path) -> None:
    store = RuleStore(tmp_path)
    store.save_route(RouteConfig(host="api.example.com", paths=["/api/v1/wallet"]))
    fixture_rule = MockRule.model_validate(
        {
            "id": "fx",
            "host": "api.example.com",
            "match": {"path": "/api/v1/wallet", "body": {"/action": "init"}},
            "response": {"mode": "fixture", "status": 200},
        }
    )
    store.save_rule(fixture_rule, b'{"v": 1}')
    _, archived = store.save_rule(fixture_rule.model_copy(update={"priority": 5}))

    # A rule-only update keeps serving the same fixture and archives a copy.
    assert store.read_fixture("api.example.com", "fx") == b'{"v": 1}'
    assert archived is not None and (Path(archived) / "fx.body").read_bytes() == b'{"v": 1}'

    host_dir = tmp_path / "_rules" / "api.example.com"
    (host_dir / "broken.json").write_text("{not json", encoding="utf-8")
    (host_dir / "moved.json").write_text(fixture_rule.model_dump_json(), encoding="utf-8")
    rules, errors = store.rules_for_host("api.example.com")

    assert [rule.id for rule in rules] == ["fx"]
    assert rules[0].priority == 5
    assert any("broken.json" in error for error in errors)
    assert any("moved.json" in error and "do not match" in error for error in errors)

    archived_to = store.remove_rule("api.example.com", "fx")
    assert not (host_dir / "fx.json").exists() and not (host_dir / "fx.body").exists()
    assert (Path(archived_to) / "fx.body").read_bytes() == b'{"v": 1}'
    assert [route.host for route in store.list_routes()] == ["api.example.com"]
