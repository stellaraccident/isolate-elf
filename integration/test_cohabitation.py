"""Cross-library cohabitation test.

Loads multiple isolated libraries AND their system counterparts in the
same process to prove there's zero symbol cross-contamination. This is
the ultimate proof of isolation — the scenario that causes crashes in
production when ROCm's bundled libs collide with system copies.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from isolib.model import IsolationConfig, IsolationResult, WarningCategory
from isolib.pipeline import isolate_library
from isolib.toolchain import Toolchain

from .conftest import BuiltLibrary, CACHE_DIR


@pytest.fixture(scope="module")
def toolchain() -> Toolchain:
    return Toolchain.discover()


def _get_built_and_isolated(name: str) -> tuple[Path, Path] | None:
    """Find pre-built and pre-isolated artifacts from other test runs.

    Returns (system_so, isolated_dir) or None if not available.
    """
    # Search common cache locations
    for version_dir in sorted(CACHE_DIR.glob(f"built/*{name}*")):
        for libdir in ["lib", "lib64"]:
            for so in (version_dir / libdir).glob(f"lib{name}*.so*"):
                if so.is_file() and not so.is_symlink():
                    # Find corresponding isolated dir
                    for iso_dir in CACHE_DIR.glob(f"isolated/*{name}*"):
                        if any(iso_dir.glob("lib*.so")):
                            return so, iso_dir
                elif so.is_symlink():
                    real = so.resolve()
                    if real.exists():
                        for iso_dir in CACHE_DIR.glob(f"isolated/*{name}*"):
                            if any(iso_dir.glob("lib*.so")):
                                return real, iso_dir
    return None


class TestMultiLibCohabitation:
    """Load system + isolated versions of multiple libraries simultaneously."""

    def test_zstd_and_zlib_cohabitation(self, toolchain: Toolchain, tmp_path: Path) -> None:
        """Load both zstd and zlib (system + isolated) in one process."""
        zstd = _get_built_and_isolated("zstd")
        zlib = _get_built_and_isolated("z")
        if not zstd or not zlib:
            pytest.skip("Need pre-built zstd and zlib from other tests")

        zstd_sys, zstd_iso = zstd
        zlib_sys, zlib_iso = zlib

        # Build a C program that calls both isolated libs through their
        # linker scripts, while also dlopening the system copies
        test_c = tmp_path / "test_cohab.c"
        test_c.write_text("""\
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>

/* These resolve through the linker script stubs to rocm_* */
extern unsigned ZSTD_versionNumber(void);
extern const char *zlibVersion(void);

int main(void) {
    /* Call isolated versions through stubs */
    unsigned zstd_ver = ZSTD_versionNumber();
    const char *zlib_ver = zlibVersion();
    if (zstd_ver == 0 || zlib_ver == NULL) {
        fprintf(stderr, "isolated call failed\\n");
        return 1;
    }

    /* Now dlopen system copies into the same process */
    void *sys_zstd = dlopen("%s", RTLD_NOW | RTLD_GLOBAL);
    void *sys_zlib = dlopen("%s", RTLD_NOW | RTLD_GLOBAL);

    if (sys_zstd) {
        unsigned (*sys_ver)(void) = dlsym(sys_zstd, "ZSTD_versionNumber");
        if (sys_ver) {
            unsigned sv = sys_ver();
            /* Both should work — different symbol namespaces */
            printf("system zstd: %%u, isolated zstd: %%u\\n", sv, zstd_ver);
        }
        dlclose(sys_zstd);
    }

    if (sys_zlib) {
        const char *(*sys_ver)(void) = dlsym(sys_zlib, "zlibVersion");
        if (sys_ver) {
            const char *sv = sys_ver();
            printf("system zlib: %%s, isolated zlib: %%s\\n", sv, zlib_ver);
        }
        dlclose(sys_zlib);
    }

    printf("cohabitation OK\\n");
    return 0;
}
""" % (str(zstd_sys), str(zlib_sys)))

        test_bin = tmp_path / "test_cohab"
        proc = subprocess.run(
            [
                str(toolchain.cc),
                "-o", str(test_bin),
                str(test_c),
                f"-L{zstd_iso}", "-lzstd",
                f"-L{zlib_iso}", "-lz",
                f"-Wl,-rpath,{zstd_iso}",
                f"-Wl,-rpath,{zlib_iso}",
                "-ldl",
            ],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, f"Build failed:\n{proc.stderr}"

        # Run with LD_DEBUG to verify bindings
        env = {
            **os.environ,
            "LD_DEBUG": "bindings",
            "LD_LIBRARY_PATH": f"{zstd_iso}:{zlib_iso}",
        }
        run = subprocess.run(
            [str(test_bin)], capture_output=True, text=True, env=env,
        )
        assert run.returncode == 0, f"Runtime failed:\n{run.stdout}\n{run.stderr[-500:]}"
        assert "cohabitation OK" in run.stdout

        # Verify no cross-contamination in LD_DEBUG output:
        # the isolated .so should never bind unprefixed ZSTD_*/zlib* symbols
        for line in run.stderr.splitlines():
            if "binding" not in line:
                continue
            # Check: nothing should bind TO an isolated .so with an unprefixed name
            for iso_name in ["librocm_sysdeps_zstd", "librocm_sysdeps_z"]:
                if f"to {iso_name}" not in line and iso_name not in line.split(" to ")[-1]:
                    continue
                # This binding targets the isolated .so — symbol must be prefixed
                import re
                m = re.search(r"symbol `([^']+)'", line)
                if m:
                    sym = m.group(1)
                    if not sym.startswith("rocm_") and not sym.startswith("_"):
                        pytest.fail(
                            f"Unprefixed symbol bound to isolated .so: {sym}\n{line}"
                        )
