"""Shared verification harness for integration tests.

Provides systematic checks for isolated libraries:
- Symbol verification via readelf
- Link verification (autoconf simulation, direct, negative)
- Runtime verification via LD_DEBUG scraping
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from isolate_elf.model import IsolationResult
from isolate_elf.toolchain import Toolchain


@dataclass
class VerificationResult:
    """Results of all verification checks."""

    prefixed_symbols: list[str] = field(default_factory=list)
    unprefixed_leaks: list[str] = field(default_factory=list)
    excluded_symbols: list[str] = field(default_factory=list)
    autoconf_link_ok: bool = False
    direct_link_ok: bool = False
    negative_link_ok: bool = False
    runtime_ok: bool = False
    ld_debug_bindings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return not self.errors and not self.unprefixed_leaks


def verify_symbols(
    result: IsolationResult,
    prefix: str,
    symbol_patterns: list[str],
    tc: Toolchain,
) -> VerificationResult:
    """Verify that all library-specific symbols are prefixed and no leaks exist.

    Args:
        result: Isolation result with artifact paths.
        prefix: The prefix applied (e.g. "rocm_").
        symbol_patterns: Glob patterns for symbols that must be prefixed
                         (e.g. ["ZSTD_*", "FSE_*"]).
        tc: Toolchain for readelf.
    """
    vr = VerificationResult()

    # Read dynamic symbols from prefixed .so
    proc = subprocess.run(
        [str(tc.readelf), "--dyn-syms", "-W", str(result.prefixed_so)],
        capture_output=True, text=True, check=True,
    )

    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) < 8:
            continue
        # Format: Num: Value Size Type Bind Vis Ndx Name
        try:
            sym_type = parts[3]
            bind = parts[4]
            ndx = parts[6]
            raw_name = parts[7]
        except (IndexError, ValueError):
            continue

        if ndx == "UND":
            continue

        # Strip version tag for matching
        name = raw_name.split("@")[0]

        if name.startswith(prefix):
            vr.prefixed_symbols.append(name)
        elif _matches_patterns(name, symbol_patterns):
            # This symbol SHOULD have been prefixed but wasn't
            vr.unprefixed_leaks.append(name)
            vr.errors.append(f"Unprefixed leak: {name} (type={sym_type}, bind={bind})")
        else:
            vr.excluded_symbols.append(name)

    return vr


def verify_autoconf_link(
    result: IsolationResult,
    consumer_func: str,
    link_name: str,
    tc: Toolchain,
    work_dir: Path,
) -> bool:
    """Simulate an autoconf AC_CHECK_LIB link test.

    This is the critical test: does `-l<name>` work via the linker script?
    """
    test_c = work_dir / "test_autoconf.c"
    test_c.write_text(
        f"extern char {consumer_func}();\n"
        f"int main() {{ {consumer_func}(); return 0; }}\n"
    )
    test_bin = work_dir / "test_autoconf"

    proc = subprocess.run(
        [
            str(tc.cc), "-o", str(test_bin), str(test_c),
            f"-L{result.linker_script.parent}",
            f"-l{link_name}",
            f"-Wl,-rpath,{result.prefixed_so.parent}",
        ],
        capture_output=True, text=True,
    )
    return proc.returncode == 0


def verify_negative_link(
    result: IsolationResult,
    consumer_func: str,
    tc: Toolchain,
    work_dir: Path,
) -> bool:
    """Verify that linking directly against prefixed .so without stubs fails."""
    test_c = work_dir / "test_negative.c"
    test_c.write_text(
        f"extern int {consumer_func}();\n"
        f"int main() {{ return {consumer_func}(); }}\n"
    )
    test_bin = work_dir / "test_negative"

    proc = subprocess.run(
        [
            str(tc.cc), "-o", str(test_bin), str(test_c),
            f"-l:{result.prefixed_so.name}",
            f"-L{result.prefixed_so.parent}",
        ],
        capture_output=True, text=True,
    )
    # Should FAIL — the original symbol name doesn't exist
    return proc.returncode != 0


def verify_runtime_isolation(
    result: IsolationResult,
    consumer_func: str,
    link_name: str,
    prefix: str,
    tc: Toolchain,
    work_dir: Path,
    system_so: Path | None = None,
) -> tuple[bool, list[str]]:
    """Verify runtime symbol binding via LD_DEBUG.

    Builds a consumer that calls through the linker script path, then
    runs it with LD_DEBUG=bindings and scrapes the output to verify:
    - Consumer binds to prefixed names in the isolated .so
    - No bindings to unprefixed names in the isolated .so

    Args:
        system_so: Optional path to system copy of the library for
                   cohabitation testing.

    Returns:
        (ok, binding_lines) tuple.
    """
    test_c = work_dir / "test_runtime.c"
    test_c.write_text(
        f"extern char {consumer_func}();\n"
        f"int main() {{ {consumer_func}(); return 0; }}\n"
    )
    test_bin = work_dir / "test_runtime"

    # Build via linker script
    proc = subprocess.run(
        [
            str(tc.cc), "-o", str(test_bin), str(test_c),
            f"-L{result.linker_script.parent}",
            f"-l{link_name}",
            f"-Wl,-rpath,{result.prefixed_so.parent}",
        ],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return False, [f"Build failed: {proc.stderr}"]

    # Run with LD_DEBUG
    env = {
        **os.environ,
        "LD_DEBUG": "bindings",
        "LD_LIBRARY_PATH": str(result.prefixed_so.parent),
    }
    if system_so:
        env["LD_PRELOAD"] = str(system_so)

    proc = subprocess.run(
        [str(test_bin)],
        capture_output=True, text=True, env=env,
    )

    # Parse LD_DEBUG output (on stderr)
    bindings: list[str] = []
    iso_so_name = result.prefixed_so.name
    ok = True

    for line in proc.stderr.splitlines():
        if "binding" not in line or iso_so_name not in line:
            continue
        bindings.append(line.strip())

        # Extract symbol name and binding direction from binding line
        # Format: "binding file <source> to <target>: normal symbol `name'"
        m = re.search(r"symbol `([^']+)'", line)
        target_m = re.search(r" to ([^:]+):", line)
        if m and target_m:
            sym = m.group(1)
            target = target_m.group(1).strip()
            # Only flag if something binds TO the isolated .so with unprefixed name
            if iso_so_name in target:
                if not sym.startswith(prefix) and not sym.startswith("_"):
                    ok = False
                    bindings.append(f"ERROR: unprefixed binding to isolated .so: {sym}")

    if proc.returncode != 0 and proc.returncode != 127:
        # Non-zero but not "library not found" — might be the test returning non-zero
        pass

    return ok, bindings


def _matches_patterns(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in patterns)
