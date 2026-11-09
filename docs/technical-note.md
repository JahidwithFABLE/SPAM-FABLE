# Technical note: historical crop areas and irrigation water for FABLE

**Scope:** Harmonizing gridded SPAM crop-area observations and constructing crop-specific irrigation water requirement inputs for FABLE-style national scenario modelling. This note is a compact shared technical description. Detailed numerical source choices and supporting citations need final scientific and institutional review before a formal methods publication.

## 1. Historical crop areas: SPAM-to-FABLE

**Raw observations.** Global SPAM source releases for 2000, 2005, 2010 and 2020 are read at native 5-arcminute resolution. Keep harvested and physical crop areas distinct: harvested area can exceed physical area where the same land is harvested more than once. For each irrigated/rainfed system, process source area independently and retain the original published totals where available for comparison. The 2000 `OTHE` physical crop category includes positive published physical totals without a reliable split between irrigation systems; its split remains missing, rather than assumed zero.

**FABLE mapping.** Map native crops to a frozen set of 56 products. One-to-one and many-to-one mappings do not require additional composition; selected broad parent crops require country-year weights. SPAM remains the source for *area magnitude*; FAOSTAT crop shares are used only for approved *composition* of broad categories. For mapped product `p`, native cell `g`, irrigation system `s` and year `t`:

`A_FABLE(g,p,s,t) = Σ_c [ A_SPAM(g,c,s,t) × w(country,t,c,p) ]`

Weights for each mapped broad parent must sum to one. Apply the same accepted weight to all four independent area components, then derive totals and intensities. Do not cap harvesting intensity at one: multiple cropping can be legitimate.

**Year 2000.** Spatial support does not cover all 56 FABLE products. Construct a separate 177-country × 56-product country panel using validated historical completion evidence. Preserve scientifically missing states separately from Calculator-only structural zeros. Distinguish the 23 MIRCA crop-magnitude fallback decisions from the 23 separate approved irrigation-share adjustments. Where a share is altered, preserve crop-level total harvested and total physical area as required by the approved method.

**Year 2015.** Match 2010 and 2020 gridded FABLE-product data, use the frozen 2015 country-assignment crosswalk and calculate the midpoint independently for irrigated harvested, rainfed harvested, irrigated physical and rainfed physical area. A sparse missing endpoint is zero only where coverage and mapping criteria support that interpretation. Recompute national totals and all harvesting intensities *after* interpolation. The original crosswalk is a frozen dependency, not new border extraction.

**Calculator output.** Five years (2000, 2005, 2010, 2015, 2020), 56 products and year-dependent country coverage yield the previously validated 52,864 country-product-year rows, with a 34-column release schema. Derived area and intensity values match the reference in the recorded stage checks; some provenance and structural-zero metadata remain inherited.

## 2. Net crop irrigation water requirement

**Target quantity.** Net irrigation water requirement is defined as water needed per unit of *irrigated harvested area*, expressed as `mm/year` or `m³/ha/year`. Conversion: `1 mm` across `1 ha` equals `10 m³`; hence `m³/ha/year = mm/year × 10`.

**Primary crop evidence.** The reviewed research workflow references ISIMIP `pirrww` series, with LPJmL providing the broader primary crop-reference layer and GEPIC/PEPIC used to characterize common-crop uncertainty. For crops not represented directly, explicit crop-family proxy and scaling decisions may use FAO seasonal crop-needs ranges, selected literature or external crop-model evidence; each decision requires provenance, source semantics and coverage checks. No unsourced water values should be imputed silently. Season-specific wheat/rice disaggregation must not be invented where the data do not support it.

**Scenarios.** The previously recorded output includes historical years and RCP2.6/RCP6.0 pathways. Verify climate scenarios, crop-code coverage, geographic reach, source year, units and the original coefficient selection against the final frozen release before publishing a numerical IWR claim.

## 3. Gross irrigation withdrawal

Gross withdrawal is not the same quantity as net irrigation requirement. To apply irrigation-system losses, let `N` be the net water requirement (`m³/ha/year`), `s_t` the technology share, and `e_t` the corresponding application efficiency. One simplified operational expression is:

`Gross withdrawal per hectare = N × Σ_t(s_t / e_t)`

The recorded loss multipliers are surface `2.380952`, sprinkler `1.403509`, drip `1.169591`. Country-year shares must sum to one and must derive from documented source evidence. The small `water/scripts/01-water-withdrawals.py` implements the **coefficient conversion only**, assuming the required net and share tables are already selected and valid. It does not replace the primary crop-water requirement workflow or prove consistency with every historical full-release exception.

**Validation semantics.** For independent national context, an AQUASTAT irrigation-water withdrawal estimate is a closer comparator for gross withdrawal than total agricultural water withdrawal; a *net requirement* should not be evaluated as if it were a withdrawn volume. SPAM's irrigated harvested area is not interchangeable with equipped or physical irrigated area. Differences in scope and year coverage must be documented before interpreting biases.

## 4. Integration and publication boundaries

Both models use the same 56-product vocabulary and historical time convention, but they are independent data products with different units, spatial aggregation and uncertainty sources. Water demand per area may be combined with compatible irrigated harvested area to estimate aggregate withdrawal; that multiplication requires matching country, crop, year, scenario and definition of the irrigated denominator.

**Scientific reporting:** archive source versions, weights, proxy choice tables, original vs corrected values, source hashes, unit checks and missing-data conventions. Do not publish only a final output without the decisions needed to understand how it was produced. For the present code release, see [`reproducibility.md`](reproducibility.md) for unresolved upstream evidence dependencies. Public dataset distribution, source redistribution and institutional permission should be confirmed separately.


## Code and data availability

The six-stage Python code and its local frozen-evidence validation record are documented in this repository. Original SPAM downloads, derived FAOSTAT/mapping weights, approved MIRCA corrections, 2015 crosswalk, stored reference datasets and the final historical release are **not bundled** in the public code distribution. A third party cannot independently reproduce the validated full workflow using original SPAM downloads alone. See [Data sources and access](data-sources.md) and [Reproducibility scope](reproducibility.md).
