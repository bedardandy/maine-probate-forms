#!/usr/bin/env python3
"""Modular PDF enhancement pipeline for the Maine probate forms.

One small registry of composable STEPS (each wraps an existing function/CLI),
PRESETS that bundle steps into "levels", and a dependency-ordered RUNNER that
threads a single PDF through the selected steps. No new PDF logic lives here —
it orchestrates the tools already in the repo.

Adding a capability later = append one `Step(...)` to CATALOG (and optionally
name it in a preset). The web control panel renders from `catalog()` dynamically,
so no UI/server change is needed.

CLI:
    python3 tools/enhance.py --form DE-101 --steps formfields,remediate_doc --out out.pdf
    python3 tools/enhance.py --form DE-101 --preset accessible-standard --out out.pdf
    python3 tools/enhance.py --form DE-101 --steps fill --case case.json --out out.pdf
    python3 tools/enhance.py --catalog            # JSON: steps + presets + tool availability

Court PDFs are not shipped; the blank source is fetched from
metadata.json.source_url at run time. Not legal advice — outputs are drafts.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import shutil
import subprocess
import sys
import urllib.request

import fitz

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tools" / "accessibility"))

CACHE = pathlib.Path("/tmp/enhance_cache")


# --------------------------------------------------------------------------- #
# Context threaded through the steps
# --------------------------------------------------------------------------- #
@dataclasses.dataclass
class Ctx:
    form_id: str
    pdf: pathlib.Path                 # current working PDF (each step rewrites it)
    work: pathlib.Path                # scratch dir for this run
    case: dict | None = None
    log: list = dataclasses.field(default_factory=list)
    n: int = 0                        # step counter (for output filenames)

    def out(self, step_id: str) -> pathlib.Path:
        self.n += 1
        return self.work / f"{self.n:02d}_{step_id}.pdf"

    def schema(self) -> pathlib.Path:
        return ROOT / "repo" / "forms" / self.form_id / "schema.json"


# --------------------------------------------------------------------------- #
# Tool availability (steps with a missing tool degrade-and-skip)
# --------------------------------------------------------------------------- #
def _have_bin(name: str) -> bool:
    return shutil.which(name) is not None


def _have_mod(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


def tool_available(tool: str | None) -> bool:
    if not tool:
        return True
    if tool == "opendataloader":
        return _have_mod("opendataloader_pdf")
    return _have_bin(tool)


# --------------------------------------------------------------------------- #
# Step implementations (thin wrappers over existing code)
# --------------------------------------------------------------------------- #
def _source_url(form_id: str) -> str:
    m = json.loads((ROOT / "repo" / "forms" / form_id / "metadata.json").read_text())
    url = m.get("source_url") or m.get("source_pdf")
    if not url:
        raise RuntimeError(f"no source_url for {form_id}")
    return url


def fetch_source(form_id: str, fresh: bool) -> pathlib.Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    dst = CACHE / f"{form_id}.pdf"
    if dst.exists() and dst.stat().st_size > 800 and not fresh:
        return dst
    url = _source_url(form_id)
    rq = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    dst.write_bytes(urllib.request.urlopen(rq, timeout=60).read())
    if dst.read_bytes()[:5] != b"%PDF-":
        raise RuntimeError(f"fetched source for {form_id} is not a PDF")
    return dst


def _run_cli(args: list[str]) -> None:
    r = subprocess.run([sys.executable, *args], cwd=str(ROOT),
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "").strip()[:400])


def _geom(form_id: str) -> dict:
    return json.loads((ROOT / "repo" / "forms" / form_id /
                       "fill_geometry.json").read_text())["fields"]


def step_formfields(ctx: Ctx) -> None:
    """Inject blank, fillable AcroForm widgets from fill_geometry (no values)."""
    import fill_pdf  # reuse the widget primitives (carries A/B font/justify logic)
    doc = fitz.open(str(ctx.pdf))
    fill_pdf._strip_widgets(doc)
    geom = _geom(ctx.form_id)
    text = check = 0
    for fid, spec in geom.items():
        for i, w in enumerate(spec.get("widgets") or []):
            fill_pdf._add_text(doc[w["page"]], w["rect"],
                               fid if i == 0 else f"{fid}__{i}", "")
            text += 1
        for j, o in enumerate(spec.get("options") or []):
            fill_pdf._add_checkbox(doc[o["page"]], o["rect"],
                                   f"{fid}__{o.get('value') or j}")
            check += 1
    out = ctx.out("formfields")
    doc.save(str(out)); doc.close(); ctx.pdf = out
    ctx.log.append(f"formfields: {text} text + {check} checkbox widgets injected")


def step_fill(ctx: Ctx) -> None:
    """Fill resolved case values (deterministic; carries A/B/C consistency)."""
    import fill_pdf
    from canonical_adapter import to_case_object
    if not ctx.case:
        raise RuntimeError("fill requires case data (upload a case JSON)")
    out = ctx.out("fill")
    res = fill_pdf.fill_pdf(ctx.form_id, to_case_object(ctx.case), ctx.pdf, out, root=ROOT)
    if not res.get("ok"):
        raise RuntimeError(res.get("error", "fill failed"))
    ctx.pdf = out
    ctx.log.append(f"fill: {res['text_written']} text written, "
                   f"{res['options_checked']} checked")


def step_embed_fonts(ctx: Ctx) -> None:
    import make_accessible
    out = ctx.out("embed_fonts")
    ok = make_accessible.embed_blank(ctx.pdf, out)
    if ok and out.exists():
        ctx.pdf = out; ctx.log.append("embed_fonts: source fonts embedded (ghostscript)")
    else:
        ctx.log.append("embed_fonts: SKIPPED (ghostscript unavailable)")


def step_remediate_doc(ctx: Ctx) -> None:
    """Doc-level accessibility: title, /Lang, DisplayDocTitle, bookmarks, links."""
    out = ctx.out("remediate_doc")
    title = json.loads((ROOT / "repo" / "forms" / ctx.form_id /
                        "metadata.json").read_text()).get("title") or ctx.form_id
    _run_cli(["tools/accessibility/remediate_pdf.py", "remediate", str(ctx.pdf),
              str(out), "--lang", "en-US", "--title", f"{ctx.form_id} — {title}"])
    ctx.pdf = out; ctx.log.append("remediate_doc: title/lang/bookmarks/links written")


def step_tag(ctx: Ctx) -> None:
    """Schema /TU names + tab order + logical tag tree + PDF/UA stamp."""
    out = ctx.out("tag")
    _run_cli(["tools/accessibility/accessibility_pipeline.py", str(ctx.pdf), str(out),
              "--schema", str(ctx.schema())])
    ctx.pdf = out; ctx.log.append("tag: field /TU + tag tree + PDF/UA stamp")


def step_verify_ua(ctx: Ctx) -> None:
    import make_accessible
    try:
        make_accessible.verify(ctx.pdf)
        ctx.log.append("verify_ua: veraPDF UA-1 report emitted")
    except Exception:
        ctx.log.append("verify_ua: SKIPPED (veraPDF unavailable)")


def step_fieldmap(ctx: Ctx) -> None:
    """Debug overlay: stamp each field's name in its box, flatten+raster."""
    import fieldmap_pdf
    out_doc = fitz.open()
    fieldmap_pdf.stamp_form(ctx.form_id, ctx.pdf, out_doc, 150)
    out = ctx.out("fieldmap")
    out_doc.save(str(out), deflate=True); out_doc.close()
    ctx.pdf = out; ctx.log.append("fieldmap: field-name debug overlay rendered")


