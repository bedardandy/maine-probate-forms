#!/usr/bin/env python3
"""Tier 1 of the geometry review: local vision-LLM voting panel.

Every candidate (analytic ∪ OCR flags, one unit per form/field/widget) gets a
red-boxed crop and is judged by N independent local vision models on a flat
micro-schema (≤6 keys — the schema large local models actually honor):

  {"text_on_line": yes|no|na, "overlaps_print": yes|no,
   "horizontal": ok|too_left|too_right, "verdict": ok|minor|major, "note": ...}

Clean controls are voted too — the controls' false-positive rate calibrates
how much to trust a lone "major". Consensus:
  confirmed  ≥2 voters say major (or 2×minor on the same axis)
  disputed   exactly 1 voter says major, or voters split on the axis
  clean      otherwise

Voters come from $GEOM_VOTERS: "name=base_url|model;name2=..." (OpenAI-style
/chat/completions with image_url). Keep endpoint lists in the environment,
not in the repo.

    GEOM_VOTERS="local=http://localhost:8088/v1|Qwen3.6-27B-FP8" \
        python3 scripts/geometry_review/vl_vote.py --out ~/geom-review-out
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import re
import sys
from concurrent.futures import ThreadPoolExecutor

import requests

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SCHEMA = {
    "type": "object",
    "properties": {
        "text_on_line": {"type": "string", "enum": ["yes", "no", "na"]},
        "overlaps_print": {"type": "string", "enum": ["yes", "no"]},
        "horizontal": {"type": "string", "enum": ["ok", "too_left", "too_right"]},
        "verdict": {"type": "string", "enum": ["ok", "minor", "major"]},
        "note": {"type": "string", "maxLength": 80},
    },
    "required": ["text_on_line", "overlaps_print", "horizontal", "verdict"],
    "additionalProperties": False,
}

PROMPT = """\
You are auditing a filled Maine probate court form for typed-text placement.
The red box marks where the software believes the value belongs. The typed
value is the token {token} (or an X mark for a checkbox).

Judge ONLY placement quality inside/near the red box:
- text_on_line: does the typed value sit ON its blank line / in its box
  (baseline at or just above the line)? "na" if there is no line (paragraph).
- overlaps_print: does the typed value collide with PRINTED text/labels
  (touching an underscore line is fine)?
- horizontal: is it horizontally where the blank is? too_left = starts under
  the printed label; too_right = starts past the blank.
- verdict: ok / minor (slightly off but legible and unambiguous) /
  major (overlapping print, on the wrong line, or in the wrong blank).

Evidence from deterministic checks (may be wrong — judge from the image):
{evidence}

