"""Tests for symbol filtering."""

from isolate_elf.filters import classify_symbol
from isolate_elf.model import (
    ElfSymbol,
    SymbolBind,
    SymbolType,
    SymbolVisibility,
    WarningCategory,
)


def _make_sym(
    name: str,
    sym_type: SymbolType = SymbolType.FUNC,
    bind: SymbolBind = SymbolBind.GLOBAL,
    vis: SymbolVisibility = SymbolVisibility.DEFAULT,
    section: str = "14",
    version: str | None = None,
) -> ElfSymbol:
    return ElfSymbol(
        name=name,
        bind=bind,
        sym_type=sym_type,
        visibility=vis,
        section=section,
        version=version,
    )


def test_normal_func_renamed() -> None:
    should, warning = classify_symbol(_make_sym("ZSTD_decompress"))
    assert should is True
    assert warning is None


def test_undefined_skipped() -> None:
    should, _ = classify_symbol(_make_sym("ZSTD_decompress", section="UND"))
    assert should is False


def test_local_skipped() -> None:
    should, _ = classify_symbol(_make_sym("foo", bind=SymbolBind.LOCAL))
    assert should is False


def test_hidden_skipped() -> None:
    should, _ = classify_symbol(
        _make_sym("foo", vis=SymbolVisibility.HIDDEN)
    )
    assert should is False


def test_crt_symbol_skipped() -> None:
    for name in ["malloc", "free", "printf", "_init", "_fini", "__bss_start"]:
        should, _ = classify_symbol(_make_sym(name))
        assert should is False, f"{name} should be skipped"


def test_crt_pattern_skipped() -> None:
    should, _ = classify_symbol(_make_sym("__cxa_finalize"))
    assert should is False

    should, _ = classify_symbol(_make_sym("pthread_mutex_lock"))
    assert should is False


def test_weak_glibc_warns() -> None:
    should, warning = classify_symbol(
        _make_sym("malloc", bind=SymbolBind.WEAK)
    )
    assert should is False
    assert warning is not None
    assert warning.category == WarningCategory.WEAK_OVERRIDE


def test_object_symbol_warns() -> None:
    should, warning = classify_symbol(
        _make_sym("ZSTD_maxCLevel", sym_type=SymbolType.OBJECT)
    )
    assert should is True
    assert warning is not None
    assert warning.category == WarningCategory.OBJECT_SYMBOL


def test_tls_symbol_warns() -> None:
    should, warning = classify_symbol(
        _make_sym("my_tls_var", sym_type=SymbolType.TLS)
    )
    assert should is True
    assert warning is not None
    assert warning.category == WarningCategory.TLS_SYMBOL


def test_ifunc_symbol_warns() -> None:
    should, warning = classify_symbol(
        _make_sym("optimized_memcpy", sym_type=SymbolType.IFUNC)
    )
    assert should is True
    assert warning is not None
    assert warning.category == WarningCategory.IFUNC_SYMBOL


def test_versioned_symbol_warns() -> None:
    should, warning = classify_symbol(
        _make_sym("elf_begin", version="ELFUTILS_0.192")
    )
    assert should is True
    assert warning is not None
    assert warning.category == WarningCategory.VERSIONED_SYMBOL


def test_amdrocm_version_no_warning() -> None:
    should, warning = classify_symbol(
        _make_sym("ZSTD_decompress", version="AMDROCM_SYSDEPS_1.0")
    )
    assert should is True
    assert warning is None


def test_extra_exclude() -> None:
    should, _ = classify_symbol(
        _make_sym("__libelf_private"), extra_exclude=["__libelf_*"]
    )
    assert should is False


def test_extra_exclude_no_match() -> None:
    should, _ = classify_symbol(
        _make_sym("elf_begin"), extra_exclude=["__libelf_*"]
    )
    assert should is True
