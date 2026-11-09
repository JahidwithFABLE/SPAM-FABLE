# SPAM to FABLE | Historical crop areas

**Purpose:** Convert four observed global SPAM releases into a harmonized 56-product, five-year FABLE Calculator historical input.

## Scripts

| Order | Name | Action |
|---|---|---|
| 01 | `scripts/01-native-spam.py` | Read raw SPAM CSVs at native 5-arcminute resolution; preserve 2000 OTHE physical-only evidence |
| 02 | `scripts/02-map-crops.py` | Rebuild frozen one-to-one, many-to-one and country-year weighted mapping evidence |
| 03 | `scripts/03-map-grids.py` | Apply mappings to observed native grids; compare spatial output |
| 04 | `scripts/04-complete-2000.py` | Rebuild 2000 country-product layer with frozen evidence and approved MIRCA share corrections |
| 05 | `scripts/05-interpolate-2015.py` | Interpolate the four mapped area components at 5 arc-minutes using the frozen country crosswalk |
| 06 | `scripts/06-build-history.py` | Assemble the five-year 34-column Calculator candidate and verify the reference |

Use `run-spam.py` for the standard order, including a fresh-output guard and all required root arguments. Each stage also has a `--check` preflight and `--execute` mode. Source data, frozen evidence **and saved reference snapshots are not distributed in this repository**. See [data sources and access](docs/data-sources.md).

```bash
python run-spam.py --check
python run-spam.py --execute --run-root runs/new-review
```

## Scientific rules that must not be shortened away

- SPAM provides crop-area **magnitudes**; FAOSTAT is used only where previously approved for broad-parent composition weights.
- Apply the same allocation weights independently to harvested-irrigated, harvested-rainfed, physical-irrigated and physical-rainfed area. Derive totals and harvesting intensity **after** aggregation.
- In SPAM2000, `OTHE` physical area may be available as a published total with **no** irrigated/rainfed split. Keep that split unresolved, do not manufacture it.
- Year 2000 has 31 directly represented spatial FABLE products; the completed 56-product country panel is a distinct country-level product.
- The 23 MIRCA magnitude fallback cases and 23 MIRCA irrigation-share correction cases are **separate** evidentiary categories.
- For 2015, interpolate the *four area components* at grid-product level, not the intensities; no intensity cap at 1.
- The original completed 2000 and 2015 geography workflows have frozen inputs. This is a **frozen-evidence replay** at those steps.

## Validated local v1.0.0 replay

On **9 November 2026**, a full integrated six-stage execution of the organized GitHub package was reported as PASS using `pythongeo` and locally supplied source/evidence/reference datasets. The run command was:

```powershell
python .\run-spam.py --execute --data-root ".\data" --run-root ".\runs\trial-02"
```

- Stage 01: 2000, 2005, 2010 and 2020 native SPAM data matched the saved references, with no spatial-key or field mismatches.
- Stage 02: 56 products, 121 direct mapping edges and 16,713 country-year weighted edges matched.
- Stage 03: observed 5-arcminute outputs matched the saved references in all four years, with zero parent-conservation failures.
- Stage 04: 9,912 completed year-2000 country-product records; 23 MIRCA magnitude fallbacks and 23 separately approved share corrections checked against frozen evidence.
- Stage 05: 24,060,400 interpolated 2015 spatial records; zero spatial or country-product mismatches.
- Stage 06: 52,864 historical rows × 34 fields; zero reference row/field mismatches and zero Calculator formula failures.

The final runner returned `Selected stages completed`. See [`docs/validation-v1.0.0.md`](docs/validation-v1.0.0.md) for recorded validation outcomes and precise remaining limitations.

**Reproducibility scope:** This is a successfully tested **local, reference-validated frozen-evidence replay**, not a complete independent derivation of all FAOSTAT weights, year-2000 MIRCA/FAOSTAT decisions, the 2015 country crosswalk or frozen metadata from raw third-party sources. The local 52-file input set is not bundled with the public code release, and input redistribution rights must be checked independently.

The optional notebooks in `notebooks/` are output-cleared research review workbooks. They are **not** a separately validated notebook-first end-to-end execution and may require local source/evidence configuration; use `run-spam.py` for the validated automated execution.

## Combined methodology

See [`docs/technical-note.md`](docs/technical-note.md) and [`docs/reproducibility.md`](docs/reproducibility.md).

## Local input data

Read [`docs/data-sources.md`](docs/data-sources.md) for verified official download links and the access limits of the project-derived evidence; [`docs/copy-inputs.md`](docs/copy-inputs.md) gives portable setup commands. `tools/SPAM-required-files.csv` contains the 52-file manifest with **relative paths only**. Check local inputs with:

```powershell
python tools/collect-spam-inputs.py --check --destination ".\data"
```

**Public v1.0.0 limitation:** Original SPAM data can be obtained from its publishers, but the approved project-specific mapping/FAOSTAT/MIRCA decisions and saved comparison references are not yet hosted for public download. Therefore a third party **cannot run the full pipeline from public SPAM downloads alone**. The package is a locally validated research-code release, not a fully self-contained independent build. See [reproducibility scope](docs/reproducibility.md).

For publication, see [release steps](docs/github-release.md). Do not publish `data/` or `runs/` even if they exist in your local working copy.
