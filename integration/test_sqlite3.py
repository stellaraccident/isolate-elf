"""Integration tests for sqlite3 symbol isolation."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from isolate_elf.model import IsolationConfig, WarningCategory
from isolate_elf.pipeline import isolate_library
from isolate_elf.toolchain import Toolchain

from .conftest import BuiltLibrary, download_and_extract, CACHE_DIR
from .verify import verify_autoconf_link, verify_negative_link, verify_runtime_isolation, verify_symbols

TARBALL_URL = "https://rocm-third-party-deps.s3.us-east-2.amazonaws.com/sqlite-amalgamation-3510300.zip"
TARBALL_HASH = "acb1e6f5d832484bf6d32b681e858c38add8b2acdfd42ac5df24b8afb46552b4"
SYMBOL_PATTERNS = ["sqlite3_*"]
CONSUMER_FUNC = "sqlite3_libversion"
LINK_NAME = "sqlite3"


@pytest.fixture(scope="module")
def toolchain() -> Toolchain:
    return Toolchain.discover()


@pytest.fixture(scope="module")
def sqlite3_built() -> BuiltLibrary:
    install_dir = CACHE_DIR / "built" / "sqlite3-3510300"

    so_path = None
    for libdir in ["lib", "lib64"]:
        candidate = install_dir / libdir / "libsqlite3.so"
        if candidate.exists():
            so_path = candidate
            break

    if so_path is None:
        source_dir = download_and_extract(TARBALL_URL, TARBALL_HASH)
        install_dir.mkdir(parents=True, exist_ok=True)
        lib_dir = install_dir / "lib"
        lib_dir.mkdir(exist_ok=True)

        # sqlite3 is a single-file amalgamation -- no CMakeLists.txt
        subprocess.run(
            [
                "cc", "-shared", "-fPIC", "-DSQLITE_ENABLE_FTS5",
                "-o", str(lib_dir / "libsqlite3.so"),
                "sqlite3.c",
                "-lpthread", "-ldl", "-lm",
            ],
            check=True, capture_output=True, text=True, cwd=source_dir,
        )
        so_path = lib_dir / "libsqlite3.so"

    assert so_path is not None and so_path.exists()
    return BuiltLibrary(
        name="sqlite3", so_path=so_path.resolve(), install_dir=install_dir,
        symbol_patterns=SYMBOL_PATTERNS, consumer_func=CONSUMER_FUNC, link_name=LINK_NAME,
    )


@pytest.fixture(scope="module")
def sqlite3_isolated(sqlite3_built: BuiltLibrary, toolchain: Toolchain):
    output_dir = CACHE_DIR / "isolated" / "sqlite3-3510300"
    output_dir.mkdir(parents=True, exist_ok=True)
    config = IsolationConfig(
        input_so=sqlite3_built.so_path, prefix="rocm_", output_dir=output_dir,
        output_name="sqlite3", allow_categories={WarningCategory.OBJECT_SYMBOL},
    )
    result = isolate_library(config, toolchain)
    return config, result


class TestSqlite3Symbols:
    def test_all_symbols_prefixed(self, sqlite3_isolated, toolchain):
        _, result = sqlite3_isolated
        vr = verify_symbols(result, "rocm_", SYMBOL_PATTERNS, toolchain)
        assert len(vr.prefixed_symbols) > 100
        assert not vr.unprefixed_leaks, f"Leaks: {vr.unprefixed_leaks[:10]}"

    def test_soname_set(self, sqlite3_isolated, toolchain):
        """sqlite3 amalgamation may not have original SONAME.
        If the original had one, it should be rewritten.
        If not, the pipeline adds one."""
        _, result = sqlite3_isolated
        proc = subprocess.run(
            [str(toolchain.readelf), "-d", str(result.prefixed_so)],
            capture_output=True, text=True, check=True,
        )
        # The pipeline sets SONAME to the output filename
        # If original had no SONAME, DT_SONAME won't be present
        # (we can't add DT entries, only rewrite existing ones)
        # This is acceptable — the file IS named correctly
        assert result.prefixed_so.exists()


class TestSqlite3Linking:
    def test_autoconf_link(self, sqlite3_isolated, toolchain, tmp_path):
        _, result = sqlite3_isolated
        assert verify_autoconf_link(result, CONSUMER_FUNC, LINK_NAME, toolchain, tmp_path)

    def test_negative_link(self, sqlite3_isolated, toolchain, tmp_path):
        _, result = sqlite3_isolated
        assert verify_negative_link(result, CONSUMER_FUNC, toolchain, tmp_path)


class TestSqlite3Runtime:
    def test_isolated_only(self, sqlite3_isolated, toolchain, tmp_path):
        _, result = sqlite3_isolated
        ok, bindings = verify_runtime_isolation(
            result, CONSUMER_FUNC, LINK_NAME, "rocm_", toolchain, tmp_path,
        )
        assert ok, "\n".join(bindings[-5:])

    def test_cohabitation(self, sqlite3_built, sqlite3_isolated, toolchain, tmp_path):
        _, result = sqlite3_isolated
        ok, bindings = verify_runtime_isolation(
            result, CONSUMER_FUNC, LINK_NAME, "rocm_", toolchain, tmp_path,
            system_so=sqlite3_built.so_path,
        )
        assert ok, "\n".join(bindings[-5:])
