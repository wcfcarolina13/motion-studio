"""Proportion check for generated stills: people and props against a sheet of their real sizes.

Studios keep a lineup or size-comparison sheet so every artist draws a prop at the same size next to the cast. This does
the same for image models, in three steps:

  1. A dimension sheet: "sizes" in script.json, each entry's real height (and width and depth when it can lie on its
     side), in metres, with the source it came from: {"man": {"h": 1.78, "source": "..."}, "porta-potty": {"h": 2.3,
     "w": 1.12, "d": 1.12, "source": "https://..."}}. A panel names what is in it with "char_in_frame" and "props".
  2. Prompts: `scale_sentence` states each prop's size against the first person in frame, and `longform.py panels`
     puts it into the prompt before the style ending.
  3. Review: `page` draws every panel with tools to mark the horizon and each thing's base and top, plus a ghost ruler
     that shows, wherever you click on the ground, how tall each thing in the sheet should be there. `check` turns the
     marks into a verdict.

The measurement is single-view metrology (Criminisi, Reid and Zisserman, 2000): on level ground, with the camera level,
a thing standing on the ground at image row y_base with its top at y_top is H = H_cam * (y_base - y_top) / (y_base -
y_horizon) tall, where H_cam is the camera's height. Each mark gives an estimate of H_cam; the people in frame set it,
and every prop must agree within TOL. This handles perspective: a toilet in front of a man can look bigger and still
be right. It breaks when the camera tilts hard (verticals converge), when a thing is not on the ground (on a truck
bed, mid-air), and with strong wide-lens distortion; mark those "skip" with a reason.
"""
import argparse
import html
import json
import statistics
import sys
import time
from pathlib import Path

TOL = 0.20          # a prop more than 20% off its real size against the people in frame fails
SAME_ROW = 0.03     # without a horizon, things whose bases sit within 3% of the frame height are compared directly
DIMS = {"h": "height", "w": "width", "d": "depth"}


def sized_items(panel, sizes):
    """The people and props in a panel that the sheet has sizes for, people first."""
    seen, out = set(), []
    for k in list(panel.get("char_in_frame") or []) + list(panel.get("props") or []):
        if k in sizes and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def is_person(key, panel):
    return key in (panel.get("char_in_frame") or [])


def sheet_problems(sizes):
    out = []
    for k, v in (sizes or {}).items():
        if not isinstance(v, dict) or not isinstance(v.get("h"), (int, float)) or v["h"] <= 0:
            out.append(f'sizes["{k}"]: needs "h", its real height in metres')
        elif not str(v.get("source") or "").strip():
            out.append(f'sizes["{k}"]: needs "source" (a spec sheet, catalogue or measured note), so the size can be checked')
    return out


def scale_sentence(panel, sizes):
    """One sentence that pins each prop's size to the first person in frame, e.g. "Scale: the porta-potty stands
    about 1.3 times the man's height (2.3 m against 1.78 m)." Empty when fewer than two sized things are in frame."""
    items = sized_items(panel, sizes)
    if len(items) < 2:
        return ""
    people = [k for k in items if is_person(k, panel)]
    ref = people[0] if people else items[0]
    H = sizes[ref]["h"]
    parts = []
    for k in items:
        if k == ref:
            continue
        h = sizes[k]["h"]
        r = h / H
        if 0.93 <= r <= 1.07:
            rel = f"about the same height as the {ref}"
        elif r > 1:
            rel = f"about {r:.1f} times the {ref}'s height"
        else:
            rel = f"about {r:.0%} of the {ref}'s height"
        land = landmark(r) if is_person(ref, panel) else ""
        parts.append(f"the {k} stands {rel} ({h:g} m against {H:g} m){land}")
    return "Scale: " + "; ".join(parts) + "."


# where on a standing adult a height lands, as a share of their height (body alignment reads better to image models
# than numbers: GenScale, 2026, and user reports where "1.4x taller" came out 1.1x)
LANDMARKS = [(0.28, "his knee"), (0.53, "his hip"), (0.72, "his chest"), (0.82, "his shoulder"), (0.93, "his eyes")]


def landmark(r):
    if r >= 1.07:
        return f", its top {'well ' if r >= 1.2 else ''}above his head"
    if r <= 0.93:
        near = min(LANDMARKS, key=lambda x: abs(x[0] - r))
        return f", its top level with {near[1]}"
    return ""


