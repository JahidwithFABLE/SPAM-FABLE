#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Stage 02 | Rebuild frozen SPAM-to-FABLE mappings from upstream source evidence.

Scientific lineage: production Notebook 02, mapping consolidation Cells 1-6,
validated publication-review v2. The script rebuilds the product reference,
country-year operational weights and direct edges from the original mapping
master, legacy weights, mapping-v2 weights, FAOSTAT composition panel, and
Notebook 01 native-country inventory. The saved 02_mapping outputs are used
ONLY as read-only comparison references, never as construction inputs.

Scope: upstream mapping-evidence replay, not a raw FAOSTAT download or
independent derivation of mapping-v2 weights from primary observations.

Usage:
  python 02_SPAM_FABLE_clean_start_mapping_rebuild.py --check
  python 02_SPAM_FABLE_clean_start_mapping_rebuild.py --execute
  python 02_SPAM_FABLE_clean_start_mapping_rebuild.py --execute --output-root C:\\new_folder
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

YEARS = (2000, 2005, 2010, 2020)
EXPECTED_PRODUCTS = 56
EXPECTED_DIRECT_EDGES = 121
EXPECTED_WEIGHTED_EDGES = 16713
EXPECTED_WEIGHTED_PARENT_GROUPS = 4877
EXPECTED_OTHE_TOTAL_HA = 559_861_492.7
PARENT_KEY = ["country_iso3", "year", "source_product_code"]
WEIGHT_KEY = PARENT_KEY + ["fable_product"]
DIRECT_KEY = ["year", "source_product_code", "fable_product"]
FABLE_ALIASES = {
    "pulses other": "pulses_other", "pulses_other": "pulses_other",
    "citrus_othe": "citrus_other", "citrus_other": "citrus_other",
    "fiber_hard_other": "abaca", "fiber_soft_other": "jute",
}
OOIL_2000_CHILDREN = [
    "coconut", "oilpalmfruit", "rapeseed", "sesame", "sunflower",
    "oilseed_other", "olive",
]
BROAD_2000_PATCH_CHILDREN = {
    "banp": ["banana", "plantain"],
    "swpy": ["sweet_potato", "yams"],
}

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()

def clean_string(series: pd.Series) -> pd.Series:
    return (series.astype("string").str.strip()
            .replace({"": pd.NA, "<NA>": pd.NA, "nan": pd.NA, "None": pd.NA}))

def normalize_fable_product(value):
    if pd.isna(value):
        return pd.NA
    value = str(value).strip()
    return FABLE_ALIASES.get(value, value)

def to_bool(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False).astype(bool)
    return series.astype("string").str.strip().str.lower().isin(["true", "1", "yes", "y"])

def sum_min_count(series: pd.Series):
    return pd.to_numeric(series, errors="coerce").sum(min_count=1)

def paths_from_roots(data_root: Path, reference: Path) -> dict[str, Path]:
    mapping = data_root / "mapping_audit"
    return {
        "mapping_master": mapping / "fable_crop_mapping_master_FINAL.csv",
        "legacy_weights": mapping / "spam_country_year_weights_FINAL.csv",
        "v2_weights": mapping / "harmonized" / "global_master" / "script_08A_mapping"
                      / "08A_SPAM_FABLE_mapping_v2_PRODUCTION_weights.csv",
        "native_country": reference / "01_native_5arcmin" / "01_SPAM_native_country_totals_QC.csv.gz",
        "faostat_panel": mapping / "faostat" / "faostat_fable_target_years_common_country_panel.csv",
        "calculator_schema": mapping / "harmonized" / "global_master"
                             / "fable_c_global_fable_product_irrigation_master_2000_2020.csv.gz",
        "reference_products": reference / "02_mapping" / "02_FABLE_56_product_reference.csv",
        "reference_direct": reference / "02_mapping" / "02_SPAM_FABLE_direct_edges.csv",
        "reference_weighted": reference / "02_mapping"
                              / "02_SPAM_FABLE_operational_country_year_weights.csv.gz",
    }

