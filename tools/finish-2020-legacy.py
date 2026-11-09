#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Validate and finalize an already raw-reconstructed 2020 native SPAM file.

This avoids rerunning ~40+ SPAM2020 CSV source chunks after an isolated
source-total representation mismatch in the otherwise validated v3 output.

Input is the already rebuilt Stage 01 v3 native 2020 Parquet, not the saved
canonical reference. The two published source-total fields are normalized
from NULL to 0.0. No area, intensity, key or provenance field is changed.
The full native scientific/metadata reference comparison is run again using
Stage 01 v4's unchanged QC comparator, plus local scientific QC and source
conservation. This is a post-processing continuation of the actual raw-source
reconstruction; it is not a separate complete raw-source rerun of Stage 01 v4.

Usage:
    python 01_SPAM_FABLE_stage01_2020_finalize_existing_v4.py --check
    python 01_SPAM_FABLE_stage01_2020_finalize_existing_v4.py --execute
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import duckdb

ROOT = Path("data/SPAM_FABLE_FINAL")
BASE = ROOT / "05_publication_audit" / "v1_0_candidate"
V3_RUN = BASE / "clean_stage01_2010_2020_v3"
DEFAULT_OUTPUT = BASE / "clean_stage01_2020_finalize_v4"
NATIVE_NAME = "01_SPAM_native_5arcmin_2020.parquet"
KEYS = ("grid_id_5min", "country_iso3", "year", "source_product_code")
FIELDS = ("harvested_total_source_ha", "physical_total_source_ha")
EXPECTED = {"harvested_total_source_ha": 25, "physical_total_source_ha": 33070}

def sql_path(path: Path) -> str:
    return path.as_posix().replace("'", "''")

def require_file(path: Path) -> Path:
    if not path.is_file():
        raise FileNotFoundError(path)
    return path

