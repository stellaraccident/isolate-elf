"""Integration test infrastructure: download, build, cache real sysdep libraries."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlretrieve

import pytest

# Cache directory for downloaded sources and built artifacts
CACHE_DIR = Path(os.environ.get(
    "ISOLIB_CACHE_DIR",
    Path(__file__).parent.parent / ".cache" / "isolib",
))


@dataclass
class LibrarySource:
    """Downloaded and extracted source tree for a sysdep library."""

    name: str
    version: str
    source_dir: Path


@dataclass
class BuiltLibrary:
    """A built shared library ready for isolation testing."""

    name: str
    so_path: Path          # The .so file
    install_dir: Path      # Prefix where headers/libs are installed
    symbol_patterns: list[str]  # Glob patterns for symbols that must be prefixed
    consumer_func: str     # A function name for autoconf-style link tests
    link_name: str         # -l name (e.g. "zstd")


def download_and_extract(
    url: str,
    expected_hash: str,
    hash_algo: str = "sha256",
) -> Path:
    """Download a tarball and extract it, caching the result.

    Args:
        url: URL to download.
        expected_hash: Expected hash of the downloaded file.
        hash_algo: Hash algorithm (sha256, sha512).

    Returns:
        Path to the extracted source directory.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Derive cache paths from URL
    filename = url.rsplit("/", 1)[-1]
    archive_path = CACHE_DIR / "archives" / filename
    extract_dir = CACHE_DIR / "sources" / filename.split(".tar")[0].split(".zip")[0]

    if extract_dir.exists():
        return extract_dir

    # Download if not cached
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if not archive_path.exists():
        print(f"Downloading {url}...")
        urlretrieve(url, archive_path)

    # Verify hash
    h = hashlib.new(hash_algo)
    h.update(archive_path.read_bytes())
    actual = h.hexdigest()
    if actual.lower() != expected_hash.lower():
        archive_path.unlink()
        raise RuntimeError(
            f"Hash mismatch for {filename}: expected {expected_hash}, got {actual}"
        )

    # Extract
    extract_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp_extract = extract_dir.with_suffix(".extracting")
    if tmp_extract.exists():
        shutil.rmtree(tmp_extract)
    tmp_extract.mkdir()

    if filename.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(tmp_extract)
    elif ".tar" in filename:
        with tarfile.open(archive_path) as tf:
            tf.extractall(tmp_extract, filter="data")

    # Most archives have a single top-level directory
    children = list(tmp_extract.iterdir())
    if len(children) == 1 and children[0].is_dir():
        children[0].rename(extract_dir)
        tmp_extract.rmdir()
    else:
        tmp_extract.rename(extract_dir)

    return extract_dir


def build_cmake_library(
    source_dir: Path,
    install_dir: Path,
    cmake_args: list[str] | None = None,
    targets: list[str] | None = None,
) -> None:
    """Build a CMake project and install it."""
    build_dir = source_dir / "_build"
    build_dir.mkdir(exist_ok=True)

    args = [
        "cmake",
        f"-S{source_dir}",
        f"-B{build_dir}",
        "-GNinja",
        f"-DCMAKE_INSTALL_PREFIX={install_dir}",
        "-DCMAKE_BUILD_TYPE=Release",
        "-DBUILD_SHARED_LIBS=ON",
        "-DCMAKE_POSITION_INDEPENDENT_CODE=ON",
    ]
    if cmake_args:
        args.extend(cmake_args)

    subprocess.run(args, check=True, capture_output=True, text=True)

    build_cmd = ["ninja", "-C", str(build_dir)]
    if targets:
        build_cmd.extend(targets)
    subprocess.run(build_cmd, check=True, capture_output=True, text=True)

    subprocess.run(
        ["ninja", "-C", str(build_dir), "install"],
        check=True, capture_output=True, text=True,
    )


def build_autotools_library(
    source_dir: Path,
    install_dir: Path,
    configure_args: list[str] | None = None,
    make_args: list[str] | None = None,
    env_extra: dict[str, str] | None = None,
) -> None:
    """Build an autotools project and install it."""
    env = {**os.environ}
    if env_extra:
        env.update(env_extra)

    args = [str(source_dir / "configure"), f"--prefix={install_dir}"]
    if configure_args:
        args.extend(configure_args)

    subprocess.run(args, check=True, capture_output=True, text=True, env=env,
                   cwd=source_dir)
    make_cmd = ["make", "-j", str(os.cpu_count() or 4)]
    if make_args:
        make_cmd.extend(make_args)
    subprocess.run(make_cmd, check=True, capture_output=True, text=True,
                   cwd=source_dir, env=env)
    subprocess.run(["make", "install"], check=True, capture_output=True, text=True,
                   cwd=source_dir, env=env)
