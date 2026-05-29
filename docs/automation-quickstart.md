# Automation quickstart

Fill one published form package end to end. The packages live in
`repo/forms/<FORM_ID>/` and contain `schema.json`, `fields.csv`,
`fill_geometry.json`, and `metadata.json`. The PDFs are **not** shipped;
`metadata.json.source_url` points to the official flat PDF on maineprobate.net.

You do **not** need the detection pipeline or a VLM to fill a form. `schema.json`
+ `fill_geometry.json` are the contract, and `tools/fill_pdf.py` injects values
straight onto the fetched flat source. (The pipeline in `modules/` is only for
*regenerating* field geometry — see `docs/maintenance.md`.)

## 1. Install

```bash
pip install -r requirements.txt
```

## 2. Inspect a package

```bash
ls repo/forms/DE-101/          # schema.json  fields.csv  fill_geometry.json  metadata.json
python3 - <<'PY'
import json
m = json.load(open("repo/forms/DE-101/metadata.json"))
s = json.load(open("repo/forms/DE-101/schema.json"))
print(m["title"], "—", m["category"], "—", s["n_fields"], "fields")
print("source PDF:", m["source_url"])
PY
```

`catalog/source_urls.json` is the consolidated form → source-PDF map.

## 3. Fetch the blank source PDF (public record, not redistributed here)

```bash
python3 - <<'PY'
import json, urllib.request
m = json.load(open("repo/forms/DE-101/metadata.json"))
urllib.request.urlretrieve(m["source_url"], "DE-101.source.pdf")  # flat — no form fields
PY
```

## 4. Plan the fill

Build a canonical case object (see `docs/agent-workflow.md` for the shape; a
worked one ships at `repo/forms/DE-101/examples/case.example.json`) and resolve
it against the schema:

```bash
python3 tools/fill_plan.py --form DE-101 --case repo/forms/DE-101/examples/case.example.json
# DE-101: 83 fields — resolved 18, narrative 45 (agent fills), recompute 0, blank 16, unresolved 0, skipped 4
```

The plan buckets every field: `resolved` (filled from the case), `narrative`
(an agent composes from the fact pattern), `recompute` (derived), `blank`
(signatures / human decisions), `unresolved` (missing facts to collect), and
`skipped` (gated off by a `when` condition — e.g. a write-in that doesn't apply
because the controlling choice isn't set to it).

## 5. Write the filled PDF

```bash
python3 tools/fill_pdf.py --form DE-101 \
    --case repo/forms/DE-101/examples/case.example.json \
    --source DE-101.source.pdf --out DE-101.filled.pdf
```

`fill_pdf.py` reads `fill_geometry.json` (`field_id → widget rects`) and writes
the resolved text and checked options directly onto the flat source — no
pipeline, no VLM. Narrative fields you compose go back under
`narrative_facts[field_id]`; re-run and they fold into `resolved`.

## 6. Route by risk and review

`schema.json` tags each field with a category and risk tier, so an automation
layer can fill low-risk constants deterministically, route ambiguous values to a
model-assisted or human step, and keep high-risk legal decisions reviewable.
`fields.csv` is the human-readable inventory for that review. Output is a draft;
verify against the official form before filing.
