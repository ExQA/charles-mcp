"""Charles records the server's own session exports; the agent must hear about it.

Found in QA: every tool call exports the session through the Charles proxy,
Charles records that request with the export as its body, and the session
grows with each call (100 KB to 2 GB in one run). The entries are filtered out
of every result, so without this warning the only symptom is slowness.
"""

from __future__ import annotations

from charles_mcp.live_state import CONTROL_HOST, LiveCaptureManager, self_recording_warning


def _app_entry(path: str = "/api/wallet") -> dict:
    return {"host": "api.example.com", "method": "POST", "path": path, "response": {"status": 200}}


def _export_entry(body_bytes: int) -> dict:
    return {
        "host": CONTROL_HOST,
        "method": "GET",
        "path": "/session/export-json",
        "response": {"status": 200, "sizes": {"body": body_bytes}},
    }


def test_a_clean_session_raises_no_warning() -> None:
    assert self_recording_warning([_app_entry(), _app_entry("/api/cards")]) is None


def test_recorded_exports_are_counted_with_their_size() -> None:
    warning = self_recording_warning([_app_entry(), _export_entry(1_048_576), _export_entry(3_145_728)])

    assert warning is not None
    assert warning.startswith("charles_records_own_exports")
    assert "2 request(s)" in warning
    assert "4.0 MB" in warning
    # The warning must carry the fix, not just the diagnosis.
    assert "Recording Settings" in warning and "Exclude" in warning


def test_the_warning_reaches_a_new_capture_and_every_read() -> None:
    session = [_app_entry(), _export_entry(1024)]
    manager = LiveCaptureManager()

    capture = manager.start(managed=False, include_existing=True, baseline_items=session)
    assert any("charles_records_own_exports" in w for w in capture.warnings)

    result = manager.read(capture.capture_id, session + [_export_entry(2048)])
    assert any("charles_records_own_exports" in w for w in result.warnings)
    # The recorded exports themselves stay out of the results.
    assert all(item["host"] != CONTROL_HOST for item in result.items)
