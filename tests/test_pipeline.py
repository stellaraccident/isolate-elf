"""Full round-trip pipeline tests on synthetic testlib."""

from pathlib import Path
import subprocess

import pytest

from isolib.elf import extract_dynamic_symbols
from isolib.model import IsolationConfig, WarningCategory
from isolib.pipeline import IsolationError, isolate_library
from isolib.toolchain import Toolchain


@pytest.fixture(scope="session")
def toolchain() -> Toolchain:
    return Toolchain.discover()


class TestBasicPipeline:
    """Test the full pipeline on the unversioned testlib."""

    def test_isolate_produces_all_artifacts(
        self, testlib_so: Path, tmp_path: Path, toolchain: Toolchain
    ) -> None:
        config = IsolationConfig(
            input_so=testlib_so,
            prefix="rocm_",
            output_dir=tmp_path,
            output_name="testlib",
        )
        result = isolate_library(config, toolchain)

        assert result.prefixed_so.exists()
        assert result.stubs_archive.exists()
        assert result.linker_script.exists()
        assert result.redirect_header.exists()

    def test_prefixed_so_has_no_original_names(
        self, testlib_so: Path, tmp_path: Path, toolchain: Toolchain
    ) -> None:
        config = IsolationConfig(
            input_so=testlib_so,
            prefix="rocm_",
            output_dir=tmp_path,
            output_name="testlib",
        )
        result = isolate_library(config, toolchain)

        symbols = extract_dynamic_symbols(result.prefixed_so, toolchain.readelf)
        names = {s.name for s in symbols if s.is_defined}

        # All testlib symbols should be prefixed
        assert "rocm_testlib_add" in names
        assert "rocm_testlib_multiply" in names
        assert "rocm_testlib_greeting" in names
        assert "rocm_testlib_version" in names

        # Originals should be gone
        assert "testlib_add" not in names
        assert "testlib_multiply" not in names

    def test_autoconf_link_test(
        self, testlib_so: Path, tmp_path: Path, toolchain: Toolchain
    ) -> None:
        """Simulate an autoconf AC_CHECK_LIB link test."""
        config = IsolationConfig(
            input_so=testlib_so,
            prefix="rocm_",
            output_dir=tmp_path,
            output_name="testlib",
        )
        result = isolate_library(config, toolchain)

        # Write a minimal autoconf-style test program
        test_c = tmp_path / "test_link.c"
        test_c.write_text(
            'extern char testlib_add(); int main() { testlib_add(); return 0; }\n'
        )
        test_bin = tmp_path / "test_link"

        # Link with -ltestlib, which should find the linker script
        proc = subprocess.run(
            [
                str(toolchain.cc),
                "-o", str(test_bin),
                str(test_c),
                f"-L{tmp_path}",
                "-ltestlib",
                f"-Wl,-rpath,{tmp_path}",
            ],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, f"Link failed:\n{proc.stderr}"
        assert test_bin.exists()

    def test_direct_link_with_header(
        self, testlib_so: Path, tmp_path: Path, toolchain: Toolchain
    ) -> None:
        """Test linking with redirect header (compile-time redirection)."""
        config = IsolationConfig(
            input_so=testlib_so,
            prefix="rocm_",
            output_dir=tmp_path,
            output_name="testlib",
        )
        result = isolate_library(config, toolchain)

        test_c = tmp_path / "test_direct.c"
        test_c.write_text(
            f'#include "{result.redirect_header}"\n'
            'extern int testlib_add(int, int);\n'
            'int main() { return testlib_add(1, 2) != 3; }\n'
        )
        test_bin = tmp_path / "test_direct"

        # Link directly against the prefixed .so (SONAME was rewritten
        # to match the filename, so runtime linker finds it correctly)
        proc = subprocess.run(
            [
                str(toolchain.cc),
                "-o", str(test_bin),
                str(test_c),
                f"-L{tmp_path}",
                f"-l:{result.prefixed_so.name}",
                f"-Wl,-rpath,{tmp_path}",
            ],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, f"Link failed:\n{proc.stderr}"

        # Run it — no symlinks needed, SONAME matches filename
        run = subprocess.run([str(test_bin)], capture_output=True)
        assert run.returncode == 0, f"Direct-linked binary failed: {run.stderr.decode()}"

    def test_negative_link_without_stubs(
        self, testlib_so: Path, tmp_path: Path, toolchain: Toolchain
    ) -> None:
        """Linking directly against prefixed .so without header/stubs should fail."""
        config = IsolationConfig(
            input_so=testlib_so,
            prefix="rocm_",
            output_dir=tmp_path,
            output_name="testlib",
        )
        result = isolate_library(config, toolchain)

        test_c = tmp_path / "test_neg.c"
        test_c.write_text(
            'extern int testlib_add(int, int);\n'
            'int main() { return testlib_add(1, 2); }\n'
        )
        test_bin = tmp_path / "test_neg"

        proc = subprocess.run(
            [
                str(toolchain.cc),
                "-o", str(test_bin),
                str(test_c),
                f"-l:{result.prefixed_so.name}",
                f"-L{tmp_path}",
            ],
            capture_output=True,
            text=True,
        )
        # Should fail — testlib_add doesn't exist in the prefixed .so
        assert proc.returncode != 0, "Link should fail without stubs/header"


class TestWerror:
    """Test --werror behavior."""

    def test_werror_fails_on_object_symbol(
        self, testlib_so: Path, tmp_path: Path, toolchain: Toolchain
    ) -> None:
        config = IsolationConfig(
            input_so=testlib_so,
            prefix="rocm_",
            output_dir=tmp_path,
            output_name="testlib",
            werror=True,
        )
        with pytest.raises(IsolationError, match="object-symbol"):
            isolate_library(config, toolchain)

    def test_werror_with_allow(
        self, testlib_so: Path, tmp_path: Path, toolchain: Toolchain
    ) -> None:
        config = IsolationConfig(
            input_so=testlib_so,
            prefix="rocm_",
            output_dir=tmp_path,
            output_name="testlib",
            werror=True,
            allow_categories={WarningCategory.OBJECT_SYMBOL},
        )
        # Should succeed — object-symbol is allowed
        result = isolate_library(config, toolchain)
        assert result.prefixed_so.exists()


class TestVersionedPipeline:
    """Test pipeline on versioned testlib."""

    def test_versioned_rename(
        self, testlib_versioned_so: Path, tmp_path: Path, toolchain: Toolchain
    ) -> None:
        config = IsolationConfig(
            input_so=testlib_versioned_so,
            prefix="rocm_",
            output_dir=tmp_path,
            output_name="testlib_versioned",
        )
        result = isolate_library(config, toolchain)

        symbols = extract_dynamic_symbols(result.prefixed_so, toolchain.readelf)
        names = {s.name for s in symbols if s.is_defined}

        assert "rocm_versioned_func_a" in names
        assert "rocm_versioned_func_b" in names
        assert "versioned_func_a" not in names

        # Version tags preserved
        func_a = [s for s in symbols if s.name == "rocm_versioned_func_a"]
        assert func_a[0].version == "TESTVER_1.0"
