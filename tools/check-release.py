#!/usr/bin/env python3
"""Read-only GitHub v1.0.0 packaging guard; not a scientific QC test."""
from pathlib import Path
import json
import re
import sys


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors = []
    scripts = sorted((root / 'scripts').glob('0?-*.py'))
    if len(scripts) != 6:
        errors.append(f'Expected six stage scripts, found {len(scripts)}')
    for path in (root / 'data').rglob('*'):
        if path.is_file() and path.name != 'README.md':
            errors.append(f'Data must not be shipped in public ZIP: {path.relative_to(root)}')
    runroot = root / 'runs'
    if runroot.exists() and any(runroot.rglob('*')):
        errors.append('Generated runs/ content must not be shipped in public ZIP')
    for notebook in (root / 'notebooks').glob('*.ipynb'):
        try:
            nb = json.loads(notebook.read_text(encoding='utf-8'))
        except Exception as e:
            errors.append(f'Invalid notebook {notebook.name}: {e}')
            continue
        if any(c.get('outputs') or c.get('execution_count') is not None for c in nb['cells']):
            errors.append(f'Notebook has execution outputs: {notebook.name}')
    for path in root.rglob('*'):
        if path.is_file() and path.stat().st_size > 50 * 1024**2:
            errors.append(f'Large file in ZIP: {path.relative_to(root)}')
    for path in list((root / 'docs').glob('*.md')) + list((root / 'tools').glob('*.csv')):
        text = path.read_text(encoding='utf-8-sig')
        if re.search(r'\bC:\\(?:Users|FABLE_JAHID)\\', text, re.I):
            errors.append(f'Potential private absolute path in {path.relative_to(root)}')
    print('Stage scripts:',len(scripts),'| Review notebooks:',len(list((root/'notebooks').glob('*.ipynb'))))
    print('Core scientific validation: see docs/validation-v1.0.0.md (not rerun by this guard)')
    print('Input accessibility: public 52-file dataset is NOT provided; see docs/data-sources.md')
    if errors:
        print('PUBLICATION PACKAGE REVIEW REQUIRED:')
        for e in errors:print('-',e)
        return 1
    print('PASS_PUBLICATION_PACKAGE_STATIC_GUARD')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