def with_scale(prompt, sentence):
    """The prompt with the scale sentence before its last sentence (the style ending, which the model weights lightly
    and the lint treats as shared boilerplate)."""
    if not sentence or sentence in prompt:
        return prompt
    p = prompt.rstrip()
    cut = max(p.rfind(". ", 0, len(p) - 1), p.rfind("! ", 0, len(p) - 1), p.rfind("? ", 0, len(p) - 1))
    if cut < 0:
        return f"{p} {sentence}"
    return f"{p[:cut + 1]} {sentence} {p[cut + 2:]}"


def real_size(sizes, key, dim):
    v = sizes[key]
    return v.get(dim) or (v["h"] if dim == "h" else None)


def judge(panel, sizes, marks, tol=TOL):
    """marks: {"horizon": y or None, "marks": [{"item", "dim", "base": [x, y], "top": [x, y]}], "skip": reason?}, all
    coordinates as 0-1 of the image. -> {"ok", "rows": [...], "problems": [...]}."""
    if marks.get("skip"):
        return {"ok": True, "skipped": marks["skip"], "rows": [], "problems": []}
    want = sized_items(panel, sizes)
    ms = [m for m in marks.get("marks") or [] if m.get("item") in sizes]
    probs, rows = [], []
    missing = [k for k in want if k not in {m["item"] for m in ms}]
    if missing:
        probs.append("not marked: " + ", ".join(missing))
    yh = marks.get("horizon")
    est = []
    for m in ms:
        dim = m.get("dim") or "h"
        H = real_size(sizes, m["item"], dim)
        if not H:
            probs.append(f'{m["item"]}: no {DIMS.get(dim, dim)} in the sheet')
            continue
        yb, yt = m["base"][1], m["top"][1]
        if yt >= yb:
            probs.append(f'{m["item"]}: top is not above base')
            continue
        if yh is not None:
            if yb <= yh:
                probs.append(f'{m["item"]}: base at or above the horizon, so it is not on the ground or the horizon is wrong; '
                             'move the horizon or mark the panel skip')
                continue
            est.append((m, dim, H, H * (yb - yh) / (yb - yt)))          # implied camera height in metres
        else:
            est.append((m, dim, H, None))
    if len(est) < 2:
        return {"ok": not probs and len(want) < 2, "rows": rows, "problems": probs or ["fewer than two marked things"]}
    people = [e for e in est if is_person(e[0]["item"], panel)] or est
    if yh is not None:
        cam = statistics.median(e[3] for e in people)
        for m, dim, H, c in est:
            drawn = H * cam / c                                          # the size it is drawn at, given the people
            r = drawn / H
            ok = abs(r - 1) <= tol
            rows.append({"item": m["item"], "dim": dim, "real_m": H, "drawn_m": round(drawn, 2), "ratio": round(r, 2), "ok": ok})
            if not ok:
                probs.append(f'{m["item"]} is drawn at about {drawn:.2f} m {DIMS.get(dim, dim)} against {H:g} m real '
                             f'({"too big" if r > 1 else "too small"} by {abs(r - 1):.0%}) next to the people in frame; '
                             f'fix: make the {m["item"]} {1 / r:.2f}x its drawn size relative to the people')
        return {"ok": not probs, "camera_m": round(cam, 2), "rows": rows, "problems": probs}
    # no horizon: only things standing on the same row can be compared, by pixel height
    ref = people[0]
    for m, dim, H, _ in est:
        if m is ref[0]:
            continue
        if abs(m["base"][1] - ref[0]["base"][1]) > SAME_ROW:
            probs.append(f'{m["item"]}: stands at a different depth from {ref[0]["item"]}; mark the horizon to compare them')
            continue
        px = (m["base"][1] - m["top"][1]) / (ref[0]["base"][1] - ref[0]["top"][1])
        r = px / (H / ref[2])
        ok = abs(r - 1) <= tol
        rows.append({"item": m["item"], "dim": dim, "real_m": H, "ratio": round(r, 2), "ok": ok})
        if not ok:
            probs.append(f'{m["item"]} is {"too big" if r > 1 else "too small"} by {abs(r - 1):.0%} next to {ref[0]["item"]}; '
                         f'fix: make the {m["item"]} {1 / r:.2f}x its drawn size relative to the {ref[0]["item"]}')
    return {"ok": not probs, "rows": rows, "problems": probs}


def check_all(root, panels, sizes, marks_by_image, tol=TOL):
    """-> {image: verdict}, each with the image's mtime so a redrawn image needs a new check."""
    out = {}
    for p in panels:
        if len(sized_items(p, sizes)) < 2:
            continue
        img = root / p["image"]
        mk = marks_by_image.get(p["image"])
        if mk is None:
            out[p["image"]] = {"ok": False, "problems": ["no marks"], "rows": []}
            continue
        v = judge(p, sizes, mk, tol)
        v["image_mtime"] = img.stat().st_mtime if img.exists() else None
        v["checked"] = time.strftime("%Y-%m-%d %H:%M")
        out[p["image"]] = v
    return out


