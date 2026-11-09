#!/usr/bin/env python3
"""Check local SPAM-FABLE inputs and optionally collect them from existing folders.

The public input inventory uses relative paths only. This helper never downloads
third-party datasets and never overwrites existing files.
"""
from __future__ import annotations
import argparse
import csv
import shutil
from pathlib import Path


def read_inventory(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    fields = {"source_group", "source_relative_path", "destination_under_SPAM_FABLE_data"}
    if not rows or not fields.issubset(rows[0]):
        raise ValueError("Expected public required-inputs manifest with relative paths")
    return rows


def relative_path(text: str) -> Path:
    value = text.replace("\\", "/")
    rel = Path(value)
    if not value or rel.is_absolute() or ".." in rel.parts or ":" in value:
        raise ValueError(f"Unsafe inventory relative path: {text}")
    return rel


def run() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="Verify local destination, or available source when provided")
    action.add_argument("--copy", action="store_true", help="Copy from explicitly given source folders")
    parser.add_argument("--destination", required=True, type=Path, help="SPAM-FABLE/data folder")
    parser.add_argument("--source-root", type=Path, help="Folder containing SPAM/, mapping_audit/, SPAM_FABLE_FINAL/")
    parser.add_argument("--legacy-root", type=Path, help="Folder containing the two 06_SPAM2000* MIRCA files")
    parser.add_argument("--manifest", type=Path, default=Path(__file__).resolve().parent / "SPAM-required-files.csv")
    parser.add_argument("--max-size-mb", type=float, default=None, help="Copy only files at or below size limit")
    args = parser.parse_args()
    if args.copy and args.source_root is None:
        parser.error("--copy requires --source-root (the location of existing SPAM source/evidence folders)")
    dest = args.destination.expanduser().resolve()
    if args.copy and not (dest.parent / "run-spam.py").is_file():
        parser.error("Destination must be the data/ folder under SPAM-FABLE/")
    roots = {"main": args.source_root, "legacy": args.legacy_root}
    counts = {"present": 0, "available": 0, "missing": 0, "copied": 0, "skipped": 0, "collision": 0}
    total = 0
    for i, row in enumerate(read_inventory(args.manifest), 1):
        dst_rel = relative_path(row["destination_under_SPAM_FABLE_data"])
        src_rel = relative_path(row["source_relative_path"])
        group = row["source_group"]
        if group not in roots:
            raise ValueError(f"Unknown source group {group!r}")
        target = dest / dst_rel
        source = roots[group] / src_rel if roots[group] else None
        if target.is_file():
            if source is not None and source.is_file() and target.stat().st_size != source.stat().st_size:
                counts["collision"] += 1
                print(f"DIFFERENT [{i:02d}/52] {dst_rel} (sizes differ, not overwritten)")
            else:
                counts["present"] += 1
                print(f"PRESENT   [{i:02d}/52] {dst_rel}")
            continue
        if source is None or not source.is_file():
            counts["missing"] += 1
            print(f"MISSING   [{i:02d}/52] {dst_rel} (source not configured or unavailable)")
            continue
        size = source.stat().st_size
        total += size
        if args.check:
            counts["available"] += 1
            print(f"AVAILABLE [{i:02d}/52] {dst_rel} ({size / 1024**2:.1f} MiB)")
        elif args.max_size_mb is not None and size > args.max_size_mb * 1024**2:
            counts["skipped"] += 1
            print(f"SKIPPED   [{i:02d}/52] {dst_rel} (> size limit)")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            counts["copied"] += 1
            print(f"COPIED    [{i:02d}/52] {dst_rel}")
    print("\nCounts:", counts)
    print("Expected files:", sum(counts.values()), "of 52")
    print("No source files modified. Existing destinations never overwritten.")
    if args.check:
        return 0 if counts["missing"] == 0 and counts["collision"] == 0 else 1
    return 0 if counts["missing"] == 0 and counts["collision"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run())
