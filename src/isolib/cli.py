"""Command-line interface for isolib."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from isolib.model import IsolationConfig, WarningCategory
from isolib.pipeline import IsolationError, isolate_library
from isolib.toolchain import Toolchain


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="isolib",
        description="ELF symbol isolation for bundled system dependencies",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose logging"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- isolate ---
    p_iso = sub.add_parser("isolate", help="Isolate symbols in a shared library")
    p_iso.add_argument("input", type=Path, help="Input .so file")
    p_iso.add_argument(
        "--prefix", default="rocm_", help="Symbol prefix (default: rocm_)"
    )
    p_iso.add_argument(
        "--output-dir", "-o", type=Path, required=True, help="Output directory"
    )
    p_iso.add_argument(
        "--name", required=True, help="Library name (e.g. 'zstd')"
    )
    p_iso.add_argument(
        "--soname", default=None, help="Override SONAME for prefixed .so"
    )
    p_iso.add_argument(
        "--exclude", action="append", default=[],
        help="Glob pattern for symbols to exclude (repeatable)",
    )
    p_iso.add_argument(
        "-Werror", "--werror", action="store_true",
        help="Treat warnings as errors",
    )
    for cat in WarningCategory:
        p_iso.add_argument(
            f"--allow-{cat.value}",
            action="append_const",
            const=cat,
            dest="allow_categories",
            help=f"Allow {cat.value} warnings even with --werror",
        )
    p_iso.add_argument(
        "--arch", default="x86_64",
        choices=["x86_64", "aarch64"],
        help="Target architecture (default: x86_64)",
    )

    # --- inspect ---
    p_insp = sub.add_parser(
        "inspect", help="Show symbols that would be renamed (dry run)"
    )
    p_insp.add_argument("input", type=Path, help="Input .so file")
    p_insp.add_argument(
        "--prefix", default="rocm_", help="Symbol prefix (default: rocm_)"
    )
    p_insp.add_argument(
        "--exclude", action="append", default=[],
        help="Glob pattern for symbols to exclude (repeatable)",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if args.command == "isolate":
        _cmd_isolate(args)
    elif args.command == "inspect":
        _cmd_inspect(args)


def _cmd_isolate(args: argparse.Namespace) -> None:
    if not args.input.exists():
        print(f"Error: input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    config = IsolationConfig(
        input_so=args.input,
        prefix=args.prefix,
        output_dir=args.output_dir,
        output_name=args.name,
        soname=args.soname,
        extra_exclude_patterns=args.exclude,
        werror=args.werror,
        allow_categories=set(args.allow_categories or []),
        arch=args.arch,
    )

    tc = Toolchain.discover()

    try:
        result = isolate_library(config, tc)
    except IsolationError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Prefixed .so:     {result.prefixed_so}")
    print(f"Stubs archive:    {result.stubs_archive}")
    print(f"Linker script:    {result.linker_script}")
    print(f"Redirect header:  {result.redirect_header}")
    print(f"Renamed symbols:  {len(result.renamed_symbols)}")
    print(f"Warnings:         {len(result.warnings)}")


def _cmd_inspect(args: argparse.Namespace) -> None:
    if not args.input.exists():
        print(f"Error: input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    from isolib.elf import extract_dynamic_symbols
    from isolib.filters import classify_symbol

    tc = Toolchain.discover()
    symbols = extract_dynamic_symbols(args.input, tc.readelf)

    rename_count = 0
    skip_count = 0
    warn_count = 0

    for sym in symbols:
        should_prefix, warning = classify_symbol(sym, args.exclude)
        if should_prefix:
            rename_count += 1
            marker = "RENAME"
            new_name = f" -> {args.prefix}{sym.name}"
        else:
            skip_count += 1
            marker = "SKIP  "
            new_name = ""

        warn_tag = ""
        if warning:
            warn_count += 1
            warn_tag = f" [{warning.category.value}]"

        ver = ""
        if sym.version:
            sep = "@@" if sym.version_default else "@"
            ver = f"{sep}{sym.version}"

        print(
            f"  {marker} {sym.sym_type.value:10s} {sym.bind.value:6s} "
            f"{sym.name}{ver}{new_name}{warn_tag}"
        )

    print(f"\nTotal: {len(symbols)} symbols, {rename_count} rename, "
          f"{skip_count} skip, {warn_count} warnings")
