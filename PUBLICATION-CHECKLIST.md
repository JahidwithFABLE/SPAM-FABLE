# v1.0.0 publication checklist

- [x] The organized six-stage runner completed its local frozen-evidence scientific replay (9 November 2026); all six stage QC statuses PASS.
- [x] The public release ZIP includes **no source/derived data**, preserved scientific scripts, five output-cleared research notebooks and documented inputs.
- [x] Exact 52-file inventory now uses portable relative paths; source dataset landing pages are linked in `docs/data-sources.md`.
- [x] Third-party reproducibility limitations, frozen-evidence dependencies and optional saved-reference requirements are stated clearly.
- [ ] Confirm rights holder / SDSN-FABLE contributor approval for code, methodology and any project-derived materials.
- [ ] Select code licence with authorized rights holder (none is automatically assigned by this ZIP).
- [ ] Review source citations, author and contributor attributions, and science narrative for the Zenodo technical note.
- [ ] Decide how other researchers may lawfully obtain frozen mapping/FAOSTAT/MIRCA/crosswalk/metadata evidence. Until hosted, **do not claim complete public reproduction**.
- [ ] Run `python tools/check-release.py` in the extracted publication ZIP; inspect Git staged changes.
- [ ] Create repository, push and tag `v1.0.0` only after the above release approvals.