def assert_safe_output(output: Path, data_root: Path, reference: Path) -> None:
    output = output.resolve()
    reference = reference.resolve()
    data_root = data_root.resolve()
    protected = [
        data_root / "mapping_audit", data_root / "SPAM",
        reference / "01_native_5arcmin", reference / "02_mapping",
        reference / "03_observed_FABLE_5arcmin", reference / "04_final_release",
    ]
    for folder in protected:
        folder = folder.resolve()
        if output == folder or folder in output.parents or output in folder.parents:
            raise ValueError(f"Unsafe output overlaps protected source: {folder}")
    if output == reference:
        raise ValueError("Output cannot be the validated reference root")

def check_inputs(paths: dict[str, Path]) -> None:
    missing = [f"{name}: {path}" for name, path in paths.items() if not path.is_file()]
    if missing:
        raise FileNotFoundError("Stage 02 missing evidence:\n" + "\n".join(missing))
    needed = {
        "mapping_master": {"source_dataset", "source_product_code", "source_version_or_year",
                           "fable_product", "mapping_type", "weight", "weight_basis"},
        "legacy_weights": {"country_iso3", "source_product_code", "fable_product", "weight"},
        "v2_weights": {"country_iso3", "source_product_code", "fable_product", "weight"},
        "native_country": {"country_iso3", "year", "source_product_code",
                           "harvested_total_ha", "physical_total_ha",
                           "physical_irrigated_ha", "physical_rainfed_ha"},
        "faostat_panel": {"country_iso3", "year", "fable_product", "harvested_total_ha"},
        "calculator_schema": {"calculator_order", "calculator_crop", "fable_product"},
        "reference_products": {"calculator_order", "calculator_crop", "fable_product"},
        "reference_direct": set(DIRECT_KEY + ["weight", "mapping_type", "weight_basis"]),
        "reference_weighted": set(WEIGHT_KEY + ["weight", "weight_basis", "weight_quality_flag"]),
    }
    for label, path in paths.items():
        columns = set(pd.read_csv(path, nrows=0).columns)
        absent = needed[label] - columns
        if absent:
            raise ValueError(f"{label} is missing required columns: {sorted(absent)}")
        if label in ("legacy_weights", "v2_weights") and not (
            "year" in columns or "source_version_or_year" in columns
        ):
            raise ValueError(f"{label} missing year and source_version_or_year")
    print("Required source and reference files:", len(paths), "verified")

def load_products(path: Path) -> pd.DataFrame:
    # Production Notebook 02 uses the old schema reference for names and order.
    source = pd.read_csv(path, usecols=["calculator_order", "calculator_crop", "fable_product"])
    products = (
        source.assign(
            fable_product=lambda x: x["fable_product"].map(normalize_fable_product),
            calculator_crop=lambda x: clean_string(x["calculator_crop"]),
            calculator_order=lambda x: pd.to_numeric(x["calculator_order"], errors="coerce"),
        )
        .dropna().drop_duplicates()
        .sort_values("calculator_order")
        .reset_index(drop=True)
    )
    if len(products) != EXPECTED_PRODUCTS or products["fable_product"].nunique() != EXPECTED_PRODUCTS:
        raise AssertionError("Calculator source schema does not resolve to exactly 56 products")
    if products["calculator_order"].duplicated().any():
        raise AssertionError("Duplicate Calculator product order")
    return products

def standardize_weights(frame: pd.DataFrame, is_v2: bool) -> pd.DataFrame:
    out = frame.copy()
    out["country_iso3"] = clean_string(out["country_iso3"]).str.upper()
    year = out["year"] if "year" in out else out["source_version_or_year"]
    out["year"] = pd.to_numeric(year, errors="coerce").astype("Int64")
    out["source_product_code"] = clean_string(out["source_product_code"]).str.lower()
    out["fable_product"] = out["fable_product"].map(normalize_fable_product)
    out["weight"] = pd.to_numeric(out["weight"], errors="coerce")
    for col in ["weight_basis", "weight_quality_flag"]:
        if col not in out.columns:
            out[col] = "mapping_v2_production" if is_v2 else "legacy_country_year_weight"
    return out[WEIGHT_KEY + ["weight", "weight_basis", "weight_quality_flag"]].copy()

