"""Run fusion pipeline on every flat-original-with-v2 form.

Imports fuse_one() from fuse_layer1_cf.py and walks the full panel.
Skips forms whose fused output already exists.
"""
from __future__ import annotations

import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import fitz  # noqa: E402
from scripts.fuse_layer1_cf import fuse_one, OUT_DIR, ORIG_DIR, CF_DIR, OURS_DIR  # noqa: E402


def list_targets() -> list[tuple[str, str]]:
    out = []
    for src in sorted(ORIG_DIR.rglob("*.pdf")):
        try:
            d = fitz.open(src)
            widgets = sum(len(list(p.widgets())) for p in d)
            d.close()
        except Exception:
            continue
        if widgets > 0:
            continue
        cat = src.parent.name
        if not (OURS_DIR / cat / (src.stem + "_fillable.pdf")).exists():
            continue
        if not (CF_DIR / cat / (src.stem + "_commonforms.pdf")).exists():
            # CF stage hasn't completed for this form
            continue
        out.append((cat, src.name))
    return out


def main() -> int:
    targets = list_targets()
    print(f"Fusing {len(targets)} forms...")
    new_, skipped, failed = 0, 0, 0
    for i, (cat, name) in enumerate(targets, 1):
        stem = pathlib.Path(name).stem
        out_pdf = OUT_DIR / cat / f"{stem}_fused.pdf"
        if out_pdf.exists():
            skipped += 1
            continue
        t0 = time.time()
        try:
            r = fuse_one(cat, name)
            elapsed = time.time() - t0
            print(f"  [{i:3d}/{len(targets)}] OK {elapsed:5.1f}s  {name[:55]}  total={r['total']}")
            new_ += 1
        except Exception as e:
            print(f"  [{i:3d}/{len(targets)}] FAIL  {name[:55]}: {e}")
            failed += 1
    print(f"\nFusion: {new_} new / {skipped} cached / {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
