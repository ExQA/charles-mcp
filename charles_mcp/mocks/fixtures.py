"""Turning a captured body back into the bytes a client will receive.

Charles hands bodies over as text, decoded with whatever charset the response
declared. A fixture has to go back out as bytes, and the header that travels
with it has to describe those bytes truthfully — a response labelled
``charset=windows-1251`` carrying UTF-8 is a mock that breaks on the first
non-ASCII character, which is exactly the kind of failure that surfaces far
from its cause.
"""

from __future__ import annotations

import re

_CHARSET = re.compile(r"charset\s*=\s*\"?([\w-]+)\"?", re.IGNORECASE)


def _is_utf8(charset: str | None) -> bool:
    return charset is not None and charset.strip().lower().replace("-", "").replace("_", "") in {
        "utf8",
        "utf8mb4",
    }


def encode_fixture(
    text: str, content_type: str, charset: str | None
) -> tuple[bytes, str, str | None]:
    """Encode a fixture body, keeping the captured charset when it can hold the text.

    Returns the bytes, the content type that describes them, and a warning when
    the charset had to change.
    """
    labelled = _CHARSET.search(content_type)
    declared = charset or (labelled.group(1) if labelled else None)

    if declared is None or _is_utf8(declared):
        return text.encode("utf-8"), content_type, None

    try:
        return text.encode(declared), content_type, None
    except (LookupError, UnicodeEncodeError):
        # The captured charset cannot carry this text (or Python does not know
        # it), so the body goes out as UTF-8 and the header is corrected to
        # match — silently keeping the old label would mis-decode in the app.
        if _CHARSET.search(content_type):
            corrected = _CHARSET.sub("charset=utf-8", content_type, count=1)
        else:
            corrected = f"{content_type}; charset=utf-8"
        warning = (
            f"the captured response declared charset {declared}, which cannot carry this body: "
            "the fixture is stored as UTF-8 and its Content-Type says so"
        )
        return text.encode("utf-8"), corrected, warning
