#!/usr/bin/env python3
"""Vision audit of FILLED forms (OSS repo) with an adversarial verify gate.

JSON validation checks values + enums; it cannot see whether typed text landed
inside the underline, overlapped a printed glyph, fell into the wrong column, or
clipped the margin. This audits HOW values sit on the page — and, crucially,
gates every finding through an independent skeptic pass to kill the ~45%
single-shot false-positive rate.

Pipeline (per form, synthetic data only — never real client data):
  1. Fetch the blank form from metadata.json.source_url (cached).
  2. Fill it with the form's examples/case.example.json via tools.fill_pdf.
  3. Render each page with poppler (pdftoppm) — the renderer that faithfully
     regenerates field appearances honoring NeedAppearances + /Q justification.
  4. FIND pass: one vision call per page lists candidate rendering defects.
  5. VERIFY gate: for each candidate, N independent skeptic calls re-examine the
     SAME page, told the specific claim and instructed to default to "not a
     defect" unless it is unmistakable. A finding survives only with majority
     confirmation. This is the accuracy mechanism, not a formality.
  6. Write router/vision_audit_oss.tsv (confirmed findings + dropped count).

Headless Claude: `claude -p` with ANTHROPIC_API_KEY stripped so OAuth (Max
subscription) auth is used (a stale inherited dev key 401s). Running this calls
Opus and consumes subscription capacity — scope with --form / --limit.

    python3 scripts/vision_audit_oss.py --form DE-101            # one form
    python3 scripts/vision_audit_oss.py                          # all w/ examples
    python3 scripts/vision_audit_oss.py --form DE-101 --pages 1  # smoke: page 1 only
"""
from __future__ import annotations
import argparse, json, os, pathlib, re, subprocess, sys, tempfile, time, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import fill_pdf  # noqa: E402

DPI = 200
PER_CALL_TIMEOUT = 300

FIND_PROMPT = """You audit a RENDERED page of a FILLED Maine probate court PDF.

The form was stamped with values via AcroForm. Look for rendering defects — how
the typed values sit on the page — NOT whether the values are correct. A
wrong-but-readable value is fine.

INTENTIONAL, NOT defects:
  - Currency / numeric values flush RIGHT in their box (they line up in columns).
  - A caption (the estate/party name line under the court header) CENTERED.
  - Typed digits sitting just to the RIGHT of a printed "$" with a small gap —
    currency widgets are designed this way. Only an actual pixel OVERLAP is a defect.
  - Blank wet-ink signature / "Dated:" lines next to a signature, court/judge
    "ORDER"/"DECREE" fields, "(if applicable)"/"(if any)" fields, footer codes,
    and trailing blank rows in multi-row list tables (rows 2+ when row 1 is filled).

Defect kinds: overlaps_glyph, above_underline, below_underline, truncated,
wrong_column, illegible, blank_required (only if a clearly-required, non-excluded
field is empty).

Return STRICTLY JSON, no prose, no fences:
{"page_ok": <bool>, "issues": [{"label":"<visible label>","value":"<what shows>",
"kind":"<kind>","severity":"minor|major","evidence":"<where on the page>"}],
"overall":"clean|minor|major"}
"""

VERIFY_PROMPT = """You are a SKEPTICAL reviewer re-checking a single claimed defect
on a rendered, filled Maine probate form page. Another reviewer flagged it; most
such flags are false alarms.

Claim: field labeled "{label}" (shows "{value}") has defect "{kind}" — {evidence}

Look at the page yourself. Default to NOT a defect unless it is unmistakable.
Remember these are INTENTIONAL and must be rejected as defects:
  - currency/number values flush right; captions centered; digits just right of a
    printed "$"; blank signature/Dated/court-order/"(if applicable)" fields;
    trailing blank table rows.

Return STRICTLY JSON: {"confirmed": <bool>, "reason": "<one short sentence>"}
"""

_JSON_RE = re.compile(r"\{[\s\S]*\}")


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    m = _JSON_RE.search(text)
    try:
        return json.loads(m.group(0)) if m else None
    except Exception:
        return None


def _claude_vision(png: pathlib.Path, system: str, user: str,
                   model: str) -> dict | None:
    cmd = ["claude", "-p", user, "--model", model, "--output-format", "json",
           "--allowedTools", "Read", "--dangerously-skip-permissions",
           "--append-system-prompt", system, "--add-dir", str(png.parent)]
    env = {**os.environ}
    env.pop("ANTHROPIC_API_KEY", None)   # force OAuth (Max) auth
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=PER_CALL_TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        return None
    try:
        outer = json.loads(proc.stdout)
    except Exception:
        return None
    if outer.get("is_error"):
        return None
    return _extract_json(outer.get("result", ""))


