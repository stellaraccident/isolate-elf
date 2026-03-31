"""Integration tests for expat symbol isolation."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from isolate_elf.model import IsolationConfig, WarningCategory
from isolate_elf.pipeline import isolate_library
from isolate_elf.toolchain import Toolchain

from .conftest import BuiltLibrary, build_cmake_library, download_and_extract, CACHE_DIR
from .verify import verify_autoconf_link, verify_negative_link, verify_runtime_isolation, verify_symbols

TARBALL_URL = "https://rocm-third-party-deps.s3.us-east-2.amazonaws.com/expat-2.7.5.tar.xz"
TARBALL_HASH = "1032dfef4ff17f70464827daa28369b20f6584d108bc36f17ab1676e1edd2f91"
SYMBOL_PATTERNS = ["XML_*"]
CONSUMER_FUNC = "XML_ExpatVersion"
LINK_NAME = "expat"


@pytest.fixture(scope="module")
def toolchain() -> Toolchain:
    return Toolchain.discover()


@pytest.fixture(scope="module")
def expat_built() -> BuiltLibrary:
    install_dir = CACHE_DIR / "built" / "expat-2.7.5"

    so_path = None
    for libdir in ["lib", "lib64"]:
        candidate = install_dir / libdir / "libexpat.so"
        if candidate.exists():
            so_path = candidate
            break

    if so_path is None:
        source_dir = download_and_extract(TARBALL_URL, TARBALL_HASH)
        build_cmake_library(
            source_dir, install_dir,
            cmake_args=[
                "-DEXPAT_BUILD_TESTS=OFF",
                "-DEXPAT_BUILD_TOOLS=OFF",
                "-DEXPAT_BUILD_EXAMPLES=OFF",
                "-DEXPAT_SHARED_LIBS=ON",
            ],
        )
        for libdir in ["lib", "lib64"]:
            candidate = install_dir / libdir / "libexpat.so"
            if candidate.exists():
                so_path = candidate
                break

    assert so_path is not None and so_path.exists()
    return BuiltLibrary(
        name="expat", so_path=so_path.resolve(), install_dir=install_dir,
        symbol_patterns=SYMBOL_PATTERNS, consumer_func=CONSUMER_FUNC, link_name=LINK_NAME,
    )


@pytest.fixture(scope="module")
def expat_isolated(expat_built: BuiltLibrary, toolchain: Toolchain):
    output_dir = CACHE_DIR / "isolated" / "expat-2.7.5"
    output_dir.mkdir(parents=True, exist_ok=True)
    config = IsolationConfig(
        input_so=expat_built.so_path, prefix="rocm_", output_dir=output_dir,
        output_name="expat", allow_categories={WarningCategory.OBJECT_SYMBOL},
    )
    result = isolate_library(config, toolchain)
    return config, result


class TestExpatSymbols:
    def test_all_symbols_prefixed(self, expat_isolated, toolchain):
        _, result = expat_isolated
        vr = verify_symbols(result, "rocm_", SYMBOL_PATTERNS, toolchain)
        assert len(vr.prefixed_symbols) > 20
        assert not vr.unprefixed_leaks, f"Leaks: {vr.unprefixed_leaks[:10]}"

    def test_soname_rewritten(self, expat_isolated, toolchain):
        _, result = expat_isolated
        proc = subprocess.run(
            [str(toolchain.readelf), "-d", str(result.prefixed_so)],
            capture_output=True, text=True, check=True,
        )
        assert "librocm_sysdeps_expat.so.1" in proc.stdout


class TestExpatLinking:
    def test_autoconf_link(self, expat_isolated, toolchain, tmp_path):
        _, result = expat_isolated
        assert verify_autoconf_link(result, CONSUMER_FUNC, LINK_NAME, toolchain, tmp_path)

    def test_negative_link(self, expat_isolated, toolchain, tmp_path):
        _, result = expat_isolated
        assert verify_negative_link(result, CONSUMER_FUNC, toolchain, tmp_path)


class TestExpatRuntime:
    def test_isolated_only(self, expat_isolated, toolchain, tmp_path):
        _, result = expat_isolated
        ok, bindings = verify_runtime_isolation(
            result, CONSUMER_FUNC, LINK_NAME, "rocm_", toolchain, tmp_path,
        )
        assert ok, "\n".join(bindings[-5:])

    def test_cohabitation(self, expat_built, expat_isolated, toolchain, tmp_path):
        _, result = expat_isolated
        ok, bindings = verify_runtime_isolation(
            result, CONSUMER_FUNC, LINK_NAME, "rocm_", toolchain, tmp_path,
            system_so=expat_built.so_path,
        )
        assert ok, "\n".join(bindings[-5:])