def gate_problems(root, panels, sizes, verdicts):
    """Panels with two or more sized things whose image exists and has no passing check of that exact image."""
    out = []
    for p in panels:
        if len(sized_items(p, sizes)) < 2:
            continue
        img = root / p["image"]
        if not img.exists():
            continue
        v = verdicts.get(p["image"])
        where = f'{p["image"]} ({", ".join(p.get("beats") or [])})'
        if not v:
            out.append(f"{where}: proportions not checked")
        elif not v.get("ok"):
            out.append(f"{where}: " + "; ".join(v.get("problems") or ["failed"]))
        elif v.get("image_mtime") and img.stat().st_mtime > v["image_mtime"] + 1:
            out.append(f"{where}: image changed after its proportion check")
    return out


PAGE = r"""<!doctype html><meta charset=utf-8><title>Proportions: __TITLE__</title>
<meta name=viewport content="width=device-width,initial-scale=1">
<style>
body{font:14px system-ui,sans-serif;margin:16px;background:#2b2a27;color:#eee}
.p{margin:0 0 28px}.bar{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:6px 0}
button,select,input{font:inherit;padding:4px 8px;border-radius:4px;border:1px solid #666;background:#3a3934;color:#eee}
button.on{background:#c8922e;color:#111}.wrap{position:relative;display:inline-block;max-width:100%}
img{display:block;max-width:100%}canvas{position:absolute;inset:0;width:100%;height:100%;cursor:crosshair}
.v{white-space:pre-wrap;color:#ddd}.bad{color:#ff8a80}.good{color:#a5d6a7}
</style>
<h1>Proportions: __TITLE__</h1>
<p>For each still: <b>Horizon</b>, click where the ground would meet the sky at eye level (it may be off the frame: type a
percentage). <b>Mark</b>, pick a thing and its dimension, click its base where it touches the ground, then its top.
<b>Ruler</b>, click any spot on the ground to see how tall each thing should be standing there. <b>Grid</b> stands
2 m posts (a tick at 1 m) on the ground at several depths, sized from the people's marks. Download the marks and run
<code>longform.py scale &lt;film&gt; --marks marks.json</code>.</p>
<p><button id=dl>Download marks.json</button> <span id=sum></span></p>
<div id=list></div>
<script>
const DATA=__DATA__, KEY="scale-marks:"+DATA.title;
let M={};try{M=JSON.parse(localStorage.getItem(KEY)||"{}")}catch(e){}
const save=()=>{try{localStorage.setItem(KEY,JSON.stringify(M))}catch(e){}};
const median=a=>{a=[...a].sort((x,y)=>x-y);const n=a.length;return n?(n%2?a[(n-1)/2]:(a[n/2-1]+a[n/2])/2):null};
function size(k,d){const s=DATA.sizes[k];return s[d]||(d=="h"?s.h:null)}
function camera(p,m){ // median implied camera height (m) from people's marks, else from all marks
  if(m.horizon==null)return null;
  const est=k=>m.marks.filter(x=>k(x.item)&&x.base[1]>m.horizon&&x.top[1]<x.base[1]).map(x=>size(x.item,x.dim||"h")*(x.base[1]-m.horizon)/(x.base[1]-x.top[1]));
  let e=est(i=>p.people.includes(i));if(!e.length)e=est(()=>true);return median(e)}
DATA.panels.forEach((p,i)=>{
  const m=M[p.image]=M[p.image]||{horizon:null,marks:[]};
  const d=document.createElement("div");d.className="p";
  d.innerHTML=`<h3>${i+1}. ${p.image} <small>(${p.beats.join(", ")})</small></h3>
  <div class=bar><button data-m=horizon>Horizon</button><input type=number step=1 placeholder="horizon %" style="width:7em">
  <button data-m=mark>Mark</button><select>${p.items.map(k=>`<option>${k}</option>`).join("")}</select>
  <select class=dim><option value=h>height</option><option value=w>width</option><option value=d>depth</option></select>
  <button data-m=ruler>Ruler</button><button class=grid>Grid</button><button class=undo>Undo</button>
  <button class=skip>Skip…</button></div>
  <div class=wrap><img src="${p.src}"><canvas></canvas></div><div class=v></div>`;
  document.getElementById("list").appendChild(d);
  const img=d.querySelector("img"),cv=d.querySelector("canvas"),ctx=cv.getContext("2d"),v=d.querySelector(".v");
  const hin=d.querySelector("input"),item=d.querySelector("select"),dim=d.querySelector(".dim");
  let mode="mark",pend=null,ruler=null,grid=false;
  const btns=[...d.querySelectorAll("[data-m]")];
  const setMode=x=>{mode=x;btns.forEach(b=>b.classList.toggle("on",b.dataset.m==x))};setMode("mark");
  btns.forEach(b=>b.onclick=()=>setMode(b.dataset.m));
  d.querySelector(".grid").onclick=e=>{grid=!grid;e.target.classList.toggle("on",grid);draw()};
  d.querySelector(".undo").onclick=()=>{if(pend)pend=null;else m.marks.pop();save();draw()};
  d.querySelector(".skip").onclick=()=>{const r=prompt("Why can't this panel be measured? (camera tilted hard, nothing on the ground…) Empty clears.",m.skip||"");if(r!==null){m.skip=r||undefined;save();draw()}};
  hin.onchange=()=>{m.horizon=hin.value===""?null:hin.value/100;save();draw()};
  if(m.horizon!=null)hin.value=Math.round(m.horizon*1000)/10;
  cv.onclick=e=>{const r=cv.getBoundingClientRect(),x=(e.clientX-r.left)/r.width,y=(e.clientY-r.top)/r.height;
    if(mode=="horizon"){m.horizon=y;hin.value=Math.round(y*1000)/10}
    else if(mode=="ruler"){ruler=[x,y]}
    else if(!pend){pend={item:item.value,dim:dim.value,base:[x,y]}}
    else{pend.top=[x,y];m.marks=m.marks.filter(q=>!(q.item==pend.item&&q.dim==pend.dim));m.marks.push(pend);pend=null}
    save();draw()};
  function draw(){
    cv.width=img.naturalWidth||1280;cv.height=img.naturalHeight||720;const W=cv.width,H=cv.height,lw=Math.max(2,W/500);
    ctx.clearRect(0,0,W,H);ctx.lineWidth=lw;ctx.font=`${Math.round(W/60)}px system-ui`;
    const cam=camera(p,m);
    if(m.horizon!=null){ctx.strokeStyle="#4fc3f7";ctx.setLineDash([12,8]);ctx.beginPath();ctx.moveTo(0,m.horizon*H);ctx.lineTo(W,m.horizon*H);ctx.stroke();ctx.setLineDash([]);
      ctx.fillStyle="#4fc3f7";ctx.fillText("eye level"+(cam?` (camera ${cam.toFixed(2)} m)`:""),8,m.horizon*H-6)}
    if(grid&&cam&&m.horizon!=null){ // 2 m posts with a tick at 1 m, standing on the ground at several depths
      ctx.strokeStyle=ctx.fillStyle="rgba(21,101,192,.55)";ctx.lineWidth=lw*0.8;
      let row=0;for(let yb=Math.max(m.horizon,0)+0.08;yb<=1;yb+=0.12,row++){const px=(yb-m.horizon)/cam*H;
        for(let x=0.05+(row%3)*0.047;x<1;x+=0.14){const X=x*W,Y=yb*H;ctx.beginPath();ctx.moveTo(X,Y);ctx.lineTo(X,Y-2*px);
          ctx.moveTo(X-6,Y-px);ctx.lineTo(X+6,Y-px);ctx.moveTo(X-6,Y-2*px);ctx.lineTo(X+6,Y-2*px);ctx.moveTo(X-9,Y);ctx.lineTo(X+9,Y);ctx.stroke()}}
      ctx.fillText("posts: 2 m tall, tick at 1 m",8,H-12);ctx.lineWidth=lw}
    m.marks.forEach(q=>{ctx.strokeStyle="#c8922e";ctx.fillStyle="#c8922e";const x=q.base[0]*W;
      ctx.beginPath();ctx.moveTo(x,q.base[1]*H);ctx.lineTo(x,q.top[1]*H);ctx.stroke();
      ctx.beginPath();ctx.moveTo(x-10,q.base[1]*H);ctx.lineTo(x+10,q.base[1]*H);ctx.moveTo(x-10,q.top[1]*H);ctx.lineTo(x+10,q.top[1]*H);ctx.stroke();
      let t=q.item+(q.dim&&q.dim!="h"?" ("+q.dim+")":"");
      if(cam&&m.horizon!=null&&q.base[1]>m.horizon){const drawn=cam*(q.base[1]-q.top[1])/(q.base[1]-m.horizon),real=size(q.item,q.dim||"h");
        t+=` ${drawn.toFixed(2)} m / real ${real} m`;if(Math.abs(drawn/real-1)>DATA.tol)ctx.fillStyle="#ff5252"}
      ctx.fillText(t,x+12,q.top[1]*H+16)});
    if(pend){ctx.fillStyle="#c8922e";ctx.beginPath();ctx.arc(pend.base[0]*W,pend.base[1]*H,lw*3,0,7);ctx.fill()}
    if(ruler&&cam&&m.horizon!=null&&ruler[1]>m.horizon){let x=ruler[0]*W;const yb=ruler[1]*H;
      p.items.forEach((k,j)=>{const hpx=size(k,"h")/cam*(ruler[1]-m.horizon)*H,xx=x+j*lw*8;
        ctx.strokeStyle=ctx.fillStyle=["#222222","#b3261e","#1565c0","#2e7d32","#6a1b9a"][j%5];ctx.setLineDash([6,5]);
        ctx.beginPath();ctx.moveTo(xx,yb);ctx.lineTo(xx,yb-hpx);ctx.stroke();ctx.setLineDash([]);ctx.fillText(k,xx+6,yb-hpx+14)})}
    else if(ruler&&mode=="ruler"){ctx.fillStyle="#ff8a80";ctx.fillText("Ruler needs the horizon and one person marked, and a spot below the horizon",8,H-12)}
    v.innerHTML=m.skip?`skipped: ${m.skip}`:"";
    sum()}
  img.onload=draw;if(img.complete)draw();
});
function sum(){const n=DATA.panels.filter(p=>M[p.image]&&(M[p.image].skip||M[p.image].marks.length>=2)).length;
  document.getElementById("sum").textContent=`${n} of ${DATA.panels.length} panels marked`}
document.getElementById("dl").onclick=()=>{const a=document.createElement("a");
  a.href=URL.createObjectURL(new Blob([JSON.stringify(M,null,1)],{type:"application/json"}));a.download="marks.json";a.click()};
</script>"""


