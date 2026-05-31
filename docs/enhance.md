# Enhancement pipeline & control panel

Compose PDF enhancements (download from court → form fields → fill → accessibility
→ debug/flatten) from one place: a CLI, an HTTP API, and a one-page web panel.
No court PDFs are shipped — the blank form is fetched from `metadata.json.source_url`
at run time. **Outputs are drafts — not legal advice.**

## Architecture (modular, not sprawling)
- `tools/enhance.py` — a registry of **steps** (each a small `Step` wrapping an
  existing function/CLI), **presets** that bundle steps into levels, and a
  dependency-ordered **runner** that threads one PDF through the selected steps.
- `tools/api_server.py` — adds `GET /` (panel), `GET /enhance/catalog`, `POST /enhance`.
- `tools/static/index.html` — the control panel; it renders from `/enhance/catalog`,
  so adding a step needs **no UI/server change**.

### Steps
`embed_fonts` (gs) · `formfields` · `fill` (needs case) · `remediate_doc`
(title/lang/bookmarks/links) · `tag` (field /TU + tag tree + PDF/UA; OpenDataLoader)
· `verify_ua` (veraPDF) · `fieldmap` (debug overlay) · `flatten`.
Steps whose external tool is missing **skip with a warning**.

### Presets
`fillable`, `filled`, `accessible-basic`, `accessible-standard`, `accessible-full`,
`fieldmap`.

## CLI
```bash
python3 tools/enhance.py --catalog                       # steps + presets + tool availability
python3 tools/enhance.py --form DE-101 --preset fillable --out out.pdf
python3 tools/enhance.py --form DE-101 --preset filled --case case.json --out out.pdf
python3 tools/enhance.py --form DE-104 --steps embed_fonts,formfields,tag --out out.pdf --fresh
```

## Web panel
```bash
pip install fastapi uvicorn                              # already used by api_server
python3 -m uvicorn tools.api_server:app --host 127.0.0.1 --port 8077
# open http://127.0.0.1:8077/
```
To reach it from another device on your tailnet, bind to the tailnet IP
(`--host <your-tailscale-ip>`) or put it behind `tailscale serve`. The panel runs
only **registered** steps on a known form id (enum-gated — no arbitrary commands).

## Adding a step (the whole change)
Append one entry to `CATALOG` in `tools/enhance.py`:
```python
Step("my_step", "My step", "Group", "What it does.", my_fn, requires=("formfields",), needs_tool=None)
```
`my_fn(ctx)` reads `ctx.pdf`, writes a new PDF, sets `ctx.pdf`, appends to `ctx.log`.
Optionally name it in a preset. The panel picks it up automatically.
