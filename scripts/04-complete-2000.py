"""Stage 04: Replay SPAM2000 country completion and approved MIRCA shares.

The observed SPAM2000 component is newly aggregated from the Stage 03 clean
5-arcminute output. The 2000 completion decisions for products not supported
by SPAM2000 are taken from the frozen, audited Cell 3S scientific and
operational tables. This is a replay with frozen completion inputs, not a
fresh derivation of all FAOSTAT, MIRCA, and later-SPAM evidence decisions.

The 23 same-year MIRCA crop-magnitude fallback cases are checked separately
from the 23 approved MIRCA irrigation-share harmonizations. The 26
Calculator-only operational zeros remain missing in the scientific layer.

No production files are changed. All outputs go to an empty directory.

Examples:
    python 04_SPAM_FABLE_clean_start_2000_country_completion.py --check
    python 04_SPAM_FABLE_clean_start_2000_country_completion.py --execute
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import duckdb
import numpy as np
import pandas as pd

REFERENCE_ROOT = Path("data")
LEGACY_ROOT = Path("data/legacy/06_harmonized_irrigation")
KEYS = ["country_iso3", "fable_product"]
AREAS = [
    "harvested_irrigated_ha", "harvested_rainfed_ha", "harvested_total_ha",
    "physical_irrigated_ha", "physical_rainfed_ha", "physical_total_ha",
]
COMPONENTS = [
    "harvested_irrigated_ha", "harvested_rainfed_ha",
    "physical_irrigated_ha", "physical_rainfed_ha",
]
INTENSITIES = ["intensity_irrigated", "intensity_rainfed", "intensity_total"]
EXPECTED_STAGE03_STATUS = "PASS_STAGE03_FRESH_OBSERVED_GRID_REFERENCE_MATCH"
EXPECTED_STAGE04_STATUS = "PASS_STAGE04_FROZEN_COMPLETION_REPLAY_AND_REFERENCE"

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as src:
        for chunk in iter(lambda: src.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def under(path: Path, base: Path) -> bool:
    path, base = path.resolve(), base.resolve()
    return path == base or base in path.parents

def quote(path: Path) -> str:
    return path.as_posix().replace("'", "''")

def prepare(frame: pd.DataFrame, name: str, require_year=False) -> pd.DataFrame:
    frame = frame.copy()
    missing = set(KEYS) - set(frame.columns)
    if missing:
        raise ValueError(f"{name}: missing keys {sorted(missing)}")
    frame["country_iso3"] = frame["country_iso3"].astype("string").str.strip().str.upper()
    frame["fable_product"] = frame["fable_product"].astype("string").str.strip().str.lower()
    if frame[KEYS].isna().any().any() or frame.duplicated(KEYS).any():
        raise AssertionError(f"{name}: null or duplicate country-product keys")
    if require_year and ("year" not in frame or not pd.to_numeric(frame["year"], errors="raise").eq(2000).all()):
        raise AssertionError(f"{name}: expected only year 2000")
    return frame

def boolean(series: pd.Series, name: str) -> np.ndarray:
    b = series.astype("string").str.strip().str.lower().map({
        "true": True, "false": False, "1": True, "0": False,
    })
    if b.isna().any():
        raise AssertionError(f"{name}: missing or invalid Boolean evidence")
    return b.to_numpy(dtype=bool)

def numeric(frame: pd.DataFrame, columns: list[str], name: str) -> None:
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise ValueError(f"{name}: missing fields {missing}")
    for col in columns:
        frame[col] = pd.to_numeric(frame[col], errors="raise")

def indexed(frame: pd.DataFrame, name: str) -> pd.DataFrame:
    frame = prepare(frame, name)
    return frame.set_index(KEYS).sort_index()

def area_equal(left, right, tol=1e-5):
    a = np.asarray(left, dtype=float)
    b = np.asarray(right, dtype=float)
    return np.isclose(a, b, atol=tol, rtol=0, equal_nan=True)

def rebuild_intensity(layer: pd.DataFrame):
    for stem in ("irrigated", "rainfed", "total"):
        h = layer[f"harvested_{stem}_ha"].to_numpy(float)
        p = layer[f"physical_{stem}_ha"].to_numpy(float)
        expected = np.full(len(layer), np.nan, dtype=float)
        valid = np.isfinite(h) & np.isfinite(p) & (p > 0)
        expected[valid] = h[valid] / p[valid]
        layer[f"intensity_{stem}"] = expected
    h = layer["harvested_total_ha"].to_numpy(float)
    hi = layer["harvested_irrigated_ha"].to_numpy(float)
    share = np.full(len(layer), np.nan, dtype=float)
    positive = np.isfinite(h) & (h > 0)
    share[positive] = hi[positive] / h[positive]
    layer["harvested_irrigated_share"] = share
    if "area_complete" in layer:
        layer["area_complete"] = layer[AREAS].notna().all(axis=1)
    return layer

def check_balance(layer: pd.DataFrame, name: str, missing_allowed: int, tol: float):
    incomplete = int(layer[AREAS].isna().any(axis=1).sum())
    if incomplete != missing_allowed:
        raise AssertionError(f"{name}: expected {missing_allowed} incomplete rows, found {incomplete}")
    valid = layer[AREAS].notna().all(axis=1)
    bad_negative = (layer.loc[valid, AREAS] < -tol).any(axis=1)
    if bad_negative.any():
        raise AssertionError(f"{name}: {int(bad_negative.sum())} negative-area records")
    errors = {}
    for stem in ("harvested", "physical"):
        delta = (layer.loc[valid, f"{stem}_irrigated_ha"]
                 + layer.loc[valid, f"{stem}_rainfed_ha"]
                 - layer.loc[valid, f"{stem}_total_ha"]).abs()
        errors[stem] = float(delta.max()) if len(delta) else 0.0
        if delta.gt(tol).any():
            raise AssertionError(f"{name}: {stem} area does not balance")
    return {"incomplete_rows": incomplete, "max_balance_error_ha": errors}

def build_inputs(args):
    root = args.reference_root.expanduser().resolve()
    stage = args.stage03_root.expanduser().resolve()
    legacy = args.legacy_root.expanduser().resolve()
    out = args.output_root.expanduser().resolve()
    paths = {
        "fresh_spatial_2000": stage / "03_observed_FABLE_5arcmin" / "03_SPAM_FABLE_observed_5arcmin_2000.parquet",
        "stage03_manifest": stage / "03_clean_start_observed_mapping_manifest.json",
        "frozen_scientific_2000": root / "02_country_completion" / "SPAM2000_FABLE56_country_SCIENTIFIC_completion.csv.gz",
        "frozen_operational_2000": root / "02_country_completion" / "SPAM2000_FABLE56_country_CALCULATOR_operational.csv.gz",
        "mirca_magnitude_23": root / "02_mapping" / "qc" / "02_CANDIDATE_FABLE2000_MIRCA23_same_year_magnitude_fallback.csv",
        "approved_mirca_provenance": legacy / "06_SPAM2000_harmonization_provenance.csv",
        "approved_mirca_full_layer": legacy / "06_SPAM2000_harmonized_H_P_intensity_layer.csv.gz",
        "original_calculator": root / "04_final_release" / "calculator" / "04_FABLE_C_SPAM_historical_input_2000_2020_FINAL.csv.gz",
        "validated_candidate": root / "04_final_release" / "publication_candidate" / "v1_0_candidate" / "04_FABLE_C_SPAM_historical_2000_2020_MIRCA23_CORRECTED_v1_0_candidate.csv.gz",
    }
    for protected in (
        root / "01_native_5arcmin", root / "02_mapping", root / "02_country_completion",
        root / "03_observed_FABLE_5arcmin", root / "04_final_release",
        legacy, stage,
    ):
        if under(out, protected) or under(protected, out):
            raise ValueError(f"Output directory overlaps protected input: {protected}")
    missing = {key: str(path) for key, path in paths.items() if not path.is_file()}
    if missing:
        raise FileNotFoundError(f"Stage 04 needs frozen source evidence: {missing}")
    if out.exists():
        raise FileExistsError(f"Choose a new, non-existing Stage 04 directory: {out}")
    return paths, out

def verify_stage03(path: Path, fresh_file: Path):
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("status") != EXPECTED_STAGE03_STATUS:
        raise AssertionError("Stage 03 four-year manifest has not passed")
    years = [x.get("year") for x in manifest.get("results", [])]
    if set(years) != {2000, 2005, 2010, 2020}:
        raise AssertionError(f"Stage 03 manifest years incomplete: {years}")
    year2000 = next(x for x in manifest["results"] if x["year"] == 2000)
    if Path(year2000["mapped_output"]).resolve() != fresh_file.resolve():
        raise AssertionError("Stage 03 manifest points to a different 2000 output")
    if sha256(fresh_file) != year2000.get("mapped_output_sha256"):
        raise AssertionError("Fresh Stage 03 output fingerprint changed")
    return manifest

def freshly_aggregate_spatial(path: Path):
    con = duckdb.connect(":memory:")
    try:
        found = set(con.execute(f"DESCRIBE SELECT * FROM read_parquet('{quote(path)}')").df()["column_name"])
        required = set(KEYS + COMPONENTS + ["year"])
        if required - found:
            raise ValueError(f"Fresh spatial schema missing: {sorted(required - found)}")
        data = con.execute(f"""
            SELECT UPPER(TRIM(country_iso3)) AS country_iso3,
                   LOWER(TRIM(fable_product)) AS fable_product,
                   SUM(harvested_irrigated_ha) AS harvested_irrigated_ha,
                   SUM(harvested_rainfed_ha) AS harvested_rainfed_ha,
                   SUM(physical_irrigated_ha) AS physical_irrigated_ha,
                   SUM(physical_rainfed_ha) AS physical_rainfed_ha,
                   COUNT(*) AS native_grid_records
            FROM read_parquet('{quote(path)}')
            WHERE CAST(year AS INTEGER) = 2000
            GROUP BY 1, 2
        """).df()
        wrong_years = con.execute(f"""
            SELECT COUNT(*) FROM read_parquet('{quote(path)}')
            WHERE year IS NULL OR CAST(year AS INTEGER) <> 2000
        """).fetchone()[0]
        if wrong_years:
            raise AssertionError(f"Spatial source contains {wrong_years} other-year records")
    finally:
        con.close()
    if data.empty or data.duplicated(KEYS).any():
        raise AssertionError("Fresh spatial country-product aggregation is empty or duplicated")
    data["harvested_total_ha"] = data["harvested_irrigated_ha"] + data["harvested_rainfed_ha"]
    data["physical_total_ha"] = data["physical_irrigated_ha"] + data["physical_rainfed_ha"]
    if data["fable_product"].nunique() != 31 or data["country_iso3"].nunique() != 177:
        raise AssertionError("Expected 31 spatially represented products and 177 countries in 2000")
    return indexed(data, "fresh observed SPAM2000")

def read_layers(paths):
    sci = indexed(pd.read_csv(paths["frozen_scientific_2000"], low_memory=False), "Cell 3S scientific")
    op = indexed(pd.read_csv(paths["frozen_operational_2000"], low_memory=False), "Cell 3S operational")
    if len(sci) != 9912 or len(op) != 9912 or not sci.index.equals(op.index):
        raise AssertionError("Cell 3S must contain the same 9,912 year-2000 country-product keys")
    if sci.index.get_level_values("country_iso3").nunique() != 177 or sci.index.get_level_values("fable_product").nunique() != 56:
        raise AssertionError("Cell 3S does not contain 177 countries and 56 products")
    for title, frame in (("scientific", sci), ("operational", op)):
        numeric(frame, AREAS, f"Cell 3S {title}")
        if "year" in frame and not pd.to_numeric(frame["year"], errors="raise").eq(2000).all():
            raise AssertionError(f"Cell 3S {title} has other years")
    observed = boolean(op["observed_SPAM"], "Cell 3S observed_SPAM")
    if not np.array_equal(observed, boolean(sci["observed_SPAM"], "scientific observed_SPAM")):
        raise AssertionError("Scientific and operational observed-row provenance disagree")
    only_zero = op["resolution_group"].astype("string").eq("CALCULATOR_ONLY_OPERATIONAL_ZERO").to_numpy(bool)
    if only_zero.sum() != 26:
        raise AssertionError(f"Expected 26 Calculator-only operational zero rows; got {only_zero.sum()}")
    if np.any(observed & only_zero):
        raise AssertionError("Observed SPAM rows cannot be Calculator-only assumption zeros")
    if not np.all(op.loc[only_zero, AREAS].to_numpy(float) == 0):
        raise AssertionError("Calculator-only operational assumption rows are not zero")
    if not sci.loc[only_zero, AREAS].isna().all().all():
        raise AssertionError("The 26 no-evidence scientific records are not missing")
    if not area_equal(sci.loc[~only_zero, AREAS], op.loc[~only_zero, AREAS]).all():
        raise AssertionError("Scientific and operational non-assumption values disagree")
    return sci, op, observed, only_zero

def reconstruct_observed(sci, op, observed, spatial, tol):
    """Replace observed SPAM rows with fresh country aggregates, not saved values."""
    rebuilt_sci = sci.copy(deep=True)
    rebuilt_op = op.copy(deep=True)
    observed_idx = op.index[observed]
    extra = spatial.index.difference(observed_idx)
    if len(extra):
        raise AssertionError(f"Fresh spatial rows not tagged observed SPAM2000 in Cell 3S: {len(extra)}")
    for frame in (rebuilt_sci, rebuilt_op):
        for col in COMPONENTS:
            new_values = spatial[col].reindex(observed_idx).fillna(0).to_numpy(float)
            frozen_values = op.loc[observed_idx, col].to_numpy(float)
            bad = ~area_equal(new_values, frozen_values, tol)
            if bad.any():
                mismatches = pd.DataFrame({
                    "country_iso3": observed_idx.get_level_values(0)[bad],
                    "fable_product": observed_idx.get_level_values(1)[bad],
                    "column": col,
                    "fresh": new_values[bad],
                    "frozen": frozen_values[bad],
                })
                raise AssertionError(f"Fresh source spatial does not match frozen observed area:\n{mismatches.head(10)}")
            frame.loc[observed_idx, col] = new_values
        frame.loc[observed_idx, "harvested_total_ha"] = (
            frame.loc[observed_idx, "harvested_irrigated_ha"]
            + frame.loc[observed_idx, "harvested_rainfed_ha"]
        )
        frame.loc[observed_idx, "physical_total_ha"] = (
            frame.loc[observed_idx, "physical_irrigated_ha"]
            + frame.loc[observed_idx, "physical_rainfed_ha"]
        )
        rebuild_intensity(frame)
    return rebuilt_sci, rebuilt_op, int(observed.sum())

def verify_original_baseline(op, path: Path, tol):
    original = pd.read_csv(path, low_memory=False)
    if "year" not in original.columns:
        raise ValueError("Canonical Calculator reference has no year")
    selected = original.loc[pd.to_numeric(original["year"], errors="raise").eq(2000)].copy()
    ref = indexed(selected, "original Calculator 2000 baseline")
    if len(ref) != 9912 or not ref.index.equals(op.index):
        raise AssertionError("Frozen 3S and the canonical 2000 panel have different keys")
    for col in AREAS:
        a = op[col].to_numpy(float)
        b = pd.to_numeric(ref[col], errors="raise").to_numpy(float)
        if not area_equal(a, b, tol).all():
            bad = np.flatnonzero(~area_equal(a, b, tol))
            raise AssertionError(
                f"The frozen 3S baseline differs from canonical Calculator 2000 "
                f"for {col} in {len(bad)} records. First key: {op.index[bad[0]]}"
            )
    return {"year_2000_baseline_records_verified": len(ref), "area_component_failures": 0}

def verify_magnitude(op, path: Path, tol):
    mag = indexed(pd.read_csv(path, low_memory=False), "Cell 3P MIRCA magnitude")
    if len(mag) != 23:
        raise AssertionError(f"Expected 23 distinct MIRCA magnitude cases, found {len(mag)}")
    mapping = {
        "harvested_irrigated_ha": "candidate_2000_harvested_irrigated_ha",
        "harvested_rainfed_ha": "candidate_2000_harvested_rainfed_ha",
        "harvested_total_ha": "candidate_2000_harvested_total_ha",
    }
    numeric(mag, list(mapping.values()) + ["mirca_irrigated_ha", "mirca_rainfed_ha", "mirca_total_ha"], "MIRCA magnitude candidate")
    for col in ("observed_SPAM", "completion_layer", "magnitude_candidate_method", "mirca_irrigated_share"):
        if col not in mag:
            raise ValueError(f"MIRCA magnitude input lacks provenance field: {col}")
    if boolean(mag["observed_SPAM"], "MIRCA magnitude observed_SPAM").any():
        raise AssertionError("Same-year MIRCA magnitude must not be labelled observed SPAM")
    if not boolean(mag["completion_layer"], "MIRCA magnitude completion_layer").all():
        raise AssertionError("Same-year MIRCA magnitude must be labelled a completion")
    if not mag["magnitude_candidate_method"].eq("MIRCA2000_SAME_YEAR_MAGNITUDE_FALLBACK").all():
        raise AssertionError("Same-year MIRCA magnitude method labels differ from Cell 3P")
    mirca_share = pd.to_numeric(mag["mirca_irrigated_share"], errors="raise").to_numpy(float)
    mirca_total = mag["mirca_total_ha"].to_numpy(float)
    mirca_ir = mag["mirca_irrigated_ha"].to_numpy(float)
    if not np.isfinite(mirca_total).all() or np.any(mirca_total <= 0):
        raise AssertionError("MIRCA fallback magnitude must be positive and finite")
    if not np.isclose(mirca_ir / mirca_total, mirca_share, atol=1e-10, rtol=0).all():
        raise AssertionError("MIRCA magnitude source irrigated share is inconsistent")
    if len(mag.index.difference(op.index)):
        raise AssertionError("MIRCA magnitude candidate keys not in 2000 output")
    rows = []
    for target, source in mapping.items():
        vals = mag[source].to_numpy(float)
        raw = mag[target.replace("harvested_", "mirca_")].to_numpy(float)
        replayed = op.loc[mag.index, target].to_numpy(float)
        bad = ~(area_equal(vals, raw, tol) & area_equal(replayed, vals, tol))
        rows.append({"component": target, "records": 23, "failures": int(bad.sum()),
                     "max_reference_difference_ha": float(np.max(np.abs(replayed - vals)))})
    result = pd.DataFrame(rows)
    if result["failures"].sum():
        raise AssertionError("Cell 3P positive MIRCA magnitude fallback differs from 3S resolution")
    return result

def apply_approved_shares(sci, op, paths, tol):
    approved = indexed(pd.read_csv(paths["approved_mirca_provenance"], low_memory=False), "approved MIRCA share provenance")
    full = indexed(pd.read_csv(paths["approved_mirca_full_layer"], low_memory=False), "approved legacy harmonization")
    if len(approved) != 23 or len(approved.index.difference(op.index)):
        raise AssertionError("Expected 23 distinct approved MIRCA share keys")
    if len(approved.index.difference(full.index)):
        raise AssertionError("Approved MIRCA shares missing from the frozen harmonization layer")
    source = full.loc[approved.index]
    numeric(source, [f"final_{col}" for col in AREAS], "approved MIRCA source")
    output_sci = sci.copy(deep=True)
    output_op = op.copy(deep=True)
    detail = pd.DataFrame(index=approved.index)
    detail["correction"] = "MIRCA_OS_2000_APPROVED_IRRIGATION_SHARE"
    detail["harvested_magnitude_policy"] = "UNCHANGED_TOTAL"
    detail["physical_area_policy"] = "UNCHANGED_TOTAL"

    for label, frame in (("scientific", output_sci), ("operational", output_op)):
        source_frame = sci if label == "scientific" else op
        htot = source_frame.loc[approved.index, "harvested_total_ha"].to_numpy(float)
        ptot = source_frame.loc[approved.index, "physical_total_ha"].to_numpy(float)
        approved_hir = source["final_harvested_irrigated_ha"].to_numpy(float)
        if np.any(~np.isfinite(htot) | ~np.isfinite(ptot) | (htot <= 0) | (ptot <= 0)):
            raise AssertionError("MIRCA share correction requires positive source H/P totals")
        if not area_equal(htot, source["final_harvested_total_ha"], tol).all():
            raise AssertionError("Approved harmonization changed harvested magnitude")
        if not area_equal(ptot, source["final_physical_total_ha"], tol).all():
            raise AssertionError("Approved harmonization changed physical magnitude")
        total_intensity = htot / ptot
        h_ir = approved_hir
        h_rf = htot - h_ir
        p_ir = h_ir / total_intensity
        p_rf = ptot - p_ir
        assignments = {
            "harvested_irrigated_ha": h_ir,
            "harvested_rainfed_ha": h_rf,
            "harvested_total_ha": htot,
            "physical_irrigated_ha": p_ir,
            "physical_rainfed_ha": p_rf,
            "physical_total_ha": ptot,
        }
        for col, values in assignments.items():
            if not area_equal(values, source[f"final_{col}"], tol).all():
                raise AssertionError(f"Frozen approved area values differ from reconstructed {col}")
            frame.loc[approved.index, col] = values
        if label == "operational":
            for col, values in assignments.items():
                detail[f"before_{col}"] = source_frame.loc[approved.index, col].to_numpy(float)
                detail[f"after_{col}"] = values
        rebuild_intensity(frame)
    return output_sci, output_op, detail.reset_index(), len(approved)

def compare_candidate(op, path: Path, tol):
    reference = pd.read_csv(path, low_memory=False)
    if "year" not in reference:
        raise ValueError("Calculator candidate does not contain year")
    ref = indexed(reference.loc[pd.to_numeric(reference["year"], errors="raise").eq(2000)],
                  "validated Calculator 2000 candidate")
    if len(ref) != 9912 or not op.index.equals(ref.index):
        raise AssertionError("Candidate 2000 year panel has a different key universe")
    rows = []
    for col in AREAS:
        delta = (op[col].to_numpy(float) - pd.to_numeric(ref[col], errors="raise").to_numpy(float))
        bad = ~area_equal(op[col], ref[col], tol)
        rows.append({"field": col, "records": len(op), "failures": int(bad.sum()),
                     "max_absolute_difference_ha": float(np.nanmax(np.abs(delta)))})
    for stem in ("irrigated", "rainfed", "total"):
        col = f"harvesting_intensity_{stem}"
        fresh = op[f"intensity_{stem}"].to_numpy(float)
        saved = pd.to_numeric(ref[col], errors="coerce").to_numpy(float)
        # The published source has both 0 and NaN for 0/0. Positive
        # physical areas always require H/P, and other missingness is strict.
        h = op[f"harvested_{stem}_ha"].to_numpy(float)
        p = op[f"physical_{stem}_ha"].to_numpy(float)
        zero_zero = np.isfinite(h) & np.isfinite(p) & (h == 0) & (p == 0)
        bad = ~(np.isclose(fresh, saved, atol=1e-10, rtol=1e-12, equal_nan=True)
                | (zero_zero & (np.isnan(saved) | (saved == 0))))
        rows.append({"field": col, "records": len(op), "failures": int(bad.sum()),
                     "max_absolute_difference_ha": float(np.nanmax(np.abs(fresh - saved))) if np.any(np.isfinite(fresh - saved)) else 0.0})
    report = pd.DataFrame(rows)
    if report["failures"].sum():
        raise AssertionError(f"Stage 04 differs from the validated Calculator reference: {report.loc[report.failures.gt(0)]}")
    return report

def execute(args, paths, output):
    stage03_manifest = verify_stage03(paths["stage03_manifest"], paths["fresh_spatial_2000"])
    spatial = freshly_aggregate_spatial(paths["fresh_spatial_2000"])
    scientific, operational, observed, zero_mask = read_layers(paths)
    fresh_sc, fresh_op, observed_count = reconstruct_observed(
        scientific, operational, observed, spatial, args.area_tol
    )
    canonical_baseline = verify_original_baseline(
        fresh_op, paths["original_calculator"], args.area_tol
    )
    mag_report = verify_magnitude(fresh_op, paths["mirca_magnitude_23"], args.area_tol)
    corrected_sc, corrected_op, correction_detail, ncorrected = apply_approved_shares(
        fresh_sc, fresh_op, paths, args.area_tol
    )
    if ncorrected != 23:
        raise AssertionError("Stage 04 must restore exactly 23 approved share corrections")
    scientific_qc = check_balance(corrected_sc, "scientific", 26, args.area_tol)
    operational_qc = check_balance(corrected_op, "operational", 0, args.area_tol)
    original_nonzero_keys = operational.index[~zero_mask]
    if not corrected_sc.loc[original_nonzero_keys, AREAS].notna().all().all():
        raise AssertionError("Scientific non-assumption values became missing")
    reference_report = compare_candidate(corrected_op, paths["validated_candidate"], args.area_tol)

    output.mkdir(parents=True, exist_ok=False)
    cleaned = [
        (corrected_sc, "04_SPAM2000_FABLE56_scientific_replay.csv.gz"),
        (corrected_op, "04_SPAM2000_FABLE56_operational_replay.csv.gz"),
    ]
    for frame, name in cleaned:
        frame.reset_index().to_csv(output / name, index=False, compression="gzip")
    correction_detail.to_csv(output / "04_MIRCA23_share_changes.csv", index=False)
    mag_report.to_csv(output / "04_MIRCA23_magnitude_QC.csv", index=False)
    reference_report.to_csv(output / "04_Calculator_2000_reference_comparison.csv", index=False)
    overview = {
        "stage": "04_2000_country_completion_and_mirca",
        "status": EXPECTED_STAGE04_STATUS,
        "validation_mode": "fresh_2000_spatial_observation_plus_frozen_3S_country_completion",
        "fully_recomputed_faostat_mirca_temporal_decisions_from_raw": False,
        "raw_to_release_reproducibility_complete": False,
        "spatially_reconstructed_observed_records": observed_count,
        "spatially_represented_products": spatial.index.get_level_values("fable_product").nunique(),
        "year_2000_country_product_records": len(corrected_op),
        "country_count": corrected_op.index.get_level_values("country_iso3").nunique(),
        "fable_products": corrected_op.index.get_level_values("fable_product").nunique(),
        "mirca_magnitude_fallback_verified": int(len(mag_report) == 3 and mag_report.failures.sum() == 0),
        "mirca_share_corrections_verified": ncorrected,
        "scientific_no_evidence_rows_preserved": scientific_qc["incomplete_rows"],
        "calculator_only_assumption_zero_rows": int(zero_mask.sum()),
        "area_and_intensity_reference_failures": int(reference_report.failures.sum()),
        "canonical_baseline_qc": canonical_baseline,
        "scientific_area_qc": scientific_qc,
        "operational_area_qc": operational_qc,
        "canonical_final_overwritten": False,
    }
    pd.DataFrame([{k: v for k, v in overview.items() if not isinstance(v, dict)}]).to_csv(
        output / "04_stage04_summary.csv", index=False
    )
    manifest = dict(overview)
    manifest["source_sha256"] = {
        k: {"path": str(v), "sha256": sha256(v)} for k, v in paths.items()
    }
    manifest["output_sha256"] = {
        p.name: sha256(p) for p in output.iterdir() if p.is_file()
    }
    (output / "04_stage04_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print("Fresh observed 2000 country rows verified:", observed_count)
    print("Country-product completion records:", len(corrected_op))
    print("Scientific missing-only 26 rows:", scientific_qc["incomplete_rows"])
    print("MIRCA magnitude fallbacks verified: 23")
    print("Approved MIRCA irrigation-share corrections: 23")
    print("Calculator country-area and intensity mismatches:", reference_report.failures.sum())
    print("Output:", output)
    print("STATUS:", EXPECTED_STAGE04_STATUS)
    print("Full FAOSTAT/MIRCA source-to-completion replay: PENDING")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-root", type=Path, default=REFERENCE_ROOT)
    parser.add_argument("--stage03-root", type=Path,
                        default=REFERENCE_ROOT / "05_publication_audit" / "v1_0_candidate" / "clean_stage03_full_4years_v2")
    parser.add_argument("--legacy-root", type=Path, default=LEGACY_ROOT)
    parser.add_argument("--output-root", type=Path,
                        default=REFERENCE_ROOT / "05_publication_audit" / "v1_0_candidate" / "clean_stage04_frozen_completion_v1")
    parser.add_argument("--area-tol", type=float, default=1e-5)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not np.isfinite(args.area_tol) or args.area_tol <= 0:
        raise ValueError("Area tolerance must be positive and finite")
    paths, output = build_inputs(args)
    print("Stage 04 country completion and MIRCA preflight")
    print("Frozen source evidence files:", len(paths))
    print("Fresh Stage 03 source:", paths["fresh_spatial_2000"])
    print("Clean output:", output)
    if args.check:
        print("STATUS: PASS_STAGE04_FROZEN_INPUT_PREFLIGHT_ONLY")
        return 0
    execute(args, paths, output)
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("STAGE04_FAILED:", repr(exc), file=sys.stderr)
        raise
