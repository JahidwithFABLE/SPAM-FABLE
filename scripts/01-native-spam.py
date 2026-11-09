#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Stage 01: rebuild native observed SPAM data directly from original source CSVs.

Source-based implementation retains the audited release-specific readers from
01_prepare_SPAM_native_5arcmin_FINAL and 02prepareSPAMnative:

* 2000: embedded I versus H+L+S; preserve OTHE published physical total when
  its irrigated/rainfed split is unavailable, without inventing that split.
* 2005/2010: matched A/I/R sources with the 2005 alloc_key coordinate lookup.
* 2020: reconcile three sparse A/I/R sources by native grid/country/crop key.
* Join harvested and physical native crop layers, compute intensities.
* Strictly check spatial keys, areas, source totals and provenance fields
  against read-only production reference outputs at native 5 arc-minutes.

--check: read-only source preflight (schema headers, file availability).
--execute: fresh source -> native build in NEW isolated destination; then QC.

All outputs are separate from canonical SPAM_FABLE_FINAL/01_native_5arcmin.
Runs on the user's Windows pythongeo environment with duckdb and pycountry.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import sys
import traceback
from itertools import zip_longest

import numpy as np
import pandas as pd

try:
    import duckdb
    import pycountry
except ImportError as exc:
    raise SystemExit(
        "Stage 01 requires pandas, numpy, duckdb, and pycountry in pythongeo."
    ) from exc

OBSERVED_YEARS = (2000, 2005, 2010, 2020)
DEFAULT_DATA_ROOT = Path("data")
AREA_ATOL_HA = 1e-4
AREA_RTOL = 1e-6
SPAM2020_ROUNDING_ATOL_HA = 0.11
CHUNK_SIZE = 50_000
GRID_RESOLUTION_DEGREES = 5.0 / 60.0
TEXT_ENCODINGS = ["utf-8-sig", "utf-8", "cp1252", "latin1"]

def display(obj):
    """Console-only representation of non-critical production QC displays."""
    if hasattr(obj, "to_string"):
        print(obj.to_string(index=False, max_rows=20))
    else:
        print(obj)

def sql_path(path: Path) -> str:
    return path.as_posix().replace("'", "''")

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()

def protected_output_check(output: Path, data: Path, reference: Path):
    output, data, reference = output.resolve(), data.resolve(), reference.resolve()
    protected = [
        data / "SPAM", reference / "01_native_5arcmin", reference / "02_mapping",
        reference / "03_observed_FABLE_5arcmin", reference / "04_final_release",
    ]
    for src in protected:
        src = src.resolve()
        if output == src or src in output.parents or output in src.parents:
            raise ValueError(f"Refusing output overlapping protected input: {src}")
    if output == reference:
        raise ValueError("Output must be different from the reference root")

# Original native taxonomy and parsing helpers

# CELL 2 — NATIVE SPAM CROP TAXONOMIES AND CORE HELPERS

SPAM_CROPS_2000 = [
    "whea", "rice", "maiz", "barl", "mill", "sorg", "pota",
    "swpy", "cass", "banp", "soyb", "bean", "opul", "sugc",
    "sugb", "coff", "cott", "ofib", "grou", "ooil", "othe",
]

SPAM_CROPS_42 = [
    "whea", "rice", "maiz", "barl", "pmil", "smil", "sorg",
    "ocer", "pota", "swpo", "yams", "cass", "orts", "bean",
    "chic", "cowp", "pige", "lent", "opul", "soyb", "grou",
    "cnut", "oilp", "sunf", "rape", "sesa", "ooil", "sugc",
    "sugb", "cott", "ofib", "acof", "rcof", "coco", "teas",
    "toba", "bana", "plnt", "trof", "temf", "vege", "rest",
]

SPAM_CROPS_2020 = [
    "whea", "rice", "maiz", "barl", "mill", "sorg", "ocer",
    "pota", "swpo", "yams", "cass", "orts", "bean", "chic",
    "cowp", "pige", "lent", "opul", "soyb", "grou", "cnut",
    "oilp", "sunf", "rape", "sesa", "ooil", "sugc", "sugb",
    "cott", "ofib", "coff", "coco", "teas", "toba", "bana",
    "plnt", "trof", "temf", "vege", "rest", "pmil", "rcof",
    "citr", "toma", "onio", "rubb",
]

SPAM_CROPS_BY_YEAR = {
    2000: SPAM_CROPS_2000,
    2005: SPAM_CROPS_42,
    2010: SPAM_CROPS_42,
    2020: SPAM_CROPS_2020,
}

def detect_csv_format(path: Path):
    last_error = None
    for encoding in TEXT_ENCODINGS:
        try:
            with path.open("r", encoding=encoding, newline="") as stream:
                sample = stream.read(100_000)
            try:
                sep = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
            except csv.Error:
                sep = ","
            return encoding, sep
        except UnicodeDecodeError as exc:
            last_error = exc
    raise UnicodeError(f"Could not decode {path}: {last_error}")

def find_col(columns, candidates, required=True):
    lookup = {str(c).strip().lower(): c for c in columns}
    for candidate in candidates:
        if candidate.lower() in lookup:
            return lookup[candidate.lower()]
    if required:
        raise KeyError(f"None of these columns were found: {candidates}")
    return None

def split_crop_column(column, year):
    text = str(column).strip().lower()
    expected = set(SPAM_CROPS_BY_YEAR[year])

    if year == 2000:
        if text in expected:
            return text, "total"
        match = re.fullmatch(r"([a-z0-9]+)_([hils])", text)
        if match and match.group(1) in expected:
            return match.group(1), match.group(2)
        return None

    if year in {2005, 2010}:
        if text in expected:
            return text, "total"
        match = re.fullmatch(r"([a-z0-9]+)_([airhls])", text)
        if match and match.group(1) in expected:
            return match.group(1), match.group(2)
        return None

    if year == 2020 and text in expected:
        return text, "total"

    return None

def detect_crop_columns(columns, sample, year):
    rows = []
    for column in columns:
        parsed = split_crop_column(column, year)
        if parsed is None:
            continue
        crop_code, suffix = parsed
        numeric = pd.to_numeric(sample[column], errors="coerce")
        if len(sample) and numeric.notna().mean() < 0.50:
            continue
        rows.append({"column": column, "crop_code": crop_code, "suffix": suffix})
    return rows

def safe_divide(numerator, denominator):
    numerator = pd.to_numeric(numerator, errors="coerce")
    denominator = pd.to_numeric(denominator, errors="coerce")
    return np.divide(
        numerator,
        denominator,
        out=np.full(len(numerator), np.nan, dtype="float64"),
        where=denominator.to_numpy() != 0,
    )

def normalize_country_name(value):
    return re.sub(r"\s+", " ", str(value).strip()).casefold()

def optional_country_name_column(value):
    """Return an optional source-header name, normalizing pandas NA/NaN.

    SPAM2000 raw files have a country code but need not have a separate
    country-name column. Pandas may represent this missing manifest field
    as float NaN, which is truthy and must not be treated as a header name.
    """
    if value is None or pd.isna(value):
        return None
    name = str(value).strip()
    return name or None

def add_native_grid(x, y):
    lon = pd.to_numeric(x, errors="coerce")
    lat = pd.to_numeric(y, errors="coerce")
    bad = (
        lon.isna() | lat.isna()
        | lon.lt(-180) | lon.gt(180)
        | lat.lt(-90) | lat.gt(90)
    )
    if bad.any():
        raise ValueError(f"Invalid coordinates detected in {int(bad.sum())} rows.")

    ncol = int(round(360 / GRID_RESOLUTION_DEGREES))
    nrow = int(round(180 / GRID_RESOLUTION_DEGREES))

    col = np.floor((lon.to_numpy() + 180) / GRID_RESOLUTION_DEGREES + 1e-9).astype("int32")
    row = np.floor((90 - lat.to_numpy()) / GRID_RESOLUTION_DEGREES + 1e-9).astype("int32")
    col = np.clip(col, 0, ncol - 1)
    row = np.clip(row, 0, nrow - 1)

    center_lon = -180 + (col.astype(float) + 0.5) * GRID_RESOLUTION_DEGREES
    center_lat = 90 - (row.astype(float) + 0.5) * GRID_RESOLUTION_DEGREES

    return pd.DataFrame({
        "grid_row_5min": row,
        "grid_col_5min": col,
        "longitude": center_lon,
        "latitude": center_lat,
        "grid_id_5min": [f"G5_{r:04d}_{c:04d}" for r, c in zip(row, col)],
    }, index=getattr(x, "index", None))

def clean_numeric(series):
    numeric = pd.to_numeric(series, errors="coerce")
    n_missing = int(numeric.isna().sum())
    n_negative = int(numeric.lt(0).sum())
    # This follows the already-audited raw SPAM preprocessing convention.
    numeric = numeric.mask(numeric < 0, 0.0).fillna(0.0)
    return numeric.astype("float64"), n_missing, n_negative

def append_gzip_csv(df, path, header_written):
    if df.empty:
        return header_written
    df.to_csv(
        path,
        mode="a",
        header=not header_written,
        index=False,
        compression="gzip",
    )
    return True

# Explicit raw release manifest, no saved-native inputs

def raw_source_manifest(spam_root: Path) -> pd.DataFrame:
    items = [
        {"year": 2000, "area_code": "H", "technology": "embedded",
         "country_column": "stat_code", "country_name_column": None,
         "path": spam_root / "2000" / "spam2000v3.0.7_global_harvested-area.dbf-csv" / "spam_h.csv"},
        {"year": 2000, "area_code": "A", "technology": "embedded",
         "country_column": "stat_code", "country_name_column": None,
         "path": spam_root / "2000" / "spam2000v3.0.7_global_physical-area.dbf-csv" / "spam_p.csv"},
    ]
    versions = {
        2005: {
            "H": "spam2005v3r2_global_harv_area.csv",
            "A": "spam2005v3r2_global_phys_area.csv",
            "template": "spam2005V3r2_global_{area}_T{tech}.csv",
            "country": "iso3", "name": "name_cntr",
        },
        2010: {
            "H": "spam2010v2r0_global_harv_area.csv",
            "A": "spam2010v2r0_global_phys_area.csv",
            "template": "spam2010V2r0_global_{area}_T{tech}.csv",
            "country": "iso3", "name": "name_cntr",
        },
        2020: {
            "H": "spam2020V2r2_global_harvested_area",
            "A": "spam2020V2r2_global_physical_area",
            "template": "spam2020V2r2_global_{area}_T{tech}.csv",
            "country": "FIPS0", "name": "ADM0_NAME",
        },
    }
    for year, version in versions.items():
        for area in ("H", "A"):
            for tech in ("A", "I", "R"):
                items.append({
                    "year": year, "area_code": area, "technology": tech,
                    "country_column": version["country"],
                    "country_name_column": version["name"],
                    "path": spam_root / str(year) / version[area]
                            / version["template"].format(area=area, tech=tech),
                })
    return pd.DataFrame(items)

def locate_coordinate_file(data_root: Path, explicit: Path | None = None) -> Path:
    if explicit:
        explicit = explicit.expanduser().resolve()
        if not explicit.is_file():
            raise FileNotFoundError(f"2005 coordinate lookup not found: {explicit}")
        return explicit
    candidates = sorted(
        (
            p for p in (data_root / "SPAM").rglob("cell5m_allockey_xy.csv")
            if "_FABLE_processing" not in str(p)
            and "SPAM_FABLE_FINAL" not in str(p)
        ),
        key=lambda p: (len(p.parts), len(str(p))),
    )
    if not candidates:
        raise FileNotFoundError(
            "SPAM2005 cell5m_allockey_xy.csv not found. Provide --coordinate-file "
            "pointing to the original Dataverse source lookup."
        )
    return candidates[0]

