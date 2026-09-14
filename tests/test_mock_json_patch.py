import pytest

from charles_mcp.mocks.json_patch import (
    JsonPatchError,
    apply_patches,
    get_pointer,
    pointers_may_alias,
)


def _doc() -> dict:
    return {"data": {"balance": 120, "items": [{"id": 1}, {"id": 2}], "a/b": 1, "t~n": 2}}


def test_set_replace_add_and_append() -> None:
    original = _doc()

    result = apply_patches(
        original,
        [
            {"op": "set", "path": "/data/balance", "value": 0},
            {"op": "set", "path": "/data/unread", "value": 3},
            {"op": "set", "path": "/data/items/0/id", "value": 10},
            {"op": "set", "path": "/data/items/-", "value": {"id": 3}},
        ],
    )

    assert result["data"]["balance"] == 0
    assert result["data"]["unread"] == 3
    assert [item["id"] for item in result["data"]["items"]] == [10, 2, 3]
    assert original == _doc(), "input document must not be mutated"


def test_remove_key_and_array_element() -> None:
    result = apply_patches(
        _doc(),
        [
            {"op": "remove", "path": "/data/items/0"},
            {"op": "remove", "path": "/data/balance"},
        ],
    )

    assert result == {"data": {"items": [{"id": 2}], "a/b": 1, "t~n": 2}}


def test_pointer_escapes() -> None:
    result = apply_patches(
        _doc(),
        [
            {"op": "set", "path": "/data/a~1b", "value": "slash"},
            {"op": "set", "path": "/data/t~0n", "value": "tilde"},
        ],
    )

    assert result["data"]["a/b"] == "slash"
    assert result["data"]["t~n"] == "tilde"


def test_set_root_replaces_document() -> None:
    assert apply_patches(_doc(), [{"op": "set", "path": "", "value": []}]) == []


def _balances(*currencies: str) -> dict:
    return {
        "data": {
            "balanceList": [
                {"currency": code, "balance": "0.00", "balanceReal": 0.0} for code in currencies
            ]
        }
    }


@pytest.mark.parametrize("order", [("EUR", "USD"), ("USD", "EUR")])
def test_selector_edits_the_matching_element_in_any_order(order: tuple[str, str]) -> None:
    patches = [
        {"op": "set", "path": "/data/balanceList/[currency=USD]/balance", "value": "1000.00"},
        {"op": "set", "path": "/data/balanceList/[currency=USD]/balanceReal", "value": 1000.0},
    ]

    result = apply_patches(_balances(*order), patches)

    by_currency = {item["currency"]: item for item in result["data"]["balanceList"]}
    assert by_currency["USD"]["balance"] == "1000.00"
    assert by_currency["USD"]["balanceReal"] == 1000.0
    assert by_currency["EUR"]["balance"] == "0.00"


def test_selector_as_last_segment_replaces_and_removes() -> None:
    replaced = apply_patches(
        _balances("EUR", "USD"),
        [{"op": "set", "path": "/data/balanceList/[currency=EUR]", "value": {"currency": "PLN"}}],
    )
    removed = apply_patches(
        _balances("EUR", "USD"), [{"op": "remove", "path": "/data/balanceList/[currency=EUR]"}]
    )

    assert [item["currency"] for item in replaced["data"]["balanceList"]] == ["PLN", "USD"]
    assert [item["currency"] for item in removed["data"]["balanceList"]] == ["USD"]


def test_selector_compares_non_strings_in_json_form() -> None:
    result = apply_patches(
        {"items": [{"id": 41, "on": False}, {"id": 42, "on": True}]},
        [
            {"op": "set", "path": "/items/[id=42]/name", "value": "by id"},
            {"op": "set", "path": "/items/[on=false]/name", "value": "by flag"},
        ],
    )

    assert result["items"] == [
        {"id": 41, "on": False, "name": "by flag"},
        {"id": 42, "on": True, "name": "by id"},
    ]


@pytest.mark.parametrize(
    ("document", "message"),
    [
        (_balances("EUR"), "no array element has `currency` = `USD`"),
        (_balances("USD", "USD"), "2 array elements have `currency` = `USD`"),
    ],
)
def test_selector_needs_exactly_one_match(document: dict, message: str) -> None:
    patch = {"op": "set", "path": "/data/balanceList/[currency=USD]/balance", "value": "1"}

    with pytest.raises(JsonPatchError, match=message):
        apply_patches(document, [patch])


def test_get_pointer_understands_selectors() -> None:
    document = _balances("EUR", "USD")

    assert get_pointer(document, "/data/balanceList/[currency=USD]/balanceReal") == 0.0
    with pytest.raises(KeyError):
        get_pointer(document, "/data/balanceList/[currency=GBP]/balance")


def test_selector_matches_numbers_and_bools_written_without_quotes() -> None:
    document = {"rows": [{"amount": 1000.0, "on": False}, {"amount": 0.0, "on": True}]}

    # A number field selects whether the literal is "1000" or "1000.0".
    assert apply_patches(
        document, [{"op": "set", "path": "/rows/[amount=1000]/on", "value": True}]
    )["rows"][0]["on"] is True
    assert apply_patches(
        document, [{"op": "set", "path": "/rows/[amount=0.0]/on", "value": "hit"}]
    )["rows"][1]["on"] == "hit"
    # A bool field selects on true/false.
    assert apply_patches(
        document, [{"op": "set", "path": "/rows/[on=false]/amount", "value": 5}]
    )["rows"][0]["amount"] == 5


def test_selector_value_escapes_slash_as_in_json_pointer() -> None:
    document = {"routes": [{"path": "/api", "id": 1}, {"path": "/web", "id": 2}]}

    assert get_pointer(document, "/routes/[path=~1api]/id") == 1
    patched = apply_patches(
        document, [{"op": "set", "path": "/routes/[path=~1web]/id", "value": 99}]
    )
    assert patched["routes"][1]["id"] == 99
    assert patched["routes"][0]["id"] == 1


def test_pointers_may_alias_flags_index_versus_selector() -> None:
    assert pointers_may_alias("/list/1/x", "/list/[id=2]/x")
    assert pointers_may_alias("/list/-", "/list/[id=2]")
    assert pointers_may_alias("/list/1/x", "/list/1/x")
    # Different leaf, different object key, or different length are not aliases.
    assert not pointers_may_alias("/list/1/x", "/list/[id=2]/y")
    assert not pointers_may_alias("/a/x", "/a/y")
    assert not pointers_may_alias("/list/1/x", "/list/1")


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"op": "set", "path": "/missing/child", "value": 1}, "does not exist"),
        ({"op": "set", "path": "/data/items/9", "value": 1}, "out of range"),
        ({"op": "set", "path": "/data/items/01", "value": 1}, "not a valid array index"),
        ({"op": "remove", "path": "/data/nope"}, "does not exist"),
        ({"op": "remove", "path": ""}, "whole document"),
        ({"op": "set", "path": "/data/balance"}, "requires `value`"),
        ({"op": "add", "path": "/data/x", "value": 1}, "unsupported op"),
        ({"op": "set", "path": "data", "value": 1}, "must start with"),
        ({"op": "set", "path": "/data/balance/x", "value": 1}, "not an object or array"),
    ],
)
def test_invalid_patches_fail_loudly(patch: dict, message: str) -> None:
    with pytest.raises(JsonPatchError, match=message):
        apply_patches(_doc(), [patch])
