"""Integration tests for liblzma (xz) symbol isolation."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from isolib.model import IsolationConfig, WarningCategory
from isolib.pipeline import isolate_library
from isolib.toolchain import Toolchain

from .conftest import BuiltLibrary, build_cmake_library, download_and_extract, CACHE_DIR
from .verify import verify_autoconf_link, verify_negative_link, verify_runtime_isolation, verify_symbols

TARBALL_URL = "https://rocm-third-party-deps.s3.us-east-2.amazonaws.com/xz-5.8.1.tar.bz2"
TARBALL_HASH = "5965c692c4c8800cd4b33ce6d0f6ac9ac9d6ab227b17c512b6561bce4f08d47e"
SYMBOL_PATTERNS = ["lzma_*"]
CONSUMER_FUNC = "lzma_version_number"
LINK_NAME = "lzma"


@pytest.fixture(scope="module")
def toolchain() -> Toolchain:
    return Toolchain.discover()


@pytest.fixture(scope="module")
def liblzma_built() -> BuiltLibrary:
    install_dir = CACHE_DIR / "built" / "xz-5.8.1"

    so_path = None
    for libdir in ["lib", "lib64"]:
        candidate = install_dir / libdir / "liblzma.so"
        if candidate.exists():
            so_path = candidate
            break

    if so_path is None:
        source_dir = download_and_extract(TARBALL_URL, TARBALL_HASH)
        build_cmake_library(
            source_dir, install_dir,
            cmake_args=[
                "-DBUILD_TESTING=OFF",
            ],
        )
        for libdir in ["lib", "lib64"]:
            candidate = install_dir / libdir / "liblzma.so"
            if candidate.exists():
                so_path = candidate
                break

    assert so_path is not None and so_path.exists()
    return BuiltLibrary(
        name="liblzma", so_path=so_path.resolve(), install_dir=install_dir,
        symbol_patterns=SYMBOL_PATTERNS, consumer_func=CONSUMER_FUNC, link_name=LINK_NAME,
    )


@pytest.fixture(scope="module")
def liblzma_isolated(liblzma_built: BuiltLibrary, toolchain: Toolchain):
    output_dir = CACHE_DIR / "isolated" / "xz-5.8.1"
    output_dir.mkdir(parents=True, exist_ok=True)
    config = IsolationConfig(
        input_so=liblzma_built.so_path, prefix="rocm_", output_dir=output_dir,
        output_name="lzma", allow_categories={WarningCategory.OBJECT_SYMBOL},
    )
    result = isolate_library(config, toolchain)
    return config, result


class TestLiblzmaSymbols:
    def test_all_symbols_prefixed(self, liblzma_isolated, toolchain):
        _, result = liblzma_isolated
        vr = verify_symbols(result, "rocm_", SYMBOL_PATTERNS, toolchain)
        assert len(vr.prefixed_symbols) > 30
        assert not vr.unprefixed_leaks, f"Leaks: {vr.unprefixed_leaks[:10]}"


class TestLiblzmaLinking:
    def test_autoconf_link(self, liblzma_isolated, toolchain, tmp_path):
        _, result = liblzma_isolated
        assert verify_autoconf_link(result, CONSUMER_FUNC, LINK_NAME, toolchain, tmp_path)

    def test_negative_link(self, liblzma_isolated, toolchain, tmp_path):
        _, result = liblzma_isolated
        assert verify_negative_link(result, CONSUMER_FUNC, toolchain, tmp_path)


class TestLiblzmaRuntime:
    def test_isolated_only(self, liblzma_isolated, toolchain, tmp_path):
        _, result = liblzma_isolated
        ok, bindings = verify_runtime_isolation(
            result, CONSUMER_FUNC, LINK_NAME, "rocm_", toolchain, tmp_path,
        )
        assert ok, "\n".join(bindings[-5:])
