import asyncio
import json
from pathlib import Path

import pytest

from charles_mcp.config import Config
from charles_mcp.server import create_server


def _tool_result(call_result):
    payload = call_result[1]
    return payload["result"] if isinstance(payload, dict) and "result" in payload else payload


def _tool_output_schema(tool_name: str) -> dict | None:
    async def _get_schema():
        server = create_server()
        tools = await server.list_tools()
        for tool in tools:
            if tool.name == tool_name:
                return tool.outputSchema
        return None

    return asyncio.run(_get_schema())


def test_history_tools_have_structured_output_schema() -> None:
    query_schema = _tool_output_schema("query_recorded_traffic")
    list_schema = _tool_output_schema("list_recordings")
    snapshot_schema = _tool_output_schema("get_recording_snapshot")

    assert query_schema is not None
    assert "properties" in query_schema
    assert "items" in query_schema["properties"]
    assert "total_items" in query_schema["properties"]
    assert "source" in query_schema["properties"]

    assert list_schema is not None
    assert "properties" in list_schema
    assert "items" in list_schema["properties"]
    assert "total_items" in list_schema["properties"]

    assert snapshot_schema is not None
    assert "properties" in snapshot_schema
    assert "items" in snapshot_schema["properties"]
    assert "total_items" in snapshot_schema["properties"]
    assert "source" in snapshot_schema["properties"]


@pytest.mark.asyncio
async def test_list_recordings_returns_structured_payload(tmp_path: Path) -> None:
    config = Config(base_dir=str(tmp_path))
    snapshot = [{"host": "api.example.com", "path": "/history"}]
    (tmp_path / "package").mkdir(parents=True, exist_ok=True)
    recording_path = tmp_path / "package" / "20260306010101.chlsj"
    recording_path.write_text(json.dumps(snapshot), encoding="utf-8")

    server = create_server(config)
    call_result = await server.call_tool("list_recordings", {})
    result = _tool_result(call_result)

    assert result["total_items"] == 1
    assert result["warnings"] == []
    assert result["items"][0]["filename"] == "20260306010101.chlsj"
    assert result["items"][0]["path"] == str(recording_path)


@pytest.mark.asyncio
async def test_get_recording_snapshot_returns_structured_payload(tmp_path: Path) -> None:
    config = Config(base_dir=str(tmp_path))
    snapshot = [{"host": "api.example.com", "path": "/snapshot"}]
    recording_path = tmp_path / "package" / "manual.chlsj"
    recording_path.parent.mkdir(parents=True, exist_ok=True)
    recording_path.write_text(json.dumps(snapshot), encoding="utf-8")

    server = create_server(config)
    call_result = await server.call_tool(
        "get_recording_snapshot",
        {"path": str(recording_path)},
    )
    result = _tool_result(call_result)

    assert result["source"] == "history"
    assert result["path"] == str(recording_path)
    assert result["total_items"] == 1
    assert result["items"][0]["path"] == "/snapshot"


def _recorded_entry(index: int, *, body_chars: int = 10_000) -> dict:
    """A recorded API call with a body large enough to matter in a model's context."""
    return {
        "status": "COMPLETE",
        "method": "POST",
        "scheme": "https",
        "host": "api.example.com",
        "path": f"/api/items/{index}",
        "times": {"start": f"2026-03-06T10:{index // 60:02d}:{index % 60:02d}.000+00:00"},
        "request": {
            "mimeType": "application/json",
            "header": {"firstLine": f"POST /api/items/{index} HTTP/1.1", "headers": []},
            "body": {"text": json.dumps({"action": "list", "page": index})},
        },
        "response": {
            "status": 200,
            "mimeType": "application/json",
            "header": {"firstLine": "HTTP/1.1 200 OK", "headers": []},
            "body": {"text": json.dumps({"needle": index, "padding": "x" * body_chars})},
        },
    }


