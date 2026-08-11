"""
Run this once from the project root (D:\\tinyagentos) to fix trailing
newlines on the two files flagged by flake8:

    python fix_newlines.py

Strips any trailing blank lines / missing newline and writes back exactly
one trailing newline, matching what flake8 (W391 / W292) expects. Does
not touch anything else in the files.
"""

import pathlib

FILES = [
    "tests/performance/test_benchmarks.py",
    "tests/unit/test_day10.py",
]

for rel_path in FILES:
    path = pathlib.Path(rel_path)
    if not path.exists():
        print(f"SKIP (not found): {path}")
        continue

    original = path.read_text(encoding="utf-8")
    fixed = original.rstrip("\r\n \t") + "\n"

    if fixed == original:
        print(f"OK, already clean: {path}")
    else:
        path.write_text(fixed, encoding="utf-8", newline="")
        print(f"FIXED: {path}")
