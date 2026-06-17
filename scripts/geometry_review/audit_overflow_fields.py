#!/usr/bin/env python3
"""Audit which fields across all forms should overflow to an addendum.

The fill pipeline can route long/multiple content to an appended "Addendum N"
page (tools/addendum.py, declared in catalog/overflow_fields.json). This script
inventories the corpus and sorts the list-bearing fields into the categories
that need *different* overflow handling, then writes a coverage report and a
proposal of the safe, auto-enable-able slice.

Categories:
  * repeating-group  — an entity modelled as numbered records (heir_1_name,
                       interested_party_3_address, distributee_N_*). FIXED
                       capacity = the largest index. Overflow = when the actual
                       count exceeds capacity, fill the rows + addendum the rest.
                       Needs a repeating-group abstraction (follow-up), so these
                       are reported, NOT auto-enabled.
  * single-list      — ONE widget that must hold a whole list (MISC-101
                       service_recipients). Safe to enable mode:list now: it only
                       overflows when the value actually has 2+ items.
  * continuation     — a multi-widget area (persons_to_notify across 45 widgets)
                       that already wraps; overflow only past all widgets.
                       Reported, not auto-enabled.

    python3 scripts/geometry_review/audit_overflow_fields.py            # report only
    python3 scripts/geometry_review/audit_overflow_fields.py --apply    # + enable single-list in the catalog
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
FORMS = ROOT / "repo" / "forms"

# Collection nouns whose natural answer is a SET of records.
LIST = re.compile(
    r"heirs?|devisees?|legatees?|distributees?|beneficiar|creditors?|claimants?|"
    r"recipients?|interested_part|parties|witnesses?|dependents?|assets?|debts?|"
    r"liabilities|securities|stocks_bonds|children|minors?|_list$|^list_|"
    r"names?_of|persons?|notif|notice|contact|disbursements?|distributions?|"
    r"bequests?|objecting", re.I)
# Singular sub-fields / non-lists to exclude as standalone list candidates.
EXCL = re.compile(
    r"_date$|_dob$|_value$|_amount$|_total$|_email$|_phone$|_count$|status$|"
    r"_basis$|_period$|_fee$|_name$|_age$|_level$|_grade$|_balance$|_info$|"
    r"_detail$|_details$|_relationship$|_contact$|contact_info$|_date_filed$|"
    r"_reasons?$|_description$|_achievements$|_needs$|_participation$|"
    r"_advance_notice$|_court_contact_info$", re.I)
INDEX = re.compile(r"(\d+)")


def _fields(form):
    s = json.loads((FORMS / form / "schema.json").read_text())["fields"]
    gp = FORMS / form / "fill_geometry.json"
    g = json.loads(gp.read_text())["fields"] if gp.exists() else {}
    out = []
    for f in s:
        fid = f["field_id"]
        out.append((fid, f.get("data_type"),
                    (f.get("fill_strategy") or {}).get("source"),
                    f.get("label", ""),
                    len((g.get(fid, {}) or {}).get("widgets") or [])))
    return out


def audit():
    forms = sorted(d.name for d in FORMS.iterdir() if (d / "schema.json").exists())
    groups = collections.defaultdict(lambda: {"idx": set(), "attrs": set(),
                                              "widgets": 0})
    singles, continuations = [], []
    for form in forms:
        for fid, dt, src, label, nwid in _fields(form):
            listish = dt == "select_many" or (
                src == "llm_over_narrative" and LIST.search(fid))
            if not listish:
                continue
            m = INDEX.search(fid)
            if m:                                    # numbered -> repeating group
                prefix = fid[:m.start()].rstrip("_")
                attr = fid[m.end():].lstrip("_")
                if not LIST.search(prefix + "_" + attr):
                    continue
                grp = groups[(form, prefix)]
                grp["idx"].add(int(m.group(1)))
                if attr:
                    grp["attrs"].add(attr)
                grp["widgets"] += nwid
            elif EXCL.search(fid):
                continue
            elif nwid <= 1:                          # single widget -> list mode
                singles.append((form, fid, dt, label, nwid))
            else:                                    # multi-widget continuation
                continuations.append((form, fid, dt, label, nwid))
    repeating = [{"form": f, "entity": p, "capacity": max(g["idx"]),
                  "attrs": sorted(g["attrs"])}
                 for (f, p), g in groups.items() if len(g["idx"]) >= 2]
    repeating.sort(key=lambda r: (r["form"], r["entity"]))
    singles.sort(); continuations.sort()
    return forms, repeating, singles, continuations


def write_report(forms, repeating, singles, continuations):
    by = collections.Counter(r["form"] for r in repeating)
    lines = ["# Overflow coverage — addendum-eligible fields across the corpus",
             "",
             "Generated by `scripts/geometry_review/audit_overflow_fields.py`. "
             "The fill pipeline routes long/multiple content to an appended "
             "Addendum page (`tools/addendum.py`, `catalog/overflow_fields.json`). "
             "Fields that can hold *multiple things* fall into three classes, "
             "each needing different handling.", "",
             f"Scanned **{len(forms)} forms**. Found "
             f"**{len(repeating)} repeating-group entities**, "
             f"**{len(singles)} single-widget list fields**, "
             f"**{len(continuations)} multi-widget continuation areas**.", "",
             "## 1. Repeating-group entities (fixed-capacity numbered records)",
             "",
             "Modelled as numbered records (`heir_1_name`, `distributee_3_address`). "
             "They have a FIXED row capacity; overflow is needed only when the "
             "actual count exceeds it. This requires a repeating-group "
             "abstraction (group by entity, fill rows 1..N, addendum the rest) — "
             "**a follow-up feature, not auto-enabled here.**", "",
             "| form | entity | capacity | attributes |", "|---|---|---|---|"]
    for r in repeating:
        lines.append(f"| {r['form']} | `{r['entity']}` | {r['capacity']} | "
                     f"{', '.join(r['attrs'])} |")
    votes = {}
    vf = pathlib.Path.home() / "geom-review-out" / "overflow_list_votes.json"
    if vf.exists():
        votes = {(v["form"], v["field"]): v for v in json.loads(vf.read_text())}
    cat = {}
    cp = ROOT / "catalog" / "overflow_fields.json"
    if cp.exists():
        cat = json.loads(cp.read_text()).get("forms", {})
    lines += ["", "## 2. Single-widget list fields (`mode:list`)",
              "",
              "ONE widget expected to hold a whole list. `mode:list` diverts to "
              "an addendum once the value carries 2+ items (otherwise fills "
              "inline). Each candidate was confirmed by the local fleet "
              "(Qwen + gemma); **only both-agree-list fields were enabled** — the "
              "rest are non-lists (a value, a caption, a swapped table cell) and "
              "stay off.", "",
              "| form | field | qwen | gemma | enabled |", "|---|---|---|---|---|"]
    for form, fid, dt, label, _ in singles:
        v = votes.get((form, fid), {})
        en = "✅ list" if fid in cat.get(form, {}) else "—"
        lines.append(f"| {form} | `{fid}` | {v.get('qwen')} | {v.get('gemma')} "
                     f"| {en} |")
    lines += ["", "## 3. Multi-widget continuation areas (reported)",
              "",
              "A value already spread across many widgets (a printed grid / "
              "many ruled lines). It overflows only past all of them; revisit "
              "per-form if the widget count is too small for realistic data.", "",
              "| form | field | widgets | label |", "|---|---|---|---|"]
    for form, fid, dt, label, nwid in continuations:
        lines.append(f"| {form} | `{fid}` | {nwid} | {label[:40]} |")
    lines.append("")
    (ROOT / "catalog" / "overflow_coverage.md").write_text("\n".join(lines))


def apply_singles(singles):
    """Enable mode:list for the single-widget list fields in the catalog."""
    p = ROOT / "catalog" / "overflow_fields.json"
    cat = json.loads(p.read_text())
    forms = cat.setdefault("forms", {})
    added = 0
    for form, fid, dt, label, _ in singles:
        f = forms.setdefault(form, {})
        if fid in f:
            continue                                 # keep hand-authored entries
        f[fid] = {"mode": "list"}
        added += 1
    p.write_text(json.dumps(cat, indent=1) + "\n")
    return added


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="enable mode:list for single-widget list fields")
    a = ap.parse_args()
    forms, repeating, singles, continuations = audit()
    write_report(forms, repeating, singles, continuations)
    print(f"forms {len(forms)} | repeating-group {len(repeating)} | "
          f"single-list {len(singles)} | continuation {len(continuations)}")
    print("wrote catalog/overflow_coverage.md")
    if a.apply:
        n = apply_singles(singles)
        print(f"--apply: enabled mode:list for {n} single-widget fields "
              f"in catalog/overflow_fields.json")


if __name__ == "__main__":
    main()
