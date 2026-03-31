"""Tests for symbol renaming via objcopy."""

from pathlib import Path

from isolib.elf import extract_dynamic_symbols
from isolib.model import SymbolRename, SymbolType
from isolib.rename import generate_redefine_map, rename_symbols


def test_generate_redefine_map() -> None:
    renames = [
        SymbolRename("foo", "rocm_foo", SymbolType.FUNC),
        SymbolRename("bar", "rocm_bar", SymbolType.OBJECT),
    ]
    text = generate_redefine_map(renames)
    assert "foo rocm_foo\n" in text
    assert "bar rocm_bar\n" in text


def test_rename_symbols(testlib_so: Path, tmp_path: Path) -> None:
    output = tmp_path / "libtestlib_prefixed.so.1"
    renames = [
        SymbolRename("testlib_add", "rocm_testlib_add", SymbolType.FUNC),
        SymbolRename("testlib_multiply", "rocm_testlib_multiply", SymbolType.FUNC),
        SymbolRename("testlib_greeting", "rocm_testlib_greeting", SymbolType.FUNC),
        SymbolRename("testlib_version", "rocm_testlib_version", SymbolType.OBJECT),
    ]

    rename_symbols(testlib_so, output, renames)

    assert output.exists()
    assert output.stat().st_size > 0

    # Verify renamed symbols appear in output
    symbols = extract_dynamic_symbols(output)
    names = {s.name for s in symbols}
    assert "rocm_testlib_add" in names
    assert "rocm_testlib_multiply" in names
    assert "rocm_testlib_greeting" in names
    assert "rocm_testlib_version" in names

    # Originals should be gone
    assert "testlib_add" not in names
    assert "testlib_multiply" not in names


def test_rename_versioned_symbols(testlib_versioned_so: Path, tmp_path: Path) -> None:
    output = tmp_path / "libtestlib_versioned_prefixed.so.1"
    renames = [
        SymbolRename("versioned_func_a", "rocm_versioned_func_a", SymbolType.FUNC, "TESTVER_1.0"),
        SymbolRename("versioned_func_b", "rocm_versioned_func_b", SymbolType.FUNC, "TESTVER_1.0"),
    ]

    rename_symbols(testlib_versioned_so, output, renames)

    symbols = extract_dynamic_symbols(output)
    names = {s.name for s in symbols}
    assert "rocm_versioned_func_a" in names
    assert "rocm_versioned_func_b" in names
    assert "versioned_func_a" not in names

    # Version tags should be preserved
    func_a = [s for s in symbols if s.name == "rocm_versioned_func_a"]
    assert len(func_a) == 1
    assert func_a[0].version == "TESTVER_1.0"
