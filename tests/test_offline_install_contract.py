"""What the offline archive promises: an installer and a prompt that never reach a registry.

These files are what a teammate runs on a machine that may have no network and
no `uv`, so the properties below are the ones that make the archive usable at
all — and the ones a careless edit would quietly break.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALLER = REPO_ROOT / "install.sh"
PROMPT = REPO_ROOT / "INSTALL-PROMPT.md"


def test_installer_is_executable() -> None:
    # git archive keeps the mode, so a lost +x would ship an archive whose
    # documented `./install.sh` fails with "permission denied".
    assert INSTALLER.is_file()
    assert os.stat(INSTALLER).st_mode & stat.S_IXUSR


def test_installer_never_reaches_a_registry() -> None:
    script = INSTALLER.read_text(encoding="utf-8")

    assert "--find-links wheels" in script
    assert "-r requirements-lock.txt" in script

    for install in ("pip install", "pip install --quiet"):
        for line in script.splitlines():
            if install in line and "--no-index" not in line and "--upgrade pip" not in line:
                raise AssertionError(f"install without --no-index: {line.strip()}")


def test_installer_installs_the_fork_itself_and_checks_it() -> None:
    script = INSTALLER.read_text(encoding="utf-8")
    # Without the project wheel there is no charles-mcp command and no version.
    assert "charles_mcp-*.whl" in script
    assert "--selftest" in script


def test_prompt_file_points_at_the_installer_and_the_check() -> None:
    prompt = PROMPT.read_text(encoding="utf-8")
    assert "./install.sh" in prompt
    assert "--selftest" in prompt
    assert "<PATH>" in prompt
    # The registry trap is the whole reason this prompt exists.
    assert "PyPI" in prompt
