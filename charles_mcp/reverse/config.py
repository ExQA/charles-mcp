"""Reverse-analysis configuration derived from the main Charles MCP config."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from charles_mcp.config import Config
from charles_mcp.config import get_config as get_base_config


@dataclass
class VNextConfig:
    state_root: Path
    database_path: Path = field(init=False)
    artifacts_dir: Path = field(init=False)
    temp_dir: Path = field(init=False)
    charles_cli_path: str | None = None
    replay_timeout_seconds: float = 20.0
    live_session_ttl_seconds: int = 900
    live_session_snapshot_history_limit: int = 20
    max_query_limit: int = 100
    charles_user: str = "admin"
    charles_pass: str = "123456"
    charles_base_url: str = "http://control.charles"
    charles_proxy_host: str = "127.0.0.1"
    charles_proxy_port: int = 8888

    def __post_init__(self) -> None:
        self.state_root = self.state_root.expanduser().resolve()
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.database_path = self.state_root / "reverse.sqlite3"
        self.artifacts_dir = self.state_root / "artifacts"
        self.temp_dir = self.state_root / "tmp"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    @property
    def charles_proxy_url(self) -> str:
        return f"http://{self.charles_proxy_host}:{self.charles_proxy_port}"


def build_reverse_config(config: Config | None = None) -> VNextConfig:
    base_config = config or get_base_config()
    return VNextConfig(
        state_root=Path(base_config.reverse_state_dir),
        charles_cli_path=base_config.charles_cli_path,
        replay_timeout_seconds=base_config.reverse_replay_timeout_seconds,
        live_session_ttl_seconds=base_config.reverse_live_session_ttl_seconds,
        live_session_snapshot_history_limit=base_config.reverse_live_snapshot_history_limit,
        max_query_limit=base_config.reverse_max_query_limit,
        charles_user=base_config.charles_user,
        charles_pass=base_config.charles_pass,
        charles_base_url=base_config.charles_base_url,
        charles_proxy_host=base_config.proxy_host,
        charles_proxy_port=base_config.proxy_port,
    )


_default_config: VNextConfig | None = None


def get_config() -> VNextConfig:
    global _default_config
    if _default_config is None:
        _default_config = build_reverse_config()
    return _default_config


def reset_config() -> None:
    global _default_config
    _default_config = None