def load_evidence(paths: dict[str, Path], canonical: set[str]):
    master = pd.read_csv(paths["mapping_master"], low_memory=False)
    master["source_dataset"] = clean_string(master["source_dataset"])
    master["source_product_code"] = clean_string(master["source_product_code"]).str.lower()
    master["source_version_or_year"] = pd.to_numeric(
        master["source_version_or_year"], errors="coerce").astype("Int64")
    master["fable_product"] = master["fable_product"].map(normalize_fable_product)
    master["include_in_water_area"] = (
        to_bool(master["include_in_water_area"])
        if "include_in_water_area" in master else True
    )
    spam = master.loc[
        master["source_dataset"].str.upper().eq("SPAM")
        & master["source_version_or_year"].isin(YEARS)
    ].copy()
    legacy = standardize_weights(pd.read_csv(paths["legacy_weights"], low_memory=False), False)
    v2 = standardize_weights(pd.read_csv(paths["v2_weights"], low_memory=False), True)
    for label, table in (("legacy", legacy), ("mapping_v2", v2)):
        invalid = table["fable_product"].notna() & ~table["fable_product"].isin(canonical)
        if invalid.any():
            raise AssertionError(f"{label}: unknown FABLE products {table.loc[invalid, 'fable_product'].unique().tolist()}")
    native = pd.read_csv(paths["native_country"], compression="infer", low_memory=False)
    native["country_iso3"] = clean_string(native["country_iso3"]).str.upper()
    native["year"] = pd.to_numeric(native["year"], errors="raise").astype(int)
    native["source_product_code"] = clean_string(native["source_product_code"]).str.lower()
    for area in ("harvested_total_ha", "physical_total_ha", "physical_irrigated_ha", "physical_rainfed_ha"):
        native[area] = pd.to_numeric(native[area], errors="coerce")
    fao = pd.read_csv(paths["faostat_panel"], low_memory=False)
    fao["country_iso3"] = clean_string(fao["country_iso3"]).str.upper()
    fao["year"] = pd.to_numeric(fao["year"], errors="coerce").astype("Int64")
    fao["fable_product"] = fao["fable_product"].map(normalize_fable_product)
    fao["harvested_total_ha"] = pd.to_numeric(fao["harvested_total_ha"], errors="coerce")
    fao = fao.groupby(["country_iso3", "year", "fable_product"], as_index=False).agg(
        harvested_total_ha=("harvested_total_ha", sum_min_count)
    )
    return spam, legacy, v2, native, fao

def make_composition(country: str, year: int, parent: str,
                     children: list[str], fao: pd.DataFrame, canonical: set[str]) -> pd.DataFrame:
    if not set(children).issubset(canonical):
        raise ValueError(f"Unknown children for {parent}: {sorted(set(children) - canonical)}")
    country_values = (
        fao.loc[(fao["country_iso3"] == country) & (fao["year"] == year)
                & fao["fable_product"].isin(children), ["fable_product", "harvested_total_ha"]]
        .drop_duplicates("fable_product").set_index("fable_product").reindex(children).reset_index()
    )
    country_sum = country_values["harvested_total_ha"].sum(min_count=1)
    if country_values["harvested_total_ha"].notna().all() and pd.notna(country_sum) and country_sum > 0:
        selected = country_values
        basis = "FAOSTAT_country_year_observed_composition"
        quality = "country_year_observed_composition"
        denom = country_sum
    else:
        global_values = (
            fao.loc[(fao["year"] == year) & fao["fable_product"].isin(children)]
            .groupby("fable_product", as_index=False)
            .agg(harvested_total_ha=("harvested_total_ha", sum_min_count))
            .set_index("fable_product").reindex(children).reset_index()
        )
        global_sum = global_values["harvested_total_ha"].sum(min_count=1)
        if not global_values["harvested_total_ha"].notna().all() or pd.isna(global_sum) or global_sum <= 0:
            return pd.DataFrame(columns=WEIGHT_KEY + ["weight", "weight_basis", "weight_quality_flag"])
        selected = global_values
        basis = "FAOSTAT_global_year_composition_fallback"
        quality = "global_year_fallback"
        denom = global_sum
    result = selected.copy()
    result["country_iso3"] = country
    result["year"] = year
    result["source_product_code"] = parent
    result["weight"] = result["harvested_total_ha"] / denom
    result["weight_basis"] = basis
    result["weight_quality_flag"] = quality
    return result[WEIGHT_KEY + ["weight", "weight_basis", "weight_quality_flag"]]

