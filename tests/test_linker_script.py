"""Tests for linker script generation."""

from isolate_elf.linker_script import generate_linker_script


def test_basic_script() -> None:
    text = generate_linker_script(
        "librocm_zstd_stubs.a",
        "librocm_sysdeps_zstd.so.1",
    )
    assert "INPUT(" in text
    assert "librocm_zstd_stubs.a" in text
    assert "AS_NEEDED(librocm_sysdeps_zstd.so.1)" in text
    assert "do not edit" in text


def test_script_is_valid_ld_syntax() -> None:
    text = generate_linker_script("stubs.a", "real.so.1")
    # Should contain exactly one INPUT() directive
    assert text.count("INPUT(") == 1
    assert text.count(")") >= 2  # outer INPUT + inner AS_NEEDED
