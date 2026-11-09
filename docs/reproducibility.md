# Reproducibility scope and known dependencies

The **organized repository's complete six-stage runner was executed and reported PASS** on 9 November 2026 using locally supplied input/evidence/reference files. The result is a validated **frozen-evidence replay**, not a complete independent recreation of every upstream evidence decision. See [`validation-v1.0.0.md`](validation-v1.0.0.md).

| Stage | Integrated local result | Remaining dependency |
|---|---|---|
| 01 native SPAM | All four observed years rebuilt from original SPAM CSV; 0 reference mismatches | Original SPAM releases and 2005 coordinate lookup must be supplied |
| 02 crop mapping | 56 products, 121 direct edges and 16,713 weighted edges matched | Frozen mapping-v2 weights and FAOSTAT composition evidence |
| 03 spatial mapping | 2000/05/10/20 mapped freshly from Stages 01–02; 0 spatial or conservation failures | Reference grids used for comparison |
| 04 2000 completion | 9,912 records, 23 magnitude cases and 23 share corrections validated | Frozen FAOSTAT/MIRCA completion evidence |
| 05 2015 midpoint | 24,060,400 grid-product records, 0 mismatch | Frozen country-border crosswalk |
| 06 Calculator history | 52,864 × 34, 0 numerical/field mismatches | Frozen validated structural metadata/key universe |

The original reference dataset is **read-only**, and the new scripts refuse to overwrite previous outputs. A reference match is strong evidence of implementation fidelity but does not validate the underlying selection of all source datasets, proxy factors, editorial decisions or external permissions.

## 2000 evidence semantics

- The spatial observation is the available source-only mapping; 31 spatial FABLE products are represented.
- The completed country-level panel has 177 countries × 56 products.
- 26 scientifically unresolved no-evidence records are not the same as the 26 operational-only Calculator zeros.
- The 23 MIRCA crop-magnitude fallbacks are not the same records/process as the 23 approved MIRCA irrigation-share updates.
- SPAM2000 `OTHE` physical total-only values remain distinct from an irrigated/rainfed area split.

## 2020 source totals

The 2020 post-processing check confirmed 25 harvested and 33,070 physical source-total NULLs were represented as 0.0 in the validated reference; no unsafe reference substitutions were needed. Other area, source-review flags and intensity calculations were unchanged. These modifications affect **source-total representation**, not measured split magnitude.

## Public release gates

Before labelling the repository a wholly independent raw-to-final reimplementation, complete and document: (1) raw FAOSTAT and mapping-v2 derivation; (2) year-2000 magnitude completion evidence derivation; (3) raw geometry to 2015 country crosswalk; (4) independent derivation of frozen metadata/key-universe decisions; and (5) independent testing on other machines with a fully documented environment. The integrated six-stage local run has now been completed. Public redistribution of raw or processed files and repository licensing require the rights holder's explicit review.
