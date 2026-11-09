#!/usr/bin/env python3
"""Run the SPAM-to-FABLE verification chain from a local data directory.

This is a frozen-evidence replay with independent numerical checks, NOT a
complete reconstruction of every FAOSTAT/MIRCA decision or border crosswalk.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import subprocess
import sys

STAGES = {
    "01": "01-native-spam.py",
    "02": "02-map-crops.py",
    "03": "03-map-grids.py",
    "04": "04-complete-2000.py",
    "05": "05-interpolate-2015.py",
    "06": "06-build-history.py",
}


def command(stage: str, data: Path, ref: Path, legacy: Path, run: Path, check: bool):
    script = Path(__file__).resolve().parent / "scripts" / STAGES[stage]
    mode = "--check" if check else "--execute"
    roots = {
        "01": ["--data-root", str(data), "--reference-root", str(ref),
               "--output-root", str(run / "01-native")],
        "02": ["--data-root", str(data), "--reference-root", str(ref),
               "--output-root", str(run / "02-mapping")],
        "03": ["--input-root", str(ref), "--native-dir",
               str(run / "01-native" / "01_native_5arcmin"), "--mapping-dir",
               str(run / "02-mapping" / "02_mapping"),
               "--reference-root", str(ref), "--output-root", str(run / "03-grids")],
        "04": ["--reference-root", str(ref), "--stage03-root", str(run / "03-grids"),
               "--legacy-root", str(legacy), "--output-root", str(run / "04-completion")],
        "05": ["--reference-root", str(ref), "--stage03-root", str(run / "03-grids"),
               "--output-root", str(run / "05-interpolation")],
        "06": ["--reference-root", str(ref), "--stage03-root", str(run / "03-grids"),
               "--stage04-root", str(run / "04-completion"),
               "--stage05-root", str(run / "05-interpolation"),
               "--output-root", str(run / "06-history")],
    }
    return [sys.executable, "-u", str(script), mode, *roots[stage]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path(__file__).resolve().parent / "data")
    parser.add_argument("--reference-root", type=Path)
    parser.add_argument("--legacy-root", type=Path)
    parser.add_argument("--run-root", type=Path, default=Path(__file__).resolve().parent / "runs" / "trial-01")
    parser.add_argument("--stage", choices=[*STAGES, "all"], default="all")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    data = args.data_root.expanduser().resolve()
    ref = (args.reference_root or data / "SPAM_FABLE_FINAL").expanduser().resolve()
    legacy = (args.legacy_root or data / "legacy" / "06_harmonized_irrigation").expanduser().resolve()
    run = args.run_root.expanduser().resolve()
    selected = list(STAGES) if args.stage == "all" else [args.stage]
    if args.execute and any((run / {"01":"01-native","02":"02-mapping","03":"03-grids",
                                     "04":"04-completion","05":"05-interpolation","06":"06-history"}[s]).exists()
                            for s in selected):
        raise FileExistsError("Run directory has an output for a selected stage. Choose a fresh --run-root.")
    if args.check and args.stage == "all":
        # On a fresh workspace, downstream checks depend on Stage 01/02 output.
        selected = ["01", "02"]
        print("Preflight first two stages only. Later stages require their fresh outputs.", flush=True)
    for stage in selected:
        call = command(stage, data, ref, legacy, run, args.check)
        print(f"\nSPAM stage {stage} | {'preflight' if args.check else 'execute'}", flush=True)
        print("$", " ".join('"'+x+'"' if ' ' in x else x for x in call), flush=True)
        process = subprocess.run(call, check=False)
        if process.returncode:
            raise SystemExit(f"Stage {stage} failed with code {process.returncode}; outputs preserved for inspection")
    print("\nSelected stages completed. Review manifests before publication.", flush=True)


if __name__ == "__main__":
    main()