def step_flatten(ctx: Ctx) -> None:
    """Rasterize to a non-editable, flattened PDF."""
    doc = fitz.open(str(ctx.pdf)); out_doc = fitz.open()
    for page in doc:
        pix = page.get_pixmap(dpi=150, alpha=False)
        np = out_doc.new_page(width=page.rect.width, height=page.rect.height)
        np.insert_image(np.rect, pixmap=pix)
    out = ctx.out("flatten")
    out_doc.save(str(out), deflate=True); out_doc.close(); doc.close()
    ctx.pdf = out; ctx.log.append("flatten: rasterized to non-editable PDF")


# --------------------------------------------------------------------------- #
# Catalog + presets
# --------------------------------------------------------------------------- #
@dataclasses.dataclass
class Step:
    id: str
    label: str
    group: str
    desc: str
    apply: object                      # callable(Ctx) -> None
    requires: tuple = ()
    needs_case: bool = False
    needs_tool: str | None = None


CATALOG = [
    # Order matters: embed_fonts runs on the BLANK source first; fields/fill add
    # onto it; remediate_doc then tag/verify finalize. The runner topo-sorts by
    # `requires` but otherwise preserves this catalog order.
    Step("embed_fonts", "Embed fonts", "Accessibility",
         "Embed the source PDF's fonts (ghostscript) — done first, on the blank form.",
         step_embed_fonts, needs_tool="gs"),
    Step("formfields", "Add fillable form fields", "Form fields",
         "Inject blank AcroForm text boxes & checkboxes from the detected geometry.",
         step_formfields),
    Step("fill", "Fill with case data", "Form fields",
         "Write resolved values into the fields (consistent font, justification, spacing).",
         step_fill, requires=("formfields",), needs_case=True),
    Step("remediate_doc", "Document accessibility (basic)", "Accessibility",
         "Title, language, DisplayDocTitle, bookmarks, link annotations. No external tools.",
         step_remediate_doc),
    Step("tag", "Tag tree + PDF/UA", "Accessibility",
         "Schema field names (/TU) + tab order + logical tag tree + PDF/UA identifier.",
         step_tag, needs_tool="opendataloader"),
    Step("verify_ua", "Verify (veraPDF UA-1)", "Report",
         "Run a veraPDF PDF/UA-1 conformance report.",
         step_verify_ua, needs_tool="verapdf"),
    Step("fieldmap", "Field map (debug overlay)", "Debug",
         "Stamp each field's name in its box and flatten — to inspect the layout.",
         step_fieldmap),
    Step("flatten", "Flatten (non-editable)", "Output",
         "Rasterize the result to a flat, non-editable PDF.",
         step_flatten),
]
STEPS = {s.id: s for s in CATALOG}

