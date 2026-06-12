#!/usr/bin/env python3
"""Route a fact pattern to candidate Maine probate forms.

Keyword search over the per-form metadata (title + category) so an agent can go
from "informal probate of a will" to the right form package(s).

    python3 tools/find_forms.py "informal probate of a will"
    python3 tools/find_forms.py --json "appoint a guardian for a minor"

Routing only. Probate PDFs are flat and not shipped: fetch each form's
metadata.json.source_url, then fill it directly with `tools/fill_pdf.py`
(values inject onto the flat source via fill_geometry.json — no pipeline or VLM
at fill time). See docs/agent-workflow.md.
"""
from __future__ import annotations

import argparse
import glob
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
_STOP = {"the", "a", "an", "of", "for", "to", "and", "or", "with", "in", "on",
         "is", "are", "form", "forms", "maine", "probate", "court", "file",
         "client", "wants", "needs", "case"}


def _tok(t: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (t or "").lower())
            if w not in _STOP and len(w) > 2}


# A form id mentioned verbatim ("DE-101", "de 101", "pp108") must win outright:
# the keyword scorer drops short prefix tokens like "de" (len>2 filter), so
# without this short-circuit an exact id routes on "101" alone — poorly.
#
# Estate forms come in formal/informal pairs sharing a number: the bare id
# (DE-101) is the FORMAL petition; the "(I)" id (DE-101(I)) is the informal
# application — that suffix is printed on the form itself. A bare-id query
# pins the formal first but surfaces both; "(i)" or the word "informal"
# pins the informal variant.
_ID_RE = re.compile(
    r"\b([A-Za-z]{2,4})[-_ ]?(\d{1,3}[A-Za-z]?)(\s*\(\s*i\s*\))?", re.I)


def _exact_id_hits(query: str) -> list[str]:
    forms = {p.name.upper(): p.name
             for p in (ROOT / "repo" / "forms").iterdir()
             if (p / "metadata.json").exists()}
    q = query or ""
    # \b keeps "informal" from matching "formal" (n→f is not a boundary).
    informal_q = bool(re.search(r"\binformal(ly)?\b", q, re.I))
    formal_q = bool(re.search(r"\bformal(ly)?\b", q, re.I))
    out = []
    for m in _ID_RE.finditer(q):
        base = f"{m.group(1)}-{m.group(2)}".upper()
        if m.group(3) or (informal_q and not formal_q):
            variants = [f"{base}(I)", base]
        else:
            variants = [base, f"{base}(I)"]
        for v in variants:
            fid = forms.get(v)
            if fid and fid not in out:
                out.append(fid)
    return out


def _meta(form_id: str) -> dict:
    return json.loads(
        (ROOT / "repo" / "forms" / form_id / "metadata.json").read_text())


def find_forms(query: str, k: int = 8) -> dict:
    exact = _exact_id_hits(query)
    q = _tok(query)
    hits = []
    for mp in glob.glob(str(ROOT / "repo" / "forms" / "*" / "metadata.json")):
        m = json.loads(open(mp).read())
        if m.get("form_id") in exact:
            continue                          # already pinned to the top
        hay_t = _tok(m.get("title", "")); hay_c = _tok(m.get("category", ""))
        score = 2 * len(q & hay_t) + len(q & hay_c)
        if score:
            hits.append((score, m))
    hits.sort(key=lambda x: (-x[0], x[1]["form_id"]))
    ranked = [_meta(fid) for fid in exact] + [m for _, m in hits]
    return {"query": query, "forms": [
        {"form_id": m["form_id"], "title": m.get("title"),
         "category": m.get("category"), "source_url": m.get("source_url"),
         "n_fields": m.get("n_fields")}
        for m in ranked[:k]]}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query"); ap.add_argument("--k", type=int, default=8)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    res = find_forms(a.query, a.k)
    if a.json:
        print(json.dumps(res, indent=2)); return 0
    if not res["forms"]:
        print("No matches — browse catalog/source_urls.json."); return 0
    for f in res["forms"]:
        print(f"  {f['form_id']:14} {(f['category'] or ''):14} {(f['title'] or '')[:46]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
