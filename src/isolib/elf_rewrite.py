"""ELF binary rewriter for .dynsym/.dynstr symbol renaming.

objcopy --redefine-syms does NOT modify .dynsym (only .symtab), so we need
our own ELF surgery to rename dynamic symbols. This module:

1. Builds a new .dynstr with renamed strings appended
2. Updates .dynsym st_name offsets to point to new strings
3. Rebuilds .gnu.hash and .hash tables with new name hashes
4. Places new .dynstr in a new PT_LOAD segment
5. Updates DT_STRTAB/DT_STRSZ in .dynamic

Approach borrowed from rocm-systems/shared/kpack ELF surgery patterns:
parse once into bytearray, modify in memory, write once.
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from pathlib import Path

from isolib.elf_types import (
    ELF64_EHDR_SIZE,
    ELF64_PHDR_SIZE,
    ELF64_SHDR_SIZE,
    ELF64_SYM_SIZE,
    PAGE_SIZE,
    DT_GNU_HASH,
    DT_HASH,
    DT_NULL,
    DT_SONAME,
    DT_STRSZ,
    DT_STRTAB,
    PF_R,
    PT_LOAD,
    SHT_DYNAMIC,
    SHT_DYNSYM,
    SHT_GNU_HASH,
    SHT_GNU_VERSYM,
    SHT_HASH,
    SHT_RELA,
    SHT_STRTAB,
    Elf64Sym,
    ElfHeader,
    ProgramHeader,
    SectionHeader,
    get_section_name,
    gnu_hash,
    read_string,
    sysv_hash,
)

log = logging.getLogger(__name__)


@dataclass
class _Section:
    index: int
    name: str
    header: SectionHeader


class ElfRewriteError(Exception):
    pass


def rename_dynamic_symbols(
    input_path: Path,
    output_path: Path,
    renames: dict[str, str],
    new_soname: str | None = None,
) -> dict[str, str]:
    """Rename symbols in a shared library's .dynsym table.

    Args:
        input_path: Path to input .so file.
        output_path: Path for output .so file.
        renames: Mapping of old_name -> new_name.
        new_soname: If set, rewrite DT_SONAME to this value.

    Returns:
        Dict of actually renamed symbols (old_name -> new_name).

    Raises:
        ElfRewriteError: On structural ELF issues.
    """
    data = bytearray(input_path.read_bytes())
    ehdr = ElfHeader.from_bytes(data)

    sections = _parse_sections(data, ehdr)
    dynstr_sec = _find_section(sections, ".dynstr")
    dynsym_sec = _find_section(sections, ".dynsym")
    dynamic_sec = _find_section(sections, ".dynamic")

    if not all([dynstr_sec, dynsym_sec, dynamic_sec]):
        raise ElfRewriteError(
            "Missing required sections (.dynstr, .dynsym, .dynamic)"
        )

    # Read original .dynstr
    old_dynstr = bytes(
        data[dynstr_sec.header.sh_offset :
             dynstr_sec.header.sh_offset + dynstr_sec.header.sh_size]
    )

    # Read .dynsym entries and build rename plan
    syms = _read_dynsym(data, dynsym_sec.header)
    rename_plan: dict[int, tuple[str, str]] = {}  # sym_index -> (old, new)
    actually_renamed: dict[str, str] = {}

    for i, sym in enumerate(syms):
        if sym.st_name == 0:
            continue
        name = read_string(old_dynstr, sym.st_name).decode("ascii", errors="replace")
        if name in renames:
            rename_plan[i] = (name, renames[name])
            actually_renamed[name] = renames[name]

    if not rename_plan:
        log.warning("No symbols matched rename list — copying input as-is")
        output_path.write_bytes(data)
        return {}

    log.info("Renaming %d symbols in .dynsym", len(rename_plan))

    # Build new .dynstr: original content + appended new names
    new_dynstr = bytearray(old_dynstr)
    new_name_offsets: dict[int, int] = {}  # sym_index -> new st_name offset

    for sym_idx, (old_name, new_name) in rename_plan.items():
        new_offset = len(new_dynstr)
        new_dynstr.extend(new_name.encode("ascii") + b"\x00")
        new_name_offsets[sym_idx] = new_offset

    # If new SONAME requested, append it to new .dynstr
    soname_dynstr_offset: int | None = None
    if new_soname is not None:
        soname_dynstr_offset = len(new_dynstr)
        new_dynstr.extend(new_soname.encode("ascii") + b"\x00")
        log.info("New SONAME: %s (at dynstr offset %d)", new_soname, soname_dynstr_offset)

    # Update .dynsym entries with new st_name offsets
    for sym_idx, new_offset in new_name_offsets.items():
        sym_file_offset = dynsym_sec.header.sh_offset + sym_idx * ELF64_SYM_SIZE
        struct.pack_into("<I", data, sym_file_offset, new_offset)

    # Place new .dynstr in the file
    _replace_dynstr(data, ehdr, sections, dynstr_sec, dynamic_sec, new_dynstr)

    # Update DT_SONAME if requested
    if soname_dynstr_offset is not None:
        _update_dynamic_entry(data, dynamic_sec.header, DT_SONAME, soname_dynstr_offset)

    # Re-read .dynstr from its new location for hash rebuild
    final_dynstr = bytearray(
        data[dynstr_sec.header.sh_offset :
             dynstr_sec.header.sh_offset + dynstr_sec.header.sh_size]
    )

    # Re-read syms (st_name offsets were updated in-place)
    syms = _read_dynsym(data, dynsym_sec.header)

    # Reorder .dynsym for GNU hash and rebuild all hash tables.
    # GNU hash requires symbols within each bucket to be contiguous in
    # .dynsym. After renaming, hash values change so bucket assignments
    # change, breaking contiguity. We fix this by reordering .dynsym
    # (and updating all structures that reference symbols by index:
    # relocations, .gnu.version).
    _reorder_dynsym_and_rebuild_hashes(data, ehdr, sections, dynsym_sec, final_dynstr)

    output_path.write_bytes(data)

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise ElfRewriteError(f"Failed to write output: {output_path}")

    return actually_renamed


# =============================================================================
# Internal helpers
# =============================================================================


def _parse_sections(
    data: bytearray, ehdr: ElfHeader,
) -> list[_Section]:
    sections: list[_Section] = []
    shstrtab_offset = 0

    if ehdr.e_shstrndx < ehdr.e_shnum:
        off = ehdr.e_shoff + ehdr.e_shstrndx * ehdr.e_shentsize
        shstrtab_hdr = SectionHeader.from_bytes(data, off)
        shstrtab_offset = shstrtab_hdr.sh_offset

    for i in range(ehdr.e_shnum):
        off = ehdr.e_shoff + i * ehdr.e_shentsize
        hdr = SectionHeader.from_bytes(data, off)
        name = get_section_name(data, shstrtab_offset, hdr.sh_name)
        sections.append(_Section(index=i, name=name, header=hdr))

    return sections


def _find_section(sections: list[_Section], name: str) -> _Section | None:
    for s in sections:
        if s.name == name:
            return s
    return None


def _read_dynsym(data: bytearray, hdr: SectionHeader) -> list[Elf64Sym]:
    syms: list[Elf64Sym] = []
    count = hdr.sh_size // ELF64_SYM_SIZE
    for i in range(count):
        offset = hdr.sh_offset + i * ELF64_SYM_SIZE
        syms.append(Elf64Sym.from_bytes(data, offset))
    return syms


def _replace_dynstr(
    data: bytearray,
    ehdr: ElfHeader,
    sections: list[_Section],
    dynstr_sec: _Section,
    dynamic_sec: _Section,
    new_dynstr: bytearray,
) -> None:
    """Place new .dynstr content and update all references.

    Strategy:
    1. If new content fits in existing section space, overwrite in-place.
    2. Otherwise, append new .dynstr to end of file with a new PT_LOAD
       segment. The PHDR table is relocated to the end of the file first
       if there's no room for an additional entry.
    """
    old_size = dynstr_sec.header.sh_size
    new_size = len(new_dynstr)

    if new_size <= old_size:
        # Fits in place
        offset = dynstr_sec.header.sh_offset
        data[offset : offset + new_size] = new_dynstr
        data[offset + new_size : offset + old_size] = b"\x00" * (old_size - new_size)
        dynstr_sec.header.sh_size = new_size
        _write_section_header(data, ehdr, dynstr_sec)
        _update_dynamic_entry(data, dynamic_sec.header, DT_STRSZ, new_size)
        return

    # New .dynstr is larger — append to file with new PT_LOAD

    # Ensure space for new PHDR entry FIRST (may add a PT_LOAD for
    # relocated PHDR table, which advances max_vaddr)
    _ensure_phdr_space(data, ehdr, sections)

    # Now compute max virtual address across all PT_LOAD segments
    max_vaddr = 0
    for i in range(ehdr.e_phnum):
        phdr = ProgramHeader.from_bytes(data, ehdr.e_phoff + i * ELF64_PHDR_SIZE)
        if phdr.p_type == PT_LOAD:
            end = phdr.p_vaddr + phdr.p_memsz
            if end > max_vaddr:
                max_vaddr = end

    # New vaddr: page-aligned past all existing segments
    new_vaddr = (max_vaddr + PAGE_SIZE - 1) & ~(PAGE_SIZE - 1)

    # File offset: page-aligned at end of file
    new_file_offset = (len(data) + PAGE_SIZE - 1) & ~(PAGE_SIZE - 1)

    # Pad file to alignment
    data.extend(b"\x00" * (new_file_offset - len(data)))

    # Write new .dynstr content
    data.extend(new_dynstr)

    # Write new PT_LOAD entry
    new_phdr = ProgramHeader(
        p_type=PT_LOAD,
        p_flags=PF_R,
        p_offset=new_file_offset,
        p_vaddr=new_vaddr,
        p_paddr=new_vaddr,
        p_filesz=new_size,
        p_memsz=new_size,
        p_align=PAGE_SIZE,
    )
    phdr_offset = ehdr.e_phoff + ehdr.e_phnum * ELF64_PHDR_SIZE
    new_phdr.write_to(data, phdr_offset)
    ehdr.e_phnum += 1
    ehdr.write_to(data)

    # Update .dynstr section header
    dynstr_sec.header.sh_offset = new_file_offset
    dynstr_sec.header.sh_size = new_size
    dynstr_sec.header.sh_addr = new_vaddr
    _write_section_header(data, ehdr, dynstr_sec)

    # Update DT_STRTAB and DT_STRSZ in .dynamic
    _update_dynamic_entry(data, dynamic_sec.header, DT_STRTAB, new_vaddr)
    _update_dynamic_entry(data, dynamic_sec.header, DT_STRSZ, new_size)

    log.debug(
        "New .dynstr: file_offset=0x%x vaddr=0x%x size=%d",
        new_file_offset, new_vaddr, new_size,
    )


def _ensure_phdr_space(
    data: bytearray,
    ehdr: ElfHeader,
    sections: list[_Section],
) -> None:
    """Ensure there's room for one more PHDR entry.

    If the current PHDR table bumps into section content, relocate the
    entire PHDR table to the end of the file (following kpack's approach).
    """
    current_end = ehdr.e_phoff + ehdr.e_phnum * ELF64_PHDR_SIZE
    needed_end = current_end + ELF64_PHDR_SIZE

    # Find earliest content after PHDR table
    min_content_offset = len(data)
    for sec in sections:
        if sec.header.sh_type != 0 and sec.header.sh_offset > ehdr.e_phoff:
            min_content_offset = min(min_content_offset, sec.header.sh_offset)

    if needed_end <= min_content_offset:
        return  # Plenty of room

    # No room — relocate PHDR table to end of file
    log.debug("Relocating PHDR table to end of file (no room at offset 0x%x)", ehdr.e_phoff)

    # Read existing PHDRs
    old_phdrs: list[ProgramHeader] = []
    for i in range(ehdr.e_phnum):
        off = ehdr.e_phoff + i * ELF64_PHDR_SIZE
        old_phdrs.append(ProgramHeader.from_bytes(data, off))

    # Compute new location at end of file, 8-byte aligned
    new_phdr_offset = (len(data) + 7) & ~7

    # Allocate space for existing PHDRs + 16 spare slots
    total_slots = ehdr.e_phnum + 16
    total_size = total_slots * ELF64_PHDR_SIZE

    # Compute virtual address for the new PHDR region
    max_vaddr = 0
    for phdr in old_phdrs:
        if phdr.p_type == PT_LOAD:
            end = phdr.p_vaddr + phdr.p_memsz
            if end > max_vaddr:
                max_vaddr = end

    new_phdr_vaddr = (max_vaddr + PAGE_SIZE - 1) & ~(PAGE_SIZE - 1)

    # File offset must satisfy mmap alignment
    new_phdr_offset = (len(data) + PAGE_SIZE - 1) & ~(PAGE_SIZE - 1)

    # Pad and allocate
    data.extend(b"\x00" * (new_phdr_offset + total_size - len(data)))

    # Write existing PHDRs at new location
    for i, phdr in enumerate(old_phdrs):
        phdr.write_to(data, new_phdr_offset + i * ELF64_PHDR_SIZE)

    # Add a PT_LOAD segment to map the new PHDR table
    phdr_load = ProgramHeader(
        p_type=PT_LOAD,
        p_flags=PF_R,
        p_offset=new_phdr_offset,
        p_vaddr=new_phdr_vaddr,
        p_paddr=new_phdr_vaddr,
        p_filesz=total_size,
        p_memsz=total_size,
        p_align=PAGE_SIZE,
    )
    phdr_load.write_to(data, new_phdr_offset + ehdr.e_phnum * ELF64_PHDR_SIZE)

    # Update PT_PHDR if present
    for i, phdr in enumerate(old_phdrs):
        if phdr.p_type == 6:  # PT_PHDR
            phdr.p_offset = new_phdr_offset
            phdr.p_vaddr = new_phdr_vaddr
            phdr.p_paddr = new_phdr_vaddr
            phdr.p_filesz = total_size
            phdr.p_memsz = total_size
            phdr.write_to(data, new_phdr_offset + i * ELF64_PHDR_SIZE)

    # Update ELF header
    ehdr.e_phoff = new_phdr_offset
    ehdr.e_phnum += 1  # The PT_LOAD for PHDR itself
    ehdr.write_to(data)

    # Relocate section header table past the new PHDR region
    _relocate_shdr_table(data, ehdr, sections)


def _write_section_header(
    data: bytearray, ehdr: ElfHeader, sec: _Section,
) -> None:
    offset = ehdr.e_shoff + sec.index * ehdr.e_shentsize
    if offset + ELF64_SHDR_SIZE <= len(data):
        sec.header.write_to(data, offset)


def _relocate_shdr_table(
    data: bytearray, ehdr: ElfHeader, sections: list[_Section],
) -> None:
    """Move section header table to end of file."""
    # Align to 8 bytes
    new_shoff = (len(data) + 7) & ~7
    padding = new_shoff - len(data)
    if padding > 0:
        data.extend(b"\x00" * padding)

    for sec in sections:
        data.extend(struct.pack(
            SectionHeader.FMT,
            sec.header.sh_name, sec.header.sh_type, sec.header.sh_flags,
            sec.header.sh_addr, sec.header.sh_offset, sec.header.sh_size,
            sec.header.sh_link, sec.header.sh_info, sec.header.sh_addralign,
            sec.header.sh_entsize,
        ))

    ehdr.e_shoff = new_shoff
    ehdr.write_to(data)


def _update_dynamic_entry(
    data: bytearray,
    dynamic_hdr: SectionHeader,
    tag: int,
    value: int,
) -> None:
    """Update a specific entry in the .dynamic section."""
    offset = dynamic_hdr.sh_offset
    end = offset + dynamic_hdr.sh_size
    entry_size = dynamic_hdr.sh_entsize or 16

    while offset < end:
        d_tag = struct.unpack_from("<q", data, offset)[0]
        if d_tag == DT_NULL:
            break
        if d_tag == tag:
            struct.pack_into("<Q", data, offset + 8, value)
            return
        offset += entry_size

    log.warning("DT tag %d not found in .dynamic", tag)


def _reorder_dynsym_and_rebuild_hashes(
    data: bytearray,
    ehdr: ElfHeader,
    sections: list[_Section],
    dynsym_sec: _Section,
    dynstr: bytearray,
) -> None:
    """Reorder .dynsym for GNU hash contiguity and rebuild all hash tables.

    GNU hash requires that symbols in the same bucket are contiguous in
    .dynsym. After renaming, hash values change, so we must reorder.
    This also requires updating:
    - .rela.dyn / .rela.plt: relocation r_info symbol indices
    - .gnu.version: parallel array reordered to match new .dynsym order

    The reorder preserves the GNU hash invariant:
    - Symbols 0..symoffset-1 are "unhashed" (local/undefined) — kept first
    - Symbols symoffset..N are sorted by (gnu_hash(name) % nbuckets)
    """
    gnu_hash_sec = _find_section(sections, ".gnu.hash")
    sysv_hash_sec = _find_section(sections, ".hash")

    syms = _read_dynsym(data, dynsym_sec.header)
    nsyms = len(syms)

    if gnu_hash_sec is not None:
        off = gnu_hash_sec.header.sh_offset
        nbuckets = struct.unpack_from("<I", data, off)[0]
        symoffset = struct.unpack_from("<I", data, off + 4)[0]
        bloom_size = struct.unpack_from("<I", data, off + 8)[0]
        bloom_shift = struct.unpack_from("<I", data, off + 12)[0]
    else:
        # No gnu.hash — just rebuild sysv hash if present
        if sysv_hash_sec is not None:
            _rebuild_sysv_hash(data, sysv_hash_sec, syms, dynstr)
        return

    # Compute new hashes for the hashed portion (symoffset..N)
    hashed_syms = list(range(symoffset, nsyms))
    sym_hashes = {}
    for i in hashed_syms:
        name = read_string(dynstr, syms[i].st_name)
        sym_hashes[i] = gnu_hash(name)

    # Sort hashed symbols by bucket for contiguity
    hashed_syms.sort(key=lambda i: sym_hashes[i] % nbuckets)

    # Build the full new ordering: [0..symoffset-1] + sorted hashed
    new_order = list(range(symoffset)) + hashed_syms

    # Build old_index -> new_index mapping
    old_to_new: dict[int, int] = {}
    for new_idx, old_idx in enumerate(new_order):
        old_to_new[old_idx] = new_idx

    # Check if reorder is actually needed
    if new_order == list(range(nsyms)):
        log.debug("No .dynsym reorder needed — rebuilding hashes in place")
    else:
        log.debug("Reordering .dynsym: %d symbols, %d hashed", nsyms, len(hashed_syms))

        # Read current .dynsym and .gnu.version as raw byte arrays
        dynsym_off = dynsym_sec.header.sh_offset
        old_dynsym_bytes = bytes(data[dynsym_off : dynsym_off + nsyms * ELF64_SYM_SIZE])

        versym_sec = _find_section(sections, ".gnu.version")
        old_versym_bytes = None
        if versym_sec is not None:
            vs_off = versym_sec.header.sh_offset
            vs_size = versym_sec.header.sh_size
            old_versym_bytes = bytes(data[vs_off : vs_off + vs_size])

        # Write reordered .dynsym
        for new_idx, old_idx in enumerate(new_order):
            src_off = old_idx * ELF64_SYM_SIZE
            dst_off = dynsym_off + new_idx * ELF64_SYM_SIZE
            data[dst_off : dst_off + ELF64_SYM_SIZE] = (
                old_dynsym_bytes[src_off : src_off + ELF64_SYM_SIZE]
            )

        # Write reordered .gnu.version
        if old_versym_bytes is not None and versym_sec is not None:
            for new_idx, old_idx in enumerate(new_order):
                src_off = old_idx * 2
                dst_off = versym_sec.header.sh_offset + new_idx * 2
                if src_off + 2 <= len(old_versym_bytes):
                    data[dst_off : dst_off + 2] = old_versym_bytes[src_off : src_off + 2]

        # Update relocation symbol indices
        for sec in sections:
            if sec.header.sh_type != SHT_RELA:
                continue
            rela_off = sec.header.sh_offset
            rela_end = rela_off + sec.header.sh_size
            entry_size = sec.header.sh_entsize or 24
            off = rela_off
            while off < rela_end:
                r_info = struct.unpack_from("<Q", data, off + 8)[0]
                old_sym = r_info >> 32
                r_type = r_info & 0xFFFFFFFF
                if old_sym in old_to_new:
                    new_sym = old_to_new[old_sym]
                    new_info = (new_sym << 32) | r_type
                    struct.pack_into("<Q", data, off + 8, new_info)
                off += entry_size

    # Re-read reordered syms for hash rebuild
    syms = _read_dynsym(data, dynsym_sec.header)

    # Rebuild .gnu.hash
    bloom_off = gnu_hash_sec.header.sh_offset + 16
    buckets_off = bloom_off + bloom_size * 8
    chains_off = buckets_off + nbuckets * 4
    num_chain_syms = nsyms - symoffset

    new_hashes = []
    for i in range(symoffset, nsyms):
        name = read_string(dynstr, syms[i].st_name)
        new_hashes.append(gnu_hash(name))

    # Rebuild bloom filter
    bloom = [0] * bloom_size
    for h in new_hashes:
        word_idx = (h // 64) % bloom_size
        bit1 = h % 64
        bit2 = (h >> bloom_shift) % 64
        bloom[word_idx] |= (1 << bit1) | (1 << bit2)

    for i, val in enumerate(bloom):
        struct.pack_into("<Q", data, bloom_off + i * 8, val)

    # Rebuild buckets — point to first symbol in each bucket
    for i in range(nbuckets):
        struct.pack_into("<I", data, buckets_off + i * 4, 0)

    sym_to_bucket = [h % nbuckets for h in new_hashes]
    seen_buckets: set[int] = set()
    for i, bucket in enumerate(sym_to_bucket):
        if bucket not in seen_buckets:
            struct.pack_into("<I", data, buckets_off + bucket * 4, i + symoffset)
            seen_buckets.add(bucket)

    # Rebuild chains — hash value with bit 0 as end-of-chain
    for i in range(num_chain_syms):
        h = new_hashes[i]
        is_last = (
            i == num_chain_syms - 1
            or sym_to_bucket[i + 1] != sym_to_bucket[i]
        )
        chain_val = (h & ~1) | (1 if is_last else 0)
        chain_off = chains_off + i * 4
        if chain_off + 4 <= len(data):
            struct.pack_into("<I", data, chain_off, chain_val)

    log.debug("Rebuilt .gnu.hash: %d buckets, %d hashed symbols", nbuckets, num_chain_syms)

    # Also rebuild .hash (SYSV) if present
    if sysv_hash_sec is not None:
        _rebuild_sysv_hash(data, sysv_hash_sec, syms, dynstr)


def _rebuild_sysv_hash(
    data: bytearray,
    sec: _Section,
    syms: list[Elf64Sym],
    dynstr: bytearray,
) -> None:
    """Rebuild .hash (SYSV) table in place."""
    off = sec.header.sh_offset
    nbuckets = struct.unpack_from("<I", data, off)[0]
    nchains = struct.unpack_from("<I", data, off + 4)[0]
    buckets_off = off + 8
    chains_off = buckets_off + nbuckets * 4

    for i in range(nbuckets):
        struct.pack_into("<I", data, buckets_off + i * 4, 0)
    for i in range(nchains):
        struct.pack_into("<I", data, chains_off + i * 4, 0)

    for i, sym in enumerate(syms):
        if sym.st_name == 0 and i == 0:
            continue
        name = read_string(dynstr, sym.st_name)
        h = sysv_hash(name)
        bucket = h % nbuckets

        head = struct.unpack_from("<I", data, buckets_off + bucket * 4)[0]
        if head == 0:
            struct.pack_into("<I", data, buckets_off + bucket * 4, i)
        else:
            cur = head
            while True:
                next_idx = struct.unpack_from("<I", data, chains_off + cur * 4)[0]
                if next_idx == 0:
                    struct.pack_into("<I", data, chains_off + cur * 4, i)
                    break
                cur = next_idx

    log.debug("Rebuilt .hash: %d buckets, %d chains", nbuckets, nchains)