def preflight(data_root: Path, reference_root: Path,
              coordinate_file: Path | None, years: tuple[int, ...],
              compare: bool) -> tuple[pd.DataFrame, pd.DataFrame, Path]:
    manifest = raw_source_manifest(data_root / "SPAM" / "CSV")
    lookup = locate_coordinate_file(data_root, coordinate_file)
    missing = manifest.loc[~manifest["path"].map(Path.is_file)]
    if len(missing):
        raise FileNotFoundError(
            "Missing original SPAM CSV files:\n" +
            "\n".join(map(str, missing["path"]))
        )
    if compare:
        for year in years:
            ref = reference_root / "01_native_5arcmin" / f"01_SPAM_native_5arcmin_{year}.parquet"
            if not ref.is_file():
                raise FileNotFoundError(f"Missing native reference for {year}: {ref}")
    schema_rows = []
    for row in manifest.itertuples(index=False):
        path = Path(row.path)
        encoding, separator = detect_csv_format(path)
        frame = pd.read_csv(path, sep=separator, encoding=encoding,
                            nrows=500, low_memory=False)
        crop_entries = detect_crop_columns(frame.columns, frame, int(row.year))
        if row.country_column not in frame.columns:
            raise ValueError(f"{row.year} {row.area_code} missing country field "
                             f"{row.country_column}: {path}")
        country_name_col = optional_country_name_column(row.country_name_column)
        if country_name_col is not None and country_name_col not in frame.columns:
            raise ValueError(f"{row.year} {row.area_code} missing country-name field "
                             f"{country_name_col}: {path}")
        if int(row.year) == 2005:
            find_col(frame.columns, ["alloc_key", "allockey"])
        else:
            find_col(frame.columns, ["x", "lon", "longitude"])
            find_col(frame.columns, ["y", "lat", "latitude"])
        expected = len(SPAM_CROPS_BY_YEAR[int(row.year)])
        if row.year == 2000:
            if len(crop_entries) != expected * 5:
                raise ValueError(f"SPAM2000 {row.area_code}: expected "
                                 f"{expected*5} embedded columns, found {len(crop_entries)}")
        elif len({x["crop_code"] for x in crop_entries}) != expected:
            raise ValueError(f"SPAM {row.year} {row.area_code} T{row.technology}: "
                             f"expected {expected} crops, observed "
                             f"{len({x['crop_code'] for x in crop_entries})}")
        schema_rows.append({
            "year": int(row.year), "area_code": row.area_code,
            "technology": row.technology, "country_column": row.country_column,
            "country_name_column": country_name_col,
            "path": path, "encoding": encoding, "separator": separator,
            "columns": frame.columns.tolist(), "crop_entries": crop_entries,
            "crop_count": len({x["crop_code"] for x in crop_entries}),
            "crop_column_count": len(crop_entries),
        })
    schema = pd.DataFrame(schema_rows)
    coord_encoding, coord_sep = detect_csv_format(lookup)
    coord_sample = pd.read_csv(lookup, nrows=5, sep=coord_sep, encoding=coord_encoding)
    find_col(coord_sample.columns, ["alloc_key", "allockey"])
    find_col(coord_sample.columns, ["x", "lon", "longitude"])
    find_col(coord_sample.columns, ["y", "lat", "latitude"])
    return manifest, schema, lookup

# Country normalization mirrors native production Notebook 01

# CELL 4 — COUNTRY CROSSWALK AND 2005 ALLOC_KEY COORDINATES

RAW_COUNTRY_NAME_TO_ISO3_OVERRIDES = {
    "Bahamas": "BHS", "The Bahamas": "BHS",
    "Bolivia": "BOL", "Bolivia (Plurinational State of)": "BOL",
    "Brunei": "BRN", "Brunei Darussalam": "BRN",
    "Cape Verde": "CPV", "Cabo Verde": "CPV",
    "Congo": "COG", "Democratic Republic of the Congo": "COD",
    "Congo, Dem. Rep.": "COD",
    "Cote dIvoire": "CIV", "Cote d'Ivoire": "CIV", "Côte d'Ivoire": "CIV",
    "Czech Republic": "CZE",
    "Eswatini": "SWZ", "Swaziland": "SWZ",
    "Iran": "IRN", "Iran (Islamic Republic of)": "IRN",
    "Kosovo": "XKX",
    "Laos": "LAO", "Lao People's Democratic Republic": "LAO",
    "Lao Peoples Democratic Republic": "LAO",
    "Micronesia (Federated States of)": "FSM",
    "Moldova": "MDA", "Moldova, Republic of": "MDA",
    "North Korea": "PRK", "Democratic People's Republic of Korea": "PRK",
    "Dem Peoples Rep of Korea": "PRK",
    "Palestine": "PSE", "State of Palestine": "PSE",
    "Russia": "RUS", "Russian Federation": "RUS",
    "South Korea": "KOR", "Republic of Korea": "KOR",
    "Syria": "SYR", "Syrian Arab Republic": "SYR",
    "Taiwan": "TWN", "Taiwan, China": "TWN",
    "Tanzania": "TZA", "Tanzania, United Republic of": "TZA",
    "Turkey": "TUR", "Türkiye": "TUR",
    "United Kingdom": "GBR",
    "United States": "USA", "United States of America": "USA",
    "Venezuela": "VEN", "Venezuela (Bolivarian Republic of)": "VEN",
    "Vietnam": "VNM", "Viet Nam": "VNM",
}
COUNTRY_NAME_TO_ISO3_OVERRIDES = {
    normalize_country_name(k): v
    for k, v in RAW_COUNTRY_NAME_TO_ISO3_OVERRIDES.items()
}
FIPS0_TO_ISO3_OVERRIDES = {
    "IV": "CIV", "KN": "PRK", "LA": "LAO", "WZ": "SWZ",
}

def country_name_to_iso3(country_name, fips0):
    raw_code = "" if pd.isna(fips0) else str(fips0).strip().upper()
    if raw_code in FIPS0_TO_ISO3_OVERRIDES:
        return FIPS0_TO_ISO3_OVERRIDES[raw_code]
    if pd.isna(country_name):
        return None
    cleaned = re.sub(r"\s+", " ", str(country_name).strip())
    normalized = normalize_country_name(cleaned)
    if normalized in COUNTRY_NAME_TO_ISO3_OVERRIDES:
        return COUNTRY_NAME_TO_ISO3_OVERRIDES[normalized]
    try:
        return pycountry.countries.lookup(cleaned).alpha_3
    except LookupError:
        try:
            return pycountry.countries.search_fuzzy(cleaned)[0].alpha_3
        except LookupError:
            return None

def standardize_country_codes(raw_codes, year, raw_names=None):
    codes = raw_codes.astype("string").str.strip().str.upper()
    result = pd.Series(pd.NA, index=codes.index, dtype="string")

    if year in {2005, 2010}:
        result[:] = codes
        return result

    if year == 2020:
        result = codes.map(SPAM2020_FIPS0_TO_ISO3).astype("string")
        if raw_names is not None:
            unresolved = result.isna()
            if unresolved.any():
                result.loc[unresolved] = [
                    country_name_to_iso3(n, c)
                    for n, c in zip(raw_names.loc[unresolved], codes.loc[unresolved])
                ]
        return result

    if year == 2000:
        already_iso = codes.str.fullmatch(r"[A-Z]{3}", na=False)
        result.loc[already_iso] = codes.loc[already_iso]

        fips_like = codes.str.fullmatch(r"[A-Z]{2}\d*", na=False)
        prefixes = codes.loc[fips_like].str[:2]

        historical = {"TO": "TGO"}
        mapped = prefixes.map(historical)
        remaining = mapped.isna()
        mapped.loc[remaining] = prefixes.loc[remaining].map(SPAM2020_FIPS0_TO_ISO3)
        result.loc[fips_like] = mapped
        return result

    raise ValueError(year)

def initialize_country_reference(data_root: Path, lookup_path: Path,
                                 source_schema: pd.DataFrame):
    global SPAM2020_FIPS0_TO_ISO3, COUNTRY_2020, COORD_2005, COORD_2005_PATH
    ref = source_schema.loc[
        source_schema["year"].eq(2020)
        & source_schema["area_code"].eq("H")
        & source_schema["technology"].eq("A")
    ].iloc[0]
    pairs = []
    for chunk in pd.read_csv(
        ref["path"], sep=ref["separator"], encoding=ref["encoding"],
        usecols=["FIPS0", "ADM0_NAME"], chunksize=CHUNK_SIZE, low_memory=False,
    ):
        pairs.append(chunk[["FIPS0", "ADM0_NAME"]].drop_duplicates())
    COUNTRY_2020 = pd.concat(pairs, ignore_index=True).drop_duplicates()
    COUNTRY_2020["FIPS0"] = COUNTRY_2020["FIPS0"].astype("string").str.strip().str.upper()
    COUNTRY_2020["ADM0_NAME"] = COUNTRY_2020["ADM0_NAME"].astype("string").str.strip()
    COUNTRY_2020["country_iso3"] = [
        country_name_to_iso3(name, code)
        for name, code in zip(COUNTRY_2020["ADM0_NAME"], COUNTRY_2020["FIPS0"])
    ]
    if COUNTRY_2020["country_iso3"].isna().any():
        display(COUNTRY_2020.loc[COUNTRY_2020["country_iso3"].isna()])
        raise ValueError("Unresolved 2020 source countries; no fallback assumptions.")
    SPAM2020_FIPS0_TO_ISO3 = dict(zip(
        COUNTRY_2020["FIPS0"], COUNTRY_2020["country_iso3"]
    ))
    COUNTRY_2020.to_csv(QC_DIR / "01_SPAM2020_country_crosswalk.csv", index=False)

    COORD_2005_PATH = lookup_path
    encoding, sep = detect_csv_format(lookup_path)
    header = pd.read_csv(lookup_path, sep=sep, encoding=encoding, nrows=5)
    alloc_col = find_col(header.columns, ["alloc_key", "allockey"])
    x_col = find_col(header.columns, ["x", "lon", "longitude"])
    y_col = find_col(header.columns, ["y", "lat", "latitude"])
    COORD_2005 = pd.read_csv(
        lookup_path, sep=sep, encoding=encoding,
        usecols=[alloc_col, x_col, y_col], low_memory=False,
    ).rename(columns={alloc_col: "alloc_key", x_col: "x", y_col: "y"})
    COORD_2005["alloc_key"] = pd.to_numeric(
        COORD_2005["alloc_key"], errors="coerce"
    ).astype("Int64")
    COORD_2005 = COORD_2005.dropna(subset=["alloc_key"]).drop_duplicates("alloc_key")
    if COORD_2005.empty:
        raise ValueError("2005 coordinate lookup produced no valid alloc_key records")
    print("2020 country crosswalk:", COUNTRY_2020["country_iso3"].nunique(), "ISO3 codes")
    print("2005 coordinate lookup:", lookup_path, len(COORD_2005), "rows")

# Production release-specific source readers

def schema_crop_lookup(schema_row):

    return {
        x["crop_code"]: x["column"]
        for x in schema_row["crop_entries"]
    }

def schema_embedded_lookup(schema_row):

    return {
        (x["crop_code"], x["suffix"]): x["column"]
        for x in schema_row["crop_entries"]
    }

def source_key_columns(schema_row):

    columns = schema_row["columns"]

    use = [
        schema_row["country_column"]
    ]

    country_name_col = optional_country_name_column(
        schema_row["country_name_column"]
    )
    if country_name_col is not None and country_name_col in columns:
        use.append(country_name_col)

    # SPAM 2005: alloc_key rather than direct x/y

    if int(schema_row["year"]) == 2005:

        use.append(
            find_col(
                columns,
                [
                    "alloc_key",
                    "allockey",
                ],
            )
        )

    else:

        use.append(
            find_col(
                columns,
                [
                    "x",
                    "lon",
                    "longitude",
                ],
            )
        )

        use.append(
            find_col(
                columns,
                [
                    "y",
                    "lat",
                    "latitude",
                ],
            )
        )

    return list(
        dict.fromkeys(use)
    )

