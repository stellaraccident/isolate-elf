#!/usr/bin/env python3
"""Create a release: update version, commit, tag, optionally bump to dev.

Usage:
    # Release 0.1.0, then bump to 0.2.0.dev0:
    python build_tools/make_release.py --version 0.1.0 --bump-dev

    # Release 0.1.0 without post-release bump:
    python build_tools/make_release.py --version 0.1.0

    # Dry run (show what would happen):
    python build_tools/make_release.py --version 0.1.0 --bump-dev --dry-run

After running:
    git push origin main --tags
    # The release.yml workflow handles building and publishing automatically.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = REPO_ROOT / "version.json"
INIT_FILE = REPO_ROOT / "src" / "isolate_elf" / "__init__.py"


def run(cmd: list[str], dry_run: bool = False):
    print(f"+ {' '.join(cmd)}", flush=True)
    if not dry_run:
        subprocess.run(cmd, check=True, cwd=REPO_ROOT)


def read_version_json() -> dict:
    with open(VERSION_FILE) as f:
        return json.load(f)


def write_version(version: str, dry_run: bool = False):
    """Update version in both version.json and src/isolate_elf/__init__.py."""
    # Update version.json
    data = read_version_json()
    data["package-version"] = version
    content = json.dumps(data, indent=2) + "\n"
    print(f"  version.json: package-version = {version}")
    if not dry_run:
        with open(VERSION_FILE, "w") as f:
            f.write(content)

    # Update __init__.py
    init_content = INIT_FILE.read_text()
    new_content = re.sub(
        r'^__version__ = "[^"]*"',
        f'__version__ = "{version}"',
        init_content,
        flags=re.MULTILINE,
    )
    print(f"  __init__.py: __version__ = {version}")
    if not dry_run:
        INIT_FILE.write_text(new_content)


def validate_version(version: str):
    """Validate PEP 440 release version (X.Y.Z)."""
    if not re.match(r"^\d+\.\d+\.\d+$", version):
        sys.exit(f"Invalid version '{version}': expected X.Y.Z (e.g., 0.1.0)")


def next_dev_version(version: str) -> str:
    """Bump minor version and append .dev0: 0.1.0 -> 0.2.0.dev0."""
    parts = version.split(".")
    parts[1] = str(int(parts[1]) + 1)
    parts[2] = "0"
    return ".".join(parts) + ".dev0"


def check_clean_tree(dry_run: bool):
    if dry_run:
        return
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    if result.stdout.strip():
        sys.exit("Working tree is not clean. Commit or stash changes first.")


def main():
    parser = argparse.ArgumentParser(description="Create an isolate-elf release")
    parser.add_argument(
        "--version", required=True, help="Release version (e.g., 0.1.0)"
    )
    parser.add_argument(
        "--bump-dev",
        action="store_true",
        help="After tagging, bump to next dev version",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without making changes",
    )
    args = parser.parse_args()

    validate_version(args.version)
    check_clean_tree(args.dry_run)

    tag = f"v{args.version}"

    # Step 1: Set release version.
    print(f"\n=== Setting version to {args.version} ===")
    write_version(args.version, args.dry_run)
    run(["git", "add", "version.json", "src/isolate_elf/__init__.py"], args.dry_run)
    run(["git", "commit", "-m", f"Release {tag}"], args.dry_run)

    # Step 2: Create tag.
    print(f"\n=== Creating tag {tag} ===")
    run(["git", "tag", tag], args.dry_run)

    # Step 3: Optionally bump to next dev version.
    if args.bump_dev:
        dev = next_dev_version(args.version)
        print(f"\n=== Bumping to {dev} ===")
        write_version(dev, args.dry_run)
        run(["git", "add", "version.json", "src/isolate_elf/__init__.py"], args.dry_run)
        run(["git", "commit", "-m", f"Bump to {dev}"], args.dry_run)

    # Done.
    print(f"\n{'='*60}")
    print("Done! Next steps:")
    print(f"  git push origin main --tags")
    print()
    print("The release.yml workflow will automatically build and publish")
    print("to PyPI when the tag is pushed.")
    if args.dry_run:
        print("\n(dry run — no changes were made)")


if __name__ == "__main__":
    main()
