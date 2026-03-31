"""Integration tests for bzip2 symbol isolation."""

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

TARBALL_URL = "https://rocm-third-party-deps.s3.us-east-2.amazonaws.com/bzip2-1.0.8.tar.gz"
TARBALL_HASH = "083f5e675d73f3233c7930ebe20425a533feedeaaa9d8cc86831312a6581cefbe6ed0d08d2fa89be81082f2a5abdabca8b3c080bf97218a1bd59dc118a30b9f3"
TARBALL_HASH_ALGO = "sha512"
SYMBOL_PATTERNS = ["BZ2_*"]
CONSUMER_FUNC = "BZ2_bzlibVersion"
LINK_NAME = "bz2"


@pytest.fixture(scope="module")
def toolchain() -> Toolchain:
    return Toolchain.discover()


@pytest.fixture(scope="module")
def bzip2_built() -> BuiltLibrary:
    install_dir = CACHE_DIR / "built" / "bzip2-1.0.8"

    so_path = None
    for libdir in ["lib", "lib64"]:
        candidate = install_dir / libdir / "libbz2.so"
        if candidate.exists():
            so_path = candidate
            break

    if so_path is None:
        source_dir = download_and_extract(TARBALL_URL, TARBALL_HASH, TARBALL_HASH_ALGO)
        # bzip2 uses plain Makefile, build shared lib manually
        install_dir.mkdir(parents=True, exist_ok=True)
        lib_dir = install_dir / "lib"
        lib_dir.mkdir(exist_ok=True)

        subprocess.run(
            ["make", "-f", "Makefile-libbz2_so", "-j", str(os.cpu_count() or 4)],
            check=True, capture_output=True, text=True, cwd=source_dir,
        )
        # Copy the .so
        for f in source_dir.glob("libbz2.so*"):
            import shutil
            shutil.copy2(f, lib_dir / f.name)

        so_path = lib_dir / "libbz2.so"
        if not so_path.exists():
            # Create symlink: libbz2.so -> libbz2.so.1.0.8
            real = list(lib_dir.glob("libbz2.so.1.0*"))
            if real:
                so_path.symlink_to(real[0].name)

    assert so_path is not None and so_path.exists()
    return BuiltLibrary(
        name="bzip2", so_path=so_path.resolve(), install_dir=install_dir,
        symbol_patterns=SYMBOL_PATTERNS, consumer_func=CONSUMER_FUNC, link_name=LINK_NAME,
    )


@pytest.fixture(scope="module")
def bzip2_isolated(bzip2_built: BuiltLibrary, toolchain: Toolchain):
    output_dir = CACHE_DIR / "isolated" / "bzip2-1.0.8"
    output_dir.mkdir(parents=True, exist_ok=True)
    config = IsolationConfig(
        input_so=bzip2_built.so_path, prefix="rocm_", output_dir=output_dir,
        output_name="bz2", allow_categories={WarningCategory.OBJECT_SYMBOL},
    )
    result = isolate_library(config, toolchain)
    return config, result


class TestBzip2Symbols:
    def test_all_symbols_prefixed(self, bzip2_isolated, toolchain):
        _, result = bzip2_isolated
        vr = verify_symbols(result, "rocm_", SYMBOL_PATTERNS, toolchain)
        assert len(vr.prefixed_symbols) > 10
        assert not vr.unprefixed_leaks, f"Leaks: {vr.unprefixed_leaks[:10]}"


class TestBzip2Linking:
    def test_autoconf_link(self, bzip2_isolated, toolchain, tmp_path):
        _, result = bzip2_isolated
        assert verify_autoconf_link(result, CONSUMER_FUNC, LINK_NAME, toolchain, tmp_path)

    def test_negative_link(self, bzip2_isolated, toolchain, tmp_path):
        _, result = bzip2_isolated
        assert verify_negative_link(result, CONSUMER_FUNC, toolchain, tmp_path)


class TestBzip2Runtime:
    def test_isolated_only(self, bzip2_isolated, toolchain, tmp_path):
        _, result = bzip2_isolated
        ok, bindings = verify_runtime_isolation(
            result, CONSUMER_FUNC, LINK_NAME, "rocm_", toolchain, tmp_path,
        )
        assert ok, "\n".join(bindings[-5:])
