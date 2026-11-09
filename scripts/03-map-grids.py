"""Rebuild observed-year SPAM to FABLE 5-arcminute grids in an isolated directory.

The implementation follows the source-only observed mapping in
03_build_SPAM_FABLE_observed_5arcmin_FINAL-Copy1:

* Apply frozen Notebook 02 direct and country-year weighted mappings.
* Allocate harvested and physical irrigated/rainfed areas independently.
* Aggregate at grid, country, year and FABLE product before computing totals
  and harvesting intensity.
* Keep unsupported SPAM2000 parent categories unsupported. This stage never
  introduces external area magnitudes or a historical country completion.
* Compare fresh spatial outputs with read-only validated references.
* Recognize the original 25-column 2005/2010/2020 reference format only
  when its five legacy metadata fields match the production specification.
  The repaired 2000 reference uses 20 columns. All 20 common fields are
  compared strictly and separately at native-grid spatial resolution.

This is Stage 03 of the reproducibility workflow. Running only this stage does
not establish full raw-source-to-Calculator reproducibility.

Usage:
  python 03_SPAM_FABLE_clean_start_observed_grid_mapping.py --check
  python 03_SPAM_FABLE_clean_start_observed_grid_mapping.py --execute

Use --input-root and --output-root for a truly isolated upstream clean build.
The script never writes the existing canonical 03_observed or 04_final_release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

import numpy as np
import pandas as pd

try:
    import duckdb
except ImportError as exc:
    raise SystemExit(
        "DuckDB is required. Activate your pythongeo environment and install duckdb."
    ) from exc

YEARS = (2000, 2005, 2010, 2020)
AREA_COMPONENTS = (
    "harvested_irrigated_ha",
    "harvested_rainfed_ha",
    "physical_irrigated_ha",
    "physical_rainfed_ha",
)
AREA_OUTPUTS = (
    "harvested_irrigated_ha",
    "harvested_rainfed_ha",
    "harvested_total_ha",
    "physical_irrigated_ha",
    "physical_rainfed_ha",
    "physical_total_ha",
)
INTENSITY_OUTPUTS = (
    "harvesting_intensity_irrigated",
    "harvesting_intensity_rainfed",
    "harvesting_intensity_total",
)
KEYS = (
    "grid_id_5min",
    "grid_row_5min",
    "grid_col_5min",
    "longitude",
    "latitude",
    "country_iso3",
    "year",
    "fable_product",
)
OUTPUT_COLUMNS = list(KEYS) + [
    "harvested_irrigated_ha", "harvested_rainfed_ha", "harvested_total_ha",
    "physical_irrigated_ha", "physical_rainfed_ha", "physical_total_ha",
    "harvesting_intensity_irrigated", "harvesting_intensity_rainfed",
    "harvesting_intensity_total", "mapping_method", "source_dataset",
    "temporal_status",
]

# The original pre-repair production Notebook 03 saved the 2005, 2010
# and 2020 observed outputs with five extra provenance columns. The
# subsequently repaired pure-SPAM year-2000 reference has 20 columns.
# Both have the same 20 scientific core fields. Additional columns are
# accepted only for this exact documented, source-verified 25-column
# legacy format and only when their values meet the original contract.
LEGACY_REFERENCE_COLUMNS = [
    "data_status",
    "h_ir_spatial_basis", "h_rf_spatial_basis",
    "p_ir_spatial_basis", "p_rf_spatial_basis",
]
LEGACY_DATA_STATUS = "observed_or_weighted_SPAM"
NATIVE_REQUIRED = [
    "grid_id_5min", "grid_row_5min", "grid_col_5min", "longitude",
    "latitude", "country_iso3", "year", "source_product_code",
    *AREA_COMPONENTS,
]
DEFAULT_REFERENCE = Path("data")

def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def quote(path: Path) -> str:
    return path.as_posix().replace("'", "''")

def is_under(candidate: Path, base: Path) -> bool:
    candidate, base = candidate.resolve(), base.resolve()
    return candidate == base or base in candidate.parents

def check_safe_output(output_root: Path, reference_root: Path, input_root: Path, clean_mode: bool) -> None:
    # Do not rely on the selected environment to protect published originals.
    protected = (
        reference_root / "01_native_5arcmin",
        reference_root / "02_mapping",
        reference_root / "03_observed_FABLE_5arcmin",
        reference_root / "04_final_release",
        input_root / "01_native_5arcmin",
        input_root / "02_mapping",
    )
    if any(is_under(output_root, part) for part in protected):
        raise ValueError("Refusing to write inside a protected input or release folder")
    if is_under(reference_root, output_root) or (is_under(input_root, output_root) and not clean_mode):
        raise ValueError("Output root cannot contain protected source data")
    if output_root == reference_root or (output_root == input_root and not clean_mode):
        raise ValueError("Output root must be separate from the saved source root")

def preflight(input_root: Path, reference_root: Path, years: tuple[int, ...],
              compare: bool, native_dir: Path | None = None,
              mapping_dir: Path | None = None) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    mapping = mapping_dir or (input_root / "02_mapping")
    paths = {
        "direct_mapping": mapping / "02_SPAM_FABLE_direct_edges.csv",
        "weighted_mapping": mapping / "02_SPAM_FABLE_operational_country_year_weights.csv.gz",
    }
    for year in years:
        paths[f"native_{year}"] = (
            (native_dir or (input_root / "01_native_5arcmin")) / f"01_SPAM_native_5arcmin_{year}.parquet"
        )
        if compare:
            paths[f"reference_mapped_{year}"] = (
                reference_root / "03_observed_FABLE_5arcmin"
                / f"03_SPAM_FABLE_observed_5arcmin_{year}.parquet"
            )
    missing = {key: str(path) for key, path in paths.items() if not path.is_file()}
    if missing:
        raise FileNotFoundError(f"Missing required Stage 03 inputs: {missing}")

    direct = pd.read_csv(paths["direct_mapping"], low_memory=False)
    weighted = pd.read_csv(paths["weighted_mapping"], low_memory=False)
    for name, frame in (("direct", direct), ("weighted", weighted)):
        required = {"year", "source_product_code", "fable_product", "weight"}
        if name == "weighted":
            required.add("country_iso3")
        absent = required - set(frame.columns)
        if absent:
            raise ValueError(f"{name} mapping missing columns: {sorted(absent)}")
        frame["year"] = pd.to_numeric(frame["year"], errors="raise").astype(int)
        frame["source_product_code"] = (
            frame["source_product_code"].astype("string").str.strip().str.lower()
        )
        frame["fable_product"] = frame["fable_product"].astype("string").str.strip()
        frame["weight"] = pd.to_numeric(frame["weight"], errors="raise")
        if "country_iso3" in frame:
            frame["country_iso3"] = (
                frame["country_iso3"].astype("string").str.strip().str.upper()
            )
        invalid = (
            frame[["year", "source_product_code", "fable_product", "weight"]]
            .isna().any(axis=1)
            | ~np.isfinite(frame["weight"])
            | ~frame["weight"].between(0, 1)
        )
        if invalid.any():
            raise ValueError(f"Invalid or missing {name} mapping entries: {int(invalid.sum())}")

    direct_cols = ["year", "source_product_code", "fable_product"]
    weighted_cols = ["country_iso3", *direct_cols]
    if direct.duplicated(direct_cols).any():
        raise ValueError("Duplicate frozen direct mapping edges")
    if weighted.duplicated(weighted_cols).any():
        raise ValueError("Duplicate frozen weighted mapping edges")

    parent_direct = set(zip(direct["year"], direct["source_product_code"]))
    parent_weighted = set(zip(weighted["year"], weighted["source_product_code"]))
    if parent_direct & parent_weighted:
        raise ValueError("Direct and weighted mappings share one or more source parent-years")

    weight_sum = weighted.groupby(
        ["country_iso3", "year", "source_product_code"], as_index=False
    )["weight"].sum()
    if not np.isclose(weight_sum["weight"], 1, atol=1e-8, rtol=1e-9).all():
        raise ValueError("Country-level weighted parent allocation does not sum to one")

    # Four native area components are necessary. The 2000 OTHE physical-only
    # source limitation must not be transformed into an invented IR/RF split.
    con = duckdb.connect(":memory:")
    try:
        for year in years:
            path = quote(paths[f"native_{year}"])
            found = set(con.execute(
                f"DESCRIBE SELECT * FROM read_parquet('{path}')"
            ).df()["column_name"])
            if set(NATIVE_REQUIRED) - found:
                raise ValueError(
                    f"Native {year} file lacks: {sorted(set(NATIVE_REQUIRED) - found)}"
                )
            wrong_years = con.execute(
                f"SELECT COUNT(*) FROM read_parquet('{path}') "
                f"WHERE year IS NULL OR CAST(year AS INTEGER) <> {year}"
            ).fetchone()[0]
            if wrong_years:
                raise ValueError(f"Native {year} has {wrong_years} rows for other years")
    finally:
        con.close()
    return paths, direct, weighted

def create_views(con, native_path: Path) -> None:
    source = quote(native_path)
    con.execute(f"CREATE VIEW native_03 AS SELECT * FROM read_parquet('{source}')")
    con.execute("""
        CREATE VIEW direct_alloc_03 AS
        SELECT
            n.grid_id_5min, n.grid_row_5min, n.grid_col_5min,
            n.longitude, n.latitude,
            UPPER(TRIM(n.country_iso3)) AS country_iso3,
            CAST(n.year AS INTEGER) AS year,
            LOWER(TRIM(n.source_product_code)) AS source_product_code,
            d.fable_product,
            n.harvested_irrigated_ha * CAST(d.weight AS DOUBLE) AS harvested_irrigated_ha,
            n.harvested_rainfed_ha * CAST(d.weight AS DOUBLE) AS harvested_rainfed_ha,
            n.physical_irrigated_ha * CAST(d.weight AS DOUBLE) AS physical_irrigated_ha,
            n.physical_rainfed_ha * CAST(d.weight AS DOUBLE) AS physical_rainfed_ha,
            0 AS is_weighted, 1 AS is_direct
        FROM native_03 n
        INNER JOIN frozen_direct_03 d
        ON CAST(n.year AS INTEGER) = d.year
           AND LOWER(TRIM(n.source_product_code)) = d.source_product_code
    """)
    con.execute("""
        CREATE VIEW weighted_alloc_03 AS
        SELECT
            n.grid_id_5min, n.grid_row_5min, n.grid_col_5min,
            n.longitude, n.latitude,
            UPPER(TRIM(n.country_iso3)) AS country_iso3,
            CAST(n.year AS INTEGER) AS year,
            LOWER(TRIM(n.source_product_code)) AS source_product_code,
            w.fable_product,
            n.harvested_irrigated_ha * CAST(w.weight AS DOUBLE) AS harvested_irrigated_ha,
            n.harvested_rainfed_ha * CAST(w.weight AS DOUBLE) AS harvested_rainfed_ha,
            n.physical_irrigated_ha * CAST(w.weight AS DOUBLE) AS physical_irrigated_ha,
            n.physical_rainfed_ha * CAST(w.weight AS DOUBLE) AS physical_rainfed_ha,
            1 AS is_weighted, 0 AS is_direct
        FROM native_03 n
        INNER JOIN frozen_weighted_03 w
        ON UPPER(TRIM(n.country_iso3)) = w.country_iso3
           AND CAST(n.year AS INTEGER) = w.year
           AND LOWER(TRIM(n.source_product_code)) = w.source_product_code
    """)
    con.execute("""
        CREATE VIEW all_alloc_03 AS
        SELECT * FROM direct_alloc_03
        UNION ALL
        SELECT * FROM weighted_alloc_03
    """)

def export_coverage(con, output: Path) -> None:
    con.execute(f"""
        COPY (
            SELECT DISTINCT grid_id_5min, grid_row_5min, grid_col_5min,
                   longitude, latitude, UPPER(TRIM(country_iso3)) AS country_iso3,
                   CAST(year AS INTEGER) AS year
            FROM native_03
        ) TO '{quote(output)}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)

