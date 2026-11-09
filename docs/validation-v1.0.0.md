# Validation record — SPAM-FABLE v1.0.0

**Execution:** Reported integrated local test on 9 November 2026, Windows/Conda `pythongeo`.

**Runner:** `python run-spam.py --execute --data-root ".\data" --run-root ".\runs\trial-02"`.

**Scope:** The six-stage organized repository executed sequentially using 52 locally collected source, evidence and reference inputs (estimated total 9.65 GiB). The original 20 SPAM CSVs and coordinate lookup were read afresh for Stage 01. Later steps used fresh outputs from preceding stages **and explicitly documented frozen evidence**. The runner ended with `Selected stages completed.`

| Stage | Local reported evidence | Result |
|---|---|---|
| 01 — Native SPAM | 2000: 6,976,370 rows (177 countries); 2005: 9,495,879 (184); 2010: 9,893,259 (201); 2020: 12,937,366 (179). All four saved-reference missing/extra keys = 0 and mismatched records = 0. | PASS |
| 02 — Crop mapping | 56-product schema; 121 direct edges; 16,713 weighted country-year edges; 4,877 weighted parent groups; zero reference record/field differences. | PASS |
| 03 — Observed 5′ grids | 2000: 9,660,113; 2005: 16,583,782; 2010: 17,119,472; 2020: 18,090,975 rows. Zero missing/extra keys, mapping-reference mismatches or parent-conservation failures. | PASS |
| 04 — Year-2000 completion | 5,487 observed country rows; 9,912 completed country-product records; 26 scientific missing-only states; 23 magnitude fallbacks; 23 approved irrigation-share corrections; zero Calculator-area/intensity mismatches. | PASS |
| 05 — Year-2015 interpolation | 24,060,400 spatial rows; zero saved spatial and country-product mismatches. | PASS |
| 06 — Historical Calculator series | 52,864 country-product-year records, 34 schema columns; zero candidate reference mismatched rows, mismatched fields or Calculator numerical formula failures. | PASS |

**Preserved source exceptions:** SPAM2000 `OTHE` 654,868 physical-total-only records (559,861,492.700 ha) were retained without inventing irrigated/rainfed splits; SPAM2020 kept ten harvested and ten physical records requiring material source-component review, with status flags. Stage 01 source-total representation follows the reference's zero convention for 25 harvested and 33,070 physical 2020 source-total null cases.

**What PASS does and does not mean:** This is reference agreement and formula/conservation QC for the integrated local run. It is not an independent derivation of the initial FAOSTAT-based mapping-v2 weights, 2000 MIRCA/FAOSTAT evidence decisions, 2015 country crosswalk or inherited historical structural metadata. It also does not imply permissions to distribute raw or processed data.

**Local evidence location (not included in public code release):** `runs/trial-02/`, with per-stage manifests and QC files. Review and retain the manifests alongside an institutional research record; stage results above were transcribed from the user's supplied terminal logs, not re-executed by the release packager.

**Public reproducibility requirement:** Users must independently obtain source data and the approved/frozen evidence files listed in `docs/inputs.md` and `tools/SPAM-required-files.csv`. Merely downloading this code repository will not supply all necessary data.