def rebuild_mapping(spam: pd.DataFrame, legacy: pd.DataFrame, v2: pd.DataFrame,
                    native: pd.DataFrame, fao: pd.DataFrame, canonical: set[str]):
    v2_keys = v2[PARENT_KEY].drop_duplicates().assign(_v2=True)
    legacy_retained = (
        legacy.merge(v2_keys, on=PARENT_KEY, how="left")
        .loc[lambda x: x["_v2"].isna()].drop(columns="_v2")
    )
    weights = pd.concat([legacy_retained, v2], ignore_index=True)
    positive_parent = native.loc[
        native["harvested_total_ha"].fillna(0).gt(0)
        | native["physical_total_ha"].fillna(0).gt(0)
    ].copy()
    # Always replace the SPAM2000 coarse OOIL parent, as in production Cell 4.
    ooil_countries = positive_parent.loc[
        positive_parent["year"].eq(2000) & positive_parent["source_product_code"].eq("ooil"),
        "country_iso3",
    ].dropna().drop_duplicates().tolist()
    ooil_parts = [make_composition(c, 2000, "ooil", OOIL_2000_CHILDREN, fao, canonical)
                  for c in ooil_countries]
    ooil_parts = [x for x in ooil_parts if not x.empty]
    ooil = (pd.concat(ooil_parts, ignore_index=True) if ooil_parts
            else pd.DataFrame(columns=weights.columns))
    weights = weights.loc[
        ~(weights["year"].eq(2000) & weights["source_product_code"].eq("ooil"))
    ].copy()
    weights = pd.concat([weights, ooil], ignore_index=True)
    patches_by_parent = {}
    for parent, children in BROAD_2000_PATCH_CHILDREN.items():
        countries = positive_parent.loc[
            positive_parent["year"].eq(2000)
            & positive_parent["source_product_code"].eq(parent), "country_iso3"
        ].dropna().drop_duplicates().tolist()
        covered = set(weights.loc[
            weights["year"].eq(2000) & weights["source_product_code"].eq(parent),
            "country_iso3",
        ].dropna())
        missing = [c for c in countries if c not in covered]
        patches = [make_composition(c, 2000, parent, children, fao, canonical) for c in missing]
        patches = [x for x in patches if not x.empty]
        patches_by_parent[parent] = sum(len(x) for x in patches)
        if patches:
            weights = pd.concat([weights, *patches], ignore_index=True)
    weights = (weights.dropna(subset=WEIGHT_KEY + ["weight"])
               .drop_duplicates().reset_index(drop=True))
    mapping_type = clean_string(spam["mapping_type"]).str.lower()
    direct = spam.loc[
        spam["include_in_water_area"]
        & spam["fable_product"].notna()
        & ~mapping_type.eq("one_to_many_weighted")
        & spam["fable_product"].isin(canonical),
        ["source_version_or_year", "source_product_code", "fable_product",
         "mapping_type", "weight", "weight_basis"],
    ].rename(columns={"source_version_or_year": "year"}).copy()
    direct["year"] = pd.to_numeric(direct["year"], errors="raise").astype(int)
    direct["weight"] = pd.to_numeric(direct["weight"], errors="coerce")
    weighted_parent_years = (
        weights[["year", "source_product_code"]].drop_duplicates().assign(_weighted=True)
    )
    direct = (direct.merge(weighted_parent_years, on=["year", "source_product_code"], how="left")
              .loc[lambda x: x["_weighted"].isna()].drop(columns="_weighted"))
    lineage = {"ooil_parent_countries": len(ooil_countries),
               "ooil_rebuilt_edges": len(ooil), "2000_patch_edges": patches_by_parent}
    return direct, weights, native, lineage

