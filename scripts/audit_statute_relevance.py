#!/usr/bin/env python3
"""Adjudicate the per-question statute considerations against the real statute text.

verify_statutes.py checks the layer is *structurally* sound (cites resolve, field
ids exist). It cannot judge *relevance*: whether the §X tied to a given form
question actually bears on it, or is over-broad / mis-tied. This does, with a
grounded Opus pass — it fetches the actual statute text (the local index carries
only titles) and asks, per (field, cite), for a verdict.

Per unit (form, field_id, field label, cite, note, statute text) -> Opus returns
{verdict: relevant | tangential | mis_tied, confidence, reason}. Only non-relevant
verdicts are worth a human glance; `relevant` confirms the authoring.

Headless Claude via OAuth (strips ANTHROPIC_API_KEY). Statute fetches are cached.
Scope with --form / --limit; this calls Opus per unit (188 units across 79 forms).

    python3 scripts/audit_statute_relevance.py --form DE-101 --limit 5   # sample
    python3 scripts/audit_statute_relevance.py                            # full
"""
from __future__ import annotations
import argparse, csv, json, os, pathlib, re, subprocess, sys, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
FORMS = ROOT / "repo" / "forms"
CACHE = pathlib.Path("/tmp/statute_text_cache")
TIMEOUT = 240

SYSTEM = """You are a Maine probate-law editor auditing a "statutes for
consideration" layer (NOT legal advice). For one form FIELD/QUESTION you are given
a cited statute section and its actual text. Judge ONLY whether the citation is a
reasonable thing to consider when answering that field — not whether it is the
single best cite.

verdict:
  relevant   - the section plausibly bears on this field/question.
  tangential - related to the form's subject but not to THIS field; over-broad.
  mis_tied   - the section is about something else; wrong citation.

Return STRICTLY JSON: {"verdict":"relevant|tangential|mis_tied",
"confidence":0.0-1.0,"reason":"<one short sentence>"}
"""


def _labels(form: str) -> dict[str, str]:
    p = FORMS / form / "fields.csv"
    if not p.exists():
        return {}
    return {r["field_id"]: r.get("label", "") for r in csv.DictReader(p.open())}


def _statute_text(url: str) -> str:
    CACHE.mkdir(parents=True, exist_ok=True)
    key = CACHE / (re.sub(r"\W+", "_", url)[-80:] + ".txt")
    if key.exists():
        return key.read_text()
    try:
        html = urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}),
            timeout=20).read().decode("utf-8", "ignore")
    except Exception:
        return ""
    h = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h))
    m = list(re.finditer(r"§\d+-\d+\.\s", t))      # body starts at the last heading repeat
    body = t[m[-1].start():m[-1].start() + 1800] if m else t[:1800]
    key.write_text(body)
    return body


def _adjudicate(field_label: str, cite: str, title: str, note: str,
                text: str, model: str) -> dict | None:
    user = (f"FIELD: {field_label or '(unlabeled)'}\nCITE: {cite} — {title}\n"
            f"AUTHOR NOTE: {note}\n\nSTATUTE TEXT:\n{text or '(unavailable)'}\n\n"
            "Return the JSON verdict.")
    env = {**os.environ}; env.pop("ANTHROPIC_API_KEY", None)
    try:
        proc = subprocess.run(
            ["claude", "-p", user, "--model", model, "--output-format", "json",
             "--dangerously-skip-permissions", "--append-system-prompt", SYSTEM],
            capture_output=True, text=True, timeout=TIMEOUT, env=env)
        outer = json.loads(proc.stdout)
        if outer.get("is_error"):
            return None
        m = re.search(r"\{[\s\S]*\}", outer.get("result", ""))
        return json.loads(m.group(0)) if m else None
    except Exception:
        return None


def collect(form: str | None) -> list[dict]:
    units = []
    forms = [form] if form else sorted(d.name for d in FORMS.iterdir() if d.is_dir())
    for f in forms:
        sp = FORMS / f / "statutes.json"
        if not sp.exists():
            continue
        labels = _labels(f)
        for q in json.loads(sp.read_text()).get("per_question", []):
            fid = q.get("field_id", "")
            for c in q.get("considerations", []):
                units.append({"form": f, "field_id": fid,
                              "label": labels.get(fid, fid),
                              "cite": c.get("cite", ""), "title": c.get("title", ""),
                              "note": c.get("note", ""), "url": c.get("url", "")})
    return units


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--form")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--model", default="opus")
    ap.add_argument("--out-tsv", type=pathlib.Path,
                    default=ROOT / "router" / "statute_relevance_audit.tsv")
    args = ap.parse_args()

    units = collect(args.form)
    if args.limit:
        units = units[: args.limit]
    print(f"adjudicating {len(units)} consideration(s)"
          + (f" for {args.form}" if args.form else " across all forms"))

    cols = ["form", "field_id", "label", "cite", "title", "verdict", "confidence",
            "reason", "note"]
    args.out_tsv.parent.mkdir(parents=True, exist_ok=True)
    # Line-buffered append so a long run survives interruption with partial data.
    fh = open(args.out_tsv, "w", buffering=1)
    fh.write("\t".join(cols) + "\n")
    rows, flagged = [], []
    for i, u in enumerate(units, 1):
        v = _adjudicate(u["label"], u["cite"], u["title"], u["note"],
                        _statute_text(u["url"]), args.model)
        verdict = (v or {}).get("verdict", "error")
        rec = {**u, **(v or {}), "verdict": verdict}
        rows.append(rec)
        fh.write("\t".join(str(rec.get(c, "")).replace("\t", " ") for c in cols) + "\n")
        mark = "" if verdict == "relevant" else "  <-- review"
        if verdict not in ("relevant", "error"):
            flagged.append(rec)
        print(f"  [{i}/{len(units)}] {u['form']}/{u['field_id']} -> {u['cite']}: "
              f"{verdict} ({(v or {}).get('confidence','?')}){mark}", flush=True)
    fh.close()
    print(f"\n{len(rows)} adjudicated; {len(flagged)} flagged (tangential/mis_tied). "
          f"Wrote {args.out_tsv}")
    for r in flagged:
        print(f"  {r['form']}/{r['field_id']} {r['cite']} [{r['verdict']}]: {r.get('reason','')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
