"""Minimal JSON edits addressed by RFC 6901 JSON Pointers.

Supported operations:

- ``{"op": "set", "path": "/data/balance", "value": 0}`` replaces a value or
  adds a new object key. For arrays, an existing index is replaced and ``-``
  (or an index equal to the length) appends.
- ``{"op": "remove", "path": "/data/banner"}`` deletes an object key or
  array element.

Wherever an array index is allowed, a ``[field=value]`` segment selects the
one element whose ``field`` equals ``value``, so the edit does not depend on
the order the server returns: ``/data/balanceList/[currency=USD]/balance``.
Strings compare as they are, other values in their JSON form (``[id=42]``,
``[active=true]``). No match, or more than one, is an error.

Intermediate containers are never created implicitly, so a typo in a path
fails loudly instead of silently producing a differently shaped response.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

SUPPORTED_OPS = ("set", "remove")
_SELECTOR = re.compile(r"^\[([^=\[\]]+)=(.*)\]$")


class JsonPatchError(ValueError):
    """A patch operation could not be applied."""


def parse_pointer(pointer: str) -> list[str]:
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise JsonPatchError(f"JSON pointer must start with `/`: `{pointer}`")
    return [token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/")]


def _token_addresses_array(token: str) -> bool:
    return token == "-" or token.isdigit() or _SELECTOR.match(token) is not None


def pointers_may_alias(a: str, b: str) -> bool:
    """Could two patch pointers hit the same element by different addressing?

    ``/list/1/x`` and ``/list/[id=2]/x`` cannot be told apart without the
    document, so a rule that carries both may edit the wrong element. Same
    length, differing only where one side indexes an array (index / selector /
    ``-``) and the other addresses that same array differently.
    """
    if a == b:
        return True
    ta, tb = parse_pointer(a), parse_pointer(b)
    if len(ta) != len(tb) or not ta:
        return False
    saw_array_divergence = False
    for x, y in zip(ta, tb):
        if x == y:
            continue
        if _token_addresses_array(x) and _token_addresses_array(y):
            saw_array_divergence = True
            continue
        return False
    return saw_array_divergence


def get_pointer(document: Any, pointer: str) -> Any:
    """Return the value at ``pointer`` or raise ``KeyError`` when it is absent."""
    current = document
    for token in parse_pointer(pointer):
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        elif isinstance(current, list) and _SELECTOR.match(token):
            try:
                current = current[_selected_index(current, token, pointer)]
            except JsonPatchError:
                raise KeyError(pointer) from None
        else:
            raise KeyError(pointer)
    return current


def type_change_warnings(document: Any, patches: list[dict[str, Any]], label: str) -> list[str]:
    """Warn when a patch changes the JSON type of a value it replaces.

    A balance captured as ``0.0`` and mocked as ``1000`` is a different type on
    the wire — ``1000`` instead of ``1000.0`` — and a strict client can reject
    it while the response still looks right to a human. Booleans are excluded
    because in Python they *are* integers.
    """
    warnings: list[str] = []
    for patch in patches:
        if patch.get("op") != "set":
            continue
        pointer = str(patch.get("path", ""))
        try:
            current = get_pointer(document, pointer)
        except (KeyError, JsonPatchError):
            # A pointer that does not fit the capture is reported by
            # apply_patches with a far better message than a type warning.
            continue
        new = patch.get("value")
        if current is None or new is None:
            continue
        if isinstance(current, bool) or isinstance(new, bool):
            continue
        if isinstance(current, float) and isinstance(new, int):
            warnings.append(
                f"{label} patch `{pointer}` replaces {current!r} with {new!r}: the capture holds a "
                f"number with a decimal point, so use {float(new)!r} to keep the type"
            )
        elif isinstance(current, int) and isinstance(new, float):
            warnings.append(
                f"{label} patch `{pointer}` replaces {current!r} with {new!r}: the capture holds a "
                f"whole number, so use {int(new)!r} to keep the type"
            )
        elif isinstance(current, str) != isinstance(new, str):
            warnings.append(
                f"{label} patch `{pointer}` replaces {current!r} with {new!r}: that changes a "
                "string into a number or back, which strict clients reject"
            )
    return warnings


def dump_like(original: str, document: Any) -> bytes:
    """Serialise ``document`` in the shape ``original`` was written in.

    Apps parse more strictly than JSON requires, and a body that arrives
    reshaped is a mock that fails while still answering 200. Whitespace rarely
    breaks a parser on its own, but a reformatted body also hides real
    differences when someone compares the mock with the capture, so the stored
    and served bodies keep the captured layout: minified stays minified,
    indented keeps its indent, and a trailing newline is kept or left off.
    """
    stripped = original.strip()
    if "\n" in stripped:
        indent: int | str = 2
        for line in stripped.splitlines()[1:]:
            leading = len(line) - len(line.lstrip(" "))
            if leading:
                indent = leading
                break
            if line.startswith("\t"):
                indent = "\t"
                break
        text = json.dumps(document, ensure_ascii=False, indent=indent)
    else:
        text = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    if original.endswith("\n"):
        text += "\n"
    return text.encode("utf-8")


def validate_patches(patches: list[dict[str, Any]]) -> None:
    """Check patch structure without a target document."""
    for index, patch in enumerate(patches):
        if not isinstance(patch, dict):
            raise JsonPatchError(f"patch #{index} must be an object")
        if patch.get("op") not in SUPPORTED_OPS:
            raise JsonPatchError(
                f"patch #{index}: unsupported op `{patch.get('op')}`; "
                f"use one of {', '.join(SUPPORTED_OPS)}"
            )
        if "path" not in patch:
            raise JsonPatchError(f"patch #{index}: missing `path`")
        parse_pointer(str(patch["path"]))
        if patch["op"] == "set" and "value" not in patch:
            raise JsonPatchError(f"patch #{index}: `set` requires `value`")


def apply_patches(document: Any, patches: list[dict[str, Any]]) -> Any:
    result = copy.deepcopy(document)
    for index, patch in enumerate(patches):
        if not isinstance(patch, dict):
            raise JsonPatchError(f"patch #{index} must be an object")
        op = patch.get("op")
        if op not in SUPPORTED_OPS:
            raise JsonPatchError(
                f"patch #{index}: unsupported op `{op}`; use one of {', '.join(SUPPORTED_OPS)}"
            )
        if "path" not in patch:
            raise JsonPatchError(f"patch #{index}: missing `path`")
        tokens = parse_pointer(str(patch["path"]))

        if op == "set":
            if "value" not in patch:
                raise JsonPatchError(f"patch #{index}: `set` requires `value`")
            if not tokens:
                result = copy.deepcopy(patch["value"])
                continue
            _set(result, tokens, copy.deepcopy(patch["value"]), patch["path"])
        else:
            if not tokens:
                raise JsonPatchError(f"patch #{index}: cannot remove the whole document")
            _remove(result, tokens, patch["path"])
    return result


def _resolve_parent(document: Any, tokens: list[str], pointer: str) -> Any:
    current = document
    for depth, token in enumerate(tokens[:-1]):
        where = "/" + "/".join(tokens[: depth + 1])
        if isinstance(current, dict):
            if token not in current:
                raise JsonPatchError(f"`{pointer}`: `{where}` does not exist")
            current = current[token]
        elif isinstance(current, list):
            current = current[_existing_index(current, token, pointer)]
        else:
            raise JsonPatchError(f"`{pointer}`: `{where}` is not an object or array")
    return current


def _selected_index(array: list[Any], token: str, pointer: str) -> int:
    match = _SELECTOR.match(token)
    if match is None:
        raise JsonPatchError(f"`{pointer}`: `{token}` is not a `[field=value]` selector")
    field, expected = match.group(1), match.group(2)
    positions = [
        position
        for position, element in enumerate(array)
        if isinstance(element, dict) and field in element and _value_matches(element[field], expected)
    ]
    if not positions:
        raise JsonPatchError(f"`{pointer}`: no array element has `{field}` = `{expected}`")
    if len(positions) > 1:
        raise JsonPatchError(
            f"`{pointer}`: {len(positions)} array elements have `{field}` = `{expected}`; "
            "use a field that identifies one element"
        )
    return positions[0]


def _value_matches(value: Any, expected: str) -> bool:
    """Compare an element field to a selector value.

    A string field compares as written. A non-string field (a number, bool or
    null) compares by its JSON form too, so ``[balance=0]`` matches ``0.0`` and
    ``[active=true]`` matches ``True`` — servers rarely quote those.
    """
    if isinstance(value, str):
        return value == expected
    try:
        return bool(value == json.loads(expected))
    except (ValueError, TypeError):
        return _as_text(value) == expected


def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _existing_index(array: list[Any], token: str, pointer: str) -> int:
    if _SELECTOR.match(token):
        return _selected_index(array, token, pointer)
    if not token.isdigit() or (len(token) > 1 and token.startswith("0")):
        raise JsonPatchError(f"`{pointer}`: `{token}` is not a valid array index")
    position = int(token)
    if position >= len(array):
        raise JsonPatchError(
            f"`{pointer}`: index {position} is out of range (length {len(array)})"
        )
    return position


def _set(document: Any, tokens: list[str], value: Any, pointer: str) -> None:
    parent = _resolve_parent(document, tokens, pointer)
    last = tokens[-1]
    if isinstance(parent, dict):
        parent[last] = value
    elif isinstance(parent, list):
        if last == "-" or last == str(len(parent)):
            parent.append(value)
        else:
            parent[_existing_index(parent, last, pointer)] = value
    else:
        raise JsonPatchError(f"`{pointer}`: parent is not an object or array")


def _remove(document: Any, tokens: list[str], pointer: str) -> None:
    parent = _resolve_parent(document, tokens, pointer)
    last = tokens[-1]
    if isinstance(parent, dict):
        if last not in parent:
            raise JsonPatchError(f"`{pointer}` does not exist")
        del parent[last]
    elif isinstance(parent, list):
        del parent[_existing_index(parent, last, pointer)]
    else:
        raise JsonPatchError(f"`{pointer}`: parent is not an object or array")