def prepare_keys(
    chunk,
    schema_row,
):

    year = int(
        schema_row["year"]
    )

    country_col = (
        schema_row["country_column"]
    )

    country_name_col = optional_country_name_column(
        schema_row["country_name_column"]
    )

    raw_names = (
        chunk[country_name_col]
        if (country_name_col is not None and country_name_col in chunk.columns)
        else None
    )

    iso3 = standardize_country_codes(
        chunk[country_col],
        year,
        raw_names,
    )

    invalid = ~iso3.str.fullmatch(
        r"[A-Z]{3}",
        na=False,
    )

    if invalid.any():

        bad = (
            chunk.loc[
                invalid,
                [country_col],
            ]
            .assign(
                country_iso3=
                iso3.loc[
                    invalid
                ].to_numpy()
            )
            .drop_duplicates()
            .head(100)
        )

        display(bad)

        raise ValueError(
            f"Unresolved country codes in SPAM {year}."
        )

    # SPAM 2005
    # alloc_key -> x/y

    if year == 2005:

        a_col = find_col(
            chunk.columns,
            [
                "alloc_key",
                "allockey",
            ],
        )

        alloc = (
            pd.to_numeric(
                chunk[a_col],
                errors="coerce",
            )
            .astype("Int64")
        )

        coords = (
            pd.DataFrame(
                {
                    "alloc_key": alloc
                },
                index=chunk.index,
            )
            .merge(
                COORD_2005,
                on="alloc_key",
                how="left",
                validate="many_to_one",
            )
        )

        missing_coords = (
            coords[
                [
                    "x",
                    "y",
                ]
            ]
            .isna()
            .any(axis=1)
        )

        if missing_coords.any():

            raise ValueError(
                f"SPAM 2005 contains "
                f"{int(missing_coords.sum())} rows "
                "without alloc_key coordinates."
            )

        grid = (
            add_native_grid(
                coords["x"],
                coords["y"],
            )
            .reset_index(
                drop=True
            )
        )

    # SPAM 2000 / 2010 / 2020
    # direct x/y

    else:

        x_col = find_col(
            chunk.columns,
            [
                "x",
                "lon",
                "longitude",
            ],
        )

        y_col = find_col(
            chunk.columns,
            [
                "y",
                "lat",
                "latitude",
            ],
        )

        grid = (
            add_native_grid(
                chunk[x_col],
                chunk[y_col],
            )
            .reset_index(
                drop=True
            )
        )

    # IMPORTANT:
    # clean positional index, independent of original CSV chunk index.

    grid["country_iso3"] = (
        iso3
        .reset_index(
            drop=True
        )
    )

    grid["year"] = year

    return grid

def make_reader(
    schema_row,
):

    usecols = (
        source_key_columns(
            schema_row
        )
        +
        [
            x["column"]
            for x in schema_row["crop_entries"]
        ]
    )

    usecols = list(
        dict.fromkeys(
            usecols
        )
    )

    processing_encoding = (
        "latin1"
        if int(schema_row["year"]) in {
            2005,
            2010,
        }
        else schema_row["encoding"]
    )

    return pd.read_csv(
        schema_row["path"],
        sep=schema_row["separator"],
        encoding=processing_encoding,
        usecols=usecols,
        chunksize=CHUNK_SIZE,
        low_memory=False,
    )

def process_2000_area(
    area_code,
    out_path,
):

    area_code = str(
        area_code
    ).strip().upper()

    if area_code not in {
        "H",
        "A",
    }:

        raise ValueError(
            f"SPAM2000 unsupported area_code: {area_code}"
        )

    # 1. RESOLVE SOURCE SCHEMA

    schema = (
        SOURCE_SCHEMA.loc[
            SOURCE_SCHEMA[
                "year"
            ].eq(
                2000
            )
            &
            SOURCE_SCHEMA[
                "area_code"
            ].eq(
                area_code
            )
        ]
        .iloc[0]
    )

    lookup = schema_embedded_lookup(
        schema
    )

    required = {
        (
            crop,
            suffix,
        )

        for crop in SPAM_CROPS_2000

        for suffix in [
            "total",
            "h",
            "l",
            "i",
            "s",
        ]
    }

    if not required.issubset(
        set(
            lookup
        )
    ):

        raise ValueError(
            f"SPAM 2000 {area_code}: "
            "missing embedded crop columns."
        )

    # 2. CLEAN OLD SPARSE OUTPUT

    if out_path.exists():

        out_path.unlink()

    header_written = False

    # 3. QC

    qc = {

        "year":
            2000,

        "area_code":
            area_code,

        "reader_mode":
            "2000_EMBEDDED_HLIS_OTHE_TOTAL_ONLY_CORRECTED",

        "rows_read":
            0,

        "missing_numeric_cells_to_zero":
            0,

        "negative_cells_to_zero":
            0,

        "rows_written":
            0,

        "othe_total_only_rows":
            0,

        "othe_total_only_source_area_ha":
            0.0,

        "othe_split_nonzero_rows":
            0,

    }

    # 4. PROCESS SOURCE CHUNKS

    for chunk_no, chunk in enumerate(
        make_reader(
            schema
        ),
        start=1,
    ):

        keys = prepare_keys(
            chunk,
            schema,
        )

        qc[
            "rows_read"
        ] += len(
            chunk
        )

        # EACH NATIVE SPAM2000 CROP

        for crop in SPAM_CROPS_2000:

            # Published total

            total, m0, n0 = clean_numeric(
                chunk[
                    lookup[
                        (
                            crop,
                            "total",
                        )
                    ]
                ]
            )

            # Irrigated component

            irrigated, m1, n1 = clean_numeric(
                chunk[
                    lookup[
                        (
                            crop,
                            "i",
                        )
                    ]
                ]
            )

            # Rainfed production systems

            h, m2, n2 = clean_numeric(
                chunk[
                    lookup[
                        (
                            crop,
                            "h",
                        )
                    ]
                ]
            )

            l, m3, n3 = clean_numeric(
                chunk[
                    lookup[
                        (
                            crop,
                            "l",
                        )
                    ]
                ]
            )

            s, m4, n4 = clean_numeric(
                chunk[
                    lookup[
                        (
                            crop,
                            "s",
                        )
                    ]
                ]
            )

            rainfed = (
                h
                +
                l
                +
                s
            )

            computed = (
                irrigated
                +
                rainfed
            )

            # Parsing QC

            qc[
                "missing_numeric_cells_to_zero"
            ] += (
                m0
                +
                m1
                +
                m2
                +
                m3
                +
                m4
            )

            qc[
                "negative_cells_to_zero"
            ] += (
                n0
                +
                n1
                +
                n2
                +
                n3
                +
                n4
            )

            # 5. IDENTIFY SPAM2000 OTHE PHYSICAL-TOTAL-ONLY CASE
            # Only physical-area OTHE rows are eligible for this exception.
            # We require:
            #   published total > 0
            #   AND
            #   I + H + L + S == 0
            # If SPAM unexpectedly contains a non-zero split for an OTHE row,
            # that row is NOT treated as total-only.

            othe_total_only = (
                area_code
                ==
                "A"
            ) & (
                crop
                ==
                "othe"
            )

            if othe_total_only:

                total_only_mask = (
                    total.gt(
                        0
                    )
                    &
                    np.isclose(
                        computed.to_numpy(
                            dtype=float
                        ),
                        0.0,
                        rtol=0.0,
                        atol=AREA_ATOL_HA,
                    )
                )

                split_nonzero_mask = (
                    total.gt(
                        0
                    )
                    &
                    ~total_only_mask
                )

                qc[
                    "othe_total_only_rows"
                ] += int(
                    total_only_mask.sum()
                )

                qc[
                    "othe_total_only_source_area_ha"
                ] += float(
                    total.loc[
                        total_only_mask
                    ]
                    .sum()
                )

                qc[
                    "othe_split_nonzero_rows"
                ] += int(
                    split_nonzero_mask.sum()
                )

            else:

                total_only_mask = pd.Series(
                    False,
                    index=total.index,
                    dtype=bool,
                )

            # 6. ACTIVE ROWS

            active = (
                total.ne(
                    0
                )
                |
                irrigated.ne(
                    0
                )
                |
                rainfed.ne(
                    0
                )
            )

            if not active.any():

                continue

            # IMPORTANT:
            # positional mask avoids chunk-index alignment problems.

            mask = active.to_numpy(
                dtype=bool
            )

            part = (
                keys
                .iloc[
                    mask
                ]
                .copy()
            )

            part[
                "source_product_code"
            ] = crop

            total_active = (
                total.to_numpy(
                    dtype=float
                )[
                    mask
                ]
            )

            irrigated_active = (
                irrigated.to_numpy(
                    dtype=float
                )[
                    mask
                ]
            )

            rainfed_active = (
                rainfed.to_numpy(
                    dtype=float
                )[
                    mask
                ]
            )

            computed_active = (
                computed.to_numpy(
                    dtype=float
                )[
                    mask
                ]
            )

            total_only_active = (
                total_only_mask.to_numpy(
                    dtype=bool
                )[
                    mask
                ]
            )

            # 7. STANDARD SOURCE VALUES

            part[
                "source_total_source_ha"
            ] = total_active

            part[
                "source_irrigated_ha"
            ] = irrigated_active

            part[
                "source_rainfed_ha"
            ] = rainfed_active

            part[
                "source_total_ha"
            ] = computed_active

            part[
                "source_balance_residual_ha"
            ] = (
                total_active
                -
                computed_active
            )

            part[
                "source_total_only_available"
            ] = False

            part[
                "source_split_available"
            ] = True

            part[
                "source_component_status"
            ] = (
                "SOURCE_COMPONENTS_AVAILABLE"
            )

            # 8. OTHE PHYSICAL-TOTAL-ONLY CORRECTION
            # DO NOT set IR or RF to zero.
            # Those components are unavailable, not genuine zero estimates.
            # DO NOT manufacture a split.
            # Keep the published source total separately and mark the split
            # explicitly unavailable.

            if total_only_active.any():

                idx = part.index[
                    total_only_active
                ]

                part.loc[
                    idx,
                    "source_irrigated_ha",
                ] = np.nan

                part.loc[
                    idx,
                    "source_rainfed_ha",
                ] = np.nan

                part.loc[
                    idx,
                    "source_total_ha",
                ] = np.nan

                part.loc[
                    idx,
                    "source_balance_residual_ha",
                ] = np.nan

                part.loc[
                    idx,
                    "source_total_only_available",
                ] = True

                part.loc[
                    idx,
                    "source_split_available",
                ] = False

                part.loc[
                    idx,
                    "source_component_status",
                ] = (
                    "SOURCE_TOTAL_ONLY_IR_RF_SPLIT_UNAVAILABLE"
                )

            # 9. WRITE SPARSE ROWS

            header_written = append_gzip_csv(
                part,
                out_path,
                header_written,
            )

            qc[
                "rows_written"
            ] += len(
                part
            )

        print(
            f"SPAM 2000 {area_code}: "
            f"chunk {chunk_no} complete."
        )

    # 10. FINAL OTHE QC

    if area_code == "A":

        print(
            "\n=== SPAM2000 OTHE PHYSICAL SOURCE-TOTAL QC ==="
        )

        print(
            "OTHE total-only rows:",
            f"{qc['othe_total_only_rows']:,}",
        )

        print(
            "OTHE published physical total preserved (ha):",
            f"{qc['othe_total_only_source_area_ha']:,.3f}",
        )

        print(
            "OTHE rows with unexpected non-zero split:",
            f"{qc['othe_split_nonzero_rows']:,}",
        )

        if (
            qc[
                "othe_total_only_rows"
            ]
            ==
            0
        ):

            raise RuntimeError(
                "No positive SPAM2000 OTHE physical-total-only rows "
                "were detected. Stop and inspect the raw source before "
                "continuing."
            )

        if (
            qc[
                "othe_total_only_source_area_ha"
            ]
            <=
            0
        ):

            raise RuntimeError(
                "SPAM2000 OTHE source physical total is not positive."
            )

    # 11. RETURN

    return qc

