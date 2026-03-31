"""ELF symbol extraction via readelf."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from isolib.model import ElfSymbol, SymbolBind, SymbolType, SymbolVisibility

# readelf -sW --dyn-syms output line pattern:
#   Num:    Value          Size Type    Bind   Vis      Ndx Name
#     1: 0000000000000000     0 FUNC    GLOBAL DEFAULT  UND __libc_start_main@GLIBC_2.34 (2)
#    42: 0000000000012345    88 FUNC    GLOBAL DEFAULT   14 ZSTD_decompress@@AMDROCM_SYSDEPS_1.0
_READELF_LINE = re.compile(
    r"\s*\d+:\s+"  # Num:
    r"[0-9a-fA-F]+\s+"  # Value
    r"(\d+)\s+"  # Size (group 1)
    r"(\S+)\s+"  # Type (group 2)
    r"(\S+)\s+"  # Bind (group 3)
    r"(\S+)\s+"  # Vis (group 4)
    r"(\S+)\s+"  # Ndx (group 5)
    r"(\S+)"  # Name + optional version (group 6)
    r"(?:\s+\(\d+\))?"  # Optional version index in parens
)

_TYPE_MAP: dict[str, SymbolType] = {
    "FUNC": SymbolType.FUNC,
    "OBJECT": SymbolType.OBJECT,
    "NOTYPE": SymbolType.NOTYPE,
    "TLS": SymbolType.TLS,
    "GNU_IFUNC": SymbolType.IFUNC,
    "IFUNC": SymbolType.IFUNC,
    "COMMON": SymbolType.COMMON,
}

_BIND_MAP: dict[str, SymbolBind] = {
    "LOCAL": SymbolBind.LOCAL,
    "GLOBAL": SymbolBind.GLOBAL,
    "WEAK": SymbolBind.WEAK,
}

_VIS_MAP: dict[str, SymbolVisibility] = {
    "DEFAULT": SymbolVisibility.DEFAULT,
    "HIDDEN": SymbolVisibility.HIDDEN,
    "PROTECTED": SymbolVisibility.PROTECTED,
    "INTERNAL": SymbolVisibility.INTERNAL,
}


def _parse_name_version(raw: str) -> tuple[str, str | None, bool]:
    """Parse 'name@@VERSION' or 'name@VERSION' or 'name'.

    Returns (name, version_or_None, is_default_version).
    """
    if "@@" in raw:
        name, version = raw.split("@@", 1)
        return name, version, True
    if "@" in raw:
        name, version = raw.split("@", 1)
        return name, version, False
    return raw, None, True


def extract_dynamic_symbols(
    so_path: Path,
    readelf: Path = Path("readelf"),
) -> list[ElfSymbol]:
    """Extract dynamic symbols from a shared library using readelf.

    Args:
        so_path: Path to the .so file.
        readelf: Path to the readelf binary.

    Returns:
        List of ElfSymbol for all entries in the dynamic symbol table.
    """
    result = subprocess.run(
        [str(readelf), "--dyn-syms", "-W", str(so_path)],
        capture_output=True,
        text=True,
        check=True,
    )

    symbols: list[ElfSymbol] = []
    for line in result.stdout.splitlines():
        m = _READELF_LINE.match(line)
        if not m:
            continue

        size_str, type_str, bind_str, vis_str, ndx, raw_name = m.groups()

        sym_type = _TYPE_MAP.get(type_str)
        if sym_type is None:
            continue  # Skip unknown types (e.g. SECTION, FILE)

        bind = _BIND_MAP.get(bind_str)
        if bind is None:
            continue

        vis = _VIS_MAP.get(vis_str)
        if vis is None:
            continue

        name, version, version_default = _parse_name_version(raw_name)
        if not name:
            continue

        symbols.append(
            ElfSymbol(
                name=name,
                bind=bind,
                sym_type=sym_type,
                visibility=vis,
                section=ndx,
                version=version,
                version_default=version_default,
                size=int(size_str),
            )
        )

    return symbols
