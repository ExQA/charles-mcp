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
