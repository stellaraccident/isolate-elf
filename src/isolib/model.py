"""Core data model for isolib."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path


class SymbolType(enum.Enum):
    FUNC = "FUNC"
    OBJECT = "OBJECT"
    NOTYPE = "NOTYPE"
    TLS = "TLS"
    IFUNC = "GNU_IFUNC"
    COMMON = "COMMON"


class SymbolBind(enum.Enum):
    LOCAL = "LOCAL"
    GLOBAL = "GLOBAL"
    WEAK = "WEAK"


class SymbolVisibility(enum.Enum):
    DEFAULT = "DEFAULT"
    HIDDEN = "HIDDEN"
    PROTECTED = "PROTECTED"
    INTERNAL = "INTERNAL"


class WarningCategory(enum.Enum):
    OBJECT_SYMBOL = "object-symbol"
    TLS_SYMBOL = "tls-symbol"
    IFUNC_SYMBOL = "ifunc-symbol"
    VERSIONED_SYMBOL = "versioned-symbol"
    WEAK_OVERRIDE = "weak-override"


@dataclass(frozen=True)
class ElfSymbol:
    """A symbol extracted from an ELF dynamic symbol table."""

    name: str
    bind: SymbolBind
    sym_type: SymbolType
    visibility: SymbolVisibility
    section: str  # "UND", ".text", ".data", index like "14", etc.
    version: str | None = None  # e.g. "AMDROCM_SYSDEPS_1.0", "ELFUTILS_0.192"
    version_default: bool = True  # True for @@ (default), False for @
    size: int = 0

    @property
    def is_defined(self) -> bool:
        return self.section != "UND"

    @property
    def is_function(self) -> bool:
        return self.sym_type in (SymbolType.FUNC, SymbolType.IFUNC)

    @property
    def is_object(self) -> bool:
        return self.sym_type == SymbolType.OBJECT

    @property
    def is_tls(self) -> bool:
        return self.sym_type == SymbolType.TLS

    @property
    def is_exportable(self) -> bool:
        """True if this symbol could be exported (defined, global/weak, visible)."""
        return (
            self.is_defined
            and self.bind in (SymbolBind.GLOBAL, SymbolBind.WEAK)
            and self.visibility in (SymbolVisibility.DEFAULT, SymbolVisibility.PROTECTED)
        )


@dataclass(frozen=True)
class SymbolRename:
    """A symbol rename mapping."""

    original: str
    prefixed: str
    sym_type: SymbolType
    version: str | None = None


@dataclass(frozen=True)
class IsolationWarning:
    """A diagnostic warning about a symbol that needs attention."""

    category: WarningCategory
    symbol_name: str
    message: str


@dataclass
class IsolationConfig:
    """Configuration for isolating a single shared library."""

    input_so: Path
    prefix: str  # e.g. "rocm_"
    output_dir: Path
    output_name: str  # e.g. "zstd" -> used to derive artifact names
    soname: str | None = None  # Override SONAME if desired
    extra_exclude_patterns: list[str] = field(default_factory=list)
    werror: bool = False
    allow_categories: set[WarningCategory] = field(default_factory=set)
    arch: str = "x86_64"


@dataclass
class IsolationResult:
    """Output artifacts from one isolation run."""

    prefixed_so: Path
    stubs_archive: Path
    linker_script: Path
    redirect_header: Path
    renamed_symbols: list[SymbolRename]
    warnings: list[IsolationWarning]
