"""Build the archive the team installs from: the tracked tree plus a wheelhouse.

The team gets this fork as a file, not from a registry, and the machines that
run it should not have to reach PyPI at all — every download is a request some
proxy logs, and ``pip install charles-mcp`` would silently fetch the *upstream*
package, which has no mock tools. So the archive carries its own dependencies:

- ``requirements-lock.txt`` — the runtime closure exported from ``uv.lock``,
  pinned and hashed, so the archive installs exactly what this fork was tested
  against;
- ``wheels/`` — those dependencies as wheels, one set per Python version and
  platform asked for. ``pip`` picks the compatible file out of the directory,
  so several sets can share it.

Install side (no uv, no network):

    python3 -m venv .venv
    .venv/bin/pip install --no-index --find-links wheels -r requirements-lock.txt

Usage:

    uv run python scripts/build_offline_archive.py
    uv run python scripts/build_offline_archive.py --python-version 3.12 \
        --platform macosx_11_0_arm64 --out ~/Downloads/charles-mcp.zip
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import date
from pathlib import Path

# Interpreters and platforms the team actually runs. Binary wheels
# (pydantic-core, brotli, zstandard, cffi) are built per version and per
# architecture; pure-Python ones are shared, so extra sets cost little.
DEFAULT_PYTHON_VERSIONS = ("3.12", "3.13", "3.14")
DEFAULT_PLATFORMS = ("macosx_11_0_arm64", "macosx_10_15_x86_64")

REPO_ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_PREFIX = "charles-mcp"


def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    """Run a command, failing loudly with its own output."""
    result = subprocess.run(command, capture_output=True, text=True, **kwargs)  # type: ignore[call-overload]
    if result.returncode != 0:
        sys.exit(f"{' '.join(command[:3])}… failed:\n{result.stderr.strip() or result.stdout.strip()}")
    return result


def pip_python() -> str:
    """An interpreter that has pip: uv's own venv usually does not ship one.

    Under ``uv run`` both ``sys.executable`` and ``python3`` on PATH are that
    pip-less venv, so the usual install locations are searched as well.
    """
    candidates = [
        sys.executable,
        shutil.which("python3"),
        shutil.which("python"),
        "/opt/homebrew/bin/python3",
        "/usr/local/bin/python3",
        "/usr/bin/python3",
    ]
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen or not Path(candidate).exists():
            continue
        seen.add(candidate)
        probe = subprocess.run(
            [candidate, "-m", "pip", "--version"], capture_output=True, text=True
        )
        if probe.returncode == 0:
            return candidate
    sys.exit("no interpreter with pip found — install pip (python3 -m ensurepip) and retry")


def export_requirements(target: Path) -> int:
    """Write the pinned runtime closure from uv.lock; return the package count."""
    exported = run(
        [
            "uv",
            "export",
            "--locked",
            "--no-dev",
            "--no-emit-project",
            "--format",
            "requirements-txt",
        ],
        cwd=REPO_ROOT,
    ).stdout
    target.write_text(exported, encoding="utf-8")
    return sum(1 for line in exported.splitlines() if "==" in line)


def download_wheels(
    requirements: Path, wheel_dir: Path, python_versions: list[str], platforms: list[str]
) -> None:
    """Fill the wheelhouse for every requested interpreter and platform."""
    wheel_dir.mkdir(parents=True, exist_ok=True)
    downloader = pip_python()
    for version in python_versions:
        for platform in platforms:
            print(f"  wheels for Python {version} on {platform}")
            run(
                [
                    downloader,
                    "-m",
                    "pip",
                    "download",
                    "--quiet",
                    "--requirement",
                    str(requirements),
                    "--dest",
                    str(wheel_dir),
                    # The requirements file is a full closure, so resolving
                    # dependencies again would only re-add what is already there
                    # — and pip refuses --platform without --no-deps.
                    "--no-deps",
                    "--only-binary",
                    ":all:",
                    "--python-version",
                    version,
                    "--platform",
                    platform,
                ]
            )


def export_tree(staging: Path) -> Path:
    """Unpack the tracked tree of HEAD into the staging directory."""
    tarball = staging / "tree.tar"
    with tarball.open("wb") as handle:
        subprocess.run(
            ["git", "archive", "--format=tar", f"--prefix={ARCHIVE_PREFIX}/", "HEAD"],
            cwd=REPO_ROOT,
            stdout=handle,
            check=True,
        )
    with tarfile.open(tarball) as archive:
        archive.extractall(staging, filter="data")
    tarball.unlink()
    return staging / ARCHIVE_PREFIX


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--python-version",
        action="append",
        dest="python_versions",
        metavar="X.Y",
        help=f"repeatable; default {' '.join(DEFAULT_PYTHON_VERSIONS)}",
    )
    parser.add_argument(
        "--platform",
        action="append",
        dest="platforms",
        help=f"repeatable pip platform tag; default {' '.join(DEFAULT_PLATFORMS)}",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "dist" / f"charles-mcp-{date.today():%Y-%m-%d}.zip",
        help="archive path (default dist/charles-mcp-<date>.zip)",
    )
    args = parser.parse_args()

    python_versions = args.python_versions or list(DEFAULT_PYTHON_VERSIONS)
    platforms = args.platforms or list(DEFAULT_PLATFORMS)

    dirty = run(["git", "status", "--porcelain"], cwd=REPO_ROOT).stdout.strip()
    if dirty:
        print("note: uncommitted changes are NOT in the archive — it is built from HEAD\n")

    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp)
        tree = export_tree(staging)

        requirements = tree / "requirements-lock.txt"
        count = export_requirements(requirements)
        print(f"runtime closure: {count} packages")

        download_wheels(requirements, tree / "wheels", python_versions, platforms)

        args.out.parent.mkdir(parents=True, exist_ok=True)
        if args.out.suffix != ".zip":
            sys.exit(f"--out must end in .zip, got {args.out}")
        made = shutil.make_archive(str(args.out.with_suffix("")), "zip", root_dir=staging)

    size_mb = Path(made).stat().st_size / 1_048_576
    print(f"\n{made}  ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
