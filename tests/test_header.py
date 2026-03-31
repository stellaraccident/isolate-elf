"""Tests for redirect header generation."""

from isolib.header import generate_redirect_header
from isolib.model import SymbolRename, SymbolType


def test_basic_header() -> None:
    renames = [
        SymbolRename("ZSTD_decompress", "rocm_ZSTD_decompress", SymbolType.FUNC),
        SymbolRename("ZSTD_compress", "rocm_ZSTD_compress", SymbolType.FUNC),
    ]
    text = generate_redirect_header(renames, "ROCM_ISOLIB_ZSTD_REDIRECT_H")

    assert "#ifndef ROCM_ISOLIB_ZSTD_REDIRECT_H" in text
    assert "#define ROCM_ISOLIB_ZSTD_REDIRECT_H" in text
    assert "#define ZSTD_compress rocm_ZSTD_compress" in text
    assert "#define ZSTD_decompress rocm_ZSTD_decompress" in text
    assert "#endif" in text


def test_header_sorted() -> None:
    renames = [
        SymbolRename("zebra", "rocm_zebra", SymbolType.FUNC),
        SymbolRename("alpha", "rocm_alpha", SymbolType.FUNC),
    ]
    text = generate_redirect_header(renames, "GUARD")
    alpha_pos = text.index("alpha")
    zebra_pos = text.index("zebra")
    assert alpha_pos < zebra_pos


def test_header_includes_objects() -> None:
    renames = [
        SymbolRename("func_sym", "rocm_func_sym", SymbolType.FUNC),
        SymbolRename("data_sym", "rocm_data_sym", SymbolType.OBJECT),
    ]
    text = generate_redirect_header(renames, "GUARD")
    assert "#define data_sym rocm_data_sym" in text
    assert "#define func_sym rocm_func_sym" in text
