#!/usr/bin/env python3
"""HTTP API over the Maine probate forms library — for plugging your own system in.

Wraps the existing (VLM-free) library functions so an external system (a backend,
a templating tool like PandaDoc, a CI golden-test) can route, plan, and fill
forms over HTTP instead of via MCP/agent. Endpoints:

    GET  /healthz                      -> {ok: true}
    GET  /forms                        -> [{form_id, title, category, source_url}]
    GET  /forms/{form_id}              -> metadata + field buckets (empty case)
    POST /route   {fact}               -> {form_id, alternates, confidence}   # LLM
    POST /plan    {form_id, case}      -> fill plan buckets                   # DETERMINISTIC
    POST /fill    {form_id, case,      -> filled PDF (application/pdf)        # DETERMINISTIC
                   source_url?}

`/plan` is the determinism / templating surface: it is a pure function of the
case object (no LLM, no network) — POST the same case repeatedly and the
`resolved` field map is byte-identical every time, so it can be golden-tested or
mapped to PandaDoc tokens. `/route` is the only LLM-backed endpoint (configure it
with the ROUTER_* env vars from tools/route_form.py). Not legal advice.

Run:  pip install fastapi uvicorn   # not in requirements.txt by default
      python3 tools/api_server.py            # serves on 0.0.0.0:8077
      uvicorn tools.api_server:app --port 8077
"""
from __future__ import annotations

import glob
import json
import pathlib
import tempfile
import urllib.request

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

import sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from canonical_adapter import to_case_object       # noqa: E402
from fill_plan import build_plan                    # noqa: E402
from fill_pdf import fill_pdf as _fill_pdf          # noqa: E402
import route_form                                   # noqa: E402
import enhance                                      # noqa: E402

STATIC = ROOT / "tools" / "static"

app = FastAPI(title="Maine Probate Forms API", version="1.0",
              description="Route / plan / fill Maine probate forms. Not legal advice.")


def _meta(form_id: str) -> dict:
    p = ROOT / "repo" / "forms" / form_id / "metadata.json"
    if not p.exists():
        raise HTTPException(404, f"unknown form {form_id!r}")
    return json.loads(p.read_text())


class RouteReq(BaseModel):
    fact: str


class PlanReq(BaseModel):
    form_id: str
    case: dict


class FillReq(BaseModel):
    form_id: str
    case: dict
    source_url: str | None = None   # override; defaults to metadata.json.source_url


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/forms")
def forms():
    out = []
    for mp in sorted(glob.glob(str(ROOT / "repo" / "forms" / "*" / "metadata.json"))):
        m = json.loads(pathlib.Path(mp).read_text())
        out.append({"form_id": m["form_id"], "title": m.get("title"),
                    "category": m.get("category"), "source_url": m.get("source_url")})
    return out


@app.get("/forms/{form_id}")
def form_detail(form_id: str):
    m = _meta(form_id)
    plan = build_plan(form_id, {}, root=ROOT)          # empty case -> bucket shape
    if not plan.get("ok"):
        raise HTTPException(404, plan.get("error"))
    return {"form_id": form_id, "title": m.get("title"), "category": m.get("category"),
            "source_url": m.get("source_url"), "n_fields": plan["n_fields"],
            "coverage": plan["coverage"],
            "narrative_worklist": plan["narrative"], "unresolved": plan["unresolved"]}


@app.post("/route")
def route(req: RouteReq):
    """LLM-backed form selection (configure via ROUTER_* env)."""
    return route_form.route(req.fact)


@app.post("/plan")
def plan(req: PlanReq):
    """Deterministic fill plan — pure function of the case object (no LLM/network)."""
    res = build_plan(req.form_id, to_case_object(req.case), root=ROOT)
    if not res.get("ok"):
        raise HTTPException(400, res.get("error"))
    return res


@app.post("/fill")
def fill(req: FillReq):
    """Deterministic PDF fill. Fetches the flat source (source_url) and injects values."""
    m = _meta(req.form_id)
    src_url = req.source_url or m.get("source_url")
    if not src_url:
        raise HTTPException(400, f"no source_url for {req.form_id}")
    tmp = pathlib.Path(tempfile.mkdtemp())
    src = tmp / "source.pdf"
    try:
        rq = urllib.request.Request(src_url, headers={"User-Agent": "Mozilla/5.0"})
        src.write_bytes(urllib.request.urlopen(rq, timeout=30).read())
    except Exception as e:
        raise HTTPException(502, f"could not fetch source_url: {e}")
    out = tmp / f"{req.form_id}.filled.pdf"
    res = _fill_pdf(req.form_id, to_case_object(req.case), src, out, root=ROOT)
    if not res.get("ok"):
        raise HTTPException(400, res.get("error"))
    return FileResponse(str(out), media_type="application/pdf",
                        filename=f"{req.form_id}.filled.pdf")


class EnhanceReq(BaseModel):
    form_id: str
    steps: list[str] | None = None      # explicit step ids
    preset: str | None = None           # or a named preset
    case: dict | None = None            # required if 'fill' is included
    fresh: bool = False                 # re-download the blank from court


@app.get("/", response_class=HTMLResponse)
def control_panel():
    """The one-page enhancement control panel."""
    page = STATIC / "index.html"
    if not page.exists():
        raise HTTPException(404, "control panel not installed (tools/static/index.html)")
    return HTMLResponse(page.read_text())


@app.get("/enhance/catalog")
def enhance_catalog():
    """Forms + enhancement steps + presets + external-tool availability (for the UI)."""
    return enhance.catalog()


@app.post("/enhance")
def enhance_run(req: EnhanceReq):
    """Run a composed enhancement pipeline; return the resulting PDF.

    Only registered step ids / presets and a known form_id are accepted (enum-
    gated — no arbitrary commands). The run log is returned in X-Enhance-Log.
    """
    steps = enhance.PRESETS.get(req.preset) if req.preset else req.steps
    if not steps:
        raise HTTPException(400, "provide 'steps' or a known 'preset'")
    res = enhance.run(req.form_id, list(steps), case=req.case, fresh=req.fresh)
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "enhancement failed"))
    summary = json.dumps({"ran": res["ran"], "skipped": res["skipped"],
                          "log": res["log"]})
    return FileResponse(res["out"], media_type="application/pdf",
                        filename=f"{req.form_id}.enhanced.pdf",
                        headers={"X-Enhance-Log": summary})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8077)
