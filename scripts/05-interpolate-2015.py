"""Stage 05: rebuild the 2015 SPAM to FABLE 5-arcminute midpoint.

Uses freshly mapped 2010 and 2020 outputs from Stage 03 and the frozen
2015 grid-to-country crosswalk. Interpolates the four area components
before country aggregation, then validates the saved 2015 spatial file
and the independently validated FABLE Calculator candidate.

Usage:
    python 05_SPAM_FABLE_clean_start_2015_spatial_interpolation.py --check
    python 05_SPAM_FABLE_clean_start_2015_spatial_interpolation.py --execute

This does not recreate the frozen 2015 crosswalk from raw borders, and
it cannot by itself demonstrate full raw-source-to-release reproducibility.
No existing canonical dataset or previous stage output is overwritten.
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

DEFAULT_ROOT = Path("data")
VERSION = "v1_0_candidate"
DEFAULT_STAGE03 = (DEFAULT_ROOT / "05_publication_audit" / VERSION
                   / "clean_stage03_full_4years_v2")
DEFAULT_OUTPUT = (DEFAULT_ROOT / "05_publication_audit" / VERSION
                  / "clean_stage05_2015_interpolation_v1")
STAGE03_STATUS = "PASS_STAGE03_FRESH_OBSERVED_GRID_REFERENCE_MATCH"
PRIMARY = (
    "harvested_irrigated_ha", "harvested_rainfed_ha",
    "physical_irrigated_ha", "physical_rainfed_ha",
)
TOTALS = ("harvested_total_ha", "physical_total_ha")
INTENSITIES = (
    "harvesting_intensity_irrigated",
    "harvesting_intensity_rainfed",
    "harvesting_intensity_total",
)
CROSSWALK_REQUIRED = (
    "grid_id_5min", "country_iso3_2015_final", "country_2010",
    "country_2020", "country_assignment_status_final",
    "country_assignment_method_final", "country_assignment_note_final",
    "refined_review_reason", "refined_review_evidence",
)
ANCHOR_REQUIRED = (
    "grid_id_5min", "grid_row_5min", "grid_col_5min",
    "longitude", "latitude", "country_iso3", "year", "fable_product",
    "mapping_method", *PRIMARY,
)
SOURCE_PRECISION_H_MAX_HA = 0.200001

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def qpath(path: Path) -> str:
    return Path(path).as_posix().replace("'", "''")

def is_under(path: Path, root: Path) -> bool:
    path, root = path.resolve(), root.resolve()
    return path == root or root in path.parents

def require(condition: bool, message: str) -> None:
    if not bool(condition):
        raise AssertionError(message)

def sources(args) -> tuple[dict[str, Path], Path]:
    ref = args.reference_root.expanduser().resolve()
    stage = args.stage03_root.expanduser().resolve()
    out = args.output_root.expanduser().resolve()
    paths = {
        "stage03_manifest": stage / "03_clean_start_observed_mapping_manifest.json",
        "fresh_2010": stage / "03_observed_FABLE_5arcmin"
                      / "03_SPAM_FABLE_observed_5arcmin_2010.parquet",
        "fresh_2020": stage / "03_observed_FABLE_5arcmin"
                      / "03_SPAM_FABLE_observed_5arcmin_2020.parquet",
        "frozen_crosswalk_2015": ref / "04_final_release" / "qc"
                                  / "04_grid_to_country_2015_FINAL.parquet",
        "saved_spatial_2015": ref / "04_final_release" / "spatial_5arcmin"
                              / "04_SPAM_FABLE_5arcmin_2015_interpolated.parquet",
        "validated_calculator_candidate": (
            ref / "04_final_release" / "publication_candidate" / VERSION
            / "04_FABLE_C_SPAM_historical_2000_2020_MIRCA23_CORRECTED_v1_0_candidate.csv.gz"
        ),
        "notebook04_release_manifest": (
            ref / "04_final_release" / "publication_candidate" / VERSION
            / "04_release_candidate_manifest.json"
        ),
    }
    protected = [
        stage, ref / "01_native_5arcmin", ref / "02_mapping",
        ref / "02_country_completion", ref / "03_observed_FABLE_5arcmin",
        ref / "03_reconstruction_audit", ref / "04_final_release",
    ]
    if out == ref or out == stage or is_under(ref, out):
        raise ValueError("Output directory overlaps a source or reference root")
    for folder in protected:
        if is_under(out, folder) or is_under(folder, out):
            raise ValueError(f"Output overlaps protected data: {folder}")
    missing = {name: str(path) for name, path in paths.items() if not path.is_file()}
    if missing:
        raise FileNotFoundError(f"Missing Stage 05 source evidence: {missing}")
    return paths, out

def verify_manifests(paths: dict[str, Path]) -> tuple[dict, dict]:
    stage03 = json.loads(paths["stage03_manifest"].read_text(encoding="utf-8"))
    release = json.loads(paths["notebook04_release_manifest"].read_text(encoding="utf-8"))
    require(stage03.get("status") == STAGE03_STATUS,
            "The four-year Stage 03 reference comparison is not PASS")
    years = [item.get("year") for item in stage03.get("results", [])]
    require(set(years) == {2000, 2005, 2010, 2020} and len(years) == 4,
            f"Stage 03 manifest has incomplete observed years: {years}")
    for year in (2010, 2020):
        row = next(item for item in stage03["results"] if item["year"] == year)
        path = paths[f"fresh_{year}"]
        require(Path(row["mapped_output"]).resolve() == path,
                f"Stage 03 manifest refers to a different year-{year} file")
        require(row.get("status") == "PASS_STAGE03_FRESH_GRID_AND_REFERENCE_QC",
                f"Stage 03 year {year} did not PASS")
        require(row.get("mapped_output_sha256") == sha256(path),
                f"Fresh Stage 03 year {year} fingerprint has changed")
    require(release.get("scientific_validation_status")
            == "PASS_RELEASE_CANDIDATE_SCIENTIFIC_VALIDATION",
            "Notebook 04 scientific candidate is not validated")
    require(release.get("year_2015_grid_replay_status")
            == "PASS_2015_NATIVE_GRID_COMPONENT_REPLAY",
            "Notebook 04 saved 2015 grid replay did not PASS")
    require(release.get("year_2015_country_reconciliation_status")
            == "PASS_2015_COUNTRY_CALCULATOR_RECONCILIATION",
            "Notebook 04 saved 2015 country reconciliation did not PASS")
    require(release.get("historical_candidate_sha256")
            == sha256(paths["validated_calculator_candidate"]),
            "Validated Calculator candidate checksum differs from Notebook 04 manifest")
    frozen_hashes = release.get("validated_input_sha256", {})
    if frozen_hashes:
        for asset, name in (
            ("observed_2010", "fresh_2010"),
            ("observed_2020", "fresh_2020"),
            ("crosswalk_2015", "frozen_crosswalk_2015"),
            ("locked_spatial_2015", "saved_spatial_2015"),
        ):
            # Fresh mapping is compared to the saved reference by Stage 03,
            # rather than using byte equality across separately written Parquet.
            if asset in {"crosswalk_2015", "locked_spatial_2015"} and asset in frozen_hashes:
                require(frozen_hashes[asset] == sha256(paths[name]),
                        f"Notebook 04 frozen evidence changed: {asset}")
    return stage03, release

def open_db(args, output: Path | None = None):
    con = duckdb.connect(":memory:")
    con.execute(f"SET threads={args.threads}")
    con.execute(f"SET memory_limit='{args.memory_limit}'")
    con.execute("SET preserve_insertion_order=false")
    if output is not None:
        spill = output / "duckdb_spill"
        spill.mkdir(parents=True, exist_ok=True)
        con.execute(f"SET temp_directory='{qpath(spill)}'")
    return con

def verify_spatial_structure(con, paths: dict[str, Path]) -> dict:
    evidence = {}
    for year in (2010, 2020):
        file = qpath(paths[f"fresh_{year}"])
        columns = [r[0] for r in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{file}')"
        ).fetchall()]
        missing = set(ANCHOR_REQUIRED) - set(columns)
        require(not missing, f"Stage 03 year {year} lacks: {sorted(missing)}")
        con.execute(f"""
            CREATE VIEW anchor_{year} AS
            SELECT grid_id_5min, grid_row_5min, grid_col_5min,
                   CAST(longitude AS DOUBLE) AS longitude,
                   CAST(latitude AS DOUBLE) AS latitude,
                   UPPER(TRIM(country_iso3)) AS country_iso3_source,
                   LOWER(TRIM(fable_product)) AS fable_product,
                   {', '.join(f'CAST({c} AS DOUBLE) AS {c}' for c in PRIMARY)},
                   mapping_method,
                   'observed_or_weighted_SPAM' AS data_status
            FROM read_parquet('{file}')
        """)
        info = con.execute(f"""
            SELECT COUNT(*) AS rows,
                   COUNT(DISTINCT fable_product) AS products,
                   COUNT(*) FILTER (WHERE grid_id_5min IS NULL OR fable_product IS NULL)
                       AS null_keys,
                   COUNT(*) - COUNT(DISTINCT (grid_id_5min, fable_product))
                       AS duplicate_keys
            FROM anchor_{year}
        """).df().iloc[0].to_dict()
        require(int(info["products"]) == 56, f"Year {year}: expected 56 products")
        require(int(info["null_keys"]) == 0 and int(info["duplicate_keys"]) == 0,
                f"Year {year}: invalid grid-product anchor keys: {info}")
        evidence[f"anchor_{year}"] = info
    cross = qpath(paths["frozen_crosswalk_2015"])
    columns = set(con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{cross}')"
    ).df()["column_name"])
    require(set(CROSSWALK_REQUIRED).issubset(columns),
            f"2015 crosswalk lacks: {sorted(set(CROSSWALK_REQUIRED) - columns)}")
    con.execute(f"CREATE VIEW frozen_country_2015 AS SELECT * FROM read_parquet('{cross}')")
    invalid = con.execute("""
        WITH needed AS (
            SELECT grid_id_5min FROM anchor_2010
            UNION SELECT grid_id_5min FROM anchor_2020
        ), duplicate AS (
            SELECT grid_id_5min FROM frozen_country_2015
            GROUP BY 1 HAVING COUNT(*) > 1
        )
        SELECT
            (SELECT COUNT(*) FROM duplicate) AS duplicate_crosswalk_keys,
            (SELECT COUNT(*) FROM needed n
             LEFT JOIN frozen_country_2015 c USING(grid_id_5min)
             WHERE c.grid_id_5min IS NULL
                OR NULLIF(TRIM(c.country_iso3_2015_final), '') IS NULL)
             AS unmatched_or_unassigned_grids
    """).df().iloc[0].to_dict()
    require(int(invalid["duplicate_crosswalk_keys"]) == 0,
            "Frozen 2015 grid-to-country crosswalk has duplicate cells")
    require(int(invalid["unmatched_or_unassigned_grids"]) == 0,
            "Some fresh anchor grid cells lack a frozen 2015 country assignment")
    evidence["country_crosswalk"] = invalid
    return evidence

def spatial_sql() -> str:
    # Faithfully adapt the original 2015 production calculation to Stage 03
    # 20-column fresh anchors. Their old 25-column data_status was constant;
    # Stage 03 independently checked that documented reference metadata.
    return f"""
        WITH endpoint_union AS (
            SELECT
                COALESCE(a.grid_id_5min, b.grid_id_5min) AS grid_id_5min,
                COALESCE(a.grid_row_5min, b.grid_row_5min) AS grid_row_5min,
                COALESCE(a.grid_col_5min, b.grid_col_5min) AS grid_col_5min,
                COALESCE(a.longitude, b.longitude) AS longitude,
                COALESCE(a.latitude, b.latitude) AS latitude,
                COALESCE(a.fable_product, b.fable_product) AS fable_product,
                a.country_iso3_source AS row_country_2010,
                b.country_iso3_source AS row_country_2020,
                a.grid_id_5min IS NOT NULL AS endpoint_row_2010_present,
                b.grid_id_5min IS NOT NULL AS endpoint_row_2020_present,
                COALESCE(a.harvested_irrigated_ha, 0.0) AS h_ir_2010,
                COALESCE(b.harvested_irrigated_ha, 0.0) AS h_ir_2020,
                COALESCE(a.harvested_rainfed_ha, 0.0) AS h_rf_2010,
                COALESCE(b.harvested_rainfed_ha, 0.0) AS h_rf_2020,
                COALESCE(a.physical_irrigated_ha, 0.0) AS p_ir_2010,
                COALESCE(b.physical_irrigated_ha, 0.0) AS p_ir_2020,
                COALESCE(a.physical_rainfed_ha, 0.0) AS p_rf_2010,
                COALESCE(b.physical_rainfed_ha, 0.0) AS p_rf_2020,
                a.mapping_method AS mapping_method_2010,
                b.mapping_method AS mapping_method_2020,
                a.data_status AS data_status_2010,
                b.data_status AS data_status_2020
            FROM anchor_2010 a
            FULL OUTER JOIN anchor_2020 b
                ON a.grid_id_5min = b.grid_id_5min
               AND a.fable_product = b.fable_product
        ),
        with_country AS (
            SELECT e.*,
                   UPPER(c.country_iso3_2015_final) AS country_iso3,
                   c.country_2010 AS grid_country_2010,
                   c.country_2020 AS grid_country_2020,
                   c.country_assignment_status_final,
                   c.country_assignment_method_final,
                   c.country_assignment_note_final,
                   c.refined_review_reason,
                   c.refined_review_evidence
            FROM endpoint_union e
            LEFT JOIN frozen_country_2015 c
                ON e.grid_id_5min = c.grid_id_5min
        ),
        primary_components AS (
            SELECT *,
                   (h_ir_2010 + h_ir_2020) / 2.0 AS harvested_irrigated_ha,
                   (h_rf_2010 + h_rf_2020) / 2.0 AS harvested_rainfed_ha,
                   (p_ir_2010 + p_ir_2020) / 2.0 AS physical_irrigated_ha,
                   (p_rf_2010 + p_rf_2020) / 2.0 AS physical_rainfed_ha
            FROM with_country
        ),
        totals AS (
            SELECT *,
                   harvested_irrigated_ha + harvested_rainfed_ha AS harvested_total_ha,
                   physical_irrigated_ha + physical_rainfed_ha AS physical_total_ha
            FROM primary_components
        ),
        statuses AS (
            SELECT *,
                   CASE WHEN physical_irrigated_ha > 0 THEN 'defined'
                        WHEN harvested_irrigated_ha = 0 AND physical_irrigated_ha = 0
                            THEN 'zero_harvest_and_zero_physical'
                        WHEN harvested_irrigated_ha > 0 AND physical_irrigated_ha <= 0
                          AND harvested_irrigated_ha <= {SOURCE_PRECISION_H_MAX_HA}
                            THEN 'undefined_source_precision_h_positive_p_zero'
                        ELSE 'review_required' END AS intensity_status_irrigated,
                   CASE WHEN physical_rainfed_ha > 0 THEN 'defined'
                        WHEN harvested_rainfed_ha = 0 AND physical_rainfed_ha = 0
                            THEN 'zero_harvest_and_zero_physical'
                        WHEN harvested_rainfed_ha > 0 AND physical_rainfed_ha <= 0
                          AND harvested_rainfed_ha <= {SOURCE_PRECISION_H_MAX_HA}
                            THEN 'undefined_source_precision_h_positive_p_zero'
                        ELSE 'review_required' END AS intensity_status_rainfed,
                   CASE WHEN physical_total_ha > 0 THEN 'defined'
                        WHEN harvested_total_ha = 0 AND physical_total_ha = 0
                            THEN 'zero_harvest_and_zero_physical'
                        WHEN harvested_total_ha > 0 AND physical_total_ha <= 0
                          AND harvested_total_ha <= {SOURCE_PRECISION_H_MAX_HA}
                            THEN 'undefined_source_precision_h_positive_p_zero'
                        ELSE 'review_required' END AS intensity_status_total
            FROM totals
        )
        SELECT
            grid_id_5min, grid_row_5min, grid_col_5min,
            longitude, latitude, country_iso3,
            CAST(2015 AS INTEGER) AS year, fable_product,
            harvested_irrigated_ha, harvested_rainfed_ha, harvested_total_ha,
            physical_irrigated_ha, physical_rainfed_ha, physical_total_ha,
            CASE WHEN physical_irrigated_ha > 0
                 THEN harvested_irrigated_ha / physical_irrigated_ha
                 WHEN harvested_irrigated_ha = 0 AND physical_irrigated_ha = 0
                 THEN 0.0 ELSE NULL END AS harvesting_intensity_irrigated,
            CASE WHEN physical_rainfed_ha > 0
                 THEN harvested_rainfed_ha / physical_rainfed_ha
                 WHEN harvested_rainfed_ha = 0 AND physical_rainfed_ha = 0
                 THEN 0.0 ELSE NULL END AS harvesting_intensity_rainfed,
            CASE WHEN physical_total_ha > 0
                 THEN harvested_total_ha / physical_total_ha
                 WHEN harvested_total_ha = 0 AND physical_total_ha = 0
                 THEN 0.0 ELSE NULL END AS harvesting_intensity_total,
            intensity_status_irrigated, intensity_status_rainfed,
            intensity_status_total,
            (intensity_status_irrigated = 'undefined_source_precision_h_positive_p_zero'
             OR intensity_status_rainfed = 'undefined_source_precision_h_positive_p_zero'
             OR intensity_status_total = 'undefined_source_precision_h_positive_p_zero')
                 AS source_precision_denominator_flag,
            'anchor_mapped_then_native_grid_midpoint' AS mapping_method,
            'SPAM_2010_2020' AS source_dataset,
            'interpolated_2015_midpoint' AS temporal_status,
            country_assignment_status_final AS data_status,
            'native_5arcmin_component_midpoint' AS h_ir_spatial_basis,
            'native_5arcmin_component_midpoint' AS h_rf_spatial_basis,
            'native_5arcmin_component_midpoint' AS p_ir_spatial_basis,
            'native_5arcmin_component_midpoint' AS p_rf_spatial_basis,
            endpoint_row_2010_present, endpoint_row_2020_present,
            CASE WHEN endpoint_row_2010_present AND endpoint_row_2020_present
                 THEN 'observed_both_endpoints'
                 WHEN endpoint_row_2010_present
                 THEN 'observed_2010_implicit_zero_2020'
                 ELSE 'implicit_zero_2010_observed_2020'
                 END AS interpolation_endpoint_status,
            row_country_2010, row_country_2020,
            grid_country_2010, grid_country_2020,
            country_assignment_status_final, country_assignment_method_final,
            country_assignment_note_final, refined_review_reason,
            refined_review_evidence,
            mapping_method_2010, mapping_method_2020,
            data_status_2010, data_status_2020
        FROM statuses
    """

def inspect_new_spatial(con, path: Path) -> dict:
    p = qpath(path)
    row = con.execute(f"""
        SELECT COUNT(*) AS rows,
               COUNT(DISTINCT grid_id_5min) AS grid_cells,
               COUNT(DISTINCT country_iso3) AS countries,
               COUNT(DISTINCT fable_product) AS products,
               COUNT(*) - COUNT(DISTINCT (grid_id_5min, fable_product))
                    AS duplicate_grid_product_keys,
               COUNT(*) FILTER (WHERE country_iso3 IS NULL OR fable_product IS NULL)
                    AS null_country_product_keys,
               COUNT(*) FILTER (WHERE harvested_irrigated_ha IS NULL
                   OR harvested_rainfed_ha IS NULL
                   OR physical_irrigated_ha IS NULL
                   OR physical_rainfed_ha IS NULL) AS missing_primary_components,
               COUNT(*) FILTER (WHERE harvested_irrigated_ha < 0
                   OR harvested_rainfed_ha < 0
                   OR physical_irrigated_ha < 0
                   OR physical_rainfed_ha < 0) AS negative_primary_rows,
               COUNT(*) FILTER (WHERE ABS(harvested_total_ha
                   - harvested_irrigated_ha - harvested_rainfed_ha) > 1e-7
                   OR ABS(physical_total_ha
                   - physical_irrigated_ha - physical_rainfed_ha) > 1e-7)
                   AS sum_identity_failures,
               COUNT(*) FILTER (WHERE intensity_status_irrigated = 'review_required'
                   OR intensity_status_rainfed = 'review_required'
                   OR intensity_status_total = 'review_required') AS material_review_rows,
               COUNT(*) FILTER (WHERE year <> 2015 OR year IS NULL)
                   AS wrong_year_rows
        FROM read_parquet('{p}')
    """).df().iloc[0].to_dict()
    for col in (
        "duplicate_grid_product_keys", "null_country_product_keys",
        "missing_primary_components", "negative_primary_rows",
        "sum_identity_failures", "material_review_rows", "wrong_year_rows",
    ):
        require(int(row[col]) == 0, f"Fresh 2015 spatial check failed: {col}={row[col]}")
    require(int(row["products"]) == 56, "Fresh 2015 output should represent 56 FABLE products")
    return {key: int(value) for key, value in row.items()}

def numeric_equal(alias_a: str, alias_b: str, col: str, tol: float) -> str:
    return (f"(CASE WHEN {alias_a}.{col} IS NULL AND {alias_b}.{col} IS NULL "
            f"THEN TRUE WHEN {alias_a}.{col} IS NULL OR {alias_b}.{col} IS NULL "
            f"THEN FALSE WHEN {alias_a}.{col} IS NOT DISTINCT FROM {alias_b}.{col} "
            f"THEN TRUE WHEN isfinite({alias_a}.{col}) AND isfinite({alias_b}.{col}) "
            f"THEN ABS({alias_a}.{col}-{alias_b}.{col}) <= {tol} "
            f"ELSE FALSE END)")

def compare_saved_spatial(con, fresh: Path, saved: Path, area_tol: float,
                          intensity_tol: float) -> tuple[dict, pd.DataFrame]:
    a, b = qpath(fresh), qpath(saved)
    fields_a = [r[0] for r in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{a}')"
    ).fetchall()]
    fields_b = [r[0] for r in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{b}')"
    ).fetchall()]
    if fields_a != fields_b:
        raise AssertionError(
            f"2015 saved spatial schema mismatch: "
            f"missing_in_rebuild={sorted(set(fields_b) - set(fields_a))}; "
            f"unexpected_rebuild={sorted(set(fields_a) - set(fields_b))}; "
            f"ordering_identical={fields_a == fields_b}"
        )
    numeric = {*PRIMARY, *TOTALS, *INTENSITIES, "longitude", "latitude"}
    tested = [col for col in fields_a if col not in {"grid_id_5min", "fable_product"}]
    comparisons: dict[str, str] = {}
    for col in tested:
        if col in numeric:
            tolerance = intensity_tol if col in INTENSITIES else (1e-12 if col in {"longitude", "latitude"} else area_tol)
            comparisons[col] = numeric_equal("a", "b", col, tolerance)
        else:
            comparisons[col] = f"(a.{col} IS NOT DISTINCT FROM b.{col})"
    bad = " OR ".join(f"NOT ({expr})" for expr in comparisons.values())
    row = con.execute(f"""
        WITH a AS (SELECT 1 AS __fresh, * FROM read_parquet('{a}')),
             b AS (SELECT 1 AS __saved, * FROM read_parquet('{b}'))
        SELECT COUNT(*) AS compared_keys,
               COUNT(*) FILTER (WHERE a.__fresh IS NULL OR b.__saved IS NULL)
                    AS missing_or_extra_keys,
               COUNT(*) FILTER (WHERE a.__fresh IS NOT NULL AND b.__saved IS NOT NULL
                    AND ({bad})) AS mismatch_records
        FROM a FULL OUTER JOIN b
          ON a.grid_id_5min = b.grid_id_5min
         AND a.fable_product = b.fable_product
    """).df().iloc[0].to_dict()
    result = {key: int(val) for key, val in row.items()}
    result.update({
        "columns_compared": len(fields_a),
        "status": "PASS_2015_FULL_SPATIAL_REFERENCE_IDENTITY"
                  if result["missing_or_extra_keys"] == 0 and result["mismatch_records"] == 0
                  else "FAIL_2015_FULL_SPATIAL_REFERENCE_MISMATCH",
    })
    # Detailed per-field diagnosis is computed only when a full-row mismatch
    # occurs. It is deliberately not replaced with country-level aggregates.
    failures = []
    if result["mismatch_records"] or result["missing_or_extra_keys"]:
        parts = [
            f"COUNT(*) FILTER (WHERE a.__fresh IS NOT NULL AND b.__saved IS NOT NULL "
            f"AND NOT ({expr})) AS \"{col}\""
            for col, expr in comparisons.items()
        ]
        detail = con.execute(f"""
            WITH a AS (SELECT 1 AS __fresh, * FROM read_parquet('{a}')),
                 b AS (SELECT 1 AS __saved, * FROM read_parquet('{b}'))
            SELECT {', '.join(parts)}
            FROM a FULL OUTER JOIN b
              ON a.grid_id_5min = b.grid_id_5min
             AND a.fable_product = b.fable_product
        """).df().iloc[0]
        failures = [{"column": field, "failed_rows": int(count)}
                    for field, count in detail.items() if count]
    return result, pd.DataFrame(failures, columns=["column", "failed_rows"])

def country_2015_reconciliation(con, fresh: Path, candidate: Path,
                                tolerance: float) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    path = qpath(fresh)
    country = con.execute(f"""
        SELECT UPPER(TRIM(country_iso3)) AS country_iso3,
               2015 AS year,
               LOWER(TRIM(fable_product)) AS fable_product,
               {', '.join(f'SUM({col}) AS {col}' for col in PRIMARY)}
        FROM read_parquet('{path}')
        GROUP BY 1, 2, 3
    """).df()
    country["harvested_total_ha"] = (
        country["harvested_irrigated_ha"] + country["harvested_rainfed_ha"]
    )
    country["physical_total_ha"] = (
        country["physical_irrigated_ha"] + country["physical_rainfed_ha"]
    )
    for component in ("irrigated", "rainfed", "total"):
        h = country[f"harvested_{component}_ha"].to_numpy(float)
        p = country[f"physical_{component}_ha"].to_numpy(float)
        expected = np.full(len(country), np.nan)
        positive = p > 0
        zero_zero = (h == 0) & (p == 0)
        expected[positive] = h[positive] / p[positive]
        expected[zero_zero] = 0.0
        country[f"harvesting_intensity_{component}"] = expected
    historical = pd.read_csv(candidate, low_memory=False)
    required = {"country_iso3", "year", "fable_product", *PRIMARY}
    require(required.issubset(historical.columns),
            "Validated historical candidate lacks required year-2015 area columns")
    year = pd.to_numeric(historical["year"], errors="raise")
    historical = historical.loc[year.eq(2015)].copy()
    historical["country_iso3"] = historical["country_iso3"].astype("string").str.upper().str.strip()
    historical["fable_product"] = historical["fable_product"].astype("string").str.lower().str.strip()
    historical["year"] = 2015
    require(len(historical) == 11368 and historical["country_iso3"].nunique() == 203,
            "Expected the validated 2015 panel of 203 countries x 56 products")
    require(historical["fable_product"].nunique() == 56,
            "Historical 2015 year should contain all 56 FABLE products")
    require(not historical.duplicated(["country_iso3", "year", "fable_product"]).any(),
            "Historical 2015 has duplicate country-product keys")
    require(set(country["country_iso3"]) == set(historical["country_iso3"]),
            "2015 spatial and historical country coverage differ")
    check = historical[["country_iso3", "year", "fable_product", *PRIMARY]].merge(
        country[["country_iso3", "year", "fable_product", *PRIMARY]],
        on=["country_iso3", "year", "fable_product"],
        how="outer", validate="one_to_one", indicator=True,
        suffixes=("_calculator", "_spatial"),
    )
    require(not check["_merge"].eq("right_only").any(),
            "2015 fresh spatial has country-products outside validated panel")
    errors = []
    for component in PRIMARY:
        calculated = pd.to_numeric(check[f"{component}_calculator"], errors="coerce")
        spatial = pd.to_numeric(check[f"{component}_spatial"], errors="coerce")
        # A missing sparse spatial product is a supported structural zero
        # only when its country is independently present in the spatial layer.
        spatial = spatial.mask(check["_merge"].eq("left_only"), 0.0)
        difference = calculated - spatial
        check[f"difference_{component}_ha"] = difference
        check[f"pass_{component}"] = calculated.notna() & spatial.notna() & difference.abs().le(tolerance)
        errors.append(f"difference_{component}_ha")
    check["all_components_pass"] = check[[f"pass_{c}" for c in PRIMARY]].all(axis=1)
    summary = pd.DataFrame([{
        "records_compared": len(check),
        "countries": country["country_iso3"].nunique(),
        "products": country["fable_product"].nunique(),
        "mismatch_records": int((~check["all_components_pass"]).sum()),
        "max_absolute_difference_ha": float(check[errors].abs().max().max()),
        "status": "PASS_STAGE05_2015_COUNTRY_CALCULATOR_RECONCILIATION"
                  if check["all_components_pass"].all()
                  else "FAIL_STAGE05_2015_COUNTRY_MISMATCH",
    }])
    return country, check, summary

def execute(args, paths: dict[str, Path], output: Path, manifest_stage03: dict, evidence: dict):
    if output.exists():
        raise FileExistsError(f"Choose an unused Stage 05 output directory: {output}")
    output.mkdir(parents=True, exist_ok=False)
    spatial_dir = output / "04_final_release" / "spatial_5arcmin"
    national_dir = output / "04_final_release" / "calculator"
    qc_dir = output / "qc"
    for directory in (spatial_dir, national_dir, qc_dir):
        directory.mkdir(parents=True, exist_ok=True)
    spatial_output = spatial_dir / "04_SPAM_FABLE_5arcmin_2015_interpolated.parquet"
    country_output = national_dir / "05_SPAM_FABLE_country_2015_fresh.csv.gz"
    con = open_db(args, output)
    try:
        # Views must be recreated in the clean-run connection.
        verify_spatial_structure(con, paths)
        sql = spatial_sql()
        con.execute(f"""
            COPY ({sql}) TO '{qpath(spatial_output)}'
            (FORMAT PARQUET, COMPRESSION ZSTD)
        """)
        built = inspect_new_spatial(con, spatial_output)
        print(f"2015 fresh midpoint spatial rows: {built['rows']:,}", flush=True)
        spatial_qc, spatial_differences = compare_saved_spatial(
            con, spatial_output, paths["saved_spatial_2015"],
            args.spatial_tol, args.intensity_tol
        )
        pd.DataFrame([spatial_qc]).to_csv(qc_dir / "05_spatial_reference_summary.csv", index=False)
        spatial_differences.to_csv(qc_dir / "05_spatial_field_mismatch_details.csv", index=False)
        print("2015 saved spatial mismatches:", spatial_qc["mismatch_records"], flush=True)
        require(spatial_qc["status"] == "PASS_2015_FULL_SPATIAL_REFERENCE_IDENTITY",
                "Fresh 2015 spatial output does not match the archived source; inspect QC")
        countries, country_details, country_qc = country_2015_reconciliation(
            con, spatial_output, paths["validated_calculator_candidate"], args.country_tol
        )
        countries.to_csv(country_output, index=False, compression="gzip")
        country_details.to_csv(qc_dir / "05_country_vs_calculator_2015.csv.gz",
                               index=False, compression="gzip")
        country_qc.to_csv(qc_dir / "05_country_reconciliation_summary.csv", index=False)
        print("2015 country-product mismatches:",
              int(country_qc.iloc[0]["mismatch_records"]), flush=True)
        require(country_qc.iloc[0]["status"]
                == "PASS_STAGE05_2015_COUNTRY_CALCULATOR_RECONCILIATION",
                "Fresh 2015 country totals disagree with validated Calculator")
    finally:
        con.close()
    summary = {
        "stage": "05_2015_native_grid_midpoint_and_frozen_country_assignment",
        "status": "PASS_STAGE05_FRESH_2015_SPATIAL_AND_COUNTRY_REFERENCE_MATCH",
        "method": "2010_2020_grid_product_area_component_midpoint_then_frozen_2015_country",
        "anchor_2010_2020_from_fresh_stage03": True,
        "frozen_2015_country_crosswalk_not_rebuilt": True,
        "full_raw_source_to_release_reproducibility": False,
        "spatial_records": built["rows"],
        "spatial_countries": built["countries"],
        "spatial_products": built["products"],
        "spatial_reference_mismatch_records": spatial_qc["mismatch_records"],
        "spatial_reference_missing_or_extra_keys": spatial_qc["missing_or_extra_keys"],
        "calculator_country_product_records": int(country_qc.iloc[0]["records_compared"]),
        "country_calculator_mismatch_records": int(country_qc.iloc[0]["mismatch_records"]),
        "max_country_component_difference_ha": float(country_qc.iloc[0]["max_absolute_difference_ha"]),
        "canonical_final_overwritten": False,
    }
    pd.DataFrame([summary]).to_csv(output / "05_stage05_summary.csv", index=False)
    artifact_files = [spatial_output, country_output,
                      qc_dir / "05_spatial_reference_summary.csv",
                      qc_dir / "05_spatial_field_mismatch_details.csv",
                      qc_dir / "05_country_vs_calculator_2015.csv.gz",
                      qc_dir / "05_country_reconciliation_summary.csv"]
    provenance = {
        **summary,
        "stage03_manifest_status": manifest_stage03["status"],
        "input_files_sha256": {key: {"path": str(path), "sha256": sha256(path)}
                               for key, path in paths.items()},
        "output_files_sha256": {str(path.relative_to(output)): sha256(path)
                                for path in artifact_files},
        "spatial_QC": spatial_qc,
        "country_QC": country_qc.iloc[0].to_dict(),
        "anchor_structure": evidence,
    }
    (output / "05_stage05_manifest.json").write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    print("Fresh 2015 spatial output:", spatial_output)
    print("Fresh 2015 Calculator country layer:", country_output)
    print("Stage 05 manifest:", output / "05_stage05_manifest.json")
    print("STATUS:", summary["status"])
    print("Raw boundary-to-2015 crosswalk rebuild: PENDING")

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--stage03-root", type=Path, default=DEFAULT_STAGE03)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--memory-limit", type=str, default="6GB")
    parser.add_argument("--spatial-tol", type=float, default=1e-7)
    parser.add_argument("--intensity-tol", type=float, default=1e-9)
    parser.add_argument("--country-tol", type=float, default=1e-3)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="Read-only input preflight")
    action.add_argument("--execute", action="store_true", help="Fresh 2015 reconstruction")
    args = parser.parse_args()
    if args.threads < 1 or min(args.spatial_tol, args.intensity_tol, args.country_tol) < 0:
        raise ValueError("Thread count and numerical tolerances must be valid")
    paths, out = sources(args)
    stage03, _release = verify_manifests(paths)
    print("Stage 05 fresh 2015 interpolation preflight")
    print("Fresh Stage 03 root:", args.stage03_root.resolve())
    print("Frozen reference root:", args.reference_root.resolve())
    print("New output root:", out)
    print("Required source evidence:", len(paths), "files")
    if out.exists():
        raise FileExistsError("Stage 05 output directory already exists; choose a new one: " + str(out))
    con = open_db(args)
    try:
        evidence = verify_spatial_structure(con, paths)
    finally:
        con.close()
    print("2010 and 2020 freshly mapped anchor products: 56 each")
    print("Frozen country crosswalk: unique and complete")
    if args.check:
        print("STATUS: PASS_STAGE05_FRESH_ANCHOR_PREFLIGHT_ONLY")
        return 0
    execute(args, paths, out, stage03, evidence)
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("STAGE05_FAILED:", repr(exc), file=sys.stderr)
        raise