def process_aligned_split_area(
    year,
    area_code,
    out_path,
):

    selected = SOURCE_SCHEMA.loc[
        SOURCE_SCHEMA["year"].eq(year)
        &
        SOURCE_SCHEMA["area_code"].eq(area_code)
    ]

    rows = {
        r["technology"]: r
        for _, r in selected.iterrows()
    }

    if set(rows) != {
        "A",
        "I",
        "R",
    }:

        raise ValueError(
            f"SPAM {year} {area_code}: "
            "A/I/R files not complete."
        )

    lookups = {
        tech:
            schema_crop_lookup(row)
        for tech, row in rows.items()
    }

    expected = (
        SPAM_CROPS_BY_YEAR[
            year
        ]
    )

    for tech in [
        "A",
        "I",
        "R",
    ]:

        missing = (
            set(expected)
            -
            set(
                lookups[tech]
            )
        )

        if missing:

            raise ValueError(
                f"SPAM {year} "
                f"{area_code} "
                f"T{tech}: "
                f"missing crops "
                f"{sorted(missing)}"
            )

    if out_path.exists():
        out_path.unlink()

    readers = {
        tech:
            make_reader(
                rows[tech]
            )
        for tech in [
            "A",
            "I",
            "R",
        ]
    }

    header_written = False

    qc = {
        "year": year,
        "area_code": area_code,
        "reader_mode": "ALIGNED_AIR",
        "rows_read": 0,
        "missing_numeric_cells_to_zero": 0,
        "negative_cells_to_zero": 0,
        "rows_written": 0,
    }

    for chunk_no, triple in enumerate(
        zip_longest(
            readers["A"],
            readers["I"],
            readers["R"],
        ),
        start=1,
    ):

        if any(
            x is None
            for x in triple
        ):

            raise ValueError(
                f"SPAM {year} "
                f"{area_code}: "
                "A/I/R files have different row counts."
            )

        chunks = dict(
            zip(
                [
                    "A",
                    "I",
                    "R",
                ],
                triple,
            )
        )

        prepared = {
            tech:
                prepare_keys(
                    chunks[tech],
                    rows[tech],
                )
            for tech in [
                "A",
                "I",
                "R",
            ]
        }

        # Confirm true positional alignment

        base_signature = (
            prepared["A"][
                [
                    "grid_id_5min",
                    "country_iso3",
                ]
            ]
            .reset_index(
                drop=True
            )
        )

        for tech in [
            "I",
            "R",
        ]:

            other = (
                prepared[tech][
                    [
                        "grid_id_5min",
                        "country_iso3",
                    ]
                ]
                .reset_index(
                    drop=True
                )
            )

            if not base_signature.equals(
                other
            ):

                raise ValueError(
                    f"SPAM {year} "
                    f"{area_code}: "
                    "source rows are not aligned "
                    "between A/I/R files "
                    f"in chunk {chunk_no}."
                )

        keys = prepared["A"]

        qc["rows_read"] += len(keys)

        for crop in expected:

            total, m0, n0 = clean_numeric(
                chunks["A"][
                    lookups["A"][crop]
                ]
            )

            irrigated, m1, n1 = clean_numeric(
                chunks["I"][
                    lookups["I"][crop]
                ]
            )

            rainfed, m2, n2 = clean_numeric(
                chunks["R"][
                    lookups["R"][crop]
                ]
            )

            computed = (
                irrigated
                + rainfed
            )

            qc[
                "missing_numeric_cells_to_zero"
            ] += (
                m0
                + m1
                + m2
            )

            qc[
                "negative_cells_to_zero"
            ] += (
                n0
                + n1
                + n2
            )

            active = (
                total.ne(0)
                |
                irrigated.ne(0)
                |
                rainfed.ne(0)
            )

            if not active.any():
                continue

            # IMPORTANT positional mask fix
            mask = active.to_numpy(
                dtype=bool
            )

            part = (
                keys
                .iloc[mask]
                .copy()
            )

            part[
                "source_product_code"
            ] = crop

            part[
                "source_total_source_ha"
            ] = total.to_numpy()[mask]

            part[
                "source_irrigated_ha"
            ] = irrigated.to_numpy()[mask]

            part[
                "source_rainfed_ha"
            ] = rainfed.to_numpy()[mask]

            part[
                "source_total_ha"
            ] = computed.to_numpy()[mask]

            part[
                "source_balance_residual_ha"
            ] = (
                total.to_numpy()[mask]
                -
                computed.to_numpy()[mask]
            )

            header_written = append_gzip_csv(
                part,
                out_path,
                header_written,
            )

            qc[
                "rows_written"
            ] += len(part)

        print(
            f"SPAM {year} "
            f"{area_code}: "
            f"chunk {chunk_no} complete."
        )

    return qc

def process_2020_component(
    area_code,
    technology,
    out_path,
):

    schema = (
        SOURCE_SCHEMA.loc[
            SOURCE_SCHEMA["year"].eq(2020)
            &
            SOURCE_SCHEMA["area_code"].eq(area_code)
            &
            SOURCE_SCHEMA["technology"].eq(technology)
        ]
        .iloc[0]
    )

    lookup = schema_crop_lookup(
        schema
    )

    expected = (
        SPAM_CROPS_BY_YEAR[
            2020
        ]
    )

    missing = (
        set(expected)
        -
        set(lookup)
    )

    if missing:

        raise ValueError(
            f"SPAM 2020 "
            f"{area_code} "
            f"T{technology}: "
            f"missing crops "
            f"{sorted(missing)}"
        )

    if out_path.exists():
        out_path.unlink()

    header_written = False

    qc = {
        "year": 2020,
        "area_code": area_code,
        "technology": technology,
        "rows_read": 0,
        "rows_written": 0,
        "missing_numeric_cells_to_zero": 0,
        "negative_cells_to_zero": 0,
    }

    for chunk_no, chunk in enumerate(
        make_reader(schema),
        start=1,
    ):

        keys = prepare_keys(
            chunk,
            schema,
        )

        qc[
            "rows_read"
        ] += len(chunk)

        for crop in expected:

            values, n_missing, n_negative = clean_numeric(
                chunk[
                    lookup[crop]
                ]
            )

            qc[
                "missing_numeric_cells_to_zero"
            ] += n_missing

            qc[
                "negative_cells_to_zero"
            ] += n_negative

            active = values.ne(0)

            if not active.any():
                continue

            mask = active.to_numpy(
                dtype=bool
            )

            part = (
                keys
                .iloc[mask]
                .copy()
            )

            part[
                "source_product_code"
            ] = crop

            part[
                "area_ha"
            ] = values.to_numpy()[mask]

            header_written = append_gzip_csv(
                part,
                out_path,
                header_written,
            )

            qc[
                "rows_written"
            ] += len(part)

        print(
            f"SPAM 2020 "
            f"{area_code} "
            f"T{technology}: "
            f"chunk {chunk_no} complete."
        )

    return qc

