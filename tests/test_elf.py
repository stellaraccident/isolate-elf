"""Tests for ELF symbol extraction."""

from pathlib import Path

from isolate_elf.elf import extract_dynamic_symbols
from isolate_elf.model import SymbolBind, SymbolType, SymbolVisibility


def test_extract_testlib_functions(testlib_so: Path) -> None:
    symbols = extract_dynamic_symbols(testlib_so)
    names = {s.name for s in symbols}

    assert "testlib_add" in names
    assert "testlib_multiply" in names
    assert "testlib_greeting" in names

    # Hidden symbol should not appear in dynamic symbols
    assert "_testlib_internal" not in names


def test_extract_testlib_object(testlib_so: Path) -> None:
    symbols = extract_dynamic_symbols(testlib_so)
    version_sym = [s for s in symbols if s.name == "testlib_version"]
    assert len(version_sym) == 1
    assert version_sym[0].sym_type == SymbolType.OBJECT
    assert version_sym[0].is_defined


def test_extract_testlib_func_properties(testlib_so: Path) -> None:
    symbols = extract_dynamic_symbols(testlib_so)
    add_sym = [s for s in symbols if s.name == "testlib_add"]
    assert len(add_sym) == 1
    s = add_sym[0]
    assert s.sym_type == SymbolType.FUNC
    assert s.bind == SymbolBind.GLOBAL
    assert s.visibility == SymbolVisibility.DEFAULT
    assert s.is_defined
    assert s.is_exportable


def test_extract_versioned_symbols(testlib_versioned_so: Path) -> None:
    symbols = extract_dynamic_symbols(testlib_versioned_so)
    func_a = [s for s in symbols if s.name == "versioned_func_a"]
    assert len(func_a) == 1
    assert func_a[0].version == "TESTVER_1.0"
    assert func_a[0].version_default is True


def test_extract_undefined_symbols(testlib_so: Path) -> None:
    """Undefined symbols (from libc) should have section=UND."""
    symbols = extract_dynamic_symbols(testlib_so)
    undef = [s for s in symbols if not s.is_defined]
    # testlib links against libc, so there should be some UND symbols
    # (at minimum the dynamic linker symbols)
    # Some minimal .so might have none, so just check they're classified correctly
    for s in undef:
        assert s.section == "UND"
        assert not s.is_exportable
