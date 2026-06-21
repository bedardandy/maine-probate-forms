#!/usr/bin/env python3
"""Deterministic citation scanner — the safety net for the citation inspector.

The forced ``[[REF: cite]]`` placeholder protocol guarantees correctness only for
the citations a model chose to wrap. A model can still drop a bare ``see 18-C
§3-203`` or ``In re Estate of Kruzynski`` into prose, outside the protocol. This
module scans free text for citation-shaped spans and resolves each against the
*closed* Maine index (``docs/statute-reference/_index/`` via
``maine_citation_db``). No training set, no NLP model, no network: the Maine
statute/case surface forms are regular and the vocabulary is finite, so a handful
of regex families + an index lookup give full recall with auditable precision.

For each hit it reports two independent axes:
  * ``resolves``       — does the normalized cite exist in the trusted index?
  * ``in_placeholder`` — does the span sit inside a ``[[REF: ...]]`` token?
                         (a citation-shaped span with ``in_placeholder=False`` in
                         a draft that uses placeholders is *leaked* — written
                         outside the protocol.)
With a ``form_id`` it also reports ``in_vocab`` (is the cite in that form's
allowed vocabulary). Not legal advice.

    python3 tools/citation_scan.py --form DE-101 --draft draft.txt
    echo "see 18-C §3-203 and 18-C §9-999" | python3 tools/citation_scan.py
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import legal_inspector                     # noqa: E402  (PLACEHOLDER)
from maine_citation_db import _index, resolves, build_vocab   # noqa: E402


# --- citation surface forms (Maine) ---------------------------------------- #
# 18-C section cite: "18-C §3-401", "18-C M.R.S.A. § 3-401(2)", "18-C section 3-401"
_RE_18C = re.compile(
    r"\b18-C\b\s*(?:M\.?\s?R\.?\s?S\.?(?:A\.?)?)?[,\s]*"
    r"(?:§|sec(?:tion|\.)?)\s*(\d+-\d+)(?:\([0-9A-Za-z]+\))?",
    re.IGNORECASE)
# Other Maine titles (cross-refs): "36 M.R.S. §4107", "19-A M.R.S."
_RE_MRS = re.compile(
    r"\b(\d+(?:-[A-Z])?)\s*M\.?\s?R\.?\s?S\.?(?:A\.?)?\s*"
    r"(?:§\s*(\d+(?:-[A-Z])?)(?:\([0-9A-Za-z]+\))?)?")
# Maine neutral case cite: "2000 ME 17"
_RE_ME = re.compile(r"\b(\d{4})\s+ME\s+(\d+)\b")
# Atlantic reporter: "457 A.2d 1123", "12 A.3d 34"
_RE_ATL = re.compile(r"\b(\d+)\s+A\.?\s?([23])d\s+(\d+)\b")
# Bare section symbol (assumed Title 18-C in this domain): "§3-401"
_RE_BARE = re.compile(r"§\s*(\d+-\d+)(?:\([0-9A-Za-z]+\))?")


def _name_variants(name: str):
    variants = {name}
    m = re.match(r"(?i)in re\s+", name)
    if m:
        variants.add(name[m.end():])
    return variants


def scan(text: str, *, form_id: str | None = None) -> list[dict]:
    """Citation-shaped spans in ``text``, resolved against the closed index.

    Returns hits ``{raw, cite, kind, span, resolves, in_placeholder[, in_vocab]}``
    in document order, with overlapping matches from different patterns deduped.
    """
    text = text or ""
    sec, xref, cases = _index()
    case_cites = {c["cite"] for c in cases.values()}
    vocab = set(build_vocab(form_id)) if form_id else None

    ph_spans = [(m.start(), m.end())
                for m in legal_inspector.PLACEHOLDER.finditer(text)]

    hits: list[dict] = []
    taken: list[tuple[int, int]] = []

    def _add(start, end, raw, cite, kind, ok):
        if any(not (end <= s or start >= e) for s, e in taken):
            return                          # overlaps an already-recorded hit
        taken.append((start, end))
        rec = {"raw": raw.strip(), "cite": cite, "kind": kind,
               "span": [start, end], "resolves": ok,
               "in_placeholder": any(s <= start and end <= e for s, e in ph_spans)}
        if vocab is not None:
            rec["in_vocab"] = cite in vocab
        hits.append(rec)

    for m in _RE_18C.finditer(text):
        cite = f"18-C §{m.group(1)}"
        _add(m.start(), m.end(), m.group(0), cite, "statute", resolves(cite, sec, xref))
    for m in _RE_ME.finditer(text):
        cite = f"{m.group(1)} ME {m.group(2)}"
        _add(m.start(), m.end(), m.group(0), cite, "case", cite in case_cites)
    for m in _RE_ATL.finditer(text):
        cite = f"{m.group(1)} A.{m.group(2)}d {m.group(3)}"
        _add(m.start(), m.end(), m.group(0), cite, "case", cite in case_cites)
    for m in _RE_MRS.finditer(text):
        title = m.group(1)
        if title == "18-C":
            continue                        # handled by _RE_18C
        cite = f"{title} M.R.S. §{m.group(2)}" if m.group(2) else f"{title} M.R.S."
        _add(m.start(), m.end(), m.group(0), cite, "crossref", cite in xref)
    for m in _RE_BARE.finditer(text):
        cite = f"18-C §{m.group(1)}"        # bare § assumed 18-C in this domain
        _add(m.start(), m.end(), m.group(0), cite, "statute", resolves(cite, sec, xref))
    for case in cases.values():
        if not case.get("name"):
            continue
        for variant in _name_variants(case["name"]):
            for m in re.finditer(re.escape(variant), text, re.IGNORECASE):
                _add(m.start(), m.end(), m.group(0), case["cite"], "case",
                     case["cite"] in case_cites)

    hits.sort(key=lambda h: h["span"][0])
    return hits


def report(text: str, *, form_id: str | None = None) -> dict:
    """Scan + bucket: ``leaked`` (citation-shaped, outside any placeholder),
    ``unresolvable`` (does not resolve to the index), ``out_of_vocab`` (resolves
    but not in this form's vocabulary). ``leaked`` is only meaningful when the
    text actually uses ``[[REF:]]`` placeholders."""
    text = text or ""
    hits = scan(text, form_id=form_id)
    uses_protocol = bool(legal_inspector.PLACEHOLDER.search(text))
    leaked = sorted({h["cite"] for h in hits
                     if uses_protocol and not h["in_placeholder"]})
    unresolvable = sorted({h["cite"] for h in hits if not h["resolves"]})
    out = {"hits": hits, "uses_protocol": uses_protocol,
           "leaked": leaked, "unresolvable": unresolvable}
    if form_id:
        out["out_of_vocab"] = sorted({h["cite"] for h in hits
                                      if h["resolves"] and not h.get("in_vocab")})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--form", help="scope vocabulary to a form id, e.g. DE-101")
    ap.add_argument("--draft", help="path to text to scan (default: stdin)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    text = pathlib.Path(a.draft).read_text(encoding="utf-8") if a.draft else sys.stdin.read()
    rep = report(text, form_id=a.form)
    if a.json:
        print(json.dumps(rep, indent=2, ensure_ascii=False))
    else:
        print(f"scanned {len(rep['hits'])} citation(s); "
              f"leaked={len(rep['leaked'])} unresolvable={len(rep['unresolvable'])}"
              + (f" out_of_vocab={len(rep.get('out_of_vocab', []))}" if a.form else ""))
        for h in rep["hits"]:
            tags = []
            if not h["resolves"]:
                tags.append("UNRESOLVABLE")
            if rep["uses_protocol"] and not h["in_placeholder"]:
                tags.append("LEAKED")
            if a.form and h["resolves"] and not h.get("in_vocab"):
                tags.append("OUT-OF-VOCAB")
            flag = ("  <- " + ", ".join(tags)) if tags else ""
            print(f"  [{h['kind']}] {h['raw']!r} -> {h['cite']}{flag}")
    return 1 if (rep["leaked"] or rep["unresolvable"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
