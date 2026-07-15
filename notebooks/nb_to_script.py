#!/usr/bin/env python3
"""Convert adequacy_clean.ipynb → adequacy_clean.py (headless-runnable).

Strips / rewrites:
  • IPython magics (%matplotlib, %time, ...) and shell escapes (!cmd)
  • get_ipython()... lines produced by `jupyter nbconvert --to script`
  • display(x) → falls back to print(x) when IPython is not installed

Usage:
    python3 nb_to_script.py adequacy_clean.ipynb adequacy_clean.py
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

HEADER = '''"""adequacy_clean — headless script (generated from adequacy_clean.ipynb)."""
from __future__ import annotations

# display() fallback so notebook-style diagnostic prints still work
try:
    from IPython.display import display  # noqa: F401
except Exception:   # plain Python, no IPython installed
    def display(*args, **_kw):
        for a in args:
            if hasattr(a, "to_string"):
                print(a.to_string())
            else:
                print(a)

'''

_MAGIC_RE       = re.compile(r"^\s*%[A-Za-z].*$", re.MULTILINE)
_SHELL_RE       = re.compile(r"^\s*![^\n]*$", re.MULTILINE)
_GET_IPYTHON_RE = re.compile(r"^\s*get_ipython\(\).*$", re.MULTILINE)
_FUTURE_RE      = re.compile(r"^\s*from\s+__future__\s+import[^\n]*$", re.MULTILINE)


def convert(ipynb: Path, out: Path) -> None:
    nb = json.loads(ipynb.read_text())
    chunks: list[str] = [HEADER]
    for i, cell in enumerate(nb["cells"]):
        if cell["cell_type"] != "code":
            continue
        src = "".join(cell["source"])
        src = _MAGIC_RE.sub("", src)
        src = _SHELL_RE.sub("", src)
        src = _GET_IPYTHON_RE.sub("", src)
        src = _FUTURE_RE.sub("", src)  # header already has the future import
        chunks.append(f"\n# ── Cell {i} " + "─" * 60 + "\n")
        chunks.append(src.rstrip() + "\n")
    out.write_text("".join(chunks))
    print(f"Wrote {out} ({out.stat().st_size/1024:.1f} KB)")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__); sys.exit(1)
    convert(Path(sys.argv[1]), Path(sys.argv[2]))
