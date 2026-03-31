"""Integration tests for hwloc symbol isolation."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from isolate_elf.model import IsolationConfig, WarningCategory
from isolate_elf.pipeline import isolate_library
from isolate_elf.toolchain import Toolchain

from .conftest import BuiltLibrary, build_autotools_library, download_and_extract, CACHE_DIR
from .verify import verify_autoconf_link, verify_negative_link, verify_runtime_isolation, verify_symbols

TARBALL_URL = "https://rocm-third-party-deps.s3.us-east-2.amazonaws.com/hwloc-1.11.13.tar.bz2"
TARBALL_HASH = "a4494b7765f517c0990d1c7f09d98cb87755bb6b841e4e2cbfebca1b14bac9c8"
SYMBOL_PATTERNS = ["hwloc_*"]
CONSUMER_FUNC = "hwloc_topology_init"
LINK_NAME = "hwloc"


@pytest.fixture(scope="module")
def toolchain() -> Toolchain:
    return Toolchain.discover()


@pytest.fixture(scope="module")
def hwloc_built() -> BuiltLibrary:
    install_dir = CACHE_DIR / "built" / "hwloc-1.11.13"

    so_path = None
    for libdir in ["lib", "lib64"]:
        candidate = install_dir / libdir / "libhwloc.so"
        if candidate.exists():
            so_path = candidate
            break

    if so_path is None:
        source_dir = download_and_extract(TARBALL_URL, TARBALL_HASH)
        try:
            build_autotools_library(
                source_dir, install_dir,
                configure_args=[
                    "--enable-shared", "--disable-static",
                    "--disable-cairo", "--disable-opencl",
                    "--disable-cuda", "--disable-nvml",
                    "--disable-gl", "--disable-libudev",
                ],
            )
        except subprocess.CalledProcessError as e:
            pytest.skip(f"hwloc build failed: {e.stderr[-300:] if e.stderr else str(e)}")
        for libdir in ["lib", "lib64"]:
            candidate = install_dir / libdir / "libhwloc.so"
            if candidate.exists():
                so_path = candidate
                break

    if so_path is None or not so_path.exists():
        pytest.skip("hwloc .so not found after build")
    return BuiltLibrary(
        name="hwloc", so_path=so_path.resolve(), install_dir=install_dir,
        symbol_patterns=SYMBOL_PATTERNS, consumer_func=CONSUMER_FUNC, link_name=LINK_NAME,
    )


@pytest.fixture(scope="module")
def hwloc_isolated(hwloc_built: BuiltLibrary, toolchain: Toolchain):
    output_dir = CACHE_DIR / "isolated" / "hwloc-1.11.13"
    output_dir.mkdir(parents=True, exist_ok=True)
    config = IsolationConfig(
        input_so=hwloc_built.so_path, prefix="rocm_", output_dir=output_dir,
        output_name="hwloc",
        allow_categories={WarningCategory.OBJECT_SYMBOL},
    )
    result = isolate_library(config, toolchain)
    return config, result


class TestHwlocSymbols:
    def test_all_symbols_prefixed(self, hwloc_isolated, toolchain):
        _, result = hwloc_isolated
        vr = verify_symbols(result, "rocm_", SYMBOL_PATTERNS, toolchain)
        assert len(vr.prefixed_symbols) > 100
        assert not vr.unprefixed_leaks, f"Leaks: {vr.unprefixed_leaks[:10]}"


class TestHwlocLinking:
    def test_autoconf_link(self, hwloc_isolated, toolchain, tmp_path):
        _, result = hwloc_isolated
        assert verify_autoconf_link(result, CONSUMER_FUNC, LINK_NAME, toolchain, tmp_path)

    def test_negative_link(self, hwloc_isolated, toolchain, tmp_path):
        _, result = hwloc_isolated
        assert verify_negative_link(result, CONSUMER_FUNC, toolchain, tmp_path)


class TestHwlocRuntime:
    def test_isolated_only(self, hwloc_isolated, toolchain, tmp_path):
        _, result = hwloc_isolated
        ok, bindings = verify_runtime_isolation(
            result, CONSUMER_FUNC, LINK_NAME, "rocm_", toolchain, tmp_path,
        )
        assert ok, "\n".join(bindings[-5:])
