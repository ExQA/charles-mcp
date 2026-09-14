from pathlib import Path


def test_docs_hub_and_related_contract_docs_exist() -> None:
    assert Path("docs/README.md").exists()
    assert Path("AGENTS.md").exists()
    assert Path("docs/agent-workflows.md").exists()
    assert Path("docs/contracts/tools.md").exists()
    assert not Path("docs/migrations").exists()


def test_docs_hub_links_and_responsibilities_are_declared() -> None:
    hub = Path("docs/README.md").read_text(encoding="utf-8")

    assert "../README.md" in hub
    assert "../README.en.md" in hub
    assert "../AGENTS.md" in hub
    assert "./agent-workflows.md" in hub
    assert "./contracts/tools.md" in hub
    assert "migrations/" not in hub
    assert "global agent behavior rules" in hub
    assert "task-oriented workflow playbooks" in hub
    assert "canonical public tool surface" in hub


def test_readme_navigation_includes_docs_and_agent_contract_entrypoints() -> None:
    readme_ru = Path("README.md").read_text(encoding="utf-8")
    readme_en = Path("README.en.md").read_text(encoding="utf-8")

    for content, suffix in ((readme_ru, ".uk.md"), (readme_en, ".md")):
        assert f"docs/README{suffix}" in content
        assert f"AGENTS{suffix}" in content
        assert f"docs/agent-workflows{suffix}" in content
        assert f"docs/contracts/tools{suffix}" in content
