"""Integration tests for zlib symbol isolation."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from isolate_elf.model import IsolationConfig, WarningCategory
from isolate_elf.pipeline import isolate_library
from isolate_elf.toolchain import Toolchain

from .conftest import BuiltLibrary, build_cmake_library, download_and_extract, CACHE_DIR
from .verify import verify_autoconf_link, verify_negative_link, verify_runtime_isolation, verify_symbols

TARBALL_URL = "https://rocm-third-party-deps.s3.us-east-2.amazonaws.com/zlib-1.3.2.tar.gz"
TARBALL_HASH = "bb329a0a2cd0274d05519d61c667c062e06990d72e125ee2dfa8de64f0119d16"
SYMBOL_PATTERNS = ["deflate*", "inflate*", "compress*", "uncompress*", "adler32*", "crc32*", "gz*", "zlib*"]
CONSUMER_FUNC = "zlibVersion"
LINK_NAME = "z"


@pytest.fixture(scope="module")
def toolchain() -> Toolchain:
    return Toolchain.discover()


@pytest.fixture(scope="module")
def zlib_built() -> BuiltLibrary:
    install_dir = CACHE_DIR / "built" / "zlib-1.3.2"

    so_path = None
    for libdir in ["lib", "lib64"]:
        candidate = install_dir / libdir / "libz.so"
        if candidate.exists():
            so_path = candidate
            break

    if so_path is None:
        source_dir = download_and_extract(TARBALL_URL, TARBALL_HASH)
        build_cmake_library(source_dir, install_dir)
        for libdir in ["lib", "lib64"]:
            candidate = install_dir / libdir / "libz.so"
            if candidate.exists():
                so_path = candidate
                break

    assert so_path is not None and so_path.exists()
    return BuiltLibrary(
        name="zlib", so_path=so_path.resolve(), install_dir=install_dir,
        symbol_patterns=SYMBOL_PATTERNS, consumer_func=CONSUMER_FUNC, link_name=LINK_NAME,
    )


@pytest.fixture(scope="module")
def zlib_isolated(zlib_built: BuiltLibrary, toolchain: Toolchain):
    output_dir = CACHE_DIR / "isolated" / "zlib-1.3.2"
    output_dir.mkdir(parents=True, exist_ok=True)
    config = IsolationConfig(
        input_so=zlib_built.so_path, prefix="rocm_", output_dir=output_dir,
        output_name="z", allow_categories={WarningCategory.OBJECT_SYMBOL},
    )
    result = isolate_library(config, toolchain)
    return config, result


class TestZlibSymbols:
    def test_all_symbols_prefixed(self, zlib_isolated, toolchain):
        _, result = zlib_isolated
        vr = verify_symbols(result, "rocm_", SYMBOL_PATTERNS, toolchain)
        assert len(vr.prefixed_symbols) > 20
        assert not vr.unprefixed_leaks, f"Leaks: {vr.unprefixed_leaks[:10]}"

    def test_soname_rewritten(self, zlib_isolated, toolchain):
        _, result = zlib_isolated
        proc = subprocess.run(
            [str(toolchain.readelf), "-d", str(result.prefixed_so)],
            capture_output=True, text=True, check=True,
        )
        assert "librocm_sysdeps_z.so.1" in proc.stdout


class TestZlibLinking:
    def test_autoconf_link(self, zlib_isolated, toolchain, tmp_path):
        _, result = zlib_isolated
        assert verify_autoconf_link(result, CONSUMER_FUNC, LINK_NAME, toolchain, tmp_path)

    def test_negative_link(self, zlib_isolated, toolchain, tmp_path):
        _, result = zlib_isolated
        assert verify_negative_link(result, CONSUMER_FUNC, toolchain, tmp_path)


class TestZlibRuntime:
    def test_isolated_only(self, zlib_isolated, toolchain, tmp_path):
        _, result = zlib_isolated
        ok, bindings = verify_runtime_isolation(
            result, CONSUMER_FUNC, LINK_NAME, "rocm_", toolchain, tmp_path,
        )
        assert ok, "\n".join(bindings[-5:])

    def test_cohabitation(self, zlib_built, zlib_isolated, toolchain, tmp_path):
        _, result = zlib_isolated
        ok, bindings = verify_runtime_isolation(
            result, CONSUMER_FUNC, LINK_NAME, "rocm_", toolchain, tmp_path,
            system_so=zlib_built.so_path,
        )
        assert ok, "\n".join(bindings[-5:])
