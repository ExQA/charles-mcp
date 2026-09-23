"""A mock must come back shaped like the capture it was built from.

Field report from a QA session: a fixture was stored pretty-printed although
the captured response was minified, a hand-written rule declared gzip over a
plain body, and a float balance was mocked as an int. Each one produced a
response that looked right and failed in the app.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from charles_mcp.mocks.dispatcher import _patch_json
from charles_mcp.mocks.fixtures import encode_fixture
from charles_mcp.mocks.json_patch import dump_like, type_change_warnings
from charles_mcp.mocks.rules import RuleResponse

MINIFIED = '{"data":{"balance":0.0,"currency":"USD"},"status":"ok"}'
INDENTED = '{\n  "data": {\n    "balance": 0.0\n  }\n}\n'


def test_dump_like_keeps_a_minified_body_minified() -> None:
    document = json.loads(MINIFIED)
    assert dump_like(MINIFIED, document).decode() == MINIFIED


def test_dump_like_keeps_indentation_and_the_trailing_newline() -> None:
    document = json.loads(INDENTED)
    written = dump_like(INDENTED, document).decode()
    assert written == INDENTED
    assert written.endswith("}\n")


def test_dump_like_follows_a_four_space_capture() -> None:
    original = '{\n    "a": 1\n}'
    assert dump_like(original, json.loads(original)).decode() == original


def test_patched_body_keeps_the_captured_layout() -> None:
    patched, warning = _patch_json(
        MINIFIED.encode(), [{"op": "set", "path": "/data/balance", "value": 1000.0}]
    )
    assert warning is None
    text = patched.decode()
    assert ": " not in text and "\n" not in text  # still minified
    assert '"balance":1000.0' in text
    # Key order survives, because json.dumps writes a dict in insertion order.
    assert text.index('"data"') < text.index('"status"')


def test_a_response_rule_cannot_claim_an_encoding_the_body_lacks() -> None:
    with pytest.raises(ValidationError, match="set by the dispatcher"):
        RuleResponse(mode="fixture", headers={"Content-Encoding": "gzip"})

    with pytest.raises(ValidationError, match="set by the dispatcher"):
        RuleResponse(mode="patch", headers={"content-length": "12"})

    # Headers that describe the content, not the transfer, stay allowed.
    assert RuleResponse(mode="fixture", headers={"Content-Type": "application/json"})


def test_a_float_replaced_by_an_int_is_reported() -> None:
    document = json.loads(MINIFIED)
    warnings = type_change_warnings(
        document, [{"op": "set", "path": "/data/balance", "value": 1000}], "response"
    )
    assert len(warnings) == 1
    assert "1000.0" in warnings[0]


def test_matching_types_and_booleans_are_not_reported() -> None:
    document = json.loads(MINIFIED)
    assert (
        type_change_warnings(
            document, [{"op": "set", "path": "/data/balance", "value": 1000.0}], "response"
        )
        == []
    )
    # bool is an int in Python; treating True as a type change would be noise.
    assert (
        type_change_warnings(
            document, [{"op": "set", "path": "/status", "value": "fail"}], "response"
        )
        == []
    )


def test_a_string_turned_into_a_number_is_reported() -> None:
    document = json.loads(MINIFIED)
    warnings = type_change_warnings(
        document, [{"op": "set", "path": "/status", "value": 1}], "response"
    )
    assert len(warnings) == 1
    assert "string into a number" in warnings[0]


def test_a_fixture_keeps_a_non_utf8_charset_when_it_fits() -> None:
    body, content_type, warning = encode_fixture(
        '{"місто":"Київ"}', "application/json; charset=windows-1251", "windows-1251"
    )
    assert warning is None
    assert content_type.endswith("charset=windows-1251")
    assert body.decode("windows-1251") == '{"місто":"Київ"}'


def test_a_fixture_switches_to_utf8_and_says_so_when_the_charset_cannot_hold_it() -> None:
    body, content_type, warning = encode_fixture(
        '{"city":"東京"}', "application/json; charset=windows-1251", "windows-1251"
    )
    assert warning is not None and "UTF-8" in warning
    assert content_type == "application/json; charset=utf-8"
    assert body.decode("utf-8") == '{"city":"東京"}'


def test_an_unknown_charset_falls_back_instead_of_raising() -> None:
    body, content_type, warning = encode_fixture(
        '{"a":1}', "application/json; charset=x-made-up", "x-made-up"
    )
    assert warning is not None
    assert content_type == "application/json; charset=utf-8"
    assert body == b'{"a":1}'


def test_unchanged_numbers_keep_their_original_spelling() -> None:
    original = '{"rate":0.10,"limit":1e3,"count":7,"balance":0.0}'
    patched, warning = _patch_json(
        original.encode(), [{"op": "set", "path": "/balance", "value": 1000.0}]
    )
    assert warning is None
    # Only the patched value is written anew; json.dumps would have produced
    # "rate":0.1 and "limit":1000.0.
    assert patched.decode() == '{"rate":0.10,"limit":1e3,"count":7,"balance":1000.0}'


def test_a_number_whose_type_the_patch_changed_is_not_respelled_back() -> None:
    original = '{"amount":1}'
    patched, _ = _patch_json(original.encode(), [{"op": "set", "path": "/amount", "value": 1.0}])
    # The patch asked for a float; reusing the literal "1" would silently undo it.
    assert patched.decode() == '{"amount":1.0}'


def test_ascii_escaped_bodies_stay_escaped() -> None:
    original = '{"city":"\\u041a\\u0438\\u0457\\u0432","n":1}'
    patched, _ = _patch_json(original.encode(), [{"op": "set", "path": "/n", "value": 2}])
    assert patched.decode() == '{"city":"\\u041a\\u0438\\u0457\\u0432","n":2}'


def test_escaped_slashes_stay_escaped() -> None:
    original = '{"url":"https:\\/\\/example.com\\/a","n":1}'
    patched, _ = _patch_json(original.encode(), [{"op": "set", "path": "/n", "value": 2}])
    assert patched.decode() == '{"url":"https:\\/\\/example.com\\/a","n":2}'


def test_indented_output_matches_json_dumps_layout() -> None:
    document = {"a": [1, {"b": None, "c": True}], "d": {}, "e": []}
    original = json.dumps(document, indent=2)
    assert dump_like(original, json.loads(original)).decode() == original