def process_2020_area(
    area_code,
    out_path,
):

    component_paths = {
        "A":
            TEMP_DIR
            / f"_2020_{area_code}_TA_sparse.csv.gz",

        "I":
            TEMP_DIR
            / f"_2020_{area_code}_TI_sparse.csv.gz",

        "R":
            TEMP_DIR
            / f"_2020_{area_code}_TR_sparse.csv.gz",
    }

    component_qc = []

    # 1. BUILD COMPONENT CACHE FROM SOURCE
    # Rebuild on every run so stale intermediate files cannot affect results.

    for tech in [
        "A",
        "I",
        "R",
    ]:

        path = component_paths[tech]

        q = process_2020_component(
            area_code=area_code,
            technology=tech,
            out_path=path,
        )

        component_qc.append(q)

    # 2. FULL UNION OF POSITIVE SOURCE KEYS

    reconciled_sql = f"""

    WITH

    stacked AS (

        SELECT
            grid_id_5min,
            grid_row_5min,
            grid_col_5min,
            longitude,
            latitude,
            country_iso3,
            year,
            source_product_code,
            'A' AS component,
            area_ha

        FROM read_csv_auto(
            '{component_paths["A"].as_posix()}',
            header=true
        )

        UNION ALL

        SELECT
            grid_id_5min,
            grid_row_5min,
            grid_col_5min,
            longitude,
            latitude,
            country_iso3,
            year,
            source_product_code,
            'I' AS component,
            area_ha

        FROM read_csv_auto(
            '{component_paths["I"].as_posix()}',
            header=true
        )

        UNION ALL

        SELECT
            grid_id_5min,
            grid_row_5min,
            grid_col_5min,
            longitude,
            latitude,
            country_iso3,
            year,
            source_product_code,
            'R' AS component,
            area_ha

        FROM read_csv_auto(
            '{component_paths["R"].as_posix()}',
            header=true
        )
    ),

    raw AS (

        SELECT

            grid_id_5min,

            MIN(grid_row_5min)
                AS grid_row_5min,

            MIN(grid_col_5min)
                AS grid_col_5min,

            MIN(longitude)
                AS longitude,

            MIN(latitude)
                AS latitude,

            country_iso3,
            year,
            source_product_code,

            SUM(
                CASE
                    WHEN component = 'A'
                    THEN area_ha
                    ELSE NULL
                END
            )
                AS total_raw_ha,

            SUM(
                CASE
                    WHEN component = 'I'
                    THEN area_ha
                    ELSE NULL
                END
            )
                AS irrigated_raw_ha,

            SUM(
                CASE
                    WHEN component = 'R'
                    THEN area_ha
                    ELSE NULL
                END
            )
                AS rainfed_raw_ha

        FROM stacked

        GROUP BY
            grid_id_5min,
            country_iso3,
            year,
            source_product_code
    ),

    resolved AS (

        SELECT

            *,

            -- Sparse component absence:
            -- no positive value exists in that component source.
            COALESCE(
                irrigated_raw_ha,
                0.0
            )
                AS irrigated_resolved_ha,

            COALESCE(
                rainfed_raw_ha,
                0.0
            )
                AS rainfed_resolved_ha

        FROM raw
    ),

    balanced AS (

        SELECT

            *,

            -- Working split total
            irrigated_resolved_ha
            +
            rainfed_resolved_ha
                AS total_computed_ha,

            -- Difference from published TA when TA exists
            CASE

                WHEN total_raw_ha IS NOT NULL

                    THEN
                        total_raw_ha
                        -
                        irrigated_resolved_ha
                        -
                        rainfed_resolved_ha

                ELSE NULL

            END
                AS balance_residual_ha

        FROM resolved
    ),

    classified AS (

        SELECT

            *,

            CASE

                -- ---------------------------------------------------------
                -- TA absent: reconstruct total from available split source.
                -- ---------------------------------------------------------

                WHEN total_raw_ha IS NULL

                 AND irrigated_raw_ha IS NULL

                 AND rainfed_raw_ha IS NOT NULL

                    THEN
                    'SOURCE_TOTAL_RECONSTRUCTED_FROM_RF_ONLY'

                WHEN total_raw_ha IS NULL

                 AND irrigated_raw_ha IS NOT NULL

                 AND rainfed_raw_ha IS NULL

                    THEN
                    'SOURCE_TOTAL_RECONSTRUCTED_FROM_IR_ONLY'

                WHEN total_raw_ha IS NULL

                 AND irrigated_raw_ha IS NOT NULL

                 AND rainfed_raw_ha IS NOT NULL

                    THEN
                    'SOURCE_TOTAL_RECONSTRUCTED_FROM_IR_PLUS_RF'

                -- ---------------------------------------------------------
                -- TA exists, both sparse split components absent.
                -- Diagnostic evidence: these are 0.1-ha source records.
                -- Do not arbitrarily assign them to irrigated or rainfed.
                -- ---------------------------------------------------------

                WHEN total_raw_ha IS NOT NULL

                 AND irrigated_raw_ha IS NULL

                 AND rainfed_raw_ha IS NULL

                 AND ABS(
                     balance_residual_ha
                 ) <= {SPAM2020_ROUNDING_ATOL_HA}

                    THEN
                    'SOURCE_COMPONENTS_SPARSE_ZERO_ROUNDING'

                -- ---------------------------------------------------------
                -- One split component absent and the TA difference is
                -- within the diagnosed ~0.1-ha source precision.
                -- ---------------------------------------------------------

                WHEN total_raw_ha IS NOT NULL

                 AND (
                        irrigated_raw_ha IS NULL
                        OR
                        rainfed_raw_ha IS NULL
                     )

                 AND ABS(
                     balance_residual_ha
                 ) <= {SPAM2020_ROUNDING_ATOL_HA}

                    THEN
                    'SOURCE_COMPONENTS_SPARSE_ZERO_ROUNDING'

                -- ---------------------------------------------------------
                -- All components present and essentially exact.
                -- ---------------------------------------------------------

                WHEN total_raw_ha IS NOT NULL

                 AND irrigated_raw_ha IS NOT NULL

                 AND rainfed_raw_ha IS NOT NULL

                 AND ABS(
                     balance_residual_ha
                 ) <= {AREA_ATOL_HA}

                    THEN
                    'SOURCE_COMPONENTS_COHERENT'

                -- ---------------------------------------------------------
                -- All components present and discrepancy is within
                -- diagnosed source reporting precision.
                -- ---------------------------------------------------------

                WHEN total_raw_ha IS NOT NULL

                 AND irrigated_raw_ha IS NOT NULL

                 AND rainfed_raw_ha IS NOT NULL

                 AND ABS(
                     balance_residual_ha
                 ) <= {SPAM2020_ROUNDING_ATOL_HA}

                    THEN
                    'SOURCE_COMPONENTS_COHERENT_ROUNDING'

                -- ---------------------------------------------------------
                -- Material source inconsistency.
                -- Retain but flag.
                -- ---------------------------------------------------------

                ELSE
                    'SOURCE_COMPONENT_REVIEW_REQUIRED'

            END
                AS source_component_status

        FROM balanced
    )

    SELECT

        *,

        source_component_status
            = 'SOURCE_COMPONENT_REVIEW_REQUIRED'
            AS manual_review_required,

        source_component_status IN (
            'SOURCE_COMPONENTS_COHERENT_ROUNDING',
            'SOURCE_COMPONENTS_SPARSE_ZERO_ROUNDING'
        )
            AS source_rounding_accepted,

        source_component_status IN (
            'SOURCE_TOTAL_RECONSTRUCTED_FROM_RF_ONLY',
            'SOURCE_TOTAL_RECONSTRUCTED_FROM_IR_ONLY',
            'SOURCE_TOTAL_RECONSTRUCTED_FROM_IR_PLUS_RF'
        )
            AS source_total_reconstructed

    FROM classified

    """

    # 3. STATUS QC

    status_qc = duckdb.sql(
        f"""

        WITH d AS (
            {reconciled_sql}
        )

        SELECT

            source_component_status,

            COUNT(*)
                AS rows,

            SUM(
                COALESCE(
                    total_raw_ha,
                    0.0
                )
            )
                AS source_TA_ha,

            SUM(
                irrigated_resolved_ha
            )
                AS irrigated_ha,

            SUM(
                rainfed_resolved_ha
            )
                AS rainfed_ha,

            SUM(
                total_computed_ha
            )
                AS computed_total_ha,

            SUM(
                COALESCE(
                    balance_residual_ha,
                    0.0
                )
            )
                AS signed_residual_ha,

            SUM(
                ABS(
                    COALESCE(
                        balance_residual_ha,
                        0.0
                    )
                )
            )
                AS absolute_residual_ha,

            MAX(
                ABS(
                    COALESCE(
                        balance_residual_ha,
                        0.0
                    )
                )
            )
                AS max_abs_residual_ha

        FROM d

        GROUP BY
            source_component_status

        ORDER BY
            source_component_status

        """
    ).df()

    status_qc.insert(
        0,
        "year",
        2020,
    )

    status_qc.insert(
        1,
        "area_code",
        area_code,
    )

    status_qc.to_csv(
        QC_DIR
        / f"02_SPAM2020_{area_code}_component_status_FINAL.csv",
        index=False,
    )

    print(
        f"\nSPAM 2020 {area_code} — FINAL component status:"
    )

    display(
        status_qc
    )

    # 4. MATERIAL REVIEW AUDIT
    # Review rows are retained, not silently changed.

    review = duckdb.sql(
        f"""

        WITH d AS (
            {reconciled_sql}
        )

        SELECT

            grid_id_5min,
            grid_row_5min,
            grid_col_5min,
            longitude,
            latitude,
            country_iso3,
            year,
            source_product_code,

            total_raw_ha,
            irrigated_raw_ha,
            rainfed_raw_ha,

            irrigated_resolved_ha,
            rainfed_resolved_ha,
            total_computed_ha,

            balance_residual_ha,

            source_component_status,
            manual_review_required

        FROM d

        WHERE
            manual_review_required

        ORDER BY
            ABS(
                balance_residual_ha
            ) DESC,
            country_iso3,
            source_product_code,
            grid_id_5min

        """
    ).df()

    review_path = (
        QC_DIR
        / f"02_SPAM2020_{area_code}_SOURCE_COMPONENT_REVIEW_REQUIRED_FINAL.csv"
    )

    review.to_csv(
        review_path,
        index=False,
    )

    if review.empty:

        print(
            f"SPAM 2020 {area_code}: "
            "material source-component review rows = 0."
        )

    else:

        print(
            f"SPAM 2020 {area_code}: "
            f"{len(review):,} material source-component "
            "review rows retained and flagged."
        )

        print(
            "Review audit:",
            review_path,
        )

    # 5. ROUNDING / TOTAL-RECONSTRUCTION AUDIT

    audit = duckdb.sql(
        f"""

        WITH d AS (
            {reconciled_sql}
        )

        SELECT

            grid_id_5min,
            grid_row_5min,
            grid_col_5min,
            longitude,
            latitude,
            country_iso3,
            year,
            source_product_code,

            total_raw_ha,
            irrigated_raw_ha,
            rainfed_raw_ha,

            irrigated_resolved_ha,
            rainfed_resolved_ha,
            total_computed_ha,

            balance_residual_ha,

            source_component_status,
            source_rounding_accepted,
            source_total_reconstructed

        FROM d

        WHERE
            source_rounding_accepted
            OR
            source_total_reconstructed

        ORDER BY
            source_component_status,
            country_iso3,
            source_product_code,
            grid_id_5min

        """
    ).df()

    audit.to_csv(
        QC_DIR
        / f"02_SPAM2020_{area_code}_reconciliation_audit_FINAL.csv",
        index=False,
    )

    # 6. WRITE FINAL 2020 SPARSE FILE

    if out_path.exists():
        out_path.unlink()

    duckdb.sql(
        f"""

        COPY (

            WITH d AS (
                {reconciled_sql}
            )

            SELECT

                grid_id_5min,
                grid_row_5min,
                grid_col_5min,
                longitude,
                latitude,
                country_iso3,
                year,
                source_product_code,

                -- Original SPAM total.
                -- NULL when TA itself was absent.
                total_raw_ha
                    AS source_total_source_ha,

                -- Published sparse split values.
                irrigated_resolved_ha
                    AS source_irrigated_ha,

                rainfed_resolved_ha
                    AS source_rainfed_ha,

                -- Working split total.
                -- Exactly IR + RF.
                total_computed_ha
                    AS source_total_ha,

                -- Published TA minus working split total.
                balance_residual_ha
                    AS source_balance_residual_ha,

                source_component_status,
                manual_review_required,
                source_rounding_accepted,
                source_total_reconstructed

            FROM d

        )

        TO '{out_path.as_posix()}'

        (
            FORMAT CSV,
            HEADER TRUE,
            COMPRESSION GZIP
        );

        """
    )

    # 7. FINAL OUTPUT SUMMARY

    final_summary = duckdb.sql(
        f"""

        SELECT

            COUNT(*)
                AS rows,

            SUM(source_irrigated_ha)
                AS irrigated_ha,

            SUM(source_rainfed_ha)
                AS rainfed_ha,

            SUM(source_total_ha)
                AS computed_total_ha,

            SUM(
                COALESCE(
                    source_total_source_ha,
                    0.0
                )
            )
                AS original_TA_ha,

            SUM(
                CASE
                    WHEN manual_review_required
                    THEN 1
                    ELSE 0
                END
            )
                AS review_rows,

            SUM(
                CASE
                    WHEN source_rounding_accepted
                    THEN 1
                    ELSE 0
                END
            )
                AS rounding_rows,

            SUM(
                CASE
                    WHEN source_total_reconstructed
                    THEN 1
                    ELSE 0
                END
            )
                AS reconstructed_total_rows

        FROM read_csv_auto(
            '{out_path.as_posix()}',
            header=true
        )

        """
    ).df()

    print(
        f"\nSPAM 2020 {area_code} — FINAL sparse output:"
    )

    display(
        final_summary
    )

    final_rows = int(
        final_summary.loc[
            0,
            "rows",
        ]
    )

    review_rows = int(
        final_summary.loc[
            0,
            "review_rows",
        ]
    )

    rounding_rows = int(
        final_summary.loc[
            0,
            "rounding_rows",
        ]
    )

    reconstructed_rows = int(
        final_summary.loc[
            0,
            "reconstructed_total_rows",
        ]
    )

    # 8. RETURN PROCESSING QC

    if component_qc:

        rows_read = sum(
            x["rows_read"]
            for x in component_qc
        )

        missing_count = sum(
            x[
                "missing_numeric_cells_to_zero"
            ]
            for x in component_qc
        )

        negative_count = sum(
            x[
                "negative_cells_to_zero"
            ]
            for x in component_qc
        )

    qc = {
        "year": 2020,
        "area_code": area_code,
        "reader_mode":
            "2020_KEY_BASED_NATIVE_5MIN_FINAL",
        "rows_read":
            rows_read,
        "rows_written":
            final_rows,
        "missing_numeric_cells_to_zero":
            missing_count,
        "negative_cells_to_zero":
            negative_count,
        "source_rounding_tolerance_ha":
            SPAM2020_ROUNDING_ATOL_HA,
        "source_rounding_accepted_rows":
            rounding_rows,
        "source_total_reconstructed_rows":
            reconstructed_rows,
        "source_component_review_rows":
            review_rows,
    }

    print(
        f"\nSPAM 2020 {area_code}: "
        f"FINAL reconciliation complete — "
        f"{final_rows:,} rows; "
        f"{rounding_rows:,} rounding-supported rows; "
        f"{reconstructed_rows:,} reconstructed-total rows; "
        f"{review_rows:,} source review rows retained."
    )

    return qc

def process_split_area(
    year,
    area_code,
    out_path,
):

    if year in {
        2005,
        2010,
    }:

        return process_aligned_split_area(
            year,
            area_code,
            out_path,
        )

    if year == 2020:

        return process_2020_area(
            area_code,
            out_path,
        )

    raise ValueError(
        f"process_split_area does not support year={year}"
    )

