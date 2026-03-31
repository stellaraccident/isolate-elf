"""Tool discovery for binutils and related tools."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


class ToolNotFoundError(Exception):
    pass


@dataclass(frozen=True)
class Toolchain:
    """Paths to required external tools."""

    objcopy: Path
    readelf: Path
    assembler: Path  # "as"
    archiver: Path  # "ar"
    cc: Path  # C compiler for linking tests

    @classmethod
    def discover(cls, prefix: str | None = None) -> Toolchain:
        """Find tools on PATH or under a given prefix.

        Args:
            prefix: Optional path prefix (e.g. "/usr/bin/"). If given,
                    tools are looked up as prefix/tool first.
        """

        def find(name: str) -> Path:
            candidates = [name]
            if prefix:
                candidates.insert(0, f"{prefix}/{name}")
            for candidate in candidates:
                found = shutil.which(candidate)
                if found:
                    return Path(found)
            raise ToolNotFoundError(
                f"Required tool '{name}' not found on PATH"
            )

        return cls(
            objcopy=find("objcopy"),
            readelf=find("readelf"),
            assembler=find("as"),
            archiver=find("ar"),
            cc=find("cc"),
        )
