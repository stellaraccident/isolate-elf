"""Low-level ELF struct definitions for binary rewriting.

Minimal subset borrowed from rocm-systems/shared/kpack/python/rocm_kpack/elf/types.py
with additions for .dynsym, .gnu.hash, and .hash manipulation.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import ClassVar

# =============================================================================
# Constants
# =============================================================================

PAGE_SIZE = 0x1000
ELF_MAGIC = b"\x7fELF"

ELF64_EHDR_SIZE = 64
ELF64_PHDR_SIZE = 56
ELF64_SHDR_SIZE = 64
ELF64_SYM_SIZE = 24

# Section types
SHT_NULL = 0
SHT_STRTAB = 3
SHT_RELA = 4
SHT_HASH = 5
SHT_DYNAMIC = 6
SHT_DYNSYM = 11
SHT_GNU_HASH = 0x6FFFFFF6
SHT_GNU_VERSYM = 0x6FFFFFFF

# Section flags
SHF_ALLOC = 0x2

# Program header types
PT_LOAD = 1

# Program header flags
PF_R = 0x4

# Dynamic tags
DT_NULL = 0
DT_HASH = 4
DT_STRTAB = 5
DT_STRSZ = 10
DT_SONAME = 14
DT_VERSYM = 0x6FFFFFF0
DT_GNU_HASH = 0x6FFFFEF5


# =============================================================================
# ELF Structures
# =============================================================================


@dataclass
class ElfHeader:
    e_ident: bytes
    e_type: int
    e_machine: int
    e_version: int
    e_entry: int
    e_phoff: int
    e_shoff: int
    e_flags: int
    e_ehsize: int
    e_phentsize: int
    e_phnum: int
    e_shentsize: int
    e_shnum: int
    e_shstrndx: int

    FMT: ClassVar[str] = "<16sHHIQQQIHHHHHH"

    @classmethod
    def from_bytes(cls, data: bytes | bytearray) -> ElfHeader:
        if len(data) < ELF64_EHDR_SIZE:
            raise ValueError(f"Data too short for ELF header: {len(data)}")
        if data[:4] != ELF_MAGIC:
            raise ValueError("Not an ELF file")
        if data[4] != 2:
            raise ValueError("Only 64-bit ELF supported")
        if data[5] != 1:
            raise ValueError("Only little-endian ELF supported")
        return cls(*struct.unpack_from(cls.FMT, data, 0))

    def write_to(self, data: bytearray) -> None:
        struct.pack_into(
            self.FMT, data, 0,
            self.e_ident, self.e_type, self.e_machine, self.e_version,
            self.e_entry, self.e_phoff, self.e_shoff, self.e_flags,
            self.e_ehsize, self.e_phentsize, self.e_phnum,
            self.e_shentsize, self.e_shnum, self.e_shstrndx,
        )


@dataclass
class ProgramHeader:
    p_type: int
    p_flags: int
    p_offset: int
    p_vaddr: int
    p_paddr: int
    p_filesz: int
    p_memsz: int
    p_align: int

    FMT: ClassVar[str] = "<IIQQQQQQ"

    @classmethod
    def from_bytes(cls, data: bytes | bytearray, offset: int) -> ProgramHeader:
        return cls(*struct.unpack_from(cls.FMT, data, offset))

    def write_to(self, data: bytearray, offset: int) -> None:
        struct.pack_into(
            self.FMT, data, offset,
            self.p_type, self.p_flags, self.p_offset, self.p_vaddr,
            self.p_paddr, self.p_filesz, self.p_memsz, self.p_align,
        )


@dataclass
class SectionHeader:
    sh_name: int
    sh_type: int
    sh_flags: int
    sh_addr: int
    sh_offset: int
    sh_size: int
    sh_link: int
    sh_info: int
    sh_addralign: int
    sh_entsize: int

    FMT: ClassVar[str] = "<IIQQQQIIQQ"

    @classmethod
    def from_bytes(cls, data: bytes | bytearray, offset: int) -> SectionHeader:
        return cls(*struct.unpack_from(cls.FMT, data, offset))

    def write_to(self, data: bytearray, offset: int) -> None:
        struct.pack_into(
            self.FMT, data, offset,
            self.sh_name, self.sh_type, self.sh_flags, self.sh_addr,
            self.sh_offset, self.sh_size, self.sh_link, self.sh_info,
            self.sh_addralign, self.sh_entsize,
        )


@dataclass
class Elf64Sym:
    """ELF64 symbol table entry (Elf64_Sym)."""

    st_name: int   # Offset into string table
    st_info: int   # Binding + type
    st_other: int  # Visibility
    st_shndx: int  # Section index
    st_value: int  # Symbol value
    st_size: int   # Symbol size

    FMT: ClassVar[str] = "<IBBHQQ"

    @classmethod
    def from_bytes(cls, data: bytes | bytearray, offset: int) -> Elf64Sym:
        return cls(*struct.unpack_from(cls.FMT, data, offset))

    def write_to(self, data: bytearray, offset: int) -> None:
        struct.pack_into(
            self.FMT, data, offset,
            self.st_name, self.st_info, self.st_other, self.st_shndx,
            self.st_value, self.st_size,
        )

    @property
    def bind(self) -> int:
        return self.st_info >> 4

    @property
    def sym_type(self) -> int:
        return self.st_info & 0xF


# =============================================================================
# Hash Functions
# =============================================================================


def gnu_hash(name: bytes) -> int:
    """GNU hash function for symbol lookup."""
    h = 5381
    for c in name:
        h = ((h * 33) + c) & 0xFFFFFFFF
    return h


def sysv_hash(name: bytes) -> int:
    """SYSV (ELF) hash function for symbol lookup."""
    h = 0
    for c in name:
        h = ((h << 4) + c) & 0xFFFFFFFF
        g = h & 0xF0000000
        if g:
            h ^= g >> 24
        h &= ~g
    return h


# =============================================================================
# Helpers
# =============================================================================


def read_string(data: bytes | bytearray, offset: int) -> bytes:
    """Read a null-terminated string from data."""
    end = data.index(b"\x00", offset)
    return bytes(data[offset:end])


def get_section_name(
    data: bytes | bytearray, shstrtab_offset: int, name_idx: int,
) -> str:
    end = data.find(b"\x00", shstrtab_offset + name_idx)
    if end == -1:
        return ""
    return data[shstrtab_offset + name_idx : end].decode("ascii", errors="replace")