def page(out_dir, root, panels, sizes, title, tol=TOL):
    """Writes out_dir/index.html for the panels with two or more sized things."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for p in panels:
        items = sized_items(p, sizes)
        if len(items) < 2 or not (root / p["image"]).exists():
            continue
        src = Path(*[".."] * len(out_dir.relative_to(root).parts), p["image"]).as_posix() if out_dir.is_relative_to(root) \
            else (root / p["image"]).resolve().as_uri()
        rows.append({"image": p["image"], "beats": p.get("beats") or [], "items": items, "src": src,
                     "people": [k for k in items if is_person(k, p)]})
    data = {"title": title, "tol": tol, "sizes": sizes, "panels": rows}
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    (out_dir / "index.html").write_text(PAGE.replace("__TITLE__", html.escape(title)).replace("__DATA__", blob))
    return out_dir / "index.html", len(rows)


def load_list(path):
    """A standalone list (stills made outside a film): {"sizes": {...}, "panels": [{"image", "char_in_frame", "props"}]},
    image paths relative to the list's folder."""
    data = json.loads(Path(path).read_text())
    return Path(path).resolve().parent, data.get("panels", []), data.get("sizes", {})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("page", help="the marking page for a standalone list"); p.add_argument("list")
    p = sp.add_parser("check", help="judge marks.json for a standalone list"); p.add_argument("list"); p.add_argument("marks")
    p.add_argument("--tol", type=float, default=TOL)
    a = ap.parse_args(argv)
    root, panels, sizes = load_list(a.list)
    for x in sheet_problems(sizes):
        print("PROBLEM:", x)
    if a.cmd == "page":
        f, n = page(root / "scale", root, panels, sizes, Path(a.list).stem)
        print(f"{n} stills to mark -> {f}")
        return 0
    verdicts = check_all(root, panels, sizes, json.loads(Path(a.marks).read_text()), a.tol)
    (root / "scale.json").write_text(json.dumps(verdicts, indent=1) + "\n")
    bad = report(verdicts)
    return 1 if bad else 0


def report(verdicts):
    bad = 0
    for img, v in verdicts.items():
        if v.get("skipped"):
            print(f"SKIP {img}: {v['skipped']}")
        elif v["ok"]:
            print(f"PASS {img}" + (f" (camera {v['camera_m']} m)" if v.get("camera_m") else ""))
        else:
            bad += 1
            print(f"FAIL {img}")
            for x in v["problems"]:
                print("    ", x)
    print(f"{len(verdicts)} stills, {bad} failing (tolerance ±{TOL:.0%} unless --tol)")
    return bad


if __name__ == "__main__":
    sys.exit(main())