def export_mapped(con, output: Path) -> None:
    # The order and labels follow the original production Notebook 03.
    con.execute(f"""
        COPY (
            WITH grouped AS (
                SELECT grid_id_5min, grid_row_5min, grid_col_5min,
                       longitude, latitude, country_iso3, year, fable_product,
                       SUM(harvested_irrigated_ha) AS harvested_irrigated_ha,
                       SUM(harvested_rainfed_ha) AS harvested_rainfed_ha,
                       SUM(physical_irrigated_ha) AS physical_irrigated_ha,
                       SUM(physical_rainfed_ha) AS physical_rainfed_ha,
                       MAX(is_weighted) AS has_weighted_component,
                       MAX(is_direct) AS has_direct_component
                FROM all_alloc_03
                GROUP BY grid_id_5min, grid_row_5min, grid_col_5min,
                         longitude, latitude, country_iso3, year, fable_product
            )
            SELECT grid_id_5min, grid_row_5min, grid_col_5min,
                   longitude, latitude, country_iso3, year, fable_product,
                   harvested_irrigated_ha,
                   harvested_rainfed_ha,
                   harvested_irrigated_ha + harvested_rainfed_ha AS harvested_total_ha,
                   physical_irrigated_ha,
                   physical_rainfed_ha,
                   physical_irrigated_ha + physical_rainfed_ha AS physical_total_ha,
                   CASE WHEN physical_irrigated_ha > 0
                        THEN harvested_irrigated_ha / physical_irrigated_ha
                        ELSE NULL END AS harvesting_intensity_irrigated,
                   CASE WHEN physical_rainfed_ha > 0
                        THEN harvested_rainfed_ha / physical_rainfed_ha
                        ELSE NULL END AS harvesting_intensity_rainfed,
                   CASE WHEN (physical_irrigated_ha + physical_rainfed_ha) > 0
                        THEN (harvested_irrigated_ha + harvested_rainfed_ha)
                             / (physical_irrigated_ha + physical_rainfed_ha)
                        ELSE NULL END AS harvesting_intensity_total,
                   CASE WHEN has_weighted_component = 1 AND has_direct_component = 1
                        THEN 'mixed_direct_and_weighted'
                        WHEN has_weighted_component = 1 THEN 'one_to_many_weighted'
                        ELSE 'direct_or_many_to_one' END AS mapping_method,
                   'SPAM' AS source_dataset,
                   'observed_SPAM' AS temporal_status
            FROM grouped
            WHERE harvested_irrigated_ha <> 0
               OR harvested_rainfed_ha <> 0
               OR physical_irrigated_ha <> 0
               OR physical_rainfed_ha <> 0
        ) TO '{quote(output)}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)

def export_parent_qc(con, output: Path, year: int) -> dict:
    source_parent = con.execute("""
        SELECT UPPER(TRIM(country_iso3)) AS country_iso3,
               CAST(year AS INTEGER) AS year,
               LOWER(TRIM(source_product_code)) AS source_product_code,
               SUM(harvested_irrigated_ha) AS src_h_ir,
               SUM(harvested_rainfed_ha) AS src_h_rf,
               SUM(physical_irrigated_ha) AS src_p_ir,
               SUM(physical_rainfed_ha) AS src_p_rf
        FROM native_03 GROUP BY 1, 2, 3
    """).df()
    allocated_parent = con.execute("""
        SELECT country_iso3, year, source_product_code,
               SUM(harvested_irrigated_ha) AS alloc_h_ir,
               SUM(harvested_rainfed_ha) AS alloc_h_rf,
               SUM(physical_irrigated_ha) AS alloc_p_ir,
               SUM(physical_rainfed_ha) AS alloc_p_rf
        FROM all_alloc_03 GROUP BY 1, 2, 3
    """).df()
    keys = ["country_iso3", "year", "source_product_code"]
    if allocated_parent.duplicated(keys).any() or source_parent.duplicated(keys).any():
        raise AssertionError(f"{year}: duplicate native or allocated parent groups")
    audit = source_parent.merge(allocated_parent, on=keys, how="left", validate="one_to_one")
    alloc_cols = ["alloc_h_ir", "alloc_h_rf", "alloc_p_ir", "alloc_p_rf"]
    audit["mapped_parent"] = audit[alloc_cols].notna().any(axis=1)
    for short in ("h_ir", "h_rf", "p_ir", "p_rf"):
        audit[f"difference_{short}_ha"] = (
            audit[f"alloc_{short}"] - audit[f"src_{short}"]
        )
    mapped = audit.loc[audit["mapped_parent"]].copy()
    fields = [(f"alloc_{s}", f"src_{s}") for s in ("h_ir", "h_rf", "p_ir", "p_rf")]
    differences = mapped[[f"difference_{s}_ha" for s in ("h_ir", "h_rf", "p_ir", "p_rf")]]
    if len(mapped):
        valid = np.logical_and.reduce([
            np.isclose(mapped[a].to_numpy(float), mapped[b].to_numpy(float),
                       atol=1e-4, rtol=1e-9, equal_nan=True)
            for a, b in fields
        ])
        mapped["conservation_pass"] = valid
        audit["conservation_pass"] = pd.NA
        audit.loc[mapped.index, "conservation_pass"] = valid
    else:
        audit["conservation_pass"] = pd.NA
        valid = np.empty(0, dtype=bool)

    # Source 2000 OTHE is total physical area only, with no observed split.
    # It must not be silently treated as zero or externally completed here.
    component_values = audit[["src_h_ir", "src_h_rf", "src_p_ir", "src_p_rf"]]
    positive = component_values.gt(1e-8).any(axis=1)
    missing_positive = audit[~audit["mapped_parent"] & positive].copy()
    for component in ("h_ir", "h_rf", "p_ir", "p_rf"):
        audit.loc[:, f"difference_{component}_ha"] = audit[f"difference_{component}_ha"]
    audit.to_csv(output, index=False, compression="gzip")
    failed = int((~valid).sum())
    if failed or len(missing_positive):
        details = {
            "conservation_failures": failed,
            "unmapped_positive_component_parents": len(missing_positive),
        }
        raise AssertionError(f"Year {year} parent conservation failed: {details}")
    return {
        "year": year,
        "source_parent_groups": len(source_parent),
        "mapped_parent_groups": len(mapped),
        "unmapped_positive_component_parents": 0,
        "conservation_failures": 0,
        "max_mapped_component_error_ha": (
            float(differences.abs().max().max()) if len(mapped) else 0.0
        ),
    }

def export_ooil_lineage(con, output: Path) -> int:
    # This is an additional source-specific sidecar, not a new area magnitude.
    con.execute(f"""
        COPY (
            SELECT country_iso3, year, source_product_code, fable_product,
                   SUM(harvested_irrigated_ha) AS harvested_irrigated_ha,
                   SUM(harvested_rainfed_ha) AS harvested_rainfed_ha,
                   SUM(physical_irrigated_ha) AS physical_irrigated_ha,
                   SUM(physical_rainfed_ha) AS physical_rainfed_ha,
                   SUM(harvested_irrigated_ha) + SUM(harvested_rainfed_ha)
                     AS harvested_total_ha,
                   SUM(physical_irrigated_ha) + SUM(physical_rainfed_ha)
                     AS physical_total_ha
            FROM all_alloc_03
            WHERE source_product_code = 'ooil'
            GROUP BY 1, 2, 3, 4
        ) TO '{quote(output)}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    return int(con.execute(
        f"SELECT COUNT(*) FROM read_parquet('{quote(output)}')"
    ).fetchone()[0])