def compare_exact(fresh: pd.DataFrame, reference: pd.DataFrame, keys: list[str],
                  numerical: set[str], label: str, tolerance: float = 1e-11) -> dict:
    missing_fresh = set(reference.columns) - set(fresh.columns)
    missing_reference = set(fresh.columns) - set(reference.columns)
    if missing_fresh or missing_reference:
        raise AssertionError(f"{label}: schema difference: fresh lacks {sorted(missing_fresh)}; reference lacks {sorted(missing_reference)}")
    if fresh.duplicated(keys).any() or reference.duplicated(keys).any():
        raise AssertionError(f"{label}: duplicate comparison keys")
    aa, bb = fresh.copy(), reference.copy()
    for frame in (aa, bb):
        for col in keys:
            if col == "year":
                frame[col] = pd.to_numeric(frame[col], errors="raise").astype(int)
            else:
                frame[col] = clean_string(frame[col])
    merged = aa.merge(bb, on=keys, how="outer", indicator=True,
                      suffixes=("_fresh", "_reference"), validate="one_to_one")
    absent = int((merged["_merge"] != "both").sum())
    common = merged.loc[merged["_merge"] == "both"].copy()
    field_failures = {}
    for col in fresh.columns:
        if col in keys:
            continue
        a, b = common[f"{col}_fresh"], common[f"{col}_reference"]
        if col in numerical:
            a = pd.to_numeric(a, errors="coerce").to_numpy(dtype=float)
            b = pd.to_numeric(b, errors="coerce").to_numpy(dtype=float)
            match = np.isclose(a, b, atol=tolerance, rtol=1e-12, equal_nan=True)
        else:
            match = clean_string(a).fillna("<NA>").to_numpy() == clean_string(b).fillna("<NA>").to_numpy()
        field_failures[col] = int((~match).sum())
    failed_fields = {k: v for k, v in field_failures.items() if v}
    return {"label": label, "fresh_rows": len(fresh), "reference_rows": len(reference),
            "missing_or_extra_keys": absent, "field_mismatches": failed_fields,
            "total_mismatched_fields": sum(failed_fields.values()),
            "pass": absent == 0 and not failed_fields}

