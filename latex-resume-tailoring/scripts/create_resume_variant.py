#!/usr/bin/env python3
"""Create a versioned editable copy of a LaTeX resume."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug or "target-role"


def next_variant_dir(root: Path, date_prefix: str, label: str) -> Path:
    for index in range(1, 100):
        candidate = root / f"{date_prefix}-{label}-v{index:02d}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Too many variants already exist for {date_prefix}-{label}")


def copy_resume(source: Path, variant_dir: Path, copy_assets: bool) -> Path:
    variant_dir.mkdir(parents=True, exist_ok=False)
    target = variant_dir / source.name

    if copy_assets:
        for item in source.parent.iterdir():
            if item.name in {variant_dir.parent.name, ".git", "__pycache__"}:
                continue
            destination = variant_dir / item.name
            if item.is_dir():
                shutil.copytree(item, destination, ignore=shutil.ignore_patterns(".git"))
            elif item.is_file():
                shutil.copy2(item, destination)
    else:
        shutil.copy2(source, target)

    if target.name != "resume.tex":
        canonical = variant_dir / "resume.tex"
        if canonical.exists():
            raise FileExistsError(f"Cannot create canonical resume.tex: {canonical}")
        target.rename(canonical)
        target = canonical

    return target


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Copy a source resume into a versioned resume_variants folder."
    )
    parser.add_argument("resume_tex", type=Path, help="Path to the source .tex resume")
    parser.add_argument(
        "--label",
        default="target-role",
        help="Short company/role label used in the generated folder name.",
    )
    parser.add_argument(
        "--out-root",
        type=Path,
        default=None,
        help="Variant root directory. Defaults to <resume-dir>/resume_variants.",
    )
    parser.add_argument(
        "--copy-assets",
        action="store_true",
        help="Copy sibling files/directories needed by templates with local assets.",
    )
    args = parser.parse_args()

    source = args.resume_tex.resolve()
    if not source.exists():
        print(json.dumps({"ok": False, "error": f"File not found: {source}"}, indent=2))
        return 1
    if source.suffix.lower() != ".tex":
        print(json.dumps({"ok": False, "error": f"Expected a .tex file: {source}"}, indent=2))
        return 1

    label = slugify(args.label)
    date_prefix = datetime.now().strftime("%Y%m%d")
    root = (args.out_root.resolve() if args.out_root else source.parent / "resume_variants")
    root.mkdir(parents=True, exist_ok=True)
    variant_dir = next_variant_dir(root, date_prefix, label)
    target = copy_resume(source, variant_dir, args.copy_assets)

    print(
        json.dumps(
            {
                "ok": True,
                "source": str(source),
                "source_unchanged": True,
                "variant_dir": str(variant_dir),
                "variant_resume_tex": str(target),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