def _render(pdf: pathlib.Path, out_dir: pathlib.Path, dpi: int) -> list[pathlib.Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = out_dir / "page"
    subprocess.run(["pdftoppm", "-png", "-r", str(dpi), str(pdf), str(prefix)],
                   check=True, capture_output=True)
    return sorted(out_dir.glob("page*.png"))


def _fetch_source(form_id: str, tmp: pathlib.Path) -> pathlib.Path | None:
    meta = json.loads((ROOT / "repo" / "forms" / form_id / "metadata.json").read_text())
    url = meta.get("source_url")
    if not url:
        return None
    dst = tmp / "source.pdf"
    rq = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    dst.write_bytes(urllib.request.urlopen(rq, timeout=30).read())
    return dst


def audit_form(form_id: str, votes: int, model: str, dpi: int,
               max_pages: int | None, verify: bool) -> list[dict]:
    case_path = ROOT / "repo" / "forms" / form_id / "examples" / "case.example.json"
    if not case_path.exists():
        print(f"  ! {form_id}: no synthetic example — skipping"); return []
    tmp = pathlib.Path(tempfile.mkdtemp(prefix=f"va_{form_id}_"))
    src = _fetch_source(form_id, tmp)
    if not src:
        print(f"  ! {form_id}: no source_url"); return []
    filled = tmp / "filled.pdf"
    res = fill_pdf.fill_pdf(form_id, json.loads(case_path.read_text()), src, filled, root=ROOT)
    if not res.get("ok"):
        print(f"  ! {form_id}: fill failed: {res.get('error')}"); return []
    pages = _render(filled, tmp, dpi)
    if max_pages:
        pages = pages[:max_pages]
    findings = []
    for pi, png in enumerate(pages, 1):
        v = _claude_vision(png, FIND_PROMPT,
                           f"Read the image at {png} and audit it. JSON only.", model)
        issues = (v or {}).get("issues", []) if v else []
        print(f"  {form_id} p{pi}: {len(issues)} candidate(s)"
              + ("" if v else " [find-call failed]"))
        for iss in issues:
            rec = {"form_id": form_id, "page": pi, **iss}
            if not verify:
                rec["confirmed"] = None; findings.append(rec); continue
            yes = 0
            for _ in range(votes):
                vr = _claude_vision(
                    png, "You are a skeptical defect verifier. JSON only.",
                    VERIFY_PROMPT.format(label=iss.get("label", ""),
                                         value=iss.get("value", ""),
                                         kind=iss.get("kind", ""),
                                         evidence=iss.get("evidence", "")), model)
                if vr and vr.get("confirmed") is True:
                    yes += 1
            rec["confirmed"] = yes > votes // 2
            rec["votes"] = f"{yes}/{votes}"
            findings.append(rec)
            print(f"     - {iss.get('kind')} @ {iss.get('label')!r}: "
                  f"{'CONFIRMED' if rec['confirmed'] else 'dropped'} ({rec.get('votes')})")
    return findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--form", help="single form_id (default: all with examples)")
    ap.add_argument("--votes", type=int, default=2, help="skeptic calls per finding")
    ap.add_argument("--model", default="opus")
    ap.add_argument("--dpi", type=int, default=DPI)
    ap.add_argument("--pages", type=int, default=None, help="cap pages per form (smoke test)")
    ap.add_argument("--no-verify", action="store_true", help="skip the verify gate")
    ap.add_argument("--out-tsv", type=pathlib.Path,
                    default=ROOT / "router" / "vision_audit_oss.tsv")
    args = ap.parse_args()

    if args.form:
        forms = [args.form]
    else:
        forms = sorted(d.name for d in (ROOT / "repo" / "forms").iterdir()
                       if (d / "examples" / "case.example.json").exists())
    print(f"auditing {len(forms)} form(s): {', '.join(forms)}")
    t0 = time.time()
    all_findings = []
    for f in forms:
        all_findings += audit_form(f, args.votes, args.model, args.dpi,
                                   args.pages, not args.no_verify)

    confirmed = [f for f in all_findings if f.get("confirmed")]
    dropped = [f for f in all_findings if f.get("confirmed") is False]
    args.out_tsv.parent.mkdir(parents=True, exist_ok=True)
    cols = ["form_id", "page", "label", "value", "kind", "severity", "confirmed",
            "votes", "evidence"]
    with open(args.out_tsv, "w") as fh:
        fh.write("\t".join(cols) + "\n")
        for r in all_findings:
            fh.write("\t".join(str(r.get(c, "")) for c in cols) + "\n")
    print(f"\n{len(all_findings)} candidate(s) -> {len(confirmed)} CONFIRMED, "
          f"{len(dropped)} dropped by verify gate. Wrote {args.out_tsv}")
    print(f"wall time: {(time.time()-t0)/60:.1f} min")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
