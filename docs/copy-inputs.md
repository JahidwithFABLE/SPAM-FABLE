# Local input setup | 52-file manifest

The **public GitHub ZIP contains code and instructions, not the 52 actual input files**. For the complete file list, open [`../tools/SPAM-required-files.csv`](../tools/SPAM-required-files.csv). It uses only *relative* paths, so it is not tied to an author's computer.

## Data folder layout

```text
SPAM-FABLE/
├── data/
│   ├── SPAM/CSV/{2000,2005,2010,2020}/
│   ├── SPAM/_07B_required_extracted/2005/cell5m_allockey_xy.csv
│   ├── mapping_audit/
│   ├── legacy/06_harmonized_irrigation/
│   └── SPAM_FABLE_FINAL/
└── runs/               # generated; not supplied
```

Read [`data-sources.md`](data-sources.md) for authoritative raw-data links and a critical explanation of the additional frozen evidence required.

## A. Public user: manually supply their data

Place all files at the manifest's `destination_under_SPAM_FABLE_data` paths. No renaming. Then, in the repository root:

```powershell
python .\tools\collect-spam-inputs.py --check --destination ".\data"
```

`PRESENT` means a file exists at its expected destination. This is a path-presence check, not a checksum or scientific-validation guarantee. The command exits nonzero when any destination is missing.

## B. Original research machine: optionally collect existing files

If you already have an **authorized** local source/evidence snapshot organized into `SPAM/`, `mapping_audit/` and `SPAM_FABLE_FINAL/`, plus the two MIRCA provenance files, give their paths explicitly. Replace the example paths with your own:

```powershell
python .\tools\collect-spam-inputs.py --check --destination ".\data" --source-root "D:\Research\Data" --legacy-root "D:\Research\MIRCA-decisions"

python .\tools\collect-spam-inputs.py --copy --destination ".\data" --source-root "D:\Research\Data" --legacy-root "D:\Research\MIRCA-decisions"
```

Here `--legacy-root` is the directory **containing** the two `06_SPAM2000_*` files. Existing destination files are never overwritten; `--max-size-mb 100` optionally skips larger files. The helper is designed to copy local files that you already possess, **not** download them or supply unavailable project evidence.

## Validation run

```powershell
python .\run-spam.py --check --data-root ".\data"
python .\run-spam.py --execute --data-root ".\data" --run-root ".\runs\new-run"
```

`--check` only checks Stages 01–02 before downstream outputs exist. `--execute` generates each later stage in order and compares to local saved reference datasets. Use a **new empty output directory**. Preserve source copyright, licence, version, and access restrictions. Never commit the populated `data/` folder or generated `runs/` directory.