def structure_check(con, produced: Path, year: int) -> dict:
    path = quote(produced)
    found = [r[0] for r in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{path}')"
    ).fetchall()]
    if found != OUTPUT_COLUMNS:
        raise AssertionError(f"{year}: output schema differs from original 20-column layout: {found}")
    result = con.execute(f"""
        SELECT COUNT(*) AS rows,
               COUNT(DISTINCT grid_id_5min) AS grids,
               COUNT(DISTINCT country_iso3) AS countries,
               COUNT(DISTINCT fable_product) AS represented_fable_products,
               COUNT(DISTINCT (grid_id_5min, grid_row_5min, grid_col_5min,
                               longitude, latitude, country_iso3, year, fable_product))
                 AS unique_grid_country_products
        FROM read_parquet('{path}')
    """).df().iloc[0].to_dict()
    if int(result["unique_grid_country_products"]) != int(result["rows"]):
        raise AssertionError(f"{year}: duplicate grid-country-product output keys")
    result["year"] = year
    return {k: int(v) if isinstance(v, (int, np.integer)) else v for k, v in result.items()}

def reference_schema_check(columns_ref: list[str], year: int) -> str:
    """Recognize only the two documented observed-grid reference schemas.

    The 20 shared core fields must appear in their original order. The
    five legacy provenance fields are permitted only as the full, ordered
    group. Unexpected scientific columns, missing columns, or arbitrary
    extras are NOT accepted.
    """
    if columns_ref == OUTPUT_COLUMNS:
        return "EXACT_20_COLUMN_CORE"
    if columns_ref == OUTPUT_COLUMNS + LEGACY_REFERENCE_COLUMNS and year != 2000:
        return "VALIDATED_25_COLUMN_LEGACY_REFERENCE"
    missing = [col for col in OUTPUT_COLUMNS if col not in columns_ref]
    extra = [col for col in columns_ref if col not in OUTPUT_COLUMNS]
    raise AssertionError(
        f"{year}: unsupported saved reference schema. "
        f"Core missing={missing}; reference-only columns={extra}; "
        f"actual={columns_ref}; expected core={OUTPUT_COLUMNS}. "
        "Only the documented extra five 2005/2010/2020 provenance "
        "columns are supported. No scientific field is silently dropped."
    )

