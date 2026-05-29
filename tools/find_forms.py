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


def find_forms(query: str, k: int = 8) -> dict:
    q = _tok(query)
    hits = []
    for mp in glob.glob(str(ROOT / "repo" / "forms" / "*" / "metadata.json")):
        m = json.loads(open(mp).read())
        hay_t = _tok(m.get("title", "")); hay_c = _tok(m.get("category", ""))
        score = 2 * len(q & hay_t) + len(q & hay_c)
        if score:
            hits.append((score, m))
    hits.sort(key=lambda x: (-x[0], x[1]["form_id"]))
    return {"query": query, "forms": [
        {"form_id": m["form_id"], "title": m.get("title"),
         "category": m.get("category"), "source_url": m.get("source_url"),
         "n_fields": m.get("n_fields")}
        for _, m in hits[:k]]}


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
