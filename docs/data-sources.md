# Data sources and access | SPAM-FABLE v1.0.0

This page distinguishes **public original data** from **project-derived, frozen evidence and reference outputs**. The repository distributes no original SPAM data, large reference grids, unpublished mapping weights, MIRCA correction tables, or FABLE Calculator datasets. Public availability of an upstream dataset **does not** mean the project-specific, processed evidence has been publicly deposited.

## Original SPAM releases

| Observation year | Source and authoritative citation | Exact source variant required by the validated code |
|---|---|---|
| 2000 | [IFPRI / Harvard Dataverse — SPAM 2000](https://doi.org/10.7910/DVN/A50I2T) | SPAM 2000 v3.0.7, harvested and physical CSV exports (2 files) |
| 2005 | [IFPRI / Harvard Dataverse — SPAM 2005](https://doi.org/10.7910/DVN/DHXBJX) | SPAM 2005 v3r2; H/A total, irrigated, rainfed CSVs (6 files), plus `cell5m_allockey_xy.csv` coordinate lookup |
| 2010 | [IFPRI / Harvard Dataverse — SPAM 2010](https://doi.org/10.7910/DVN/PRFF8V) | SPAM 2010 v2r0; H/A total, irrigated, rainfed CSVs (6 files) |
| 2020 | [IFPRI / Harvard Dataverse — SPAM 2020](https://doi.org/10.7910/DVN/SWPENT) · [CGIAR MAPSPAM 2020 v2r2 catalog](https://cgiar-climate-data-hub.github.io/catalog/spam2020/) | SPAM 2020 v2r2; H/A total, irrigated, rainfed **original CSV exports** (6 files). The CGIAR Zarr/COG representation is not a drop-in replacement for these CSVs. |

The corresponding **exact 21 native source/lookup filenames** and folder structure are in [`../tools/SPAM-required-files.csv`](../tools/SPAM-required-files.csv). Download the matching releases, unpack the appropriate CSV archives, and preserve the original filenames. If a Dataverse file requires access terms, download through its official interface. The repository does not embed credentials, automate license acceptance, or fabricate download links for individual files.

## Other independent data origins

| Data family | Official access | What the project actually needs |
|---|---|---|
| FAOSTAT | [FAOSTAT database](https://www.fao.org/faostat/) | A **frozen, harmonized project-specific FAOSTAT crop-product panel** and mapping-v2 weights, not an arbitrary newer FAOSTAT download. Recomputing these exact weights independently remains **pending**. |
| MIRCA2000 | [Portmann et al. MIRCA2000 on Zenodo](https://zenodo.org/records/7422506) | Context/source for some approved irrigation harmonization decisions; the project uses **derived year-2000 correction/provenance files**. Raw MIRCA2000 downloads alone do **not** replace them. |
| MIRCA-OS (used in the original project context) | [Published MIRCA-OS historical resource](https://doi.org/10.4211/hs.60a890eb841c460192c03bb590687145) | This is a distinct dataset from MIRCA2000. Its downloadable raster layers cannot simply be substituted for the 23 approved frozen project-specific correction cases. |

Never silently substitute a different source vintage, a revised FAOSTAT download, or the revised 2026 MIRCA-OS release in a v1.0.0 validation run: that would change the scientific evidence base.

## Project-derived complementary data: not publicly distributed in v1.0.0

The 52-file manifest categorizes required assets. The **31 files beyond the 21 original SPAM source/lookup assets** comprise frozen mapping/FAOSTAT evidence, SPAM2000/MIRCA decisions, and saved reference datasets used for comparison. These were locally available to the authors for the validated 9 November 2026 run, but are not included in this public code ZIP and have **no claimed public download URL**.

- **Frozen mapping evidence** under `data/mapping_audit/`: crop taxonomy, parent composition weights, mapping-v2 weights, FAOSTAT country-year panel. Raw-to-weights reconstruction remains pending.
- **Approved SPAM2000/MIRCA evidence** under `data/legacy/06_harmonized_irrigation/` and `data/SPAM_FABLE_FINAL/02_country_completion/`: reviewable provenance and corrected irrigation-share decisions. These are not equivalent to publicly downloadable MIRCA rasters.
- **Saved validation reference snapshots** under `data/SPAM_FABLE_FINAL/`: native and mapped grids, the 2015 geographic crosswalk, historical candidate, metadata and manifests. These are read-only comparison references; several also currently supply frozen decisions.

**For an independent user in v1.0.0:** obtaining public raw SPAM data is necessary but *insufficient* to execute all six stages. They must also obtain the exact approved frozen evidence and saved reference snapshot from an authorized project distribution. If these are unavailable, they can read and inspect code and source schemas, but **cannot reproduce the validated full pipeline as published**. Do not label v1.0.0 as raw-SPAM-only or fully self-contained.

## Data publication plan (not an active link)

With rights-holder approval, project-owned small evidence files could be released as a separately versioned Zenodo evidence archive with individual file hashes, a licence/rights statement, and a DOI; large reference data may require separate storage or reference-free validation changes. **No evidence archive DOI has been assigned in this package.** Do not publish these files merely because they exist on the local workstation.

## Provenance and citation

See [`inputs.md`](inputs.md) for the relative folder contract, [`reproducibility.md`](reproducibility.md) for validation limits, and the four upstream SPAM Dataverse records for the official attribution and version-specific licence. The code licence must be decided separately with the relevant rights holder. Data licences are not inherited from the code repository.