PRESETS = {
    "fillable":             ["formfields"],
    "filled":               ["formfields", "fill"],
    "accessible-basic":     ["remediate_doc"],
    "accessible-standard":  ["embed_fonts", "formfields", "tag", "remediate_doc"],
    "accessible-full":      ["embed_fonts", "formfields", "fill", "tag", "remediate_doc", "verify_ua"],
    "fieldmap":             ["fieldmap"],
}


def catalog() -> dict:
    forms = sorted(p.parent.name for p in
                   (ROOT / "repo" / "forms").glob("*/metadata.json"))
    return {
        "forms": forms,
        "steps": [{"id": s.id, "label": s.label, "group": s.group, "desc": s.desc,
                   "requires": list(s.requires), "needs_case": s.needs_case,
                   "needs_tool": s.needs_tool, "available": tool_available(s.needs_tool)}
                  for s in CATALOG],
        "presets": PRESETS,
        "tools": {t: tool_available(t) for t in ("gs", "opendataloader", "verapdf")},
    }


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
def _order(step_ids: list[str]) -> list[str]:
    """Topological order over `requires`, preserving catalog order as tiebreak."""
    want = list(dict.fromkeys(step_ids))
    cat_order = [s.id for s in CATALOG]
    out, seen = [], set()

    def visit(sid, stack):
        if sid in seen:
            return
        if sid in stack:
            raise RuntimeError(f"cyclic requires at {sid}")
        for dep in STEPS[sid].requires:
            if dep in want:                # only auto-run deps the user selected
                visit(dep, stack | {sid})
        seen.add(sid); out.append(sid)

    for sid in sorted(want, key=cat_order.index):
        visit(sid, set())
    return out


def run(form_id: str, step_ids: list[str], case: dict | None = None,
        fresh: bool = False, work: pathlib.Path | None = None) -> dict:
    if (ROOT / "repo" / "forms" / form_id / "metadata.json").exists() is False:
        return {"ok": False, "error": f"unknown form {form_id!r}"}
    bad = [s for s in step_ids if s not in STEPS]
    if bad:
        return {"ok": False, "error": f"unknown step(s): {', '.join(bad)}"}

    work = work or pathlib.Path("/tmp/enhance_runs") / f"{form_id}"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    try:
        src = fetch_source(form_id, fresh)
    except Exception as e:
        return {"ok": False, "error": f"fetch source failed: {e}"}
    start = work / "00_source.pdf"
    shutil.copy(src, start)
    ctx = Ctx(form_id=form_id, pdf=start, work=work, case=case)
    ctx.log.append(f"source: fetched {form_id} from court ({'fresh' if fresh else 'cache ok'})")

    ran, skipped = [], []
    for sid in _order(step_ids):
        step = STEPS[sid]
        if not tool_available(step.needs_tool):
            skipped.append(sid)
            ctx.log.append(f"{sid}: SKIPPED — needs '{step.needs_tool}' (not installed)")
            continue
        try:
            step.apply(ctx)
            ran.append(sid)
        except Exception as e:
            return {"ok": False, "error": f"step '{sid}' failed: {e}",
                    "ran": ran, "log": ctx.log}
    return {"ok": True, "form_id": form_id, "out": str(ctx.pdf),
            "ran": ran, "skipped": skipped, "log": ctx.log}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", action="store_true")
    ap.add_argument("--form")
    ap.add_argument("--steps", help="comma-separated step ids")
    ap.add_argument("--preset", choices=list(PRESETS))
    ap.add_argument("--case", help="case JSON path (for fill)")
    ap.add_argument("--out")
    ap.add_argument("--fresh", action="store_true", help="re-download from court")
    a = ap.parse_args()

    if a.catalog:
        print(json.dumps(catalog(), indent=2)); return 0
    if not a.form or not (a.steps or a.preset):
        ap.error("need --form and (--steps or --preset), or --catalog")
    steps = PRESETS[a.preset] if a.preset else a.steps.split(",")
    case = json.loads(pathlib.Path(a.case).read_text()) if a.case else None
    res = run(a.form, steps, case=case, fresh=a.fresh)
    if not res["ok"]:
        print(json.dumps(res, indent=2), file=sys.stderr); return 1
    if a.out:
        shutil.copy(res["out"], a.out); res["out"] = a.out
    print(json.dumps({k: v for k, v in res.items() if k != "log"}, indent=2))
    for line in res["log"]:
        print("  ·", line, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
