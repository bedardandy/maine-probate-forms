"""Stage 1: full 79-form fused sweep — CF at 3200 + fusion.

For every flat original with a v2 layer1 output, run CommonForms maxrecall_hires
(image-size 3200, conf 0.20, --use-signature-fields --multiline) then the
fusion pipeline. Skips forms whose outputs already exist (so the script is
idempotent — safe to re-run after partial failures).
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import time
import re

import fitz

ROOT = pathlib.Path(__file__).resolve().parent.parent
ORIG_DIR = ROOT / "forms"
V2_DIR = ROOT / "output_layer1"
CF_DIR = ROOT / "output_commonforms" / "imgsize_3200"
FUSED_DIR = ROOT / "output_fused"
CF_VENV = ROOT / ".venv-commonforms" / "bin" / "commonforms"


def list_targets() -> list[tuple[str, str]]:
    """Return (cat, name) for flat originals that have a v2 _fillable.pdf."""
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
        v2 = V2_DIR / cat / (src.stem + "_fillable.pdf")
        if not v2.exists():
            continue
        out.append((cat, src.name))
    return out


def run_cf(cat: str, name: str) -> bool:
    """Run CF on a single form. Returns True if newly written."""
    src = ORIG_DIR / cat / name
    dst = CF_DIR / cat / (pathlib.Path(name).stem + "_commonforms.pdf")
    if dst.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(CF_VENV), str(src), str(dst),
        "--device", "cuda",
        "--image-size", "3200",
        "--confidence", "0.20",
        "--use-signature-fields",
        "--multiline",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if res.returncode != 0:
        print(f"  ! CF failed for {name}: {res.stderr.strip().splitlines()[-1] if res.stderr else 'unknown'}")
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cf-only", action="store_true", help="only run CF; skip fusion")
    ap.add_argument("--fusion-only", action="store_true", help="only run fusion; skip CF")
    ap.add_argument("--limit", type=int, default=None, help="limit to first N forms")
    args = ap.parse_args()

    targets = list_targets()
    if args.limit:
        targets = targets[: args.limit]
    print(f"Found {len(targets)} flat originals with v2 outputs.")

    # Stage A: CF at 3200
    if not args.fusion_only:
        new_cf, skipped_cf, failed_cf = 0, 0, 0
        for i, (cat, name) in enumerate(targets, 1):
            stem = pathlib.Path(name).stem
            dst = CF_DIR / cat / f"{stem}_commonforms.pdf"
            if dst.exists():
                skipped_cf += 1
                continue
            t0 = time.time()
            ok = run_cf(cat, name)
            elapsed = time.time() - t0
            tag = "OK" if ok else "FAIL"
            print(f"  [{i:3d}/{len(targets)}] {tag} {elapsed:5.1f}s  {name[:60]}")
            if ok:
                new_cf += 1
            else:
                failed_cf += 1
        print(f"\nCF stage: {new_cf} new / {skipped_cf} cached / {failed_cf} failed")

    # Stage B: fusion
    if args.cf_only:
        return 0
    print("\nFusion stage...")
    fuse_script = ROOT / "scripts" / "fuse_full_sweep.py"
    if not fuse_script.exists():
        print(f"  ! Fusion sweep script missing: {fuse_script}")
        return 1
    res = subprocess.run([sys.executable, str(fuse_script)], cwd=ROOT)
    return res.returncode


if __name__ == "__main__":
    sys.exit(main())
