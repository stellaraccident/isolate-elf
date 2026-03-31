"""Shared fixtures for isolate-elf unit tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

TESTLIB_DIR = Path(__file__).parent / "testlib"


@pytest.fixture(scope="session")
def testlib_so(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build tests/testlib/testlib.c into a shared library."""
    out_dir = tmp_path_factory.mktemp("testlib")
    so_path = out_dir / "libtestlib.so.1"

    subprocess.run(
        [
            "cc",
            "-shared",
            "-fPIC",
            "-fvisibility=default",
            "-Wl,-soname,libtestlib.so.1",
            "-o",
            str(so_path),
            str(TESTLIB_DIR / "testlib.c"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert so_path.exists() and so_path.stat().st_size > 0
    return so_path


@pytest.fixture(scope="session")
def testlib_versioned_so(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build tests/testlib/testlib_versioned.c into a versioned shared library."""
    out_dir = tmp_path_factory.mktemp("testlib_versioned")
    so_path = out_dir / "libtestlib_versioned.so.1"
    map_path = TESTLIB_DIR / "testlib_versioned.map"

    subprocess.run(
        [
            "cc",
            "-shared",
            "-fPIC",
            f"-Wl,--version-script={map_path}",
            "-Wl,-soname,libtestlib_versioned.so.1",
            "-o",
            str(so_path),
            str(TESTLIB_DIR / "testlib_versioned.c"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert so_path.exists() and so_path.stat().st_size > 0
    return so_path
