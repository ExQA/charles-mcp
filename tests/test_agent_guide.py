import asyncio

from charles_mcp.agent_instructions import AGENT_GUIDE_URI, SERVER_INSTRUCTIONS, agent_guide
from charles_mcp.server import create_server
from charles_mcp.tools.public_surface import CANONICAL_PUBLIC_TOOL_NAMES


def test_guide_mentions_every_public_tool() -> None:
    guide = agent_guide()
    missing = [name for name in CANONICAL_PUBLIC_TOOL_NAMES if f"`{name}" not in guide]
    assert missing == [], f"agent guide does not mention: {missing}"


def test_server_exposes_instructions_and_guide_resource() -> None:
    async def run() -> tuple[str | None, str]:
        server = create_server()
        contents = list(await server.read_resource(AGENT_GUIDE_URI))
        return server.instructions, contents[0].content

    instructions, content = asyncio.run(run())
    assert instructions == SERVER_INSTRUCTIONS
    assert AGENT_GUIDE_URI in instructions
    for rule in ("Never clear", "Charles runs", "reset_environment", "always includes"):
        assert rule in instructions
    assert "include_existing" not in instructions
    assert content == agent_guide()


def test_guide_covers_the_charles_specific_pitfalls() -> None:
    guide = agent_guide()
    assert "include_existing" not in guide
    for topic in (
        "always includes the traffic recorded before the call",
        "from memory on quit",
        "Preserve host header",
        "Path **empty**",
        "SSL Proxying",
        "Done",
        "X-Charles-MCP-Rule",
        "X-Charles-Map-Local",
        "passthrough",
        "verify_tls",
        "macOS Proxy",
    ):
        assert topic in guide, topic