def compare_reference(con, produced: Path, reference: Path, year: int,
                      area_tol: float, intensity_tol: float) -> dict:
    fresh = quote(produced)
    ref = quote(reference)
    columns_ref = [r[0] for r in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{ref}')"
    ).fetchall()]
    schema_mode = reference_schema_check(columns_ref, year)

    # The original 25-column observed-year producer wrote one constant
    # data-status value and four NULL spatial-basis fields. They are not
    # ignored: a changed or populated provenance field is a hard failure.
    legacy_failures = {}
    if schema_mode == "VALIDATED_25_COLUMN_LEGACY_REFERENCE":
        clauses = [
            f"COUNT(*) FILTER (WHERE data_status IS DISTINCT FROM "
            f"'{LEGACY_DATA_STATUS}') AS invalid_data_status"
        ]
        clauses.extend(
            f"COUNT(*) FILTER (WHERE {col} IS NOT NULL) AS nonnull_{col}"
            for col in LEGACY_REFERENCE_COLUMNS[1:]
        )
        row = con.execute(
            f"SELECT {', '.join(clauses)} FROM read_parquet('{ref}')"
        ).df().iloc[0].to_dict()
        legacy_failures = {name: int(value) for name, value in row.items()}
        if any(legacy_failures.values()):
            raise AssertionError(
                f"{year}: legacy reference provenance does not match "
                f"the original observed-year production contract: {legacy_failures}"
            )

    # Compare every common scientific/core metadata field using the
    # full spatial key. No country/year/product aggregates substitute for
    # native-grid identity. All null mismatches remain strict failures.
    joint = " AND ".join(f"a.{col} IS NOT DISTINCT FROM b.{col}" for col in KEYS)
    values = []
    for col in AREA_OUTPUTS + INTENSITY_OUTPUTS:
        tol = area_tol if col in AREA_OUTPUTS else intensity_tol
        equal = (
            f"(CASE "
            f"WHEN a.{col} IS NULL AND b.{col} IS NULL THEN TRUE "
            f"WHEN a.{col} IS NULL OR b.{col} IS NULL THEN FALSE "
            f"WHEN a.{col} IS NOT DISTINCT FROM b.{col} THEN TRUE "
            f"WHEN isfinite(a.{col}) AND isfinite(b.{col}) "
            f"THEN ABS(a.{col}-b.{col}) <= {tol} "
            f"ELSE FALSE END)"
        )
        values.append((col, equal))
    for col in ("mapping_method", "source_dataset", "temporal_status"):
        values.append((col, f"a.{col} IS NOT DISTINCT FROM b.{col}"))
    field_failure_selects = [
        f"SUM(CASE WHEN a.__is_a IS NOT NULL AND b.__is_b IS NOT NULL "
        f"AND NOT ({expr}) THEN 1 ELSE 0 END) AS failures_{col}"
        for col, expr in values
    ]
    row_bad = " OR ".join(f"NOT ({expr})" for _, expr in values)
    result = con.execute(f"""
        WITH a AS (
            SELECT 1 AS __is_a, * FROM read_parquet('{fresh}')
        ), b AS (
            SELECT 1 AS __is_b, * FROM read_parquet('{ref}')
        )
        SELECT
            COUNT(*) AS compared_key_union,
            COUNT(*) FILTER (WHERE a.__is_a IS NULL OR b.__is_b IS NULL)
                AS missing_or_extra_keys,
            COUNT(*) FILTER (WHERE a.__is_a IS NOT NULL AND b.__is_b IS NOT NULL
                             AND ({row_bad})) AS mismatched_rows,
            {', '.join(field_failure_selects)}
        FROM a FULL OUTER JOIN b ON {joint}
    """).df().iloc[0].to_dict()
    failures = int(result["missing_or_extra_keys"]) + int(result["mismatched_rows"])
    result["reference_schema_mode"] = schema_mode
    result["reference_only_columns"] = (
        LEGACY_REFERENCE_COLUMNS if legacy_failures else []
    )
    result["legacy_metadata_failures"] = sum(legacy_failures.values())
    result["legacy_metadata_check_details"] = legacy_failures
    result["status"] = (
        "PASS_FRESH_OBSERVED_GRID_REFERENCE_CORE_IDENTITY_WITH_LEGACY_METADATA"
        if failures == 0 and legacy_failures
        else "PASS_FRESH_OBSERVED_GRID_REFERENCE_IDENTITY"
        if failures == 0
        else "FAIL_FRESH_OBSERVED_GRID_REFERENCE_MISMATCH"
    )
    result["year"] = year
    return result

