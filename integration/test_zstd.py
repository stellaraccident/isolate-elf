"""Integration tests for zstd symbol isolation."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from isolib.model import IsolationConfig, WarningCategory
from isolib.pipeline import isolate_library
from isolib.toolchain import Toolchain

from .conftest import (
    BuiltLibrary,
    build_cmake_library,
    download_and_extract,
    CACHE_DIR,
)
from .verify import (
    verify_autoconf_link,
    verify_negative_link,
    verify_runtime_isolation,
    verify_symbols,
)

TARBALL_URL = "https://rocm-third-party-deps.s3.us-east-2.amazonaws.com/zstd-1.5.7.tar.gz"
TARBALL_HASH = "eb33e51f49a15e023950cd7825ca74a4a2b43db8354825ac24fc1b7ee09e6fa3"
SYMBOL_PATTERNS = ["ZSTD_*"]
CONSUMER_FUNC = "ZSTD_versionNumber"
LINK_NAME = "zstd"


@pytest.fixture(scope="module")
def toolchain() -> Toolchain:
    return Toolchain.discover()


@pytest.fixture(scope="module")
def zstd_built() -> BuiltLibrary:
    """Download, build, and cache zstd."""
    install_dir = CACHE_DIR / "built" / "zstd-1.5.7"

    # Find .so in lib/ or lib64/
    so_path = None
    for libdir in ["lib", "lib64"]:
        candidate = install_dir / libdir / "libzstd.so"
        if candidate.exists():
            so_path = candidate
            break

    if so_path is None:
        source_dir = download_and_extract(TARBALL_URL, TARBALL_HASH)
        cmake_source = source_dir / "build" / "cmake"
        build_cmake_library(
            cmake_source, install_dir,
            cmake_args=[
                "-DZSTD_BUILD_PROGRAMS=OFF",
                "-DZSTD_BUILD_TESTS=OFF",
                "-DZSTD_BUILD_STATIC=OFF",
            ],
        )
        for libdir in ["lib", "lib64"]:
            candidate = install_dir / libdir / "libzstd.so"
            if candidate.exists():
                so_path = candidate
                break

    assert so_path is not None and so_path.exists(), (
        f"zstd .so not found under {install_dir}"
    )
    real_so = so_path.resolve()

    return BuiltLibrary(
        name="zstd",
        so_path=real_so,
        install_dir=install_dir,
        symbol_patterns=SYMBOL_PATTERNS,
        consumer_func=CONSUMER_FUNC,
        link_name=LINK_NAME,
    )


@pytest.fixture(scope="module")
def zstd_isolated(zstd_built: BuiltLibrary, toolchain: Toolchain) -> tuple[IsolationConfig, "IsolationResult"]:
    """Run isolib on the built zstd library."""
    from isolib.pipeline import IsolationResult

    output_dir = CACHE_DIR / "isolated" / "zstd-1.5.7"
    output_dir.mkdir(parents=True, exist_ok=True)

    config = IsolationConfig(
        input_so=zstd_built.so_path,
        prefix="rocm_",
        output_dir=output_dir,
        output_name="zstd",
        allow_categories={WarningCategory.OBJECT_SYMBOL},
    )
    result = isolate_library(config, toolchain)
    return config, result


class TestZstdSymbols:
    """Verify symbol renaming on real zstd."""

    def test_all_zstd_symbols_prefixed(
        self, zstd_isolated: tuple, toolchain: Toolchain,
    ) -> None:
        config, result = zstd_isolated
        vr = verify_symbols(result, "rocm_", SYMBOL_PATTERNS, toolchain)

        assert len(vr.prefixed_symbols) > 50, (
            f"Expected 50+ prefixed symbols, got {len(vr.prefixed_symbols)}"
        )
        assert not vr.unprefixed_leaks, (
            f"Unprefixed symbol leaks: {vr.unprefixed_leaks[:10]}"
        )

    def test_no_glibc_symbols_prefixed(
        self, zstd_isolated: tuple, toolchain: Toolchain,
    ) -> None:
        config, result = zstd_isolated
        vr = verify_symbols(result, "rocm_", SYMBOL_PATTERNS, toolchain)

        # Symbols like malloc, free, etc. should NOT be in prefixed list
        glibc_names = {"rocm_malloc", "rocm_free", "rocm_printf", "rocm_memcpy"}
        leaked_glibc = glibc_names & set(vr.prefixed_symbols)
        assert not leaked_glibc, f"glibc symbols incorrectly prefixed: {leaked_glibc}"

    def test_artifacts_exist(self, zstd_isolated: tuple) -> None:
        _, result = zstd_isolated
        assert result.prefixed_so.exists()
        assert result.prefixed_so.stat().st_size > 0
        assert result.stubs_archive.exists()
        assert result.linker_script.exists()
        assert result.redirect_header.exists()

    def test_soname_rewritten(self, zstd_isolated: tuple, toolchain: Toolchain) -> None:
        _, result = zstd_isolated
        proc = subprocess.run(
            [str(toolchain.readelf), "-d", str(result.prefixed_so)],
            capture_output=True, text=True, check=True,
        )
        assert "librocm_sysdeps_zstd.so.1" in proc.stdout, (
            f"SONAME not rewritten. Dynamic section:\n{proc.stdout}"
        )


class TestZstdLinking:
    """Verify link-time behavior."""

    def test_autoconf_link(
        self, zstd_isolated: tuple, toolchain: Toolchain, tmp_path: Path,
    ) -> None:
        _, result = zstd_isolated
        ok = verify_autoconf_link(result, CONSUMER_FUNC, LINK_NAME, toolchain, tmp_path)
        assert ok, "Autoconf-style link test failed"

    def test_negative_link(
        self, zstd_isolated: tuple, toolchain: Toolchain, tmp_path: Path,
    ) -> None:
        _, result = zstd_isolated
        ok = verify_negative_link(result, CONSUMER_FUNC, toolchain, tmp_path)
        assert ok, "Negative link test should fail but succeeded"


class TestZstdRuntime:
    """Verify runtime behavior via LD_DEBUG."""

    def test_isolated_only(
        self, zstd_isolated: tuple, toolchain: Toolchain, tmp_path: Path,
    ) -> None:
        _, result = zstd_isolated
        ok, bindings = verify_runtime_isolation(
            result, CONSUMER_FUNC, LINK_NAME, "rocm_", toolchain, tmp_path,
        )
        assert ok, f"Runtime isolation check failed:\n" + "\n".join(bindings[-5:])

    def test_cohabitation_with_system(
        self, zstd_built: BuiltLibrary, zstd_isolated: tuple,
        toolchain: Toolchain, tmp_path: Path,
    ) -> None:
        """Load both system (pristine) and isolated zstd in same process."""
        _, result = zstd_isolated

        # Build a consumer that calls through the isolated path
        ok, bindings = verify_runtime_isolation(
            result, CONSUMER_FUNC, LINK_NAME, "rocm_", toolchain, tmp_path,
            system_so=zstd_built.so_path,
        )
        assert ok, (
            f"Cohabitation test failed — symbol leaked across boundary:\n"
            + "\n".join(bindings[-5:])
        )
