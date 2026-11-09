"""Stage 06: assemble a fresh five-year FABLE Calculator country-area table.

The six area components are assembled independently from:
  2000: Stage 04 freshly re-aggregated SPAM2000 + frozen 3S completion replay.
  2005, 2010, 2020: Stage 03 freshly reconstructed mapped 5-arcminute grids.
  2015: Stage 05 freshly interpolated grid-to-country totals.

The historical 34-column Calculator key universe, data-status metadata and
historical zero/zero intensity encoding are explicitly inherited from the
validated versioned candidate, then independently checked. Consequently a
PASS here demonstrates downstream numeric reconstruction, NOT a complete
raw-source-to-Calculator rebuild of all upstream completion and status decisions.

Existing production data, canonical FINAL, and previous stage outputs are read
only. All new files are written into a separate, previously unused folder.

Usage:
  python 06_SPAM_FABLE_clean_start_historical_calculator_assembly.py --check
  python 06_SPAM_FABLE_clean_start_historical_calculator_assembly.py --execute
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import duckdb
except ImportError as exc:
    raise SystemExit("DuckDB is required. Use your pythongeo environment.") from exc

ROOT = Path("data")
VERSION = "v1_0_candidate"
AUDIT = ROOT / "05_publication_audit" / VERSION
STAGE03 = AUDIT / "clean_stage03_full_4years_v2"
STAGE04 = AUDIT / "clean_stage04_frozen_completion_v1"
STAGE05 = AUDIT / "clean_stage05_2015_interpolation_v1"
OUTPUT = AUDIT / "clean_stage06_historical_assembly_v1"

YEARS = (2000, 2005, 2010, 2015, 2020)
YEAR_COUNTRIES = {2000: 177, 2005: 184, 2010: 201, 2015: 203, 2020: 179}
KEYS = ["country_iso3", "year", "fable_product"]
COMPONENTS = [
    "harvested_irrigated_ha", "harvested_rainfed_ha",
    "physical_irrigated_ha", "physical_rainfed_ha",
]
AREAS = [
    "harvested_irrigated_ha", "harvested_rainfed_ha", "harvested_total_ha",
    "physical_irrigated_ha", "physical_rainfed_ha", "physical_total_ha",
]
INTENSITY = [
    "harvesting_intensity_irrigated", "harvesting_intensity_rainfed",
    "harvesting_intensity_total",
]
UNIT_MAP = {
    "harvested_irrigated_ha": "irrigated_harvested_area_1000ha",
    "harvested_rainfed_ha": "rainfed_harvested_area_1000ha",
    "harvested_total_ha": "total_harvested_area_1000ha",
    "physical_irrigated_ha": "irrigated_physical_area_1000ha",
    "physical_rainfed_ha": "rainfed_physical_area_1000ha",
    "physical_total_ha": "total_physical_area_1000ha",
}
CALCULATOR_COLUMNS = [
    *KEYS, *AREAS, *INTENSITY,
    "intensity_status_irrigated", "intensity_status_rainfed", "intensity_status_total",
    "data_status", "temporal_status", "gapfill_method", "source_dataset",
    "source_country_coverage", "fable_area_available", "mapping_complete",
    "calculator_ready", "high_intensity_review_irrigated",
    "high_intensity_review_rainfed", "high_intensity_review_total",
    "high_intensity_review_any", "intensity_quality_flag",
    *UNIT_MAP.values(),
]
FROZEN_METADATA_COLUMNS = [
    col for col in CALCULATOR_COLUMNS
    if col not in KEYS + AREAS + INTENSITY + list(UNIT_MAP.values())
    and not col.startswith("high_intensity_review_")
]
EXPECTED_STAGE03 = "PASS_STAGE03_FRESH_OBSERVED_GRID_REFERENCE_MATCH"
EXPECTED_STAGE04 = "PASS_STAGE04_FROZEN_COMPLETION_REPLAY_AND_REFERENCE"
EXPECTED_STAGE05 = "PASS_STAGE05_FRESH_2015_SPATIAL_AND_COUNTRY_REFERENCE_MATCH"
EXPECTED_NOTEBOOK04 = "PASS_RELEASE_CANDIDATE_SCIENTIFIC_VALIDATION"
FINAL_STATUS = "PASS_STAGE06_FRESH_NUMERIC_ASSEMBLY_WITH_FROZEN_METADATA"

def require(condition: bool, description: str) -> None:
    if not bool(condition):
        raise AssertionError(description)

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as file:
        for block in iter(lambda: file.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def q(path: Path) -> str:
    return path.as_posix().replace("'", "''")

def under(path: Path, directory: Path) -> bool:
    path, directory = path.resolve(), directory.resolve()
    return path == directory or directory in path.parents

def json_file(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))

def bool_array(series: pd.Series) -> np.ndarray:
    if pd.api.types.is_bool_dtype(series):
        result = series.astype("boolean")
    else:
        result = (series.astype("string").str.strip().str.lower().map({
            "true": True, "false": False, "1": True, "0": False,
            "yes": True, "no": False,
        }).astype("boolean"))
    require(result.notna().all(), f"Invalid Boolean source field: {series.name}")
    return result.to_numpy(dtype=bool)

def normalize(frame: pd.DataFrame, name: str) -> pd.DataFrame:
    missing = set(KEYS) - set(frame.columns)
    require(not missing, f"{name}: missing identifiers {sorted(missing)}")
    frame = frame.copy()
    frame["country_iso3"] = frame["country_iso3"].astype("string").str.upper().str.strip()
    frame["fable_product"] = frame["fable_product"].astype("string").str.lower().str.strip()
    frame["year"] = pd.to_numeric(frame["year"], errors="raise").astype(int)
    require(not frame[KEYS].isna().any().any(), f"{name}: null keys")
    require(not frame.duplicated(KEYS).any(), f"{name}: duplicate country-year-product keys")
    return frame

def numeric(frame: pd.DataFrame, columns: list[str], title: str) -> None:
    missing = set(columns) - set(frame.columns)
    require(not missing, f"{title}: missing columns {sorted(missing)}")
    for col in columns:
        frame[col] = pd.to_numeric(frame[col], errors="raise")

def source_paths(args) -> tuple[dict[str, Path], Path]:
    ref = args.reference_root.expanduser().resolve()
    stage03 = args.stage03_root.expanduser().resolve()
    stage04 = args.stage04_root.expanduser().resolve()
    stage05 = args.stage05_root.expanduser().resolve()
    output = args.output_root.expanduser().resolve()

    inputs = {
        "stage03_manifest": stage03 / "03_clean_start_observed_mapping_manifest.json",
        "stage04_manifest": stage04 / "04_stage04_manifest.json",
        "stage05_manifest": stage05 / "05_stage05_manifest.json",
        "stage04_2000_operational": stage04 / "04_SPAM2000_FABLE56_operational_replay.csv.gz",
        "stage05_2015_country": stage05 / "04_final_release" / "calculator"
                                     / "05_SPAM_FABLE_country_2015_fresh.csv.gz",
        "reference_candidate": ref / "04_final_release" / "publication_candidate" / VERSION
                              / "04_FABLE_C_SPAM_historical_2000_2020_MIRCA23_CORRECTED_v1_0_candidate.csv.gz",
        "notebook04_manifest": ref / "04_final_release" / "publication_candidate" / VERSION
                               / "04_release_candidate_manifest.json",
        "canonical_final": ref / "04_final_release" / "calculator"
                           / "04_FABLE_C_SPAM_historical_input_2000_2020_FINAL.csv.gz",
    }
    for year in (2005, 2010, 2020):
        inputs[f"stage03_grid_{year}"] = (
            stage03 / "03_observed_FABLE_5arcmin"
            / f"03_SPAM_FABLE_observed_5arcmin_{year}.parquet"
        )
    protected = [
        ref / "01_native_5arcmin", ref / "02_mapping", ref / "02_country_completion",
        ref / "03_observed_FABLE_5arcmin", ref / "03_reconstruction_audit",
        ref / "04_final_release", stage03, stage04, stage05,
    ]
    require(output != ref, "Cannot use canonical data root as output")
    require(not under(ref, output), "Cannot use a parent of canonical data as output")
    for path in protected:
        require(not under(output, path) and not under(path, output),
                f"Output overlaps protected source: {path}")
    missing = {key: str(path) for key, path in inputs.items() if not path.is_file()}
    require(not missing, f"Stage 06 missing inputs: {missing}")
    return inputs, output

def verify_upstream(paths: dict[str, Path]) -> dict:
    three = json_file(paths["stage03_manifest"])
    four = json_file(paths["stage04_manifest"])
    five = json_file(paths["stage05_manifest"])
    release = json_file(paths["notebook04_manifest"])
    require(three.get("status") == EXPECTED_STAGE03, "Stage 03 manifest did not PASS")
    require(four.get("status") == EXPECTED_STAGE04, "Stage 04 manifest did not PASS")
    require(five.get("status") == EXPECTED_STAGE05, "Stage 05 manifest did not PASS")
    require(release.get("scientific_validation_status") == EXPECTED_NOTEBOOK04,
            "Notebook 04 release candidate did not PASS")
    require(release.get("historical_candidate_sha256") == sha256(paths["reference_candidate"]),
            "Notebook 04 candidate source checksum changed")
    require(release.get("canonical_final_sha256") == sha256(paths["canonical_final"]),
            "Canonical FINAL checksum changed since Notebook 04")
    require(four.get("validation_mode")
            == "fresh_2000_spatial_observation_plus_frozen_3S_country_completion",
            "Stage 04 reconstruction mode is not the verified frozen-completion replay")
    require(five.get("frozen_2015_country_crosswalk_not_rebuilt") is True,
            "Stage 05 country assignment provenance changed")

    rows = three.get("results", [])
    require(len(rows) == 4 and {int(row["year"]) for row in rows} == {2000, 2005, 2010, 2020},
            "Stage 03 has not verified all four observed years")
    for year in (2005, 2010, 2020):
        entry = next(row for row in rows if int(row["year"]) == year)
        path = paths[f"stage03_grid_{year}"]
        require(Path(entry["mapped_output"]).resolve() == path,
                f"Stage 03 mapped {year} file does not match manifest")
        require(entry.get("mapped_output_sha256") == sha256(path),
                f"Stage 03 {year} mapped grid checksum changed")
        require(entry.get("status") == "PASS_STAGE03_FRESH_GRID_AND_REFERENCE_QC",
                f"Stage 03 {year} did not PASS")

    for key, name in [
        ("stage04_2000_operational", "04_SPAM2000_FABLE56_operational_replay.csv.gz"),
    ]:
        require(four.get("output_sha256", {}).get(name) == sha256(paths[key]),
                f"Stage 04 produced source checksum changed: {name}")
    stage05_rel = "04_final_release/calculator/05_SPAM_FABLE_country_2015_fresh.csv.gz"
    stage05_hashes = {str(key).replace("\\", "/"): value
                      for key, value in five.get("output_files_sha256", {}).items()}
    require(stage05_hashes.get(stage05_rel)
            == sha256(paths["stage05_2015_country"]),
            "Stage 05 country output checksum changed")
    require(int(four.get("year_2000_country_product_records", -1)) == 9912,
            "Stage 04 full 2000 country-product panel is missing")
    require(int(four.get("mirca_share_corrections_verified", -1)) == 23,
            "Stage 04 approved MIRCA share corrections are incomplete")
    require(int(four.get("area_and_intensity_reference_failures", -1)) == 0,
            "Stage 04 Calculator area or intensity comparison failed")
    require(int(five.get("calculator_country_product_records", -1)) == 11368,
            "Stage 05 country panel does not contain 11,368 records")
    require(int(five.get("spatial_reference_mismatch_records", -1)) == 0 and
            int(five.get("country_calculator_mismatch_records", -1)) == 0,
            "Stage 05 reference reconciliation contains mismatches")
    return {
        "stage03_status": three["status"],
        "stage04_status": four["status"],
        "stage05_status": five["status"],
        "notebook04_status": release["scientific_validation_status"],
    }

def read_candidate(path: Path) -> pd.DataFrame:
    candidate = pd.read_csv(path, low_memory=False)
    require(list(candidate.columns) == CALCULATOR_COLUMNS,
            "Validated candidate does not have the frozen 34-column schema")
    candidate = normalize(candidate, "validated historical reference")
    require(len(candidate) == 52864 and set(candidate["year"]) == set(YEARS),
            "Validated historical panel is not 52,864 rows / 5 years")
    for year, countries in YEAR_COUNTRIES.items():
        sel = candidate.loc[candidate.year.eq(year)]
        require(len(sel) == countries * 56, f"{year}: candidate country-product count differs")
        require(sel.country_iso3.nunique() == countries and sel.fable_product.nunique() == 56,
                f"{year}: candidate country or 56-product coverage incomplete")
        require(sel.groupby("country_iso3").fable_product.nunique().eq(56).all(),
                f"{year}: incomplete country-product panel")
    numeric(candidate, AREAS + INTENSITY + list(UNIT_MAP.values()), "candidate")
    return candidate

def observed_country_from_grids(paths: dict[str, Path], args) -> pd.DataFrame:
    con = duckdb.connect(":memory:")
    con.execute(f"SET threads={int(args.threads)}")
    con.execute(f"SET memory_limit='{args.memory_limit}'")
    con.execute("SET preserve_insertion_order=false")
    chunks = []
    try:
        for year in (2005, 2010, 2020):
            path = q(paths[f"stage03_grid_{year}"])
            cols = {row[0] for row in con.execute(
                f"DESCRIBE SELECT * FROM read_parquet('{path}')"
            ).fetchall()}
            require(set(KEYS + COMPONENTS).issubset(cols),
                    f"Fresh Stage 03 year {year} lacks required spatial components")
            wrong = con.execute(f"""
                SELECT COUNT(*) FROM read_parquet('{path}')
                WHERE year IS NULL OR CAST(year AS INTEGER) <> {year}
            """).fetchone()[0]
            require(wrong == 0, f"Stage 03 grid file has other years: {year}")
            group = con.execute(f"""
                SELECT UPPER(TRIM(country_iso3)) AS country_iso3,
                       {year} AS year,
                       LOWER(TRIM(fable_product)) AS fable_product,
                       {', '.join(f'SUM(CAST({c} AS DOUBLE)) AS {c}' for c in COMPONENTS)},
                       COUNT(*) AS grid_product_records
                FROM read_parquet('{path}')
                GROUP BY 1,2,3
            """).df()
            group = normalize(group, f"Stage 03 fresh {year} national aggregation")
            numeric(group, COMPONENTS, f"Stage 03 fresh {year}")
            require(not group.empty, f"Stage 03 {year} country aggregates are empty")
            chunks.append(group)
            print(f"Fresh national aggregation {year}: {len(group):,} nonzero product groups", flush=True)
    finally:
        con.close()
    return pd.concat(chunks, ignore_index=True)

def country_2000(paths) -> pd.DataFrame:
    layer = normalize(pd.read_csv(paths["stage04_2000_operational"], low_memory=False),
                      "Stage 04 corrected operational 2000")
    require(len(layer) == 9912 and layer.year.eq(2000).all(),
            "Stage 04 output must have 9,912 year-2000 rows")
    numeric(layer, COMPONENTS, "Stage 04 corrected operational 2000")
    require(layer[COMPONENTS].notna().all().all(),
            "Stage 04 operational layer has missing primary area components")
    layer["grid_product_records"] = pd.NA
    return layer[KEYS + COMPONENTS + ["grid_product_records"]]

def country_2015(paths) -> pd.DataFrame:
    layer = normalize(pd.read_csv(paths["stage05_2015_country"], low_memory=False),
                      "Stage 05 fresh 2015 country output")
    require(layer.year.eq(2015).all(), "Stage 05 country output contains other years")
    numeric(layer, COMPONENTS, "Stage 05 fresh 2015 country data")
    layer["grid_product_records"] = pd.NA
    return layer[KEYS + COMPONENTS + ["grid_product_records"]]

def assemble_areas(candidate, sparse, area_tol):
    sparse = normalize(sparse, "fresh all-year country components")
    numeric(sparse, COMPONENTS, "fresh country areas")
    require(sparse[COMPONENTS].notna().all().all(),
            "Fresh country aggregation has missing components")
    require(sparse["year"].isin(YEARS).all(), "Fresh country areas contain other years")
    keys = set(map(tuple, candidate[KEYS].itertuples(index=False, name=None)))
    supplied = set(map(tuple, sparse[KEYS].itertuples(index=False, name=None)))
    extra = supplied - keys
    require(not extra, f"Fresh country areas contain {len(extra)} out-of-panel records: {list(sorted(extra))[:5]}")
    combined = candidate[KEYS].merge(
        sparse, on=KEYS, how="left", validate="one_to_one", indicator="_fresh_merge"
    )
    is_missing = combined["_fresh_merge"].eq("left_only")
    missing_by_year = combined.loc[is_missing].groupby("year").size().to_dict()
    # A country-product absent in a sparse spatial source can only be
    # materialized as zero when the independently saved Calculator evidence
    # has zero in all four original components. This is verified, not assumed.
    if is_missing.any():
        reference = candidate.loc[is_missing.to_numpy(), COMPONENTS].to_numpy(float)
        zero_allowed = np.isfinite(reference).all(axis=1) & np.isclose(
            reference, 0.0, atol=area_tol, rtol=0
        ).all(axis=1)
        require(zero_allowed.all(),
                f"{int((~zero_allowed).sum())} absent spatial country-product keys lack zero evidence")
        combined.loc[is_missing, COMPONENTS] = 0.0
    require(combined[COMPONENTS].notna().all().all(),
            "Fresh country assembly contains unresolved missing components")
    combined["harvested_total_ha"] = (
        combined.harvested_irrigated_ha + combined.harvested_rainfed_ha
    )
    combined["physical_total_ha"] = (
        combined.physical_irrigated_ha + combined.physical_rainfed_ha
    )
    return combined.drop(columns=["_fresh_merge"]), missing_by_year

def build_calculator(candidate, areas):
    # Explicit boundary: statuses and source-country decisions from the
    # frozen validated candidate, numeric areas from new upstream outputs.
    result = candidate[KEYS + FROZEN_METADATA_COLUMNS].merge(
        areas[KEYS + AREAS], on=KEYS, how="left", validate="one_to_one"
    )
    require(len(result) == len(candidate) and result[AREAS].notna().all().all(),
            "Stage 06 lost keys or area components during assembly")
    source = candidate[KEYS + INTENSITY].copy()
    result = result.merge(source, on=KEYS, how="left", validate="one_to_one", suffixes=("", "_frozen"))
    # Recalculate all intensities from fresh national areas. The historical
    # difference between zero and NaN for 0/0 is inherited from the saved
    # Calculator encoding, never silently changed.
    for part in ("irrigated", "rainfed", "total"):
        h = result[f"harvested_{part}_ha"].to_numpy(dtype=float)
        p = result[f"physical_{part}_ha"].to_numpy(dtype=float)
        original = result[f"harvesting_intensity_{part}"].to_numpy(dtype=float)
        rebuilt = np.full(len(result), np.nan, dtype=float)
        valid = np.isfinite(h) & np.isfinite(p) & (p > 0)
        zeros = np.isfinite(h) & np.isfinite(p) & (h == 0) & (p == 0)
        rebuilt[valid] = h[valid] / p[valid]
        require(np.logical_or(~zeros, np.isnan(original) | (original == 0)).all(),
                f"Unexpected historical zero/zero encoding: {part}")
        rebuilt[zeros & (original == 0)] = 0.0
        result[f"harvesting_intensity_{part}"] = rebuilt
    for component, scaled in UNIT_MAP.items():
        result[scaled] = result[component].to_numpy(dtype=float) / 1000.0
    for part in ("irrigated", "rainfed", "total"):
        result[f"high_intensity_review_{part}"] = (
            result[f"harvesting_intensity_{part}"].to_numpy(float) >= 5
        )
    result["high_intensity_review_any"] = result[[
        f"high_intensity_review_{part}" for part in ("irrigated", "rainfed", "total")
    ]].any(axis=1)
    result = result.loc[:, CALCULATOR_COLUMNS]
    return result

def compare_reference(candidate, fresh, tolerance_ha, tolerance_intensity):
    require(candidate[KEYS].equals(fresh[KEYS]),
            "New historical row order or country-year-product keys differ")
    details = []
    wrong_rows = np.zeros(len(candidate), dtype=bool)
    numeric_columns = AREAS + INTENSITY + list(UNIT_MAP.values())
    for col in CALCULATOR_COLUMNS:
        if col in KEYS:
            continue
        if col in numeric_columns:
            old = pd.to_numeric(candidate[col], errors="raise").to_numpy(float)
            new = pd.to_numeric(fresh[col], errors="raise").to_numpy(float)
            tolerance = (tolerance_ha if col in AREAS else
                         tolerance_ha / 1000.0 if col in UNIT_MAP.values() else
                         tolerance_intensity)
            good = np.isclose(old, new, atol=tolerance, rtol=1e-12 if col in INTENSITY else 0,
                              equal_nan=True)
            magnitude = np.abs(old - new)
            max_difference = float(np.max(magnitude[np.isfinite(magnitude)])) if np.isfinite(magnitude).any() else 0.0
            details.append({"field": col, "failed_rows": int((~good).sum()),
                            "max_difference": max_difference, "tolerance": tolerance})
        else:
            if col in ("source_country_coverage", "fable_area_available", "mapping_complete",
                       "calculator_ready", "high_intensity_review_irrigated",
                       "high_intensity_review_rainfed", "high_intensity_review_total",
                       "high_intensity_review_any"):
                good = bool_array(candidate[col]) == bool_array(fresh[col])
            else:
                good = (candidate[col].astype("string").fillna("<MISSING>")
                        == fresh[col].astype("string").fillna("<MISSING>")).to_numpy(bool)
            details.append({"field": col, "failed_rows": int((~good).sum()),
                            "max_difference": pd.NA, "tolerance": pd.NA})
        wrong_rows |= ~good
    report = pd.DataFrame(details)
    return report, int(wrong_rows.sum())

def formula_qc(frame, tolerance_ha, tolerance_intensity):
    problems = []
    for stem in ("harvested", "physical"):
        diff = (frame[f"{stem}_irrigated_ha"] + frame[f"{stem}_rainfed_ha"]
                - frame[f"{stem}_total_ha"])
        failures = (~np.isfinite(diff.to_numpy(float)) | diff.abs().gt(tolerance_ha)).sum()
        problems.append({"check": f"{stem}_area_conservation", "failures": int(failures)})
    components = frame[AREAS].to_numpy(float)
    problems.append({"check": "missing_or_negative_area", "failures": int(
        (~np.isfinite(components).all(axis=1) | (components < -tolerance_ha).any(axis=1)).sum())})
    for part in ("irrigated", "rainfed", "total"):
        h = frame[f"harvested_{part}_ha"].to_numpy(float)
        p = frame[f"physical_{part}_ha"].to_numpy(float)
        i = frame[f"harvesting_intensity_{part}"].to_numpy(float)
        expected = np.full(len(frame), np.nan)
        defined = np.isfinite(h) & np.isfinite(p) & (p > 0)
        zero_zero = np.isfinite(h) & np.isfinite(p) & (h == 0) & (p == 0)
        expected[defined] = h[defined] / p[defined]
        expected[zero_zero] = 0.0
        ok = np.isclose(expected, i, atol=tolerance_intensity, rtol=1e-12,
                        equal_nan=True) | (zero_zero & np.isnan(i))
        problems.append({"check": f"harvesting_intensity_{part}",
                         "failures": int((~ok).sum())})
        expected_flags = i >= 5
        bad = bool_array(frame[f"high_intensity_review_{part}"]) != expected_flags
        problems.append({"check": f"review_flag_{part}", "failures": int(bad.sum())})
    part_flags = np.column_stack([
        bool_array(frame[f"high_intensity_review_{part}"])
        for part in ("irrigated", "rainfed", "total")
    ])
    problems.append({"check": "review_flag_any", "failures": int((
        bool_array(frame["high_intensity_review_any"]) != part_flags.any(axis=1)).sum())})
    for area, scaled in UNIT_MAP.items():
        match = np.isclose(frame[area].to_numpy(float), frame[scaled].to_numpy(float) * 1000,
                           atol=tolerance_ha, rtol=0, equal_nan=True)
        problems.append({"check": f"unit_conversion_{scaled}", "failures": int((~match).sum())})
    for label in ("calculator_ready", "source_country_coverage"):
        problems.append({"check": f"required_{label}",
                         "failures": int((~bool_array(frame[label])).sum())})
    return pd.DataFrame(problems)

def execute(args, paths, output, upstream):
    if output.exists():
        raise FileExistsError(f"Stage 06 output already exists: {output}")
    candidate = read_candidate(paths["reference_candidate"])
    observed = observed_country_from_grids(paths, args)
    stage04 = country_2000(paths)
    stage05 = country_2015(paths)
    sparse = pd.concat([stage04, observed, stage05], ignore_index=True)
    areas, missing_by_year = assemble_areas(candidate, sparse, args.area_tol)
    fresh = build_calculator(candidate, areas)
    report, mismatch_rows = compare_reference(candidate, fresh, args.area_tol, args.intensity_tol)
    checks = formula_qc(fresh, args.area_tol, args.intensity_tol)
    if report.failed_rows.sum() or mismatch_rows or checks.failures.sum():
        raise AssertionError(
            "Stage 06 historical assembly differs from reference or failed formula QC. "
            f"Reference differences: {report.loc[report.failed_rows.gt(0)].to_dict('records')}; "
            f"QC failures: {checks.loc[checks.failures.gt(0)].to_dict('records')}"
        )
    require(len(fresh) == 52864 and len(fresh.columns) == 34,
            "Fresh historical candidate does not have 52,864 rows and 34 columns")

    output.mkdir(parents=True, exist_ok=False)
    qcdir = output / "qc"
    qcdir.mkdir()
    primary = output / "06_FABLE_C_SPAM_historical_2000_2020_fresh_numeric_replay.csv.gz"
    fresh.to_csv(primary, index=False, compression="gzip")
    report.to_csv(qcdir / "06_34column_reference_comparison.csv", index=False)
    checks.to_csv(qcdir / "06_independent_formula_qc.csv", index=False)
    pd.DataFrame([{"year": year,
                   "countries": YEAR_COUNTRIES[year],
                   "products": 56,
                   "records": YEAR_COUNTRIES[year] * 56,
                   "structural_zero_rows_from_frozen_key_evidence": missing_by_year.get(year, 0)}
                  for year in YEARS]).to_csv(qcdir / "06_country_year_panel_and_zero_evidence.csv", index=False)
    provenance = pd.DataFrame([
        {"column": col, "source": (
            "fresh_stage04_2000_or_stage03_observed_or_stage05_2015"
            if col in COMPONENTS else
            "sum_of_fresh_primary_components" if col in ("harvested_total_ha", "physical_total_ha") else
            "ratio_of_fresh_components_with_frozen_zero_zero_encoding" if col in INTENSITY else
            "fresh_area_divided_by_1000" if col in UNIT_MAP.values() else
            "recomputed_from_fresh_intensity" if col.startswith("high_intensity_review_") else
            "frozen_notebook04_validated_metadata_or_key_universe")}
        for col in CALCULATOR_COLUMNS
    ])
    provenance.to_csv(qcdir / "06_column_level_reconstruction_provenance.csv", index=False)
    # Read back the exported CSV to catch serialization and ordering changes.
    saved = read_candidate_like(primary)
    reread, failed_rows = compare_reference(candidate, saved, args.area_tol, args.intensity_tol)
    require(not reread.failed_rows.sum() and failed_rows == 0,
            "Saved Stage 06 file differs after CSV serialization")
    summary = {
        "stage": "06_five_year_historical_calculator_assembly",
        "status": FINAL_STATUS,
        "reconstructed_primary_area_sources": {
            "2000": "Stage04 fresh spatial observation plus frozen completion",
            "2005_2010_2020": "Stage03 fresh mapped spatial country aggregation",
            "2015": "Stage05 fresh interpolated spatial country aggregation",
        },
        "historical_records": 52864,
        "calculator_columns": 34,
        "fable_products": 56,
        "historical_years": list(YEARS),
        "reference_mismatch_records": mismatch_rows,
        "reference_mismatch_fields": int(report.failed_rows.gt(0).sum()),
        "formula_qc_failures": int(checks.failures.sum()),
        "mirca_approved_2000_share_corrections_inherited_from_stage04": 23,
        "frozen_historical_metadata_and_key_universe_inherited": True,
        "frozen_2000_completion_decisions_inherited_upstream": True,
        "frozen_2015_country_crosswalk_inherited_upstream": True,
        "full_raw_to_final_source_rebuild_verified": False,
        "canonical_final_overwritten": False,
        "public_release_approved": False,
    }
    pd.DataFrame([{k: v for k, v in summary.items() if not isinstance(v, dict)}]).to_csv(
        output / "06_stage06_summary.csv", index=False
    )
    manifest = {
        **summary,
        "upstream_statuses": upstream,
        "input_files_sha256": {name: {"path": str(path), "sha256": sha256(path)}
                               for name, path in paths.items()},
        "output_files_sha256": {str(path.relative_to(output)): sha256(path)
                                for path in output.rglob("*") if path.is_file()},
    }
    (output / "06_stage06_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print("Fresh five-year Calculator rows:", len(fresh))
    print("Frozen schema columns:", len(fresh.columns))
    print("Fresh vs saved candidate mismatch rows:", mismatch_rows)
    print("34-column mismatched fields:", int(report.failed_rows.gt(0).sum()))
    print("Calculator numerical formula failures:", int(checks.failures.sum()))
    print("Final Stage 06 output:", primary)
    print("Manifest:", output / "06_stage06_manifest.json")
    print("STATUS:", FINAL_STATUS)
    print("Entire raw-source-to-release reproduction: PENDING")

def read_candidate_like(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, low_memory=False)
    require(list(frame.columns) == CALCULATOR_COLUMNS, "Stage 06 saved schema changed")
    frame = normalize(frame, "saved Stage 06")
    require(len(frame) == 52864, "Saved Stage 06 record count changed")
    numeric(frame, AREAS + INTENSITY + list(UNIT_MAP.values()), "saved Stage 06")
    return frame

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-root", type=Path, default=ROOT)
    parser.add_argument("--stage03-root", type=Path, default=STAGE03)
    parser.add_argument("--stage04-root", type=Path, default=STAGE04)
    parser.add_argument("--stage05-root", type=Path, default=STAGE05)
    parser.add_argument("--output-root", type=Path, default=OUTPUT)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--memory-limit", default="6GB")
    parser.add_argument("--area-tol", type=float, default=1e-5)
    parser.add_argument("--intensity-tol", type=float, default=1e-10)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--check", action="store_true")
    actions.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    require(args.threads >= 1 and np.isfinite(args.area_tol) and args.area_tol > 0 and
            np.isfinite(args.intensity_tol) and args.intensity_tol > 0,
            "Invalid threads or numerical tolerances")
    paths, output = source_paths(args)
    upstream = verify_upstream(paths)
    print("Stage 06 historical Calculator preflight")
    print("Stage 03, 04 and 05 manifests: PASS")
    print("Required source files:", len(paths), "found and checksummed")
    print("Versioned reference:", paths["reference_candidate"])
    print("New output root:", output)
    print("Canonical FINAL will remain unchanged")
    if args.check:
        print("STATUS: PASS_STAGE06_INPUT_AND_MANIFEST_PREFLIGHT_ONLY")
        return 0
    execute(args, paths, output, upstream)
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("STAGE06_FAILED:", repr(exc), file=sys.stderr)
        raise
