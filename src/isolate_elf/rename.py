"""Symbol renaming in shared library .dynsym via ELF binary rewriting.

Note: objcopy --redefine-syms does NOT modify .dynsym (only .symtab).
We use our own ELF rewriter (elf_rewrite.py) to rename dynamic symbols.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from isolate_elf.elf_rewrite import rename_dynamic_symbols
from isolate_elf.model import SymbolRename


def generate_redefine_map(renames: list[SymbolRename]) -> str:
    """Generate the contents of a redefine-syms mapping file.

    Each line: `original_name prefixed_name`
    Useful for debugging/inspection even though we don't use objcopy.
    """
    lines = [f"{r.original} {r.prefixed}" for r in renames]
    return "\n".join(lines) + "\n"


def rename_symbols(
    input_so: Path,
    output_so: Path,
    renames: list[SymbolRename],
    new_soname: str | None = None,
) -> None:
    """Rename symbols in a shared library's .dynsym table.

    Uses direct ELF binary rewriting to modify .dynstr and rebuild hash
    tables. This works on .dynsym (unlike objcopy --redefine-syms which
    only modifies .symtab).

    Also rewrites DT_SONAME if new_soname is provided, so the runtime
    linker looks for the correct filename.

    Args:
        input_so: Path to the input .so file.
        output_so: Path for the output .so file.
        renames: List of symbol renames to apply.
        new_soname: New SONAME to set (e.g. "librocm_sysdeps_zstd.so.1").

    Raises:
        ValueError: If no renames provided.
        RuntimeError: If output file is missing or empty.
    """
    if not renames:
        raise ValueError("No symbol renames provided")

    rename_dict = {r.original: r.prefixed for r in renames}

    # Copy input to output first, then modify in place
    shutil.copy2(input_so, output_so)

    actually_renamed = rename_dynamic_symbols(
        output_so, output_so, rename_dict, new_soname=new_soname,
    )

    if not output_so.exists():
        raise RuntimeError(f"ELF rewrite produced no output: {output_so}")
    if output_so.stat().st_size == 0:
        raise RuntimeError(f"ELF rewrite produced empty output: {output_so}")

    if len(actually_renamed) != len(rename_dict):
        missing = set(rename_dict) - set(actually_renamed)
        if missing:
            # Some symbols weren't in .dynsym — they might be .symtab only
            pass  # Not an error, just informational
