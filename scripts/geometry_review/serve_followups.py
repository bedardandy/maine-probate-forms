#!/usr/bin/env python3
"""Serve the *follow-up* decision poll (the un-automatable schema/fill-logic
calls) over HTTP — a text-first companion to the visual A/B/C geometry poll
(serve_poll.py on 8770).

Each unit shows the problem, an optional context crop (field outlined in red on
the real form), and options where every option states WHAT CHANGES IN THE
FILLED PDF if you pick it. You can pick an option, add a free-text note, or
both; notes are saved with the choice. Decisions go to
<out>/followup_decisions.jsonl (last write per unit wins; "skip" clears it),
which is separate from the geometry poll's human_decisions.jsonl.

    python3 scripts/geometry_review/build_followup_poll.py --out ~/geom-review-out
    python3 scripts/geometry_review/serve_followups.py    --out ~/geom-review-out --port 8771
Then browse http://<host>:8771/  (or ssh -L 8771:localhost:8771 <host>).
"""
from __future__ import annotations

import argparse
import json
import pathlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OUT: pathlib.Path
DECISIONS_FILE = "followup_decisions.jsonl"

PAGE = """<!doctype html><html><head><meta charset=utf-8>
<title>Probate follow-up decisions</title><style>
body{font:15px/1.5 system-ui,sans-serif;margin:0;background:#0f1115;color:#e6e6e6}
header{position:sticky;top:0;background:#161922;padding:10px 16px;border-bottom:1px solid #2a2f3a;z-index:5}
#prog{font-weight:600}
.bar{height:6px;background:#2a2f3a;border-radius:3px;margin-top:6px;overflow:hidden}
.bar>i{display:block;height:100%;background:#3b82f6;width:0;transition:width .2s}
.filterbar{margin:8px 0 0;font-size:13px}
.filterbar a{color:#9aa4b2;margin-right:12px;cursor:pointer;text-decoration:underline}
.filterbar a.on{color:#e6e6e6;font-weight:700}
.hint{color:#6b7585;font-size:12px;margin-top:6px}
main{padding:16px;max-width:980px;margin:0 auto}
.u{background:#161922;border:1px solid #2a2f3a;border-radius:10px;padding:16px;margin:0 0 18px}
.u h2{margin:0 0 4px;font-size:17px}
.tag{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;
 padding:2px 8px;border-radius:20px;margin-right:8px;vertical-align:middle}
.t-Structural{background:#7c2d12;color:#fed7aa}.t-Semantic{background:#1e3a8a;color:#bfdbfe}
.t-Continuation{background:#365314;color:#d9f99d}.t-Multiline{background:#581c87;color:#e9d5ff}
.who{color:#9aa4b2;font-size:13px}
.problem{color:#c5ccd6;margin:10px 0;background:#0f1115;border-left:3px solid #2a2f3a;padding:8px 12px;border-radius:0 6px 6px 0}
.precedent{color:#9aa4b2;font-size:13px;font-style:italic;margin:8px 0}
.crop{margin:10px 0}.crop img{max-width:100%;border-radius:6px;background:#fff;border:1px solid #2a2f3a}
.opts{display:flex;flex-direction:column;gap:8px;margin-top:8px}
.opt{border:2px solid #2a2f3a;border-radius:8px;padding:10px 12px;cursor:pointer;background:#0f1115;transition:.1s}
.opt:hover{border-color:#5b6472}
.opt.sel{border-color:#22c55e;box-shadow:0 0 0 2px #22c55e44;background:#0e1f16}
.opt .k{font-weight:700}.opt .k b{color:#3b82f6;margin-right:6px}
.opt .res{color:#9aa4b2;font-size:13px;margin-top:4px}.opt.sel .res{color:#bbf7d0}
.row2{margin-top:12px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.row2 textarea{flex:1;min-width:280px;background:#0f1115;border:1px solid #2a2f3a;color:#e6e6e6;
 padding:8px;border-radius:6px;font:14px system-ui;resize:vertical;min-height:38px}
.btn{background:#3b82f6;border:0;color:#fff;padding:8px 14px;border-radius:6px;cursor:pointer;font-weight:600}
.btn.ghost{background:#2a2f3a}
.done{color:#22c55e;font-weight:600;font-size:13px}
.saved{color:#22c55e;font-size:12px;margin-left:8px;opacity:0;transition:opacity .2s}
.saved.show{opacity:1}
</style></head><body>
<header><span id=prog>loading…</span>
<span class=filterbar id=filt></span>
<div class=bar><i id=barfill></i></div>
<div class=hint>Pick the option you want, and/or add a note. Both are saved. "Other / note only"
saves just your text. Decisions write to followup_decisions.jsonl.</div></header>
<main id=app></main>
<script>
let UNITS=[],DEC={},FILT='todo',CATS=[];
async function load(){
 UNITS=await (await fetch('api/units')).json();
 DEC=await (await fetch('api/decisions')).json();
 CATS=[...new Set(UNITS.map(u=>u.category))];
 buildFilters();render();
}
function buildFilters(){
 const f=document.getElementById('filt');
 const mk=(k,l)=>`<a class="${FILT==k?'on':''}" onclick="setF('${k}')">${l}</a>`;
 f.innerHTML=mk('todo','undecided')+mk('done','decided')+mk('all','all')
  +' &nbsp;|&nbsp; '+CATS.map(c=>mk('cat:'+c,c)).join('');
}
function setF(f){FILT=f;buildFilters();render();}
function show(u){
 if(FILT=='all')return true;
 if(FILT=='todo')return !(u.id in DEC);
 if(FILT=='done')return (u.id in DEC);
 if(FILT.startsWith('cat:'))return u.category==FILT.slice(4);
 return true;
}
function prog(){let n=UNITS.length,d=Object.keys(DEC).length;
 document.getElementById('prog').textContent=`${d} / ${n} decided`;
 document.getElementById('barfill').style.width=(100*d/Math.max(1,n))+'%';}
function render(){
 prog();
 const app=document.getElementById('app');app.innerHTML='';
 UNITS.filter(show).forEach(u=>{
  const dec=DEC[u.id];
  const d=document.createElement('div');d.className='u';d.id='u_'+u.id;
  let opts=u.options.map(o=>{
   const sel=dec&&dec.choice==o.key?' sel':'';
   return `<div class="opt${sel}" data-k="${o.key}">
     <div class=k><b>${o.key}</b>${o.label}</div>
     <div class=res>→ ${o.result}</div></div>`;
  }).join('');
  d.innerHTML=`<h2><span class="tag t-${u.category}">${u.category}</span>${u.title}</h2>
   <div class=who>${u.form} &middot; ${u.field} <span style="color:#6b7585">w${u.widget_idx}</span>
     ${dec?`<span class=done>✓ ${dec.choice}${dec.note?' + note':''}</span>`:''}</div>
   <div class=problem>${u.problem}</div>
   ${u.precedent?`<div class=precedent>Precedent: ${u.precedent}</div>`:''}
   ${u.crop?`<div class=crop><img src="${u.crop}" loading=lazy></div>`:''}
   <div class=opts>${opts}</div>
   <div class=row2>
     <textarea placeholder="Notes — refine the choice, describe the correct behavior, or flag a concern (optional)">${dec&&dec.note?dec.note:''}</textarea>
   </div>
   <div class=row2>
     <button class=btn data-act=save>Save</button>
     <button class="btn ghost" data-act=noteonly>Save note only</button>
     <button class="btn ghost" data-act=skip>Skip / clear</button>
     <span class=saved>saved ✓</span>
   </div>`;
  let pick=dec?dec.choice:null;
  d.querySelectorAll('.opt').forEach(el=>el.onclick=()=>{
   pick=el.dataset.k;
   d.querySelectorAll('.opt').forEach(x=>x.classList.toggle('sel',x===el));
  });
  const ta=d.querySelector('textarea');
  d.querySelector('[data-act=save]').onclick=()=>vote(u,pick&&pick!='other'?pick:'other',ta.value,d);
  d.querySelector('[data-act=noteonly]').onclick=()=>vote(u,'other',ta.value,d);
  d.querySelector('[data-act=skip]').onclick=()=>vote(u,'skip','',d);
  app.appendChild(d);
 });
}
async function vote(u,choice,note,d){
 if(choice!='skip'&&choice!='other'&&!choice){choice='other';}
 const body={id:u.id,form:u.form,field:u.field,widget_idx:u.widget_idx,
   category:u.category,choice,note};
 await fetch('api/vote',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify(body)});
 if(choice=='skip'){delete DEC[u.id];}else{DEC[u.id]=body;}
 const s=d.querySelector('.saved');if(s){s.classList.add('show');setTimeout(()=>s.classList.remove('show'),1200);}
 prog();
 // refresh the done-badge without nuking the card unless filtering hides it
 if((FILT=='todo'&&choice!='skip')||(FILT=='done'&&choice=='skip'))setTimeout(render,300);
 else{const badge=d.querySelector('.who .done')||d.querySelector('.who');}
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
            p = OUT / "followup_poll.json"
            if not p.exists():
                return self._send(404, b'{"error":"run build_followup_poll.py first"}')
            self._send(200, p.read_bytes())
        elif path == "/api/decisions":
            dec = {}
            p = OUT / DECISIONS_FILE
            if p.exists():
                for line in p.open():
                    line = line.strip()
                    if not line:
                        continue
                    o = json.loads(line)
                    if o.get("choice") == "skip":
                        dec.pop(o["id"], None)
                    else:
                        dec[o["id"]] = o
            self._send(200, json.dumps(dec).encode())
        elif path.startswith("/followup_crops/"):
            f = OUT / path.lstrip("/")
            if f.exists() and f.suffix == ".png":
                body = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
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
        import datetime
        try:
            o["ts"] = datetime.datetime.now().isoformat(timespec="seconds")
        except Exception:
            pass
        with (OUT / DECISIONS_FILE).open("a") as fh:
            fh.write(json.dumps(o) + "\n")
        self._send(200, b'{"ok":true}')


def main() -> int:
    global OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path,
                    default=pathlib.Path.home() / "geom-review-out")
    ap.add_argument("--port", type=int, default=8771)
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()
    OUT = args.out
    srv = ThreadingHTTPServer((args.host, args.port), H)
    print(f"serving follow-up decisions on http://{args.host}:{args.port}/  (out={OUT})")
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