def compare_coverage(con, produced: Path, reference: Path, year: int) -> dict:
    if not reference.is_file():
        return {"year": year, "status": "REFERENCE_COVERAGE_NOT_ARCHIVED"}
    a, b = quote(produced), quote(reference)
    # Coverage is a set of native grid-country-year records, not a sorted file.
    difference = con.execute(f"""
        WITH a AS (SELECT * FROM read_parquet('{a}')),
             b AS (SELECT * FROM read_parquet('{b}'))
        SELECT (
            SELECT COUNT(*) FROM (SELECT * FROM a EXCEPT SELECT * FROM b)
        ) + (
            SELECT COUNT(*) FROM (SELECT * FROM b EXCEPT SELECT * FROM a)
        ) AS different_keys
    """).fetchone()[0]
    return {
        "year": year,
        "different_keys": int(difference),
        "status": "PASS_GRID_COVERAGE_IDENTITY" if difference == 0 else "FAIL_GRID_COVERAGE_MISMATCH",
    }

def build_year(year: int, source_root: Path, output_root: Path,
               reference_root: Path, direct: pd.DataFrame, weighted: pd.DataFrame,
               compare: bool, threads: int, memory_limit: str,
               area_tol: float, intensity_tol: float,
               native_dir: Path | None = None) -> dict:
    base = output_root / "03_observed_FABLE_5arcmin"
    cov = base / "grid_coverage"
    qc = base / "qc"
    for directory in (base, cov, qc):
        directory.mkdir(parents=True, exist_ok=True)
    native_path = (native_dir or (source_root / "01_native_5arcmin")) / f"01_SPAM_native_5arcmin_{year}.parquet"
    target = base / f"03_SPAM_FABLE_observed_5arcmin_{year}.parquet"
    coverage = cov / f"03_SPAM_grid_coverage_5arcmin_{year}.parquet"
    parent_file = qc / f"03_parent_conservation_{year}.csv.gz"
    ooil_file = qc / "03_OOIL_source_parent_child_2000.parquet"
    expected_files = [target, coverage, parent_file] + ([ooil_file] if year == 2000 else [])
    if any(path.exists() for path in expected_files):
        raise FileExistsError(
            f"Refusing to overwrite an existing clean-run output: "
            f"{[str(p) for p in expected_files if p.exists()]}"
        )

    d = direct.loc[direct["year"].eq(year)].copy()
    w = weighted.loc[weighted["year"].eq(year)].copy()
    if d.empty and w.empty:
        raise ValueError(f"{year}: no frozen mapping is available")
    con = duckdb.connect(":memory:")
    try:
        con.execute(f"SET threads={int(threads)}")
        con.execute(f"SET memory_limit='{memory_limit}'")
        con.execute("SET preserve_insertion_order=false")
        spill = output_root / "duckdb_spill"
        spill.mkdir(parents=True, exist_ok=True)
        con.execute(f"SET temp_directory='{quote(spill)}'")
        con.register("frozen_direct_03", d)
        con.register("frozen_weighted_03", w)
        create_views(con, native_path)
        export_coverage(con, coverage)
        export_mapped(con, target)
        source_parent_qc = export_parent_qc(con, parent_file, year)
        ooil_edges = export_ooil_lineage(con, ooil_file) if year == 2000 else None
        output_structure = structure_check(con, target, year)
        ref_check = None
        cov_check = None
        if compare:
            ref = reference_root / "03_observed_FABLE_5arcmin" / target.name
            ref_check = compare_reference(con, target, ref, year, area_tol, intensity_tol)
            ref_cov = reference_root / "03_observed_FABLE_5arcmin" / "grid_coverage" / coverage.name
            cov_check = compare_coverage(con, coverage, ref_cov, year)
            pd.DataFrame([ref_check]).to_csv(
                qc / f"03_reference_comparison_{year}.csv", index=False
            )
            pd.DataFrame([cov_check]).to_csv(
                qc / f"03_coverage_comparison_{year}.csv", index=False
            )
            if ref_check["status"].startswith("FAIL") or (cov_check and cov_check["status"].startswith("FAIL")):
                raise AssertionError(
                    f"Year {year} fresh spatial output differs from validated reference. "
                    f"Mapped QC: {ref_check}; coverage QC: {cov_check}"
                )
        return {
            "year": year,
            "native_input": str(native_path),
            "mapped_output": str(target),
            "grid_coverage": str(coverage),
            "parent_qc_file": str(parent_file),
            "ooil_lineage_file": str(ooil_file) if year == 2000 else None,
            "ooil_parent_child_edges": ooil_edges,
            "source_parent_qc": source_parent_qc,
            "output_structure": output_structure,
            "reference_comparison": ref_check,
            "coverage_comparison": cov_check,
            "mapped_output_sha256": digest(target),
            "status": (
                "PASS_STAGE03_FRESH_GRID_AND_REFERENCE_QC"
                if compare else "BUILT_STAGE03_REFERENCE_NOT_CHECKED"
            ),
        }
    finally:
        con.close()

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-dir", type=Path, default=None,
                        help="Directory with freshly built native Parquet files")
    parser.add_argument("--mapping-dir", type=Path, default=None,
                        help="Directory with freshly rebuilt mapping CSV files")
    parser.add_argument("--input-root", type=Path, default=None,
                        help="SPAM_FABLE_FINAL containing native and mapping folders")
    parser.add_argument("--reference-root", type=Path, default=DEFAULT_REFERENCE,
                        help="Read-only validated SPAM_FABLE_FINAL reference")
    parser.add_argument("--output-root", type=Path, default=None,
                        help="Separate new directory; no existing output files overwritten")
    parser.add_argument("--years", nargs="+", type=int, default=list(YEARS), choices=YEARS)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--memory-limit", default="6GB")
    parser.add_argument("--area-tol", type=float, default=1e-5)
    parser.add_argument("--intensity-tol", type=float, default=1e-9)
    parser.add_argument("--skip-reference", action="store_true",
                        help="Build only. No claim of saved-reference identity")
    execution = parser.add_mutually_exclusive_group(required=True)
    execution.add_argument("--check", action="store_true", help="Read-only input preflight")
    execution.add_argument("--execute", action="store_true", help="Run fresh Stage 03")
    args = parser.parse_args()
    years = tuple(sorted(set(args.years)))
    ref = args.reference_root.expanduser().resolve()
    inroot = (args.input_root or Path(os.environ.get("SPAM_FABLE_ROOT", str(ref)))).expanduser().resolve()
    clean_mode = os.environ.get("SPAM_CLEAN_REBUILD", "0") == "1"
    default_output = (
        inroot if clean_mode else
        inroot / "05_publication_audit" / "v1_0_candidate" / "clean_stage03"
    )
    outroot = (args.output_root or default_output).expanduser().resolve()
    if clean_mode and inroot == ref:
        raise ValueError("Clean mode cannot use the canonical reference as the source root")
    check_safe_output(outroot, ref, inroot, clean_mode)
    if args.threads < 1 or args.area_tol < 0 or args.intensity_tol < 0:
        raise ValueError("Invalid threads or tolerance")
    compare = not args.skip_reference
    native_dir = args.native_dir.expanduser().resolve() if args.native_dir else None
    mapping_dir = args.mapping_dir.expanduser().resolve() if args.mapping_dir else None
    paths, direct, weighted = preflight(inroot, ref, years, compare,
                                       native_dir=native_dir, mapping_dir=mapping_dir)
    print("Stage 03 observed-year mapping preflight")
    print("Source root:", inroot)
    print("Clean output root:", outroot)
    print("Reference root:", str(ref) if compare else "Not compared")
    print("Years:", years)
    print("Required inputs:", len(paths), "found")
    print("Direct mapping edges:", len(direct))
    print("Weighted mapping edges:", len(weighted))
    if args.check:
        print("STATUS: PASS_STAGE03_INPUT_PREFLIGHT_ONLY")
        return 0
    if not clean_mode and outroot.exists() and any(outroot.iterdir()):
        raise FileExistsError(
            "Output root is not empty. Choose a new clean directory to avoid reusing outputs: "
            + str(outroot)
        )
    if clean_mode and (outroot / "03_observed_FABLE_5arcmin").exists():
        raise FileExistsError(
            "Stage 03 output directory already exists in clean workspace: "
            + str(outroot / "03_observed_FABLE_5arcmin")
        )
    outroot.mkdir(parents=True, exist_ok=True)
    results = []
    for year in years:
        print(f"Building fresh mapped 5-arcminute year {year} ...", flush=True)
        result = build_year(year, inroot, outroot, ref, direct, weighted, compare,
                            args.threads, args.memory_limit,
                            args.area_tol, args.intensity_tol, native_dir=native_dir)
        results.append(result)
        mode = (result["reference_comparison"] or {}).get(
            "reference_schema_mode", "NOT_COMPARED"
        )
        print(
            f"{year}: {result['status']}; "
            f"rows={result['output_structure']['rows']:,}; "
            f"reference_schema={mode}",
            flush=True,
        )
    summary = pd.DataFrame([
        {
            "year": item["year"],
            "rows": item["output_structure"]["rows"],
            "countries": item["output_structure"]["countries"],
            "represented_products": item["output_structure"]["represented_fable_products"],
            "parent_conservation_failures": item["source_parent_qc"]["conservation_failures"],
            "reference_mismatch_rows": (
                item["reference_comparison"]["mismatched_rows"]
                if item["reference_comparison"] else pd.NA
            ),
            "reference_schema_mode": (
                item["reference_comparison"].get("reference_schema_mode", "NOT_COMPARED")
                if item["reference_comparison"] else "NOT_COMPARED"
            ),
            "reference_missing_or_extra_keys": (
                item["reference_comparison"]["missing_or_extra_keys"]
                if item["reference_comparison"] else pd.NA
            ),
            "status": item["status"],
        }
        for item in results
    ])
    summary.to_csv(outroot / "03_clean_start_observed_mapping_summary.csv", index=False)
    inputs = {
        key: {"path": str(path), "sha256": digest(path)}
        for key, path in paths.items()
    }
    manifest = {
        "stage": "03_observed_grid_mapping",
        "execution": "new_outputs_from_native_and_frozen_mapping",
        "full_raw_to_release_reproducibility": False,
        "reference_comparison_performed": compare,
        "source_paths": inputs,
        "source_root": str(inroot),
        "clean_output_root": str(outroot),
        "years": list(years),
        "results": results,
        "status": (
            "PASS_STAGE03_FRESH_OBSERVED_GRID_REFERENCE_MATCH"
            if compare else "STAGE03_FRESH_OUTPUT_REFERENCE_NOT_CHECKED"
        ),
    }
    (outroot / "03_clean_start_observed_mapping_manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str), encoding="utf-8"
    )
    print("\n", summary.to_string(index=False), sep="")
    print("Stage 03 manifest:", outroot / "03_clean_start_observed_mapping_manifest.json")
    print("STATUS:", manifest["status"])
    return 0

if __name__ == "__main__":
    # The six-stage runner invokes scripts without CLI arguments.
    # Explicit clean-run environment is required for this default.
    if len(sys.argv) == 1 and os.environ.get("SPAM_CLEAN_REBUILD") == "1":
        sys.argv.append("--execute")
    try:
        raise SystemExit(main())
    except Exception as error:
        print("STAGE03_FAILED:", repr(error), file=sys.stderr)
        raise
