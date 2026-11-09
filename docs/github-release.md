# GitHub v1.0.0 release steps

This is a **code-only research release** with a documented local integrated six-stage PASS, not a fully public-data-runnable package. The `v1.0.0` tag must refer to the exact reviewed code/documentation, and the linked technical note should identify this limitation.

## Before publishing

1. Confirm author/contributor list, ownership, affiliation, institutional publication approval and scientific note review.
2. Select and add a code `LICENSE` **only** after approval. This package intentionally does not impose a licence on behalf of the rights holder.
3. If project-owned evidence is later released separately, put its actual DOI/URL and licence in `docs/data-sources.md`. Do not claim an uncreated Zenodo deposit exists.
4. Keep `data/` (except its README) and `runs/` outside Git. The frozen reference files and original SPAM spatial files are large and may have distinct redistribution terms.
5. In the extracted publication repository, run `python tools/check-release.py` and inspect Git's staged file list before committing.

## On a computer with Git installed (PowerShell)

```powershell
python .\tools\check-release.py

git init -b main
git add .gitignore README.md CHANGELOG.md PUBLICATION-CHECKLIST.md requirements.txt run-spam.py scripts notebooks docs tools data/README.md
git diff --cached --name-only
git diff --cached --stat
```

If the staged files contain any dataset, large binary, undisclosed information or a disallowed file, unstage it and resolve the problem **before** proceeding. Then, subject to rights approval:

```powershell
git commit -m "Release SPAM-FABLE research pipeline v1.0.0"
git remote add origin https://github.com/YOUR-USERNAME/SPAM-FABLE.git
git push -u origin main
git tag -a v1.0.0 -m "SPAM-FABLE v1.0.0 — validated frozen-evidence replay"
git push origin v1.0.0
```

Replace `YOUR-USERNAME` with the actual account or organization and create an empty GitHub repository first. If a remote is already configured, inspect `git remote -v` instead of adding another. Git credentials/authentication are handled locally by GitHub/Git, not this package.

## Suggested release title and scope

**SPAM-FABLE v1.0.0 — Historical Crop-Area Harmonization for the FABLE Calculator**

Validated local replay: 2000/2005/2010/2020 native source reconstruction, frozen-evidence crop mapping, 2000 completion, 2015 interpolation, final 52,864-row × 34-column numerical assembly; zero saved-reference mismatches in the recorded integrated run. External mapping/FAOSTAT/MIRCA decisions, geographic crosswalk and historical metadata remain frozen dependencies. Public code ZIP excludes all 52 local data inputs.

**Suggested Zenodo technical-note sentence:**

> The companion code is available as SPAM-FABLE, version 1.0.0 (insert the actual GitHub release URL after publication). Its six-stage local frozen-evidence replay matched the validated historical Calculator reference. The repository does not contain source or project-derived input data; complete public reproduction additionally requires access to the frozen mapping, harmonization and reference evidence described in the data-availability documentation.

Do not invent a GitHub URL, DOI, licence, or hosting endpoint before the corresponding action has actually happened.
