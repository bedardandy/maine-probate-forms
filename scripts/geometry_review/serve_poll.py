#!/usr/bin/env python3
"""Serve the geometry-review human poll over HTTP.

Renders each flagged unit's problem area with candidate fixes (A/B/C/...) as
clickable cards; records the reviewer's pick (or a free-text "other" note) to
<out>/human_decisions.jsonl. Resumable: decided units show their choice and
the progress count persists.

    python3 scripts/geometry_review/serve_poll.py --out ~/geom-review-out --port 8770
Then browse to http://<this-host>:8770/  (over Tailscale/LAN), or from your
laptop:  ssh -L 8770:localhost:8770 <host>  and open http://localhost:8770/
"""
from __future__ import annotations

import argparse
import json
import pathlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OUT: pathlib.Path
PAGE = """<!doctype html><html><head><meta charset=utf-8>
<title>Probate geometry review</title><style>
body{font:15px/1.45 system-ui,sans-serif;margin:0;background:#0f1115;color:#e6e6e6}
header{position:sticky;top:0;background:#161922;padding:10px 16px;border-bottom:1px solid #2a2f3a;z-index:5}
#prog{font-weight:600}
.bar{height:6px;background:#2a2f3a;border-radius:3px;margin-top:6px;overflow:hidden}
.bar>i{display:block;height:100%;background:#3b82f6;width:0}
main{padding:16px;max-width:1200px;margin:0 auto}
.u{background:#161922;border:1px solid #2a2f3a;border-radius:10px;padding:14px;margin:0 0 18px}
.u h2{margin:0 0 2px;font-size:16px}
.meta{color:#9aa4b2;font-size:13px;margin-bottom:10px;word-break:break-word}
.opts{display:flex;flex-wrap:wrap;gap:12px}
.opt{border:2px solid #2a2f3a;border-radius:8px;padding:6px;cursor:pointer;background:#0f1115;transition:.1s}
.opt:hover{border-color:#5b6472}
.opt.sel{border-color:#22c55e;box-shadow:0 0 0 2px #22c55e55}
.opt img{display:block;max-width:540px;border-radius:4px;background:#fff}
.opt .k{font-weight:700;margin-bottom:4px}
.opt .k span{color:#9aa4b2;font-weight:400}
.row2{margin-top:10px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.row2 input{flex:1;min-width:240px;background:#0f1115;border:1px solid #2a2f3a;color:#e6e6e6;padding:7px;border-radius:6px}
.btn{background:#3b82f6;border:0;color:#fff;padding:7px 14px;border-radius:6px;cursor:pointer}
.btn.ghost{background:#2a2f3a}
.done{color:#22c55e;font-weight:600}
.filterbar{margin:6px 0 0;font-size:13px}
.filterbar a{color:#9aa4b2;margin-right:10px;cursor:pointer;text-decoration:underline}
</style></head><body>
<header><span id=prog>loading…</span> <span class=filterbar><a onclick="setF('all')">all</a>
<a onclick="setF('todo')">undecided</a><a onclick="setF('done')">decided</a></span>
<div class=bar><i id=barfill></i></div></header>
<main id=app></main>
<script>
let UNITS=[],DEC={},FILT='todo';
async function load(){
 UNITS=await (await fetch('api/units')).json();
 DEC=await (await fetch('api/decisions')).json();
 render();
}
function setF(f){FILT=f;render();}
function prog(){let n=UNITS.length,d=Object.keys(DEC).length;
 document.getElementById('prog').textContent=`${d} / ${n} decided`;
 document.getElementById('barfill').style.width=(100*d/Math.max(1,n))+'%';}
function render(){
 prog();
 const app=document.getElementById('app');app.innerHTML='';
 UNITS.filter(u=>FILT=='all'||(FILT=='done')==(u.id in DEC)).forEach(u=>{
  const d=document.createElement('div');d.className='u';
  const chosen=DEC[u.id];
  d.innerHTML=`<h2>${u.form} &middot; ${u.field} <span style="color:#9aa4b2">w${u.widget_idx} p${u.page+1}</span>
   ${chosen?`<span class=done>✓ ${chosen.choice}</span>`:''}</h2>
   <div class=meta>flagged via ${u.via} (${u.signal}); sample shown: "${u.value_shown}"<br>${u.detail||''}</div>
   <div class=opts></div>
   <div class=row2><input placeholder="Other: describe the correct placement (optional note)"
     value="${chosen&&chosen.choice=='other'?(chosen.note||''):''}">
     <button class=btn>Save "Other"</button>
     <button class="btn ghost">Skip</button></div>`;
  const opts=d.querySelector('.opts');
  u.options.forEach(o=>{
   const c=document.createElement('div');c.className='opt'+(chosen&&chosen.choice==o.key?' sel':'');
   c.innerHTML=`<div class=k>${o.key} <span>${o.label}</span></div><img src="${o.crop}">`;
   c.onclick=()=>vote(u,o.key,'');
   opts.appendChild(c);
  });
  const inp=d.querySelector('input');
  d.querySelector('.btn:not(.ghost)').onclick=()=>vote(u,'other',inp.value);
  d.querySelector('.btn.ghost').onclick=()=>vote(u,'skip','');
  app.appendChild(d);
 });
}
async function vote(u,choice,note){
 const rect=(u.options.find(o=>o.key==choice)||{}).rect||null;
 const body={id:u.id,form:u.form,field:u.field,widget_idx:u.widget_idx,
   choice,note,chosen_rect:rect};
 await fetch('api/vote',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify(body)});
 if(choice=='skip'){delete DEC[u.id];}else{DEC[u.id]=body;}
 render();
}
load();
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        elif path == "/api/units":
            self._send(200, (OUT / "poll_data.json").read_bytes())
        elif path == "/api/decisions":
            dec = {}
            p = OUT / "human_decisions.jsonl"
            if p.exists():
                for line in p.open():
                    o = json.loads(line)
                    if o["choice"] == "skip":
                        dec.pop(o["id"], None)
                    else:
                        dec[o["id"]] = o
            self._send(200, json.dumps(dec).encode())
        elif path.startswith("/poll_crops/"):
            f = OUT / path.lstrip("/")
            if f.exists() and f.suffix == ".png":
                body = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")  # crops change on rebuild
                self.end_headers()
                self.wfile.write(body)
            else:
                self._send(404, b"not found", "text/plain")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if self.path != "/api/vote":
            return self._send(404, b"no", "text/plain")
        n = int(self.headers.get("Content-Length", 0))
        o = json.loads(self.rfile.read(n))
        import datetime  # noqa: not used for randomness; ts is informational
        try:
            o["ts"] = datetime.datetime.now().isoformat(timespec="seconds")
        except Exception:
            pass
        with (OUT / "human_decisions.jsonl").open("a") as fh:
            fh.write(json.dumps(o) + "\n")
        self._send(200, b'{"ok":true}')


def main() -> int:
    global OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path,
                    default=pathlib.Path.home() / "geom-review-out")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()
    OUT = args.out
    srv = ThreadingHTTPServer((args.host, args.port), H)
    print(f"serving poll on http://{args.host}:{args.port}/  (out={OUT})")
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