def _saved_recording(tmp_path: Path, count: int) -> tuple[Config, Path]:
    config = Config(base_dir=str(tmp_path))
    recording_path = tmp_path / "package" / "20260306120000.chlsj"
    recording_path.parent.mkdir(parents=True, exist_ok=True)
    recording_path.write_text(
        json.dumps([_recorded_entry(index) for index in range(count)]), encoding="utf-8"
    )
    return config, recording_path


def _payload_chars(call_result) -> int:
    return sum(len(getattr(block, "text", "") or "") for block in call_result[0])


@pytest.mark.asyncio
async def test_a_recording_snapshot_is_listed_in_pages_without_bodies(tmp_path: Path) -> None:
    """A 120-entry recording is 1.2 MB of bodies; the listing must not carry them."""
    config, recording_path = _saved_recording(tmp_path, 120)
    server = create_server(config)

    first_call = await server.call_tool("get_recording_snapshot", {"path": str(recording_path)})
    first = _tool_result(first_call)
    last = _tool_result(
        await server.call_tool(
            "get_recording_snapshot", {"path": str(recording_path), "offset": 100}
        )
    )

    assert first["total_items"] == 120
    assert len(first["items"]) == 50
    assert first["truncated"] is True and first["next_offset"] == 50
    assert [item["path"] for item in first["items"][:2]] == ["/api/items/0", "/api/items/1"]
    assert all(item["entry_id"] and "response" not in item for item in first["items"])
    assert _payload_chars(first_call) < 30_000

    assert len(last["items"]) == 20
    assert last["truncated"] is False and last.get("next_offset") is None


@pytest.mark.asyncio
async def test_a_recording_query_returns_bounded_summaries_with_previews(tmp_path: Path) -> None:
    config, _recording_path = _saved_recording(tmp_path, 60)
    server = create_server(config)

    call = await server.call_tool("query_recorded_traffic", {"keyword_regex": "needle"})
    result = _tool_result(call)
    quiet = _tool_result(
        await server.call_tool(
            "query_recorded_traffic",
            {"keyword_regex": "needle", "keep_request": False, "keep_response": False, "max_items": 5},
        )
    )

    assert result["total_items"] == 60  # every match is counted
    assert len(result["items"]) == 20 and result["truncated"] is True
    item = result["items"][0]
    assert item["request_body_preview"] == '{"action": "list", "page": 0}'
    assert len(item["response_body_preview"]) == 256 and item["preview_truncated"] is True
    assert "needle" in item["_match_location"]["content"]
    assert _payload_chars(call) < 30_000

    assert len(quiet["items"]) == 5
    assert all("response_body_preview" not in entry for entry in quiet["items"])


@pytest.mark.asyncio
async def test_an_entry_listed_from_a_recording_can_be_opened_by_its_id(tmp_path: Path) -> None:
    config, recording_path = _saved_recording(tmp_path, 3)
    server = create_server(config)
    listed = _tool_result(
        await server.call_tool("get_recording_snapshot", {"path": str(recording_path)})
    )

    detail = _tool_result(
        await server.call_tool(
            "get_traffic_entry_detail",
            {
                "source": "history",
                "entry_id": listed["items"][2]["entry_id"],
                "recording_path": listed["path"],
                "include_full_body": True,
                "max_body_chars": 20_000,
            },
        )
    )

    entry = detail["detail"]["entry"]
    assert entry["path"] == "/api/items/2"
    assert json.loads(entry["response"]["body"]["full_text"])["needle"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("get_recording_snapshot", {"limit": 0}),
        ("get_recording_snapshot", {"offset": -1}),
        ("query_recorded_traffic", {"max_items": 0}),
    ],
)
async def test_page_arguments_are_checked(tmp_path: Path, tool: str, arguments: dict) -> None:
    config, _recording_path = _saved_recording(tmp_path, 2)
    server = create_server(config)

    with pytest.raises(Exception, match="must"):
        await server.call_tool(tool, arguments)