Respond with ONLY the JSON object, no prose."""


def parse_voters() -> list[dict]:
    spec = os.environ.get("GEOM_VOTERS", "")
    voters = []
    for part in spec.split(";"):
        part = part.strip()
        if not part:
            continue
        name, rest = part.split("=", 1)
        url, model = rest.split("|", 1)
        voters.append({"name": name, "url": url.rstrip("/"), "model": model})
    if not voters:
        sys.exit("set GEOM_VOTERS=name=base_url|model;… (no endpoints in-repo)")
    return voters


def ask(voter: dict, crop: pathlib.Path, token: str, evidence: str) -> dict:
    b64 = base64.b64encode(crop.read_bytes()).decode()
    body = {
        "model": voter["model"],
        "max_tokens": 300,
        "temperature": 0.0,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "verdict", "strict": True,
                                            "schema": SCHEMA}},
        "messages": [{"role": "user", "content": [
            {"type": "text",
             "text": PROMPT.format(token=token, evidence=evidence)},
            {"type": "image_url",
             "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ]}],
    }
    try:
        r = requests.post(voter["url"] + "/chat/completions", json=body,
                          timeout=240)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"].get("content") or ""
    except Exception as e:
        return {"error": str(e)[:120]}
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        return {"error": f"no json: {content[:80]}"}
    try:
        return json.loads(m.group(0).replace("“", '"').replace("”", '"'))
    except Exception:
        return {"error": f"bad json: {m.group(0)[:80]}"}


def load_units(out: pathlib.Path) -> list[dict]:
    units: dict[tuple, dict] = {}
    for line in (out / "candidates.jsonl").open():
        o = json.loads(line)
        k = (o["form"], o["field"], o.get("widget_idx", o.get("option")))
        u = units.setdefault(k, {"form": o["form"], "field": o["field"],
                                 "key": "|".join(map(str, k)),
                                 "kind": o.get("kind"), "page": o["page"],
                                 "rect": o["rect"], "token": o.get("token"),
                                 "crop": o.get("crop"), "evidence": {}})
        u["evidence"]["analytic"] = o["flags"]
    ocr_p = out / "ocr_results.jsonl"
    if ocr_p.exists():
        for line in ocr_p.open():
            o = json.loads(line)
            if o.get("ocr") in ("ok", "unreadable_small", None):
                continue
            k = (o["form"], o["field"], o.get("widget_idx"))
            u = units.setdefault(k, {"form": o["form"], "field": o["field"],
                                     "key": "|".join(map(str, k)),
                                     "kind": "text", "page": o["page"],
                                     "rect": o["rect"], "token": o.get("token"),
                                     "crop": None, "evidence": {}})
            u["evidence"]["ocr"] = {kk: o[kk] for kk in
                                    ("ocr", "dx_pt", "dy_pt", "line_text")
                                    if kk in o}
    return list(units.values())


def ensure_crop(u: dict, out: pathlib.Path) -> pathlib.Path | None:
    if u.get("crop") and pathlib.Path(u["crop"]).exists():
        return pathlib.Path(u["crop"])
    from scripts.geometry_review.sweep import crop as mkcrop
    pages = sorted((out / u["form"]).glob("page-*.png"))
    if u["page"] >= len(pages):
        return None
    cp = out / u["form"] / "crops" / f"{u['field']}__u{u['page']}_{abs(hash(u['key']))%997}.png"
    mkcrop(pages[u["page"]], u["rect"], cp)
    u["crop"] = str(cp)
    return cp


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path,
                    default=pathlib.Path.home() / "geom-review-out")
    ap.add_argument("--controls", action="store_true",
                    help="also vote the clean controls (calibration)")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--keys", type=pathlib.Path,
                    help="vote only units whose key is listed in this file")
    args = ap.parse_args()
    voters = parse_voters()

    units = load_units(args.out)
    if args.controls and (args.out / "controls.jsonl").exists():
        for line in (args.out / "controls.jsonl").open():
            o = json.loads(line)
            k = f"CTRL|{o['form']}|{o['field']}|{o.get('widget_idx', o.get('option'))}"
            units.append({"form": o["form"], "field": o["field"], "key": k,
                          "kind": o.get("kind"), "page": o["page"],
                          "rect": o["rect"], "token": o.get("token"),
                          "crop": o.get("crop"), "evidence": {"control": True}})
    if args.keys:
        want = set(args.keys.read_text().split())
        units = [u for u in units if u["key"] in want]
    if args.limit:
        units = units[: args.limit]

    votes_p = args.out / "votes.jsonl"
    seen = set()
    if votes_p.exists():
        for line in votes_p.open():
            o = json.loads(line)
            seen.add((o["key"], o["voter"]))
    votes_f = votes_p.open("a")

    def vote(u, v):
        if (u["key"], v["name"]) in seen:
            return None
        cp = ensure_crop(u, args.out)
        if cp is None:
            return None
        ev = json.dumps(u["evidence"])
        res = ask(v, cp, u.get("token") or "X", ev)
        return {"key": u["key"], "form": u["form"], "field": u["field"],
                "voter": v["name"], **res}

    # one worker per endpoint — each voter walks all units independently
    with ThreadPoolExecutor(max_workers=len(voters)) as ex:
        futs = []
        for v in voters:
            def run_voter(v=v):
                outl = []
                for u in units:
                    r = vote(u, v)
                    if r:
                        votes_f.write(json.dumps(r) + "\n")
                        votes_f.flush()
                        outl.append(r)
                return v["name"], len(outl)
            futs.append(ex.submit(run_voter))
        for f in futs:
            name, n = f.result()
            print(f"voter {name}: {n} new votes")

    # consensus
    by_key: dict[str, list] = {}
    for line in votes_p.open():
        o = json.loads(line)
        if "verdict" in o:
            by_key.setdefault(o["key"], []).append(o)
    cons_f = (args.out / "consensus.jsonl").open("w")
    counts = {"confirmed": 0, "disputed": 0, "clean": 0}
    for u in units:
        vs = by_key.get(u["key"], [])
        majors = [v for v in vs if v.get("verdict") == "major"]
        minors = [v for v in vs if v.get("verdict") == "minor"]
        if len(majors) >= 2:
            status = "confirmed"
        elif len(majors) == 1 or len(minors) >= 2:
            status = "disputed"
        else:
            status = "clean"
        counts[status] += 1
        cons_f.write(json.dumps({**{k: u[k] for k in
                                    ("key", "form", "field", "kind", "page",
                                     "rect", "token", "crop", "evidence")},
                                 "status": status,
                                 "votes": [{k: v.get(k) for k in
                                            ("voter", "verdict", "text_on_line",
                                             "overlaps_print", "horizontal",
                                             "note")} for v in vs]}) + "\n")
    print("consensus:", counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