# Fresh native construction from sparse raw records

def build_native_parquet(year, harvested_sparse, physical_sparse, out_path):
    """Merge harvested and physical sparse tables into one native 5′ asset."""

    year = int(year)

    if out_path.exists():
        out_path.unlink()

    if year == 2000:
        # Authoritative repaired production SQL from original Notebook 01.
        # This keeps the 8-field spatial join and OTHE physical total-only rule.
        con = duckdb.connect()
        try:
            con.execute(f"""
COPY
        (
            WITH

            h AS
            (
                SELECT

                    grid_id_5min,
                    grid_row_5min,
                    grid_col_5min,
                    longitude,
                    latitude,

                    country_iso3,
                    year,
                    source_product_code,

                    SUM(
                        source_total_source_ha
                    )
                        AS h_total_source,

                    SUM(
                        source_irrigated_ha
                    )
                        AS h_ir,

                    SUM(
                        source_rainfed_ha
                    )
                        AS h_rf,

                    SUM(
                        source_total_ha
                    )
                        AS h_total,

                    SUM(
                        source_balance_residual_ha
                    )
                        AS h_residual

                FROM read_csv_auto(
                    '{harvested_sparse.as_posix()}',
                    header = true
                )

                GROUP BY ALL
            ),

            p AS
            (
                SELECT

                    grid_id_5min,
                    grid_row_5min,
                    grid_col_5min,
                    longitude,
                    latitude,

                    country_iso3,
                    year,
                    source_product_code,

                    SUM(
                        source_total_source_ha
                    )
                        AS p_total_source,

                    SUM(
                        source_irrigated_ha
                    )
                        AS p_ir,

                    SUM(
                        source_rainfed_ha
                    )
                        AS p_rf,

                    SUM(
                        source_total_ha
                    )
                        AS p_total,

                    SUM(
                        source_balance_residual_ha
                    )
                        AS p_residual,

                    MAX(
                        CASE
                            WHEN TRY_CAST(
                                source_total_only_available
                                AS BOOLEAN
                            )
                            THEN 1
                            ELSE 0
                        END
                    )
                        AS p_total_only_available

                FROM read_csv_auto(
                    '{physical_sparse.as_posix()}',
                    header = true
                )

                GROUP BY ALL
            ),

            joined AS
            (
                SELECT

                    COALESCE(
                        h.grid_id_5min,
                        p.grid_id_5min
                    )
                        AS grid_id_5min,

                    COALESCE(
                        h.grid_row_5min,
                        p.grid_row_5min
                    )
                        AS grid_row_5min,

                    COALESCE(
                        h.grid_col_5min,
                        p.grid_col_5min
                    )
                        AS grid_col_5min,

                    COALESCE(
                        h.longitude,
                        p.longitude
                    )
                        AS longitude,

                    COALESCE(
                        h.latitude,
                        p.latitude
                    )
                        AS latitude,

                    COALESCE(
                        h.country_iso3,
                        p.country_iso3
                    )
                        AS country_iso3,

                    COALESCE(
                        h.year,
                        p.year
                    )
                        AS year,

                    COALESCE(
                        h.source_product_code,
                        p.source_product_code
                    )
                        AS source_product_code,

                    -- =========================================================
                    -- HARVESTED AREA
                    -- =========================================================

                    COALESCE(
                        h.h_ir,
                        0.0
                    )
                        AS harvested_irrigated_ha,

                    COALESCE(
                        h.h_rf,
                        0.0
                    )
                        AS harvested_rainfed_ha,

                    COALESCE(
                        h.h_total,
                        0.0
                    )
                        AS harvested_total_ha,

                    COALESCE(
                        h.h_total_source,
                        0.0
                    )
                        AS harvested_total_source_ha,

                    COALESCE(
                        h.h_residual,
                        0.0
                    )
                        AS harvested_balance_residual_ha,

                    -- =========================================================
                    -- PHYSICAL AREA
                    -- =========================================================
                    --
                    -- Standard crops:
                    --     preserve old behaviour.
                    --
                    -- OTHE total-only rows:
                    --     IR/RF = NULL
                    --     total = published source total.
                    -- =========================================================

                    CASE

                        WHEN COALESCE(
                            p.p_total_only_available,
                            0
                        ) = 1

                        THEN CAST(
                            NULL AS DOUBLE
                        )

                        ELSE COALESCE(
                            p.p_ir,
                            0.0
                        )

                    END
                        AS physical_irrigated_ha,

                    CASE

                        WHEN COALESCE(
                            p.p_total_only_available,
                            0
                        ) = 1

                        THEN CAST(
                            NULL AS DOUBLE
                        )

                        ELSE COALESCE(
                            p.p_rf,
                            0.0
                        )

                    END
                        AS physical_rainfed_ha,

                    CASE

                        WHEN COALESCE(
                            p.p_total_only_available,
                            0
                        ) = 1

                        THEN p.p_total_source

                        ELSE COALESCE(
                            p.p_total,
                            0.0
                        )

                    END
                        AS physical_total_ha,

                    COALESCE(
                        p.p_total_source,
                        0.0
                    )
                        AS physical_total_source_ha,

                    CASE

                        WHEN COALESCE(
                            p.p_total_only_available,
                            0
                        ) = 1

                        THEN CAST(
                            NULL AS DOUBLE
                        )

                        ELSE COALESCE(
                            p.p_residual,
                            0.0
                        )

                    END
                        AS physical_balance_residual_ha,

                    CASE

                        WHEN COALESCE(
                            p.p_total_only_available,
                            0
                        ) = 1

                        THEN TRUE

                        ELSE FALSE

                    END
                        AS physical_total_only_available,

                    CASE

                        WHEN COALESCE(
                            p.p_total_only_available,
                            0
                        ) = 1

                        THEN FALSE

                        ELSE TRUE

                    END
                        AS physical_split_available,

                    CASE

                        WHEN COALESCE(
                            p.p_total_only_available,
                            0
                        ) = 1

                        THEN
                            'SOURCE_TOTAL_ONLY_IR_RF_SPLIT_UNAVAILABLE'

                        ELSE
                            'SOURCE_COMPONENTS_AVAILABLE'

                    END
                        AS physical_component_status

                FROM h

                FULL OUTER JOIN p

                USING
                (
                    grid_id_5min,
                    grid_row_5min,
                    grid_col_5min,
                    longitude,
                    latitude,
                    country_iso3,
                    year,
                    source_product_code
                )
            )

            SELECT

                *,

                CASE

                    WHEN physical_irrigated_ha > 0

                    THEN
                        harvested_irrigated_ha
                        /
                        physical_irrigated_ha

                    ELSE NULL

                END
                    AS harvesting_intensity_irrigated,

                CASE

                    WHEN physical_rainfed_ha > 0

                    THEN
                        harvested_rainfed_ha
                        /
                        physical_rainfed_ha

                    ELSE NULL

                END
                    AS harvesting_intensity_rainfed,

                CASE

                    WHEN physical_total_only_available

                    THEN NULL

                    WHEN physical_total_ha > 0

                    THEN
                        harvested_total_ha
                        /
                        physical_total_ha

                    ELSE NULL

                END
                    AS harvesting_intensity_total,

                'SPAM'
                    AS source_dataset,

                'observed_SPAM'
                    AS temporal_status

            FROM joined
        )

        TO '{out_path.as_posix()}'

        (
            FORMAT PARQUET,
            COMPRESSION ZSTD
        )
            """)
        finally:
            con.close()
        return

    elif year in {2005, 2010}:
        query = f"""
        WITH
        h AS (
            SELECT
                grid_id_5min,
                grid_row_5min,
                grid_col_5min,
                longitude,
                latitude,
                country_iso3,
                year,
                source_product_code,
                SUM(source_total_source_ha) AS h_total_source,
                SUM(source_irrigated_ha) AS h_ir,
                SUM(source_rainfed_ha) AS h_rf,
                SUM(source_total_ha) AS h_total,
                SUM(source_balance_residual_ha) AS h_residual
            FROM read_csv_auto('{harvested_sparse.as_posix()}', header=true)
            GROUP BY ALL
        ),
        p AS (
            SELECT
                grid_id_5min,
                grid_row_5min,
                grid_col_5min,
                longitude,
                latitude,
                country_iso3,
                year,
                source_product_code,
                SUM(source_total_source_ha) AS p_total_source,
                SUM(source_irrigated_ha) AS p_ir,
                SUM(source_rainfed_ha) AS p_rf,
                SUM(source_total_ha) AS p_total,
                SUM(source_balance_residual_ha) AS p_residual
            FROM read_csv_auto('{physical_sparse.as_posix()}', header=true)
            GROUP BY ALL
        )
        SELECT
            COALESCE(h.grid_id_5min, p.grid_id_5min) AS grid_id_5min,
            COALESCE(h.grid_row_5min, p.grid_row_5min) AS grid_row_5min,
            COALESCE(h.grid_col_5min, p.grid_col_5min) AS grid_col_5min,
            COALESCE(h.longitude, p.longitude) AS longitude,
            COALESCE(h.latitude, p.latitude) AS latitude,
            COALESCE(h.country_iso3, p.country_iso3) AS country_iso3,
            COALESCE(h.year, p.year) AS year,
            COALESCE(h.source_product_code, p.source_product_code) AS source_product_code,

            -- A physical-area-only native record has no H row. The validated
            -- 2005/2010 production convention records this absent harvested
            -- source total as 0.0, matching a zero harvested component sum.
            -- Keep the NULL distinction for source fields in other branches.
            COALESCE(h.h_total_source, 0.0) AS harvested_total_source_ha,
            COALESCE(h.h_ir, 0.0) AS harvested_irrigated_ha,
            COALESCE(h.h_rf, 0.0) AS harvested_rainfed_ha,
            COALESCE(h.h_total, 0.0) AS harvested_total_ha,
            h.h_residual AS harvested_balance_residual_ha,
            'SOURCE_COMPONENTS_AVAILABLE' AS harvested_component_status,
            FALSE AS harvested_manual_review_required,
            FALSE AS harvested_source_rounding_accepted,
            FALSE AS harvested_source_total_reconstructed,

            p.p_total_source AS physical_total_source_ha,
            COALESCE(p.p_ir, 0.0) AS physical_irrigated_ha,
            COALESCE(p.p_rf, 0.0) AS physical_rainfed_ha,
            COALESCE(p.p_total, 0.0) AS physical_total_ha,
            p.p_residual AS physical_balance_residual_ha,
            FALSE AS physical_total_only_available,
            TRUE AS physical_split_available,
            'SOURCE_COMPONENTS_AVAILABLE' AS physical_component_status,
            FALSE AS physical_manual_review_required,
            FALSE AS physical_source_rounding_accepted,
            FALSE AS physical_source_total_reconstructed,

            CASE WHEN COALESCE(p.p_ir, 0.0) > 0
                 THEN COALESCE(h.h_ir, 0.0) / p.p_ir
                 ELSE NULL END AS harvesting_intensity_irrigated,
            CASE WHEN COALESCE(p.p_rf, 0.0) > 0
                 THEN COALESCE(h.h_rf, 0.0) / p.p_rf
                 ELSE NULL END AS harvesting_intensity_rainfed,
            CASE WHEN COALESCE(p.p_total, 0.0) > 0
                 THEN COALESCE(h.h_total, 0.0) / p.p_total
                 ELSE NULL END AS harvesting_intensity_total,
            'SPAM' AS source_dataset,
            'observed_SPAM' AS temporal_status
        FROM h
        FULL OUTER JOIN p
        USING (
            grid_id_5min,
            country_iso3,
            year,
            source_product_code
        )
        """

    elif year == 2020:
        query = f"""
        WITH
        h AS (
            SELECT
                grid_id_5min,
                grid_row_5min,
                grid_col_5min,
                longitude,
                latitude,
                country_iso3,
                year,
                source_product_code,
                source_total_source_ha AS h_total_source,
                source_irrigated_ha AS h_ir,
                source_rainfed_ha AS h_rf,
                source_total_ha AS h_total,
                source_balance_residual_ha AS h_residual,
                source_component_status AS h_status,
                TRY_CAST(manual_review_required AS BOOLEAN) AS h_review,
                TRY_CAST(source_rounding_accepted AS BOOLEAN) AS h_rounding,
                TRY_CAST(source_total_reconstructed AS BOOLEAN) AS h_reconstructed
            FROM read_csv_auto('{harvested_sparse.as_posix()}', header=true)
        ),
        p AS (
            SELECT
                grid_id_5min,
                grid_row_5min,
                grid_col_5min,
                longitude,
                latitude,
                country_iso3,
                year,
                source_product_code,
                source_total_source_ha AS p_total_source,
                source_irrigated_ha AS p_ir,
                source_rainfed_ha AS p_rf,
                source_total_ha AS p_total,
                source_balance_residual_ha AS p_residual,
                source_component_status AS p_status,
                TRY_CAST(manual_review_required AS BOOLEAN) AS p_review,
                TRY_CAST(source_rounding_accepted AS BOOLEAN) AS p_rounding,
                TRY_CAST(source_total_reconstructed AS BOOLEAN) AS p_reconstructed
            FROM read_csv_auto('{physical_sparse.as_posix()}', header=true)
        )
        SELECT
            COALESCE(h.grid_id_5min, p.grid_id_5min) AS grid_id_5min,
            COALESCE(h.grid_row_5min, p.grid_row_5min) AS grid_row_5min,
            COALESCE(h.grid_col_5min, p.grid_col_5min) AS grid_col_5min,
            COALESCE(h.longitude, p.longitude) AS longitude,
            COALESCE(h.latitude, p.latitude) AS latitude,
            COALESCE(h.country_iso3, p.country_iso3) AS country_iso3,
            COALESCE(h.year, p.year) AS year,
            COALESCE(h.source_product_code, p.source_product_code) AS source_product_code,

            -- The saved 2020 native convention records an absent published
            -- H total as 0.0. Working reconstructed H areas stay unchanged.
            COALESCE(h.h_total_source, 0.0) AS harvested_total_source_ha,
            COALESCE(h.h_ir, 0.0) AS harvested_irrigated_ha,
            COALESCE(h.h_rf, 0.0) AS harvested_rainfed_ha,
            COALESCE(h.h_total, 0.0) AS harvested_total_ha,
            h.h_residual AS harvested_balance_residual_ha,
            COALESCE(h.h_status, 'SOURCE_COMPONENTS_ABSENT') AS harvested_component_status,
            COALESCE(h.h_review, FALSE) AS harvested_manual_review_required,
            COALESCE(h.h_rounding, FALSE) AS harvested_source_rounding_accepted,
            COALESCE(h.h_reconstructed, FALSE) AS harvested_source_total_reconstructed,

            -- The saved 2020 native convention likewise encodes an absent
            -- published A total as 0.0, without altering resolved physical area.
            COALESCE(p.p_total_source, 0.0) AS physical_total_source_ha,
            COALESCE(p.p_ir, 0.0) AS physical_irrigated_ha,
            COALESCE(p.p_rf, 0.0) AS physical_rainfed_ha,
            COALESCE(p.p_total, 0.0) AS physical_total_ha,
            p.p_residual AS physical_balance_residual_ha,
            FALSE AS physical_total_only_available,
            TRUE AS physical_split_available,
            COALESCE(p.p_status, 'SOURCE_COMPONENTS_ABSENT') AS physical_component_status,
            COALESCE(p.p_review, FALSE) AS physical_manual_review_required,
            COALESCE(p.p_rounding, FALSE) AS physical_source_rounding_accepted,
            COALESCE(p.p_reconstructed, FALSE) AS physical_source_total_reconstructed,

            CASE WHEN COALESCE(p.p_ir, 0.0) > 0
                 THEN COALESCE(h.h_ir, 0.0) / p.p_ir
                 ELSE NULL END AS harvesting_intensity_irrigated,
            CASE WHEN COALESCE(p.p_rf, 0.0) > 0
                 THEN COALESCE(h.h_rf, 0.0) / p.p_rf
                 ELSE NULL END AS harvesting_intensity_rainfed,
            CASE WHEN COALESCE(p.p_total, 0.0) > 0
                 THEN COALESCE(h.h_total, 0.0) / p.p_total
                 ELSE NULL END AS harvesting_intensity_total,
            'SPAM' AS source_dataset,
            'observed_SPAM' AS temporal_status
        FROM h
        FULL OUTER JOIN p
        USING (
            grid_id_5min,
            country_iso3,
            year,
            source_product_code
        )
        """

    else:
        raise ValueError(f"Unsupported year: {year}")

    con = duckdb.connect()
    try:
        con.execute(
            f"""
            COPY ({query})
            TO '{out_path.as_posix()}'
            (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
    finally:
        con.close()

# Source conservation and spatial QC

CORE_COLUMNS = [
    "grid_id_5min", "grid_row_5min", "grid_col_5min", "longitude", "latitude",
    "country_iso3", "year", "source_product_code",
    "harvested_irrigated_ha", "harvested_rainfed_ha", "harvested_total_ha",
    "physical_irrigated_ha", "physical_rainfed_ha", "physical_total_ha",
]
SOURCE_TOTAL_COLUMNS = ["harvested_total_source_ha", "physical_total_source_ha"]
INTENSITY_COLUMNS = [
    "harvesting_intensity_irrigated", "harvesting_intensity_rainfed",
    "harvesting_intensity_total",
]
METADATA_COLUMNS = [
    "physical_total_only_available", "physical_split_available",
    "harvested_component_status", "physical_component_status",
    "harvested_manual_review_required", "physical_manual_review_required",
    "harvested_source_rounding_accepted", "physical_source_rounding_accepted",
    "harvested_source_total_reconstructed", "physical_source_total_reconstructed",
    "source_dataset", "temporal_status",
]

def column_names(con, path: Path) -> list[str]:
    return [r[0] for r in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{sql_path(path)}')"
    ).fetchall()]

def numeric_equals(a: str, b: str, column: str, tolerance: float) -> str:
    return (
        f"(CASE WHEN {a}.{column} IS NULL AND {b}.{column} IS NULL THEN TRUE "
        f"WHEN {a}.{column} IS NULL OR {b}.{column} IS NULL THEN FALSE "
        f"WHEN {a}.{column} IS NOT DISTINCT FROM {b}.{column} THEN TRUE "
        f"WHEN isfinite({a}.{column}) AND isfinite({b}.{column}) "
        f"THEN abs({a}.{column} - {b}.{column}) <= {tolerance} "
        f"ELSE FALSE END)"
    )

def compare_reference(con, output: Path, reference: Path, year: int,
                      area_tolerance: float = 1e-4,
                      intensity_tolerance: float = 1e-8) -> dict:
    fresh_columns = column_names(con, output)
    reference_columns = column_names(con, reference)
    required = CORE_COLUMNS + SOURCE_TOTAL_COLUMNS + INTENSITY_COLUMNS
    for label, columns in (("fresh", fresh_columns), ("reference", reference_columns)):
        missing = set(required) - set(columns)
        if missing:
            raise AssertionError(f"{year}: {label} missing required scientific fields: {sorted(missing)}")
    key_cols = ["grid_id_5min", "country_iso3", "year", "source_product_code"]
    numeric = [x for x in CORE_COLUMNS + SOURCE_TOTAL_COLUMNS + INTENSITY_COLUMNS
               if x not in key_cols]
    common_metadata = [x for x in METADATA_COLUMNS
                       if x in fresh_columns and x in reference_columns]
    comparable = []
    for col in numeric:
        tol = intensity_tolerance if col in INTENSITY_COLUMNS else (1e-9 if col in
            ("longitude", "latitude", "grid_row_5min", "grid_col_5min")
            else area_tolerance)
        comparable.append((col, numeric_equals("a", "b", col, tol)))
    for col in common_metadata:
        comparable.append((col, f"a.{col} IS NOT DISTINCT FROM b.{col}"))
    predicates = " OR ".join("NOT ("+expr+")" for _, expr in comparable)
    failures = ",\n".join(
        f"COUNT(*) FILTER (WHERE a.__present IS NOT NULL AND b.__present IS NOT NULL "
        f"AND NOT ({expr})) AS fail_{name}"
        for name, expr in comparable
    )
    join = " AND ".join(f"a.{key} IS NOT DISTINCT FROM b.{key}" for key in key_cols)
    sql = f"""
        WITH a AS (SELECT 1 AS __present, * FROM read_parquet('{sql_path(output)}')),
             b AS (SELECT 1 AS __present, * FROM read_parquet('{sql_path(reference)}'))
        SELECT
            COUNT(*) AS union_rows,
            COUNT(*) FILTER (WHERE a.__present IS NULL OR b.__present IS NULL)
                AS missing_or_extra_native_keys,
            COUNT(*) FILTER (WHERE a.__present IS NOT NULL AND b.__present IS NOT NULL
                             AND ({predicates})) AS mismatched_records,
            {failures}
        FROM a FULL OUTER JOIN b ON {join}
    """
    record = con.execute(sql).df().iloc[0].to_dict()
    record = {k: int(v) if k not in ("maximum_area_error_ha",) else float(v)
              for k, v in record.items()}
    record["year"] = int(year)
    record["columns_compared"] = [name for name, _ in comparable]
    record["reference_only_columns"] = sorted(set(reference_columns) - set(fresh_columns))
    record["fresh_only_columns"] = sorted(set(fresh_columns) - set(reference_columns))
    record["scientific_columns_compared"] = len(numeric)
    record["provenance_columns_compared"] = len(common_metadata)
    record["status"] = (
        "PASS_NATIVE_SPATIAL_AND_PROVENANCE_REFERENCE_MATCH"
        if record["missing_or_extra_native_keys"] == 0
        and record["mismatched_records"] == 0
        else "FAIL_NATIVE_REFERENCE_MISMATCH"
    )
    return record

def local_qc(con, native: Path, year: int) -> dict:
    cols = set(column_names(con, native))
    mandatory = (set(CORE_COLUMNS) | set(SOURCE_TOTAL_COLUMNS) |
                 set(INTENSITY_COLUMNS) |
                 {"physical_total_only_available", "physical_split_available"})
    if not mandatory <= cols:
        raise AssertionError(f"{year}: invalid native output schema: {sorted(mandatory - cols)}")
    path = sql_path(native)
    row = con.execute(f"""
        SELECT COUNT(*) AS rows,
            COUNT(DISTINCT (grid_id_5min,country_iso3,year,source_product_code)) AS unique_keys,
            COUNT(DISTINCT country_iso3) AS countries,
            COUNT(DISTINCT source_product_code) AS native_crops,
            COUNT(*) FILTER (WHERE
                harvested_irrigated_ha < 0 OR harvested_rainfed_ha < 0 OR
                harvested_total_ha < 0 OR physical_irrigated_ha < 0 OR
                physical_rainfed_ha < 0 OR physical_total_ha < 0) AS negative_rows,
            COUNT(*) FILTER (WHERE
                ABS(harvested_total_ha - harvested_irrigated_ha - harvested_rainfed_ha)
                    > {AREA_ATOL_HA}) AS harvested_split_failures,
            COUNT(*) FILTER (WHERE physical_split_available AND
                ABS(physical_total_ha - physical_irrigated_ha - physical_rainfed_ha)
                    > {AREA_ATOL_HA}) AS physical_split_failures,
            COUNT(*) FILTER (WHERE physical_total_only_available AND
                (physical_irrigated_ha IS NOT NULL OR
                 physical_rainfed_ha IS NOT NULL OR
                 physical_total_ha IS DISTINCT FROM physical_total_source_ha))
                 AS invalid_total_only_rows,
            COUNT(*) FILTER (WHERE physical_total_only_available)
                 AS total_only_rows,
            SUM(physical_total_ha) FILTER (
                WHERE source_product_code = 'othe') AS othe_physical_total_ha
        FROM read_parquet('{path}')
    """).df().iloc[0].to_dict()
    result = {k: (int(v) if k not in ("othe_physical_total_ha",) else
                  (None if pd.isna(v) else float(v))) for k, v in row.items()}
    result["year"] = year
    result["duplicate_keys"] = result["rows"] - result["unique_keys"]
    for field in ("duplicate_keys", "negative_rows", "harvested_split_failures",
                  "physical_split_failures", "invalid_total_only_rows"):
        if result[field] != 0:
            raise AssertionError(f"Native {year} QC failed {field}: {result[field]}")
    if year == 2000:
        if result["total_only_rows"] <= 0 or not result["othe_physical_total_ha"]:
            raise AssertionError("2000 OTHE physical total-only evidence missing")
    return result

def source_conservation(con, raw_sparse: Path, native: Path,
                        area_code: str, year: int) -> dict:
    value = "harvested_total_source_ha" if area_code == "H" else "physical_total_source_ha"
    source_sum = con.execute(
        f"SELECT SUM(source_total_source_ha) FROM read_csv_auto('{sql_path(raw_sparse)}',header=true)"
    ).fetchone()[0]
    final_sum = con.execute(
        f"SELECT SUM({value}) FROM read_parquet('{sql_path(native)}')"
    ).fetchone()[0]
    source_sum = float(source_sum or 0)
    final_sum = float(final_sum or 0)
    passed = bool(np.isclose(source_sum, final_sum, atol=1e-3, rtol=1e-10))
    if not passed:
        raise AssertionError(f"{year} {area_code}: raw-sparse to native conservation failed: "
                             f"source={source_sum} native={final_sum}")
    return {
        "year": year, "area_code": area_code,
        "source_total_source_ha": source_sum,
        "native_total_source_ha": final_sum,
        "difference_ha": final_sum - source_sum,
        "pass": passed,
    }

def build_and_test_year(year: int, reference_root: Path, compare: bool,
                        threads: int, memory_limit: str,
                        area_tol: float, intensity_tol: float) -> dict:
    harvested = TEMP_DIR / f"SPAM_{year}_harvested_sparse_5arcmin.csv.gz"
    physical = TEMP_DIR / f"SPAM_{year}_physical_sparse_5arcmin.csv.gz"
    native = OUTPUT_DIR / f"01_SPAM_native_5arcmin_{year}.parquet"
    files = [harvested, physical, native]
    if year == 2020:
        files += [TEMP_DIR / f"_2020_{area}_T{t}_sparse.csv.gz"
                  for area in ("H", "A") for t in ("A", "I", "R")]
    if any(p.exists() for p in files):
        raise FileExistsError(f"Refusing to reuse year {year} outputs: "
                              f"{[str(p) for p in files if p.exists()]}")
    (TEMP_DIR / "duckdb_spill").mkdir(exist_ok=True, parents=True)
    duckdb.sql(f"SET threads={int(threads)}")
    duckdb.sql(f"SET memory_limit='{memory_limit}'")
    duckdb.sql(f"SET temp_directory='{sql_path(TEMP_DIR / 'duckdb_spill')}'")

    print(f"Building year {year} from raw SPAM source CSV files...", flush=True)
    if year == 2000:
        qc_h = process_2000_area("H", harvested)
        qc_p = process_2000_area("A", physical)
    else:
        qc_h = process_split_area(year, "H", harvested)
        qc_p = process_split_area(year, "A", physical)
    build_native_parquet(year, harvested, physical, native)
    if not native.is_file():
        raise FileNotFoundError(f"Native source build did not create {native}")
    con = duckdb.connect(":memory:")
    try:
        con.execute(f"SET threads={int(threads)}")
        con.execute(f"SET memory_limit='{memory_limit}'")
        con.execute(f"SET temp_directory='{sql_path(TEMP_DIR / 'duckdb_spill')}'")
        qc = local_qc(con, native, year)
        conservation = [
            source_conservation(con, harvested, native, "H", year),
            source_conservation(con, physical, native, "A", year),
        ]
        matched = None
        if compare:
            ref = reference_root / "01_native_5arcmin" / native.name
            matched = compare_reference(con, native, ref, year, area_tol, intensity_tol)
            if matched["status"].startswith("FAIL"):
                (QC_DIR / f"01_reference_compare_{year}.json").write_text(
                    json.dumps(matched, indent=2, default=str), encoding="utf-8"
                )
                raise AssertionError(
                    f"Year {year} fresh native data differs from saved reference: "
                    f"keys={matched['missing_or_extra_native_keys']}, "
                    f"rows={matched['mismatched_records']}"
                )
        processing = [qc_h, qc_p]
        pd.DataFrame(processing).to_csv(QC_DIR / f"01_raw_processing_{year}.csv", index=False)
        if matched:
            (QC_DIR / f"01_reference_compare_{year}.json").write_text(
                json.dumps(matched, indent=2, default=str), encoding="utf-8"
            )
        result = {
            "year": year,
            "fresh_native": str(native),
            "fresh_native_sha256": sha256(native),
            "local_qc": qc, "source_conservation": conservation,
            "reference_comparison": matched,
            "processing_qc": processing,
            "status": "PASS_FRESH_NATIVE_SOURCE_AND_REFERENCE_QC" if compare
                      else "BUILT_RAW_NATIVE_REFERENCE_NOT_COMPARED",
        }
        print(f"{year}: {result['status']}; "
              f"rows={qc['rows']:,}; countries={qc['countries']}; "
              f"native crops={qc['native_crops']}", flush=True)
        return result
    finally:
        con.close()

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Read-only source and schema preflight")
    parser.add_argument("--execute", action="store_true", help="Build fresh native datasets")
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT,
                        help="Folder containing SPAM/CSV source files")
    parser.add_argument("--reference-root", type=Path, default=None,
                        help="Read-only existing SPAM_FABLE_FINAL root")
    parser.add_argument("--output-root", type=Path, default=None,
                        help="New clean Stage 01 output root")
    parser.add_argument("--coordinate-file", type=Path, default=None,
                        help="Original SPAM2005 alloc_key coordinate lookup CSV")
    parser.add_argument("--years", nargs="+", type=int,
                        default=list(OBSERVED_YEARS), choices=OBSERVED_YEARS)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--memory-limit", default="6GB")
    parser.add_argument("--area-tol", type=float, default=1e-4)
    parser.add_argument("--intensity-tol", type=float, default=1e-8)
    parser.add_argument("--skip-reference", action="store_true",
                        help="Build but explicitly do not claim reference match")
    args = parser.parse_args()
    if args.check == args.execute:
        parser.error("Select exactly one: --check or --execute")
    if args.threads < 1 or args.area_tol < 0 or args.intensity_tol < 0:
        raise ValueError("Invalid threads/tolerances")
    years = tuple(sorted(set(args.years)))
    data = args.data_root.expanduser().resolve()
    reference = (args.reference_root or data / "SPAM_FABLE_FINAL").expanduser().resolve()
    output = (args.output_root or reference / "05_publication_audit" /
              "v1_0_candidate" / "clean_stage01_raw_native_v1").expanduser().resolve()
    protected_output_check(output, data, reference)
    manifest, schema, coord = preflight(
        data, reference, args.coordinate_file, years, not args.skip_reference
    )
    print("Stage 01 raw SPAM native source preflight")
    print("Original source CSV files verified:", len(manifest))
    print("2005 coordinate lookup:", coord)
    print("Fresh output root:", output)
    print("Read-only reference:", reference if not args.skip_reference else "NOT COMPARED")
    print("Requested years:", years)
    print("Native source crop counts:",
          {int(y): len(SPAM_CROPS_BY_YEAR[int(y)]) for y in years})
    if args.check:
        print("STATUS: PASS_STAGE01_RAW_SOURCE_PREFLIGHT_ONLY")
        return 0
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("Stage 01 output directory is not empty: " + str(output))

    global SOURCE_MANIFEST, SOURCE_SCHEMA, DATA_ROOT, SPAM_CSV_ROOT
    global OUTPUT_DIR, TEMP_DIR, QC_DIR, CHUNK_SIZE
    SOURCE_MANIFEST = manifest
    SOURCE_SCHEMA = schema
    DATA_ROOT = data
    SPAM_CSV_ROOT = data / "SPAM" / "CSV"
    OUTPUT_DIR = output / "01_native_5arcmin"
    TEMP_DIR = OUTPUT_DIR / "_temp_sparse"
    QC_DIR = OUTPUT_DIR / "qc"
    for directory in (OUTPUT_DIR, TEMP_DIR, QC_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    schema_printable = schema.drop(columns=["columns", "crop_entries"])
    schema_printable.to_csv(QC_DIR / "01_source_schema.csv", index=False)
    initialize_country_reference(data, coord, schema)
    results = []
    for year in years:
        results.append(build_and_test_year(
            year, reference, not args.skip_reference,
            args.threads, args.memory_limit, args.area_tol, args.intensity_tol,
        ))
    status = (
        "PASS_STAGE01_RAW_SOURCE_NATIVE_FOUR_YEAR_REFERENCE_MATCH"
        if not args.skip_reference and set(years) == set(OBSERVED_YEARS)
        else "PASS_STAGE01_RAW_SOURCE_NATIVE_SELECTED_YEARS_REFERENCE_MATCH"
        if not args.skip_reference
        else "STAGE01_RAW_SOURCE_BUILT_WITHOUT_REFERENCE_COMPARISON"
    )
    file_paths = list(manifest["path"]) + [coord]
    hashes = [{"path": str(path), "sha256": sha256(path)} for path in file_paths]
    audit = {
        "stage": "01_native_source_reconstruction",
        "production_methodology": "notebook_01_native_SPAM_release_specific_source_readers",
        "execution": "source_CSV_to_fresh_native_without_prior_native_input",
        "raw_source_csv_files": len(manifest),
        "coordinate_lookup": str(coord),
        "reference_comparison_performed": not args.skip_reference,
        "year_selection": list(years),
        "frozen_country_name_overrides_used": True,
        "external_country_normalization_dependency": "pycountry",
        "raw_source_hashes": hashes,
        "clean_output_root": str(output),
        "results": results,
        "status": status,
    }
    (output / "01_stage01_native_rebuild_manifest.json").write_text(
        json.dumps(audit, indent=2, default=str), encoding="utf-8"
    )
    summary = pd.DataFrame([{
        "year": r["year"],
        "rows": r["local_qc"]["rows"],
        "countries": r["local_qc"]["countries"],
        "source_crops": r["local_qc"]["native_crops"],
        "reference_mismatched_records":
            r["reference_comparison"]["mismatched_records"] if r["reference_comparison"] else pd.NA,
        "reference_missing_extra_keys":
            r["reference_comparison"]["missing_or_extra_native_keys"] if r["reference_comparison"] else pd.NA,
        "status": r["status"],
    } for r in results])
    summary.to_csv(output / "01_stage01_native_rebuild_summary.csv", index=False)
    print("\n", summary.to_string(index=False), sep="")
    print("Manifest:", output / "01_stage01_native_rebuild_manifest.json")
    print("STATUS:", status)
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("STAGE01_FAILED:", repr(exc), file=sys.stderr)
        raise
