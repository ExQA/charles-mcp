from pathlib import Path


def test_readme_documents_current_install_and_runtime_contract() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "Швидкий старт" in readme
    assert "Встановіть і налаштуйте MCP-клієнт" in readme
    assert "Каталог інструментів" in readme
    assert "uvx" in readme
    assert "Claude Code CLI" in readme
    assert "Codex CLI" in readme
    assert "загальний JSON-конфіг" in readme
    assert "Автовстановлення через AI-агента" in readme
    assert "charles_mcp.main" in readme
    assert "CHARLES_MANAGE_LIFECYCLE" in readme
    assert "CHARLES_USER" in readme
    assert "admin" in readme
    assert "start_live_capture" in readme
    assert "read_live_capture" in readme
    assert "group_capture_analysis" in readme
    assert "reverse_start_live_analysis" in readme
    assert "reverse_import_session" in readme
    assert "mock_route_setup" in readme
    assert "mock_discover_variants" in readme
    assert "stop_failed" in readme
    assert "recoverable" in readme
    assert "active_capture_preserved" in readme
    assert "README.en.md" in readme
    assert "docs/README.uk.md" in readme
    assert "docs/contracts/tools.uk.md" in readme
    assert "AGENTS.uk.md" in readme
    assert "docs/agent-workflows.uk.md" in readme
    assert "docs/team-installation.uk.md" in readme
    assert "PowerShell" not in readme
    assert "Windows CMD" not in readme
    assert "Antigravity" not in readme