def load_stage_01_v4(script: Path):
    spec = importlib.util.spec_from_file_location("stage01_native_v4", str(script))
    if spec is None or spec.loader is None:
        raise ImportError("Cannot import Stage 01 v4 script")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def source_null_audit(con: duckdb.DuckDBPyConnection, fresh: Path,
                      reference: Path) -> dict:
    # A reference value is used only as a strict check, never as a replacement.
    # The output transformation itself is the deterministic COALESCE(NULL, 0.0).
    join = ", ".join(KEYS)
    expressions = ["COUNT(*) AS matched_rows"]
    for field in FIELDS:
        expressions += [
            f"COUNT(*) FILTER (WHERE a.{field} IS NULL AND b.{field} = 0.0) AS fix_{field}",
            f"COUNT(*) FILTER (WHERE a.{field} IS NULL AND "
            f"(b.{field} IS NULL OR b.{field} <> 0.0)) AS unsafe_{field}",
            f"COUNT(*) FILTER (WHERE a.{field} IS NULL) AS total_null_{field}",
        ]
    query = f"""
        SELECT {', '.join(expressions)}
        FROM read_parquet('{sql_path(fresh)}') a
        INNER JOIN read_parquet('{sql_path(reference)}') b USING ({join})
    """
    result = {name: int(value) for name, value in
              con.execute(query).df().iloc[0].to_dict().items()}
    for field, expected in EXPECTED.items():
        if result[f"fix_{field}"] != expected:
            raise AssertionError(
                f"Unexpected {field} NULL->reference-zero count: "
                f"{result[f'fix_{field}']} (expected {expected})"
            )
        if result[f"unsafe_{field}"] != 0:
            raise AssertionError(
                f"Not all missing {field} values are reference-zero; refusing normalization"
            )
        if result[f"total_null_{field}"] != expected:
            raise AssertionError(
                f"Additional {field} NULLs need investigation before normalization"
            )
    return result

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--source-run", type=Path, default=None)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--v4-script", type=Path, default=None)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--memory-limit", default="6GB")
    args = parser.parse_args()

    root = args.root.expanduser().resolve()
    base = root / "05_publication_audit" / "v1_0_candidate"
    source_run = (args.source_run or base / "clean_stage01_2010_2020_v3").resolve()
    output_root = (args.output_root or base / "clean_stage01_2020_finalize_v4").resolve()
    script = (args.v4_script or Path(__file__).resolve().parents[1] / "scripts" / "01-native-spam.py").resolve()
    input_file = require_file(source_run / "01_native_5arcmin" / NATIVE_NAME)
    reference = require_file(root / "01_native_5arcmin" / NATIVE_NAME)
    v3_qc_file = require_file(source_run / "01_native_5arcmin" / "qc" /
                              "01_reference_compare_2020.json")
    raw_h = require_file(source_run / "01_native_5arcmin" / "_temp_sparse" /
                         "SPAM_2020_harvested_sparse_5arcmin.csv.gz")
    raw_a = require_file(source_run / "01_native_5arcmin" / "_temp_sparse" /
                         "SPAM_2020_physical_sparse_5arcmin.csv.gz")
    require_file(script)
    if output_root == root or output_root == source_run or output_root == root / "01_native_5arcmin":
        raise ValueError("Output must be a new isolated directory")
    if output_root in source_run.parents or source_run in output_root.parents:
        raise ValueError("Output may not contain the previous run")
    if output_root.exists() and any(output_root.iterdir()):
        raise FileExistsError(f"Refusing to overwrite existing output: {output_root}")

    qc_report = json.loads(v3_qc_file.read_text(encoding="utf-8"))
    if int(qc_report.get("missing_or_extra_native_keys", -1)) != 0 or int(qc_report.get("mismatched_records", -1)) != 33070:
        raise AssertionError("v3 2020 QC differs from documented mismatch state")
    failures = {k[5:]: int(v) for k, v in qc_report.items() if k.startswith("fail_") and int(v) > 0}
    if failures != {"harvested_total_source_ha": 25,
                    "physical_total_source_ha": 33070}:
        raise AssertionError(f"Unexpected previous QC failing fields: {failures}")

    stage = load_stage_01_v4(script)
    with duckdb.connect(":memory:") as con:
        con.execute(f"SET threads={int(args.threads)}")
        con.execute(f"SET memory_limit='{args.memory_limit}'")
        audits = source_null_audit(con, input_file, reference)
        print("Stage 01 | 2020 targeted source-total completion")
        print("Fresh original native 2020:", input_file)
        print("Read-only reference:", reference)
        print("2020 source-total NULL reconciliation:", audits)
        print("Output:", output_root)
        if args.check:
            print("STATUS: PASS_STAGE01_2020_NORMALIZATION_PREFLIGHT_ONLY")
            return 0

        target_dir = output_root / "01_native_5arcmin"
        target_dir.mkdir(parents=True, exist_ok=False)
        target_file = target_dir / NATIVE_NAME
        # This is not a reference-value copy; the operation is the same
        # data-independent source-total normalization implemented in v4.
        con.execute(f"""
            COPY (
                SELECT * REPLACE (
                    COALESCE(harvested_total_source_ha, 0.0)
                        AS harvested_total_source_ha,
                    COALESCE(physical_total_source_ha, 0.0)
                        AS physical_total_source_ha
                )
                FROM read_parquet('{sql_path(input_file)}')
            ) TO '{sql_path(target_file)}'
            (FORMAT PARQUET, COMPRESSION ZSTD)
        """)
        scientific = stage.local_qc(con, target_file, 2020)
        conservation = [
            stage.source_conservation(con, raw_h, target_file, "H", 2020),
            stage.source_conservation(con, raw_a, target_file, "A", 2020),
        ]
        identity = stage.compare_reference(con, target_file, reference, 2020)
        if identity["status"] != "PASS_NATIVE_SPATIAL_AND_PROVENANCE_REFERENCE_MATCH":
            raise AssertionError(
                "Postprocessed 2020 still differs; see failed fresh output and manifest check"
            )
        record = {
            "year": 2020,
            "kind": "raw_source_2020_v3_output_plus_deterministic_v4_source_total_normalization",
            "is_full_v4_raw_csv_rerun": False,
            "source_native_parquet": str(input_file),
            "source_native_sha256": stage.sha256(input_file),
            "source_sparse_h": str(raw_h),
            "source_sparse_a": str(raw_a),
            "reference": str(reference),
            "reference_sha256": stage.sha256(reference),
            "normalized_output": str(target_file),
            "normalized_sha256": stage.sha256(target_file),
            "null_to_zero_representation_count": {k: audits[f"fix_{k}"] for k in FIELDS},
            "all_non_source_total_fields_derived_from_raw_v3_unchanged": True,
            "raw_sparse_conservation": conservation,
            "local_qc": scientific,
            "full_reference_comparison": identity,
            "status": "PASS_STAGE01_2020_SOURCE_DERIVED_NORMALIZATION_REFERENCE_MATCH",
        }
        (output_root / "01_stage01_2020_normalization_manifest.json").write_text(
            json.dumps(record, indent=2, default=str), encoding="utf-8"
        )
        print("Source-total normalization counts:", record["null_to_zero_representation_count"])
        print("2020 rows:", scientific["rows"])
        print("Missing/extra spatial keys:", identity["missing_or_extra_native_keys"])
        print("Remaining mismatched records:", identity["mismatched_records"])
        print("STATUS:", record["status"])
        return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("STAGE01_V4_FINALIZATION_FAILED:", repr(exc), file=sys.stderr)
        raise
