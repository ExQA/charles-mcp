from charles_mcp.config import Config
from charles_mcp.reverse.config import build_reverse_config


def test_reverse_config_lives_under_the_state_dir(tmp_path, monkeypatch) -> None:
    old_root = tmp_path / "old-vnext"
    old_root.mkdir()
    (old_root / "reverse.sqlite3").write_text("old-db", encoding="utf-8")
    state_root = tmp_path / "charles-state"
    monkeypatch.setenv("CHARLES_STATE_DIR", str(state_root))
    monkeypatch.setenv("CHARLES_VNEXT_STATE_DIR", str(old_root))

    reverse_config = build_reverse_config(Config())

    target_root = (state_root / "reverse").resolve()
    assert reverse_config.state_root == target_root
    assert reverse_config.database_path == target_root / "reverse.sqlite3"
    # The old vnext location is neither read nor moved.
    assert (old_root / "reverse.sqlite3").read_text(encoding="utf-8") == "old-db"
    assert not (target_root / "reverse.sqlite3").exists()
