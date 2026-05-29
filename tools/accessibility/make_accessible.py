#!/usr/bin/env python3
"""End-to-end: official Maine court form -> filled, tagged, accessible PDF.

This is the one-command driver behind the project's claim — it distributes no
court PDFs. It fetches the blank form from its `source_url` (maineprobate.net) at
runtime, then runs the deterministic chain:

  1. fetch    blank form from repo/forms/<ID>/metadata.json -> source_url
  2. embed    fonts on the BLANK source (ghostscript) — before fill, never after
  3. fill     resolved case data via tools/fill_pdf.py (geometry injection)
  4. tag      schema field names + OpenDataLoader tag tree (accessibility_pipeline)
  5. repair   widget/checkbox/subset fonts + ToUnicode (embed_widget_font)
  6. verify   (optional) veraPDF UA-1 report

Each external tool is optional and auto-detected; missing ones degrade with a
warning (e.g. no ghostscript -> the source-font embed is skipped, so a few font
checks may remain). Override binaries via env: GHOSTSCRIPT, VERAPDF, ODL_PYTHON.

    python3 tools/accessibility/make_accessible.py \
        --form DE-101 --case case.json --out DE-101.accessible.pdf [--verify]

    # already have the blank? skip the fetch:
    python3 tools/accessibility/make_accessible.py \
        --form DE-101 --case case.json --source blank.pdf --out out.pdf

Not legal advice — output is a draft to verify against the official form.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent          # tools/accessibility
TOOLS = HERE.parent                                      # tools
ROOT = TOOLS.parent                                      # repo root
GS = os.environ.get("GHOSTSCRIPT", "gs")
VERAPDF = os.environ.get("VERAPDF", "verapdf")
UA = {"User-Agent": "Mozilla/5.0 (maine-probate-forms-oss accessibility driver)"}


def _have(binary: str) -> bool:
    return shutil.which(binary) is not None


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def fetch_source(form: str, root: pathlib.Path, dest: pathlib.Path) -> str:
    meta = json.loads((root / "repo" / "forms" / form / "metadata.json").read_text())
    url = meta.get("source_url")
    if not url:
        raise SystemExit(f"no source_url for {form} in metadata.json")
    print(f"[1/6] fetch  {url}")
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        dest.write_bytes(r.read())
    return url


def embed_blank(src: pathlib.Path, out: pathlib.Path) -> bool:
    """Embed all fonts on the BLANK source with ghostscript. Doing this before the
    fill avoids the flatten/ToUnicode loss you get re-distilling a filled form."""
    if not _have(GS):
        print("[2/6] embed  (skipped — ghostscript not found; set GHOSTSCRIPT). "
              "A few source-font checks may remain.")
        shutil.copy(src, out)
        return False
    r = _run([GS, "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pdfwrite",
              "-dEmbedAllFonts=true", "-dSubsetFonts=true", "-dCompatibilityLevel=1.7",
              f"-sOutputFile={out}", "-c", "<</NeverEmbed [ ]>> setdistillerparams",
              "-f", str(src)])
    if not out.exists() or out.stat().st_size == 0:
        print(f"[2/6] embed  (ghostscript failed; using raw source)\n{r.stderr[-300:]}")
        shutil.copy(src, out)
        return False
    print("[2/6] embed  fonts embedded on blank source")
    return True


def fill(form: str, case: pathlib.Path, source: pathlib.Path, out: pathlib.Path,
         root: pathlib.Path) -> None:
    print(f"[3/6] fill   {form} from {case.name}")
    r = _run([sys.executable, str(TOOLS / "fill_pdf.py"), "--form", form,
              "--case", str(case), "--source", str(source), "--out", str(out)])
    ok = out.exists()
    # fill_pdf prints a JSON result; surface its error if it failed.
    if not ok:
        msg = r.stdout.strip() or r.stderr.strip()
        try:
            msg = json.loads(r.stdout).get("error", msg)
        except Exception:
            pass
        raise SystemExit(f"fill failed: {msg}")


def tag(filled: pathlib.Path, out: pathlib.Path, schema: pathlib.Path) -> None:
    print("[4/6] tag    field names + OpenDataLoader tag tree")
    r = _run([sys.executable, str(HERE / "accessibility_pipeline.py"),
              str(filled), str(out), "--schema", str(schema)])
    if not out.exists():
        raise SystemExit("tagging failed (field-name remediation step errored):\n"
                         f"{r.stderr[-400:]}")
    # The pipeline degrades when OpenDataLoader is absent (field names still applied,
    # tag tree skipped); surface that one-line note rather than swallowing it.
    if "OpenDataLoader unavailable" in r.stderr:
        print("       " + next(l for l in r.stderr.splitlines()
                                if "OpenDataLoader unavailable" in l).strip())


def repair_fonts(tagged: pathlib.Path, out: pathlib.Path, ttf=None, zapf=None) -> None:
    print("[5/6] repair widget/checkbox/subset fonts + ToUnicode")
    cmd = [sys.executable, str(HERE / "embed_widget_font.py"), str(tagged), str(out)]
    if ttf:
        cmd += ["--ttf", ttf]
    if zapf:
        cmd += ["--zapf", zapf]
    r = _run(cmd)
    if not out.exists():
        print(f"[5/6] repair (font repair failed; using tagged output)\n{r.stderr[-300:]}")
        shutil.copy(tagged, out)


def verify(pdf: pathlib.Path) -> None:
    if not _have(VERAPDF):
        print("[6/6] verify (skipped — veraPDF not found; set VERAPDF)")
        return
    xml = _run([VERAPDF, "--flavour", "ua1", str(pdf)]).stdout
    comp = re.search(r'isCompliant="(\w+)"', xml)
    fails = re.search(r'failedChecks="(\d+)"', xml)
    c = comp.group(1) if comp else "?"
    f = fails.group(1) if fails else "?"
    mark = "✓" if c == "true" else "✗"
    print(f"[6/6] verify {mark} veraPDF UA-1: compliant={c} failedChecks={f}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--form", required=True, help="form id, e.g. DE-101")
    ap.add_argument("--case", required=True, help="case data JSON (canonical or probate-native)")
    ap.add_argument("--out", required=True, help="final accessible PDF path")
    ap.add_argument("--source", help="local blank PDF (skip the fetch)")
    ap.add_argument("--root", default=str(ROOT), help="repo root (contains repo/forms/)")
    ap.add_argument("--verify", action="store_true", help="run veraPDF UA-1 at the end")
    ap.add_argument("--ttf", help="widget substitute font (else WIDGET_TTF/system Liberation Sans)")
    ap.add_argument("--zapf", help="ZapfDingbats TTF for checkbox font (else ZAPF_TTF)")
    ap.add_argument("--keep", action="store_true", help="keep intermediate files (print dir)")
    a = ap.parse_args()

    root = pathlib.Path(a.root)
    schema = root / "repo" / "forms" / a.form / "schema.json"
    if not schema.exists():
        print(f"no form package for {a.form} under {root}/repo/forms/", file=sys.stderr)
        return 2
    case = pathlib.Path(a.case)
    if not case.exists():
        print(f"case file not found: {case}", file=sys.stderr)
        return 2
    out = pathlib.Path(a.out).resolve()

    tmp = pathlib.Path(tempfile.mkdtemp(prefix=f"a11y_{a.form}_"))
    raw = tmp / "raw.pdf"
    if a.source:
        print(f"[1/6] fetch  (skipped — using local {a.source})")
        shutil.copy(a.source, raw)
    else:
        fetch_source(a.form, root, raw)

    embedded = tmp / "embedded.pdf"
    embed_blank(raw, embedded)
    filled = tmp / "filled.pdf"
    fill(a.form, case, embedded, filled, root)
    tagged = tmp / "tagged.pdf"
    tag(filled, tagged, schema)
    out.parent.mkdir(parents=True, exist_ok=True)
    repair_fonts(tagged, out, a.ttf, a.zapf)
    print(f"  -> {out}")
    if a.verify:
        verify(out)
    if a.keep:
        print(f"  intermediates: {tmp}")
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
