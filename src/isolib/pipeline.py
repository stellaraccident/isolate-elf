"""End-to-end isolation pipeline."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from isolib.elf import extract_dynamic_symbols
from isolib.filters import classify_symbol
from isolib.header import generate_redirect_header
from isolib.linker_script import generate_linker_script
from isolib.model import (
    IsolationConfig,
    IsolationResult,
    IsolationWarning,
    SymbolRename,
    WarningCategory,
)
from isolib.rename import rename_symbols
from isolib.toolchain import Toolchain
from isolib.trampoline import build_stubs_archive, generate_trampoline_asm

log = logging.getLogger(__name__)


class IsolationError(Exception):
    """Raised when isolation fails due to --werror or fatal issues."""


def isolate_library(
    config: IsolationConfig,
    toolchain: Toolchain | None = None,
) -> IsolationResult:
    """Run the full isolation pipeline on a single shared library.

    Steps:
        1. Extract dynamic symbols from input .so
        2. Classify each symbol (prefix, skip, warn)
        3. Rename symbols via objcopy
        4. Generate trampoline stubs, assemble, archive
        5. Generate linker script
        6. Generate redirect header
        7. Check warnings against --werror policy

    Args:
        config: Isolation configuration.
        toolchain: External tools. Discovered automatically if None.

    Returns:
        IsolationResult with paths to all generated artifacts.

    Raises:
        IsolationError: If --werror is set and warnings are emitted.
    """
    tc = toolchain or Toolchain.discover()
    config.output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Extract symbols
    symbols = extract_dynamic_symbols(config.input_so, tc.readelf)
    log.info("Extracted %d dynamic symbols from %s", len(symbols), config.input_so.name)

    # 2. Classify (deduplicate by symbol name)
    renames: list[SymbolRename] = []
    warnings: list[IsolationWarning] = []
    seen_names: set[str] = set()

    for sym in symbols:
        should_prefix, warning = classify_symbol(
            sym, config.extra_exclude_patterns
        )
        if warning and sym.name not in seen_names:
            warnings.append(warning)
        if should_prefix and sym.name not in seen_names:
            seen_names.add(sym.name)
            renames.append(
                SymbolRename(
                    original=sym.name,
                    prefixed=f"{config.prefix}{sym.name}",
                    sym_type=sym.sym_type,
                    version=sym.version,
                )
            )

    log.info(
        "Classified: %d to rename, %d warnings",
        len(renames),
        len(warnings),
    )

    # 3. Check warnings against policy
    _check_warnings(config, warnings)

    # 4. Derive artifact names
    base = config.output_name
    prefix = config.prefix.rstrip("_")
    prefixed_so_name = config.soname or f"lib{prefix}_sysdeps_{base}.so.1"
    stubs_name = f"lib{prefix}_{base}_stubs.a"
    script_name = f"lib{base}.so"
    header_name = f"{prefix}_isolib_{base}_redirect.h"
    guard = f"{prefix.upper()}_ISOLIB_{base.upper()}_REDIRECT_H"

    prefixed_so_path = config.output_dir / prefixed_so_name
    stubs_path = config.output_dir / stubs_name
    script_path = config.output_dir / script_name
    header_path = config.output_dir / header_name

    # 5. Rename symbols and set SONAME to match output filename
    if renames:
        rename_symbols(
            config.input_so, prefixed_so_path, renames,
            new_soname=prefixed_so_name,
        )
        log.info("Renamed %d symbols -> %s", len(renames), prefixed_so_path.name)
    else:
        log.warning("No symbols to rename — copying input as-is")
        import shutil

        shutil.copy2(config.input_so, prefixed_so_path)

    # 6. Generate trampolines
    asm_source = generate_trampoline_asm(renames, config.arch)
    if asm_source.strip():
        build_stubs_archive(asm_source, stubs_path, config.arch, tc.assembler, tc.archiver)
        log.info("Built stubs archive: %s", stubs_path.name)
    else:
        log.warning("No function symbols — creating empty stubs archive")
        _create_empty_archive(stubs_path, tc.archiver)

    # 7. Linker script
    script_text = generate_linker_script(stubs_name, prefixed_so_name)
    script_path.write_text(script_text)
    log.info("Generated linker script: %s", script_path.name)

    # 8. Redirect header
    header_text = generate_redirect_header(renames, guard)
    header_path.write_text(header_text)
    log.info("Generated redirect header: %s", header_path.name)

    return IsolationResult(
        prefixed_so=prefixed_so_path,
        stubs_archive=stubs_path,
        linker_script=script_path,
        redirect_header=header_path,
        renamed_symbols=renames,
        warnings=warnings,
    )


def _check_warnings(
    config: IsolationConfig,
    warnings: list[IsolationWarning],
) -> None:
    """Check warnings against the error policy.

    In --werror mode, any warning not in allow_categories is fatal.
    """
    for w in warnings:
        if config.werror and w.category not in config.allow_categories:
            raise IsolationError(
                f"--werror: {w.message}\n"
                f"  To allow this, pass --allow-{w.category.value}"
            )
        else:
            log.warning("[%s] %s", w.category.value, w.message)


def _create_empty_archive(path: Path, archiver: Path) -> None:
    """Create an empty .a archive."""
    import subprocess

    subprocess.run(
        [str(archiver), "rcs", str(path)],
        check=True,
        capture_output=True,
    )
