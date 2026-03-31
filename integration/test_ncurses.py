"""Integration tests for ncurses symbol isolation."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from isolate_elf.model import IsolationConfig, WarningCategory
from isolate_elf.pipeline import isolate_library
from isolate_elf.toolchain import Toolchain

from .conftest import BuiltLibrary, build_autotools_library, download_and_extract, CACHE_DIR
from .verify import verify_autoconf_link, verify_negative_link, verify_runtime_isolation, verify_symbols

TARBALL_URL = "https://rocm-third-party-deps.s3.us-east-2.amazonaws.com/ncurses-6.6.tar.gz"
TARBALL_HASH = "02647baae53abc844fbadee5b0a2187ad073125c4e8950df6d1c4feb781cb74ba64fb838cedfee2c246c39932187f6775b1df124f18b99a4233f0d98c72191de"
TARBALL_HASH_ALGO = "sha512"
SYMBOL_PATTERNS = ["_nc_*", "waddch", "wmove", "newwin", "delwin", "initscr", "endwin", "curses_version"]
CONSUMER_FUNC = "curses_version"
LINK_NAME = "ncursesw"


@pytest.fixture(scope="module")
def toolchain() -> Toolchain:
    return Toolchain.discover()


@pytest.fixture(scope="module")
def ncurses_built() -> BuiltLibrary:
    install_dir = CACHE_DIR / "built" / "ncurses-6.6"

    so_path = None
    for libdir in ["lib", "lib64"]:
        candidate = install_dir / libdir / "libncursesw.so"
        if candidate.exists():
            so_path = candidate
            break

    if so_path is None:
        source_dir = download_and_extract(TARBALL_URL, TARBALL_HASH, TARBALL_HASH_ALGO)
        build_autotools_library(
            source_dir, install_dir,
            configure_args=[
                "--with-shared",
                "--without-debug",
                "--without-ada",
                "--enable-widec",
            ],
        )
        for libdir in ["lib", "lib64"]:
            candidate = install_dir / libdir / "libncursesw.so"
            if candidate.exists():
                so_path = candidate
                break

    assert so_path is not None and so_path.exists()
    return BuiltLibrary(
        name="ncurses", so_path=so_path.resolve(), install_dir=install_dir,
        symbol_patterns=SYMBOL_PATTERNS, consumer_func=CONSUMER_FUNC, link_name=LINK_NAME,
    )


@pytest.fixture(scope="module")
def ncurses_isolated(ncurses_built: BuiltLibrary, toolchain: Toolchain):
    output_dir = CACHE_DIR / "isolated" / "ncurses-6.6"
    output_dir.mkdir(parents=True, exist_ok=True)
    config = IsolationConfig(
        input_so=ncurses_built.so_path, prefix="rocm_", output_dir=output_dir,
        output_name="ncursesw", allow_categories={WarningCategory.OBJECT_SYMBOL},
    )
    result = isolate_library(config, toolchain)
    return config, result


class TestNcursesSymbols:
    def test_all_symbols_prefixed(self, ncurses_isolated, toolchain):
        _, result = ncurses_isolated
        vr = verify_symbols(result, "rocm_", SYMBOL_PATTERNS, toolchain)
        assert len(vr.prefixed_symbols) > 20
        assert not vr.unprefixed_leaks, f"Leaks: {vr.unprefixed_leaks[:10]}"

    def test_soname_rewritten(self, ncurses_isolated, toolchain):
        _, result = ncurses_isolated
        proc = subprocess.run(
            [str(toolchain.readelf), "-d", str(result.prefixed_so)],
            capture_output=True, text=True, check=True,
        )
        assert "librocm_sysdeps_ncursesw.so.1" in proc.stdout


class TestNcursesLinking:
    def test_autoconf_link(self, ncurses_isolated, toolchain, tmp_path):
        _, result = ncurses_isolated
        assert verify_autoconf_link(result, CONSUMER_FUNC, LINK_NAME, toolchain, tmp_path)

    def test_negative_link(self, ncurses_isolated, toolchain, tmp_path):
        _, result = ncurses_isolated
        assert verify_negative_link(result, CONSUMER_FUNC, toolchain, tmp_path)


class TestNcursesRuntime:
    def test_isolated_only(self, ncurses_isolated, toolchain, tmp_path):
        _, result = ncurses_isolated
        ok, bindings = verify_runtime_isolation(
            result, CONSUMER_FUNC, LINK_NAME, "rocm_", toolchain, tmp_path,
        )
        assert ok, "\n".join(bindings[-5:])

    def test_cohabitation(self, ncurses_built, ncurses_isolated, toolchain, tmp_path):
        _, result = ncurses_isolated
        ok, bindings = verify_runtime_isolation(
            result, CONSUMER_FUNC, LINK_NAME, "rocm_", toolchain, tmp_path,
            system_so=ncurses_built.so_path,
        )
        assert ok, "\n".join(bindings[-5:])
