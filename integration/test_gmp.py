"""Integration tests for GMP symbol isolation.

NOTE: GMP 6.3.0 fails to build with GCC 15+ due to K&R-style function
declarations in configure tests. Skip on affected systems until GMP
updates their configure scripts.
"""

from __future__ import annotations

import subprocess  # noqa: F401 - used in CalledProcessError catch
from pathlib import Path

import pytest

from isolib.model import IsolationConfig, WarningCategory
from isolib.pipeline import isolate_library
from isolib.toolchain import Toolchain

from .conftest import BuiltLibrary, build_autotools_library, download_and_extract, CACHE_DIR
from .verify import verify_autoconf_link, verify_negative_link, verify_runtime_isolation, verify_symbols

TARBALL_URL = "https://rocm-third-party-deps.s3.us-east-2.amazonaws.com/gmp-6.3.0.tar.xz"
TARBALL_HASH = "e85a0dab5195889948a3462189f0e0598d331d3457612e2d3350799dba2e244316d256f8161df5219538eb003e4b5343f989aaa00f96321559063ed8c8f29fd2"
TARBALL_HASH_ALGO = "sha512"
SYMBOL_PATTERNS = ["__gmp*", "__mpz_*", "__mpq_*", "__mpf_*"]
CONSUMER_FUNC = "__gmpz_init"
LINK_NAME = "gmp"


@pytest.fixture(scope="module")
def toolchain() -> Toolchain:
    return Toolchain.discover()


@pytest.fixture(scope="module")
def gmp_built() -> BuiltLibrary:
    install_dir = CACHE_DIR / "built" / "gmp-6.3.0"

    so_path = None
    for libdir in ["lib", "lib64"]:
        candidate = install_dir / libdir / "libgmp.so"
        if candidate.exists():
            so_path = candidate
            break

    if so_path is None:
        source_dir = download_and_extract(TARBALL_URL, TARBALL_HASH, TARBALL_HASH_ALGO)
        try:
            build_autotools_library(
                source_dir, install_dir,
                configure_args=["--enable-shared", "--disable-static"],
            )
        except subprocess.CalledProcessError as e:
            pytest.skip(f"GMP build failed (likely GCC 15+ compat issue): {e}")
        for libdir in ["lib", "lib64"]:
            candidate = install_dir / libdir / "libgmp.so"
            if candidate.exists():
                so_path = candidate
                break

    if so_path is None or not so_path.exists():
        pytest.skip("GMP .so not found after build")
    return BuiltLibrary(
        name="gmp", so_path=so_path.resolve(), install_dir=install_dir,
        symbol_patterns=SYMBOL_PATTERNS, consumer_func=CONSUMER_FUNC, link_name=LINK_NAME,
    )


@pytest.fixture(scope="module")
def gmp_isolated(gmp_built: BuiltLibrary, toolchain: Toolchain):
    output_dir = CACHE_DIR / "isolated" / "gmp-6.3.0"
    output_dir.mkdir(parents=True, exist_ok=True)
    config = IsolationConfig(
        input_so=gmp_built.so_path, prefix="rocm_", output_dir=output_dir,
        output_name="gmp", allow_categories={WarningCategory.OBJECT_SYMBOL},
    )
    result = isolate_library(config, toolchain)
    return config, result


class TestGmpSymbols:
    def test_all_symbols_prefixed(self, gmp_isolated, toolchain):
        _, result = gmp_isolated
        vr = verify_symbols(result, "rocm_", SYMBOL_PATTERNS, toolchain)
        assert len(vr.prefixed_symbols) > 50
        assert not vr.unprefixed_leaks, f"Leaks: {vr.unprefixed_leaks[:10]}"

    def test_soname_rewritten(self, gmp_isolated, toolchain):
        _, result = gmp_isolated
        proc = subprocess.run(
            [str(toolchain.readelf), "-d", str(result.prefixed_so)],
            capture_output=True, text=True, check=True,
        )
        assert "librocm_sysdeps_gmp.so.1" in proc.stdout


class TestGmpLinking:
    def test_autoconf_link(self, gmp_isolated, toolchain, tmp_path):
        _, result = gmp_isolated
        assert verify_autoconf_link(result, CONSUMER_FUNC, LINK_NAME, toolchain, tmp_path)

    def test_negative_link(self, gmp_isolated, toolchain, tmp_path):
        _, result = gmp_isolated
        assert verify_negative_link(result, CONSUMER_FUNC, toolchain, tmp_path)


class TestGmpRuntime:
    def test_isolated_only(self, gmp_isolated, toolchain, tmp_path):
        _, result = gmp_isolated
        ok, bindings = verify_runtime_isolation(
            result, CONSUMER_FUNC, LINK_NAME, "rocm_", toolchain, tmp_path,
        )
        assert ok, "\n".join(bindings[-5:])

    def test_cohabitation(self, gmp_built, gmp_isolated, toolchain, tmp_path):
        _, result = gmp_isolated
        ok, bindings = verify_runtime_isolation(
            result, CONSUMER_FUNC, LINK_NAME, "rocm_", toolchain, tmp_path,
            system_so=gmp_built.so_path,
        )
        assert ok, "\n".join(bindings[-5:])
