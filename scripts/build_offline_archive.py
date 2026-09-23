"""Build the archive the team installs from: the tracked tree plus a wheelhouse.

The team gets this fork as a file, not from a registry, and the machines that
run it should not have to reach PyPI at all — every download is a request some
proxy logs, and ``pip install charles-mcp`` would silently fetch the *upstream*
package, which has no mock tools. So the archive carries its own dependencies:

- ``requirements-lock.txt`` — the runtime closure exported from ``uv.lock``,
  pinned and hashed, so the archive installs exactly what this fork was tested
  against;
- ``wheels/`` — those dependencies as wheels, one set per Python version and
  platform asked for (``pip`` picks the compatible file, so several sets share
  the directory), plus this fork's own wheel.

Install side (no uv, no network): ``./install.sh`` in the unpacked archive, or
by hand:

    python3 -m venv .venv
    .venv/bin/pip install --no-index --find-links wheels -r requirements-lock.txt
    .venv/bin/pip install --no-index --no-deps wheels/charles_mcp-*.whl

Usage:

    uv run python scripts/build_offline_archive.py
    uv run python scripts/build_offline_archive.py --python-version 3.12 \
        --platform macosx_11_0_arm64 --out ~/Downloads/charles-mcp.zip
"""

from __future__ import annotations

import argparse
import re
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
# Apple Silicon only: the team has no Intel Macs, and since cryptography 50
# there are no Intel macOS wheels for it at all, so an x86_64 set cannot be
# built without pinning a cryptography release that has open advisories.
DEFAULT_PLATFORMS = ("macosx_11_0_arm64",)

REPO_ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_PREFIX = "charles-mcp"


def run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command, failing loudly with its own output."""
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(
            f"{' '.join(command[:3])}… failed:\n{result.stderr.strip() or result.stdout.strip()}"
        )
    return result


def zip_destination(value: str) -> Path:
    """Validate --out while parsing, before minutes of downloads are spent."""
    path = Path(value).expanduser()
    if path.suffix != ".zip":
        raise argparse.ArgumentTypeError(f"must end in .zip, got {path.name}")
    return path


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
    names = {
        match.group(1).lower()
        for match in (re.match(r"([A-Za-z0-9._-]+)==", line) for line in exported.splitlines())
        if match
    }
    return len(names)


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


def build_project_wheel(source: Path, wheel_dir: Path) -> str:
    """Build this fork's own wheel, so the archive installs the package too.

    Without it the wheelhouse holds only dependencies and the server has to be
    started from the source tree, which leaves no version to check and no
    ``charles-mcp`` command. The wheel is pure Python (``py3-none-any``), so one
    file serves every interpreter and architecture in the archive.
    """
    # Built from the staged copy, not the working tree: the wheel then matches
    # what the archive ships, and setuptools leaves its build/ and egg-info in
    # the temporary directory instead of the repository.
    run(["uv", "build", "--wheel", "--out-dir", str(wheel_dir), str(source)], cwd=REPO_ROOT)
    built = sorted(wheel_dir.glob("charles_mcp-*.whl"))
    if not built:
        sys.exit("uv build produced no wheel for charles-mcp")
    return built[-1].name


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
        if hasattr(tarfile, "data_filter"):
            archive.extractall(staging, filter="data")
        else:
            # Older 3.10/3.11 patch levels have no PEP 706 filters. The tarball
            # is the output of our own `git archive`, so there is nothing to
            # sanitise it against.
            archive.extractall(staging)
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
        type=zip_destination,
        default=REPO_ROOT / "dist" / f"charles-mcp-{date.today():%Y-%m-%d}.zip",
        help="archive path, must end in .zip (default dist/charles-mcp-<date>.zip)",
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

        wheel_dir = tree / "wheels"
        download_wheels(requirements, wheel_dir, python_versions, platforms)
        print(f"project wheel: {build_project_wheel(tree, wheel_dir)}")

        args.out.parent.mkdir(parents=True, exist_ok=True)
        made = shutil.make_archive(str(args.out.with_suffix("")), "zip", root_dir=staging)

    size_mb = Path(made).stat().st_size / 1_048_576
    print(f"\n{made}  ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
