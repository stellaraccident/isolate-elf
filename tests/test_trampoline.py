"""Tests for trampoline ASM generation."""

from pathlib import Path

from isolib.model import SymbolRename, SymbolType
from isolib.trampoline import build_stubs_archive, generate_trampoline_asm


def test_x86_64_asm_generation() -> None:
    renames = [
        SymbolRename("foo", "rocm_foo", SymbolType.FUNC),
        SymbolRename("bar", "rocm_bar", SymbolType.FUNC),
    ]
    asm = generate_trampoline_asm(renames, "x86_64")

    assert ".globl foo" in asm
    assert "foo:" in asm
    assert "jmp rocm_foo@PLT" in asm
    assert ".globl bar" in asm
    assert "jmp rocm_bar@PLT" in asm
    assert ".type foo, @function" in asm
    assert ".size foo, .-foo" in asm


def test_aarch64_asm_generation() -> None:
    renames = [
        SymbolRename("foo", "rocm_foo", SymbolType.FUNC),
    ]
    asm = generate_trampoline_asm(renames, "aarch64")

    assert ".globl foo" in asm
    assert "b rocm_foo" in asm


def test_object_symbols_skipped() -> None:
    renames = [
        SymbolRename("func_sym", "rocm_func_sym", SymbolType.FUNC),
        SymbolRename("data_sym", "rocm_data_sym", SymbolType.OBJECT),
        SymbolRename("tls_sym", "rocm_tls_sym", SymbolType.TLS),
    ]
    asm = generate_trampoline_asm(renames, "x86_64")

    assert "func_sym:" in asm
    assert "data_sym:" not in asm
    assert "tls_sym:" not in asm
    assert "1 trampoline stubs" in asm


def test_ifunc_gets_trampoline() -> None:
    renames = [
        SymbolRename("optimized", "rocm_optimized", SymbolType.IFUNC),
    ]
    asm = generate_trampoline_asm(renames, "x86_64")
    assert "optimized:" in asm
    assert "jmp rocm_optimized@PLT" in asm


def test_build_stubs_archive(tmp_path: Path) -> None:
    renames = [
        SymbolRename("foo", "rocm_foo", SymbolType.FUNC),
        SymbolRename("bar", "rocm_bar", SymbolType.FUNC),
    ]
    asm = generate_trampoline_asm(renames, "x86_64")
    archive = tmp_path / "stubs.a"

    build_stubs_archive(asm, archive, "x86_64")

    assert archive.exists()
    assert archive.stat().st_size > 0