def scientific_qc(direct: pd.DataFrame, weights: pd.DataFrame, native: pd.DataFrame,
                  canonical: set[str], qc_dir: Path) -> dict:
    if len(direct) != EXPECTED_DIRECT_EDGES or len(weights) != EXPECTED_WEIGHTED_EDGES:
        raise AssertionError(f"Unexpected mapping size, direct={len(direct)}, weighted={len(weights)}")
    for name, df, key in (("direct", direct, DIRECT_KEY), ("weighted", weights, WEIGHT_KEY)):
        if df[key].isna().any(axis=None) or df.duplicated(key).any():
            raise AssertionError(f"{name}: missing or duplicate mapping keys")
        values = pd.to_numeric(df["weight"], errors="coerce").to_numpy(dtype=float)
        if (~np.isfinite(values)).any() or (values < 0).any() or (values > 1).any():
            raise AssertionError(f"{name}: invalid mapping weights")
        if not set(df["fable_product"]).issubset(canonical):
            raise AssertionError(f"{name}: unknown FABLE targets")
    if set(zip(direct["year"], direct["source_product_code"])) & set(
        zip(weights["year"], weights["source_product_code"])):
        raise AssertionError("Direct and weighted parent-year overlap")
    weight_qa = weights.groupby(PARENT_KEY, as_index=False).agg(
        n_children=("fable_product", "nunique"), weight_sum=("weight", "sum")
    )
    weight_qa["weight_sum_error"] = (weight_qa["weight_sum"] - 1.0).abs()
    if len(weight_qa) != EXPECTED_WEIGHTED_PARENT_GROUPS:
        raise AssertionError(f"Expected {EXPECTED_WEIGHTED_PARENT_GROUPS} weighted parent groups, got {len(weight_qa)}")
    if weight_qa["weight_sum_error"].gt(1e-10).any():
        raise AssertionError("Weighted parent allocations do not conserve area")
    direct_qa = direct.groupby(["year", "source_product_code"], as_index=False).agg(
        n_children=("fable_product", "nunique"), weight_sum=("weight", "sum")
    )
    if not np.isclose(direct_qa["weight_sum"].to_numpy(dtype=float), 1.0, atol=1e-10).all():
        raise AssertionError("Direct parent allocations do not conserve area")
    inventory = native.groupby(PARENT_KEY, as_index=False).agg(
        harvested_total_ha=("harvested_total_ha", sum_min_count),
        physical_total_ha=("physical_total_ha", sum_min_count),
    )
    inventory["positive_parent_area"] = (
        inventory["harvested_total_ha"].fillna(0).gt(0)
        | inventory["physical_total_ha"].fillna(0).gt(0)
    )
    direct_parents = direct[["year", "source_product_code"]].drop_duplicates().assign(direct_mapping_available=True)
    weighted_parents = weights[PARENT_KEY].drop_duplicates().assign(weighted_mapping_available=True)
    coverage = (inventory.merge(direct_parents, on=["year", "source_product_code"], how="left")
                .merge(weighted_parents, on=PARENT_KEY, how="left"))
    for col in ("direct_mapping_available", "weighted_mapping_available"):
        coverage[col] = coverage[col].fillna(False).astype(bool)
    coverage["mapping_available"] = coverage["direct_mapping_available"] | coverage["weighted_mapping_available"]
    gap = coverage["positive_parent_area"] & ~coverage["mapping_available"]
    othe_mask = (gap & coverage["year"].eq(2000)
                 & coverage["source_product_code"].eq("othe")
                 & coverage["harvested_total_ha"].fillna(0).eq(0)
                 & coverage["physical_total_ha"].gt(0))
    harvested_gap = gap & coverage["harvested_total_ha"].fillna(0).gt(0)
    physical_gap = gap & ~(othe_mask | harvested_gap)
    coverage["coverage_status"] = np.select(
        [harvested_gap, othe_mask, physical_gap, coverage["mapping_available"]],
        ["BLOCKING_HARVESTED_MAPPING_GAP", "DOCUMENTED_OTHE_2000_PHYSICAL_ONLY",
         "REVIEW_OTHER_PHYSICAL_ONLY", "MAPPED"],
        default="ZERO_AREA_OR_UNCOVERED",
    )
    unresolved = coverage.loc[gap].copy()
    othe = native.loc[(native["year"] == 2000) & (native["source_product_code"] == "othe")].copy()
    othe_sum = othe["physical_total_ha"].sum(min_count=1)
    if othe.empty or not np.isclose(othe_sum, EXPECTED_OTHE_TOTAL_HA, atol=0.1, rtol=1e-10):
        raise AssertionError(f"2000 OTHE physical source total does not match: {othe_sum}")
    if othe[["physical_irrigated_ha", "physical_rainfed_ha"]].notna().any(axis=None):
        raise AssertionError("2000 OTHE has an unsupported physical irrigation split")
    if harvested_gap.any() or physical_gap.any() or int(othe_mask.sum()) != len(unresolved):
        raise AssertionError("Unmapped positive native parents include unsupported cases")
    if len(othe) != int(othe_mask.sum()):
        raise AssertionError("Unmapped OTHE count differs from country OTHE inventory")
    qc_dir.mkdir(parents=True, exist_ok=True)
    weight_qa.to_csv(qc_dir / "02_operational_weight_QA.csv", index=False)
    direct_qa.to_csv(qc_dir / "02_direct_parent_weight_QA.csv", index=False)
    coverage.to_csv(qc_dir / "02_native_parent_mapping_coverage.csv.gz", index=False, compression="gzip")
    unresolved.to_csv(qc_dir / "02_unresolved_positive_native_parents.csv.gz", index=False, compression="gzip")
    return {"direct_edges": len(direct), "weighted_edges": len(weights),
            "weighted_parent_groups": len(weight_qa),
            "max_weight_sum_error": float(weight_qa["weight_sum_error"].max()),
            "2000_othe_unmapped_physical_only_groups": int(othe_mask.sum()),
            "2000_othe_physical_area_ha": float(othe_sum),
            "other_unmapped_positive_groups": int(harvested_gap.sum() + physical_gap.sum()),
            "status": "PASS_MAPPING_RULES_WITH_DOCUMENTED_OTHE_LIMITATION"}

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--check", action="store_true", help="Read-only source and schema preflight")
    choice.add_argument("--execute", action="store_true", help="Fresh mapping evidence replay")
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--reference-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args()
    data_root = args.data_root.expanduser().resolve()
    reference = (args.reference_root or data_root / "SPAM_FABLE_FINAL").expanduser().resolve()
    output = (args.output_root or reference / "05_publication_audit" / "v1_0_candidate"
              / "clean_stage02_mapping_v1").expanduser().resolve()
    assert_safe_output(output, data_root, reference)
    paths = paths_from_roots(data_root, reference)
    check_inputs(paths)
    print("Stage 02 SPAM-to-FABLE mapping source preflight")
    print("Source evidence root:", data_root / "mapping_audit")
    print("Native country reference:", paths["native_country"])
    print("Read-only mapping reference:", reference / "02_mapping")
    print("New clean output root:", output)
    if args.check:
        print("STATUS: PASS_STAGE02_MAPPING_SOURCE_PREFLIGHT_ONLY")
        return 0
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Stage 02 output directory is not empty: {output}")
    products = load_products(paths["calculator_schema"])
    canonical = set(products["fable_product"])
    spam, legacy, v2, native, fao = load_evidence(paths, canonical)
    direct, weights, native, lineage = rebuild_mapping(spam, legacy, v2, native, fao, canonical)
    qc = scientific_qc(direct, weights, native, canonical, output / "02_mapping" / "qc")
    reference_products = pd.read_csv(paths["reference_products"], low_memory=False)
    reference_direct = pd.read_csv(paths["reference_direct"], low_memory=False)
    reference_weights = pd.read_csv(paths["reference_weighted"], low_memory=False)
    ref_checks = [
        compare_exact(products, reference_products, ["fable_product"], {"calculator_order"}, "56_product_schema"),
        compare_exact(direct, reference_direct, DIRECT_KEY, {"weight"}, "direct_mapping"),
        compare_exact(weights, reference_weights, WEIGHT_KEY, {"weight"}, "country_year_weights"),
    ]
    for result in ref_checks:
        print(f"{result['label']}: fresh={result['fresh_rows']:,}; "
              f"reference={result['reference_rows']:,}; missing/extra={result['missing_or_extra_keys']}; "
              f"field mismatches={result['total_mismatched_fields']}; "
              f"status={'PASS' if result['pass'] else 'FAIL'}", flush=True)
    if not all(result["pass"] for result in ref_checks):
        (output / "02_mapping" / "qc" / "02_reference_comparison_FAILED.json").write_text(
            json.dumps(ref_checks, indent=2), encoding="utf-8"
        )
        raise AssertionError("Stage 02 rebuilt mapping differs from validated reference")
    folder = output / "02_mapping"
    folder.mkdir(parents=True, exist_ok=True)
    products.to_csv(folder / "02_FABLE_56_product_reference.csv", index=False)
    direct.to_csv(folder / "02_SPAM_FABLE_direct_edges.csv", index=False)
    weights.to_csv(folder / "02_SPAM_FABLE_operational_country_year_weights.csv.gz",
                   index=False, compression="gzip")
    (folder / "qc" / "02_reference_comparison.json").write_text(
        json.dumps(ref_checks, indent=2), encoding="utf-8")
    input_hashes = {name: {"path": str(path), "sha256": sha256(path)}
                    for name, path in paths.items()}
    outputs = {p.name: sha256(p) for p in (
        folder / "02_FABLE_56_product_reference.csv",
        folder / "02_SPAM_FABLE_direct_edges.csv",
        folder / "02_SPAM_FABLE_operational_country_year_weights.csv.gz",
    )}
    manifest = {
        "stage": "02_mapping_evidence_replay",
        "input_evidence": input_hashes, "lineage": lineage, "scientific_qc": qc,
        "reference_checks": ref_checks, "output_hashes": outputs,
        "raw_FAOSTAT_and_mapping_v2_weight_derivation_replayed": False,
        "native_country_source": "frozen Notebook 01 national QC file, not a fresh Stage 01 aggregate",
        "status": "PASS_STAGE02_FROZEN_EVIDENCE_MAPPING_REBUILD_AND_REFERENCE_MATCH",
    }
    (output / "02_stage02_mapping_manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    print("FABLE products:", len(products))
    print("Direct edges:", len(direct))
    print("Weighted edges:", len(weights))
    print("Weighted parent groups:", qc["weighted_parent_groups"])
    print("2000 OTHE physical-only parents documented:", qc["2000_othe_unmapped_physical_only_groups"])
    print("Manifest:", output / "02_stage02_mapping_manifest.json")
    print("STATUS:", manifest["status"])
    print("Raw FAOSTAT / mapping-v2 weight derivation: PENDING")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("STAGE02_FAILED:", repr(exc), file=sys.stderr)
        raise
