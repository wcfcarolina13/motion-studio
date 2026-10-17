"""Panel quality for illustrated films: prompt lint before generation, a review sheet after it, and region repair.

The rules come from K-factory's anatomy research (2026-10-17): small step-distilled image models break hands, limbs and
multi-person scenes, no licence-safe automatic judge agrees with people better than about 60% of the time, and mflux has
no masked inpainting for the commercially licensed edit models. So prompts are linted for the known failure patterns,
a person marks faults on a contact sheet, and a fault is repaired by redrawing an upscaled crop and pasting back only
the marked box with our own mask.

  lint(items)                         warnings per panel prompt (no network, no model)
  review_page(film, panels, checks)   out/review/index.html: numbered panels, checker FAIL outlines, click and drag to mark
  repair(...)                         crop, redraw with mflux, paste back -> candidates in repairs/<image>/rNN/
  accept(...) / revert(...)           put a candidate into the panel, or restore the panel from before it
"""
import html
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np

MAX_IMAGES = 2              # reference images per call (refs + plate); identity mixes above this
MAX_WORDS = 80              # klein reads 512 tokens and weights the end lightly; one action and one setting fit in 40-80
MAX_CHARS = 2               # characters in frame; attention bleeds between look-alike subjects
MAX_HANDS = 2               # visible hands per panel
PLATE_SHOTS = {"CU", "ECU", "INS"}     # a plate's framing gets copied, which breaks a close shot
NEG_OK = {"no text"}        # the shared style suffix; left alone
HIDDEN = re.compile(r"pocket|behind (?:his|her|their|the) back|under the table|below the (?:frame|panel)|out of (?:frame|shot)"
                    r"|in (?:his|her|their) lap|in shadow|hidden|cropped|off[- ]frame", re.I)
CONTACT = re.compile(r"handshake|shak\w* hands|hug\w*|embrac\w*|holding hands|hand on (?:his|her|their) (?:shoulder|arm|back|cheek)"
                     r"|grabs? (?:his|her|their) (?:arm|wrist|hand)", re.I)
MULTI = re.compile(r"\bwhile\b|\bthen\b|\bas (?:he|she|they)\b|\bidle\b", re.I)
NEG = re.compile(r"\b(?:no|without|not|never|avoid|don't|doesn't|isn't)\s+[\w-]+", re.I)


HAND = re.compile(r"\b(?:(his|her|their|its|[A-Z][\w-]*'s|(?:man|woman|girl|boy|person|someone)'s)\s+)?"
                  r"(?:[a-z]+\s+)?(left|right)?\s*\b(hands|hand|fist|fists)\b", re.I)
TOUCH = re.compile(r"\b(?:around|over|onto|into|against|touch\w*|hold\w*|grip\w*|clasp\w*|squeez\w*|takes?)\b", re.I)


def hands_in(prompt):
    """(visible hands, a clause where two people's hands touch). A heuristic: "hands" counts two for its owner, a named
    left or right hand one each, and repeat mentions of one owner's hand count once. Clauses that hide hands are skipped."""
    owners, anon, contact = {}, 0, None
    for clause in re.split(r"[.;:]|,(?=\s)", prompt):
        if HIDDEN.search(clause):
            continue
        seen = set()
        for m in HAND.finditer(clause):
            owner, side, word = (m.group(1) or "").lower(), (m.group(2) or "").lower(), m.group(3).lower()
            if not owner:
                anon += 2 if word.endswith("s") else 1
                continue
            o = owners.setdefault(owner, set())
            o.update({"left", "right"} if word.endswith("s") else {side or "one"})
            seen.add(owner)
        if len(seen) > 1 and TOUCH.search(clause):
            contact = clause.strip()
    n = anon + sum(min(2, len(o - {"one"}) or 1) if "one" not in o or len(o) == 1 else min(2, len(o)) for o in owners.values())
    return n, contact


def lint(items):
    """items: [{"id", "prompt", "shot"?, "refs"?, "plate"?, "char_in_frame"?}] -> [(id, [warnings])] for panels with any."""
    out = []
    for it in items:
        p, w = it.get("prompt") or "", []
        if not p:
            continue
        shot = (it.get("shot") or "").upper()
        refs = it.get("refs") or []
        n_img = len(refs) + (1 if it.get("plate") else 0)
        if n_img > MAX_IMAGES:
            w.append(f"{n_img} reference images; keep 1-2 in one style (identity mixes above {MAX_IMAGES})")
        words = len(p.split())
        if words > MAX_WORDS:
            w.append(f"{words} words; keep to {MAX_WORDS} (one action, one setting)")
        chars = it.get("char_in_frame") or []
        if len(chars) > MAX_CHARS:
            w.append(f"{len(chars)} characters in frame; keep to {MAX_CHARS}, the rest as silhouettes or out of frame")
        hands, touch = hands_in(p)
        if hands > MAX_HANDS:
            w.append(f"about {hands} visible hands; keep to {MAX_HANDS} (hide hands that don't act, give acting hands an insert)")
        touch = (CONTACT.search(p) and CONTACT.search(p).group(0)) or touch
        if touch:
            w.append(f"contact between people (\"{touch[:60]}\"); limbs land on the wrong body, cut it unless the beat needs it")
        m = MULTI.search(p)
        if m:
            w.append(f"maybe more than one action (\"{m.group(0)}\"); one verb per character, split the beat in two if needed")
        if it.get("plate") and shot in PLATE_SHOTS:
            w.append(f"plate on a {shot} shot; its framing gets copied, leave it out or pre-crop it")
        negs = [x.group(0) for x in NEG.finditer(p) if x.group(0).lower() not in NEG_OK]
        if negs:
            w.append(f"negative phrasing ({', '.join(repr(x) for x in negs[:3])}); klein has no negative prompt, say what is there instead")
        if w:
            out.append((it.get("id"), w))
    return out


# ---- images through ffmpeg (the engine has no PIL dependency) ----

def size(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout.strip()
    w, h = out.split(",")[:2]
    return int(w), int(h)


def read_rgb(path, w=None, h=None):
    """An image as an (h, w, 3) float array, scaled with Lanczos to w x h if given."""
    if w is None:
        w, h = size(path)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"scale={w}:{h}:flags=lanczos", "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 3).astype(np.float32)


def write_rgb(arr, path):
    h, w = arr.shape[:2]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-i", "-",
                    "-frames:v", "1", str(path)], input=np.clip(arr + 0.5, 0, 255).astype(np.uint8).tobytes(), check=True)


def parse_box(box, W, H):
    """"x,y,w,h" in image pixels, or all four in 0-1 (fractions of the image, as the review sheet exports them)."""
    v = [float(x) for x in str(box).split(",")]
    if len(v) != 4 or v[2] <= 0 or v[3] <= 0:
        raise SystemExit(f"--box wants x,y,w,h with a positive width and height, got {box!r}")
    if all(0 <= x <= 1 for x in v):
        v = [v[0] * W, v[1] * H, v[2] * W, v[3] * H]
    x, y, w, h = (int(round(t)) for t in v)
    x, y = max(0, x), max(0, y)
    return x, y, min(w, W - x), min(h, H - y)


def context_square(box, W, H, context):
    """A square around the box, context times its longer side, moved (not shrunk) to stay inside the image."""
    x, y, w, h = box
    side = int(min(max(w, h) * context, W, H))
    cx, cy = x + w / 2, y + h / 2
    sx = int(min(max(cx - side / 2, 0), W - side))
    sy = int(min(max(cy - side / 2, 0), H - side))
    return sx, sy, side


def feather_mask(h, w, box, feather):
    """1 inside the box, falling linearly to 0 over `feather` pixels outside it (a hard core, a small feather)."""
    x, y, bw, bh = box
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dx = np.maximum(np.maximum(x - xx, xx - (x + bw - 1)), 0)
    dy = np.maximum(np.maximum(y - yy, yy - (y + bh - 1)), 0)
    d = np.hypot(dx, dy)
    return np.clip(1 - d / max(feather, 1e-6), 0, 1)[..., None] if feather > 0 else (d == 0).astype(np.float32)[..., None]


def composite(base, out_png, crop, box, feather):
    """Paste the box (plus its feather) of a generated crop onto a panel array; everything else stays as it is."""
    sx, sy, side = crop
    new = read_rgb(out_png, side, side)
    mask = feather_mask(side, side, (box[0] - sx, box[1] - sy, box[2], box[3]), feather)
    res = base.copy()
    res[sy:sy + side, sx:sx + side] = res[sy:sy + side, sx:sx + side] * (1 - mask) + new * mask
    return res


GENERATORS = {      # mflux 0.23 CLIs; neither takes a mask, so the crop is redrawn whole and the paste confines it
    "klein": ["mflux-generate-flux2-edit", "--model", "flux2-klein-4b"],
    "qwen": ["mflux-generate-qwen-edit", "--model", "qwen-image-edit-2511", "-q", "6", "--low-ram"],
}
CROP = 1024


def repair(film, img, box, prompt, *, ref=None, model="klein", seeds=(1,), context=2.25, feather=6, gen_args=(), beats=(),
           generate=None):
    """Redraw one region of a panel. Writes repairs/<image path>/rNN/: crop.png (the 1024 square sent to the model), out_<seed>.png
    (what came back), candidate_<seed>.png (the whole panel with only the box changed) and compare_<seed>.png (the crop
    before and after), and logs the run in repairs/log.json. The panel itself is untouched until `accept`."""
    src = film / img
    if not src.exists():
        raise SystemExit(f"no panel image {img}")
    W, H = size(src)
    b = parse_box(box, W, H)
    sx, sy, side = context_square(b, W, H, context)
    key = Path(img).with_suffix("").as_posix()
    root = film / "repairs" / key
    n = 1 + max([int(p.name[1:]) for p in root.glob("r[0-9]*") if p.name[1:].isdigit()] or [0])
    rdir = root / f"r{n:02d}"
    rdir.mkdir(parents=True)
    crop = rdir / "crop.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf",
                    f"crop={side}:{side}:{sx}:{sy},scale={CROP}:{CROP}:flags=lanczos", "-frames:v", "1", str(crop)], check=True)
    images = [str(crop)] + ([str(Path(ref).resolve())] if ref else [])
    cmd = [*GENERATORS[model], "--image-paths", *images, "--prompt", prompt, "--seed", *[str(s) for s in seeds],
           "--width", str(CROP), "--height", str(CROP), "--output", str(rdir / "out_{seed}.png"), *gen_args]
    t0 = time.time()
    if generate:
        generate(cmd)
    else:
        exe = shutil.which(cmd[0])
        if not exe:
            raise SystemExit(f"{cmd[0]} not found: install mflux 0.23 or later (pip install -U mflux, MIT)")
        subprocess.run(cmd, check=True)
    secs = round(time.time() - t0, 1)
    base = read_rgb(src)
    made = []
    for s in seeds:
        out = rdir / f"out_{s}.png"
        if not out.exists():
            print(f"  seed {s}: the generator wrote no {out.name}")
            continue
        write_rgb(composite(base, out, (sx, sy, side), b, feather), rdir / f"candidate_{s}.png")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-i", str(rdir / f"candidate_{s}.png"), "-filter_complex",
                        f"[0]crop={side}:{side}:{sx}:{sy},scale=768:768[a];[1]crop={side}:{side}:{sx}:{sy},scale=768:768[b];"
                        "[a][b]hstack", "-frames:v", "1", str(rdir / f"compare_{s}.png")], check=True)
        made.append(s)
    log_path = film / "repairs" / "log.json"
    log = json.loads(log_path.read_text()) if log_path.exists() else []
    log.append({"id": f"{key}/r{n:02d}", "image": img, "beats": list(beats), "box": list(b), "crop": [sx, sy, side],
                "prompt": prompt, "model": model, "ref": ref, "seeds": list(seeds), "candidates": made, "feather": feather,
                "seconds": secs, "at": time.strftime("%Y-%m-%d %H:%M"), "status": "candidate" if made else "failed"})
    log_path.write_text(json.dumps(log, indent=1, ensure_ascii=False) + "\n")
    return rdir, made, secs


def _entry(film, rid):
    log_path = film / "repairs" / "log.json"
    log = json.loads(log_path.read_text()) if log_path.exists() else []
    for e in log:
        if e["id"] == rid:
            return log, log_path, e
    raise SystemExit(f"no repair {rid} in {log_path}")


def accept(film, rid, seed=None):
    """Paste a repair's region into the panel as it is now (so two repairs of one panel both survive, in either order).
    The panel as it was goes to before.<ext> in the repair folder first, so `revert` can restore it. The upscaled copy is
    now older than the panel, so `timeline` asks for `upscale` again."""
    log, log_path, e = _entry(film, rid)
    if e.get("status") == "accepted":
        raise SystemExit(f"{rid} is already in the panel; `revert` it first to accept another seed")
    seed = seed if seed is not None else (e["candidates"][0] if len(e["candidates"]) == 1 else None)
    if seed is None or seed not in e["candidates"]:
        raise SystemExit(f"{rid} has candidates for seeds {e['candidates']}: pass --seed")
    rdir, src = film / "repairs" / rid, film / e["image"]
    shutil.copy2(src, rdir / ("before" + src.suffix))
    tmp = rdir / "accepted.png"
    write_rgb(composite(read_rgb(src), rdir / f"out_{seed}.png", e["crop"], e["box"], e["feather"]), tmp)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(tmp), "-frames:v", "1",
                    *(["-q:v", "2"] if src.suffix.lower() in (".jpg", ".jpeg") else []), str(src)], check=True)
    seq = 1 + max([x.get("accepted_seq", 0) for x in log] or [0])
    e.update(status="accepted", accepted_seed=seed, accepted_seq=seq, accepted_at=time.strftime("%Y-%m-%d %H:%M"))
    log_path.write_text(json.dumps(log, indent=1, ensure_ascii=False) + "\n")
    return src


def revert(film, rid):
    log, log_path, e = _entry(film, rid)
    src = film / e["image"]
    before = film / "repairs" / rid / ("before" + src.suffix)
    if e.get("status") != "accepted" or not before.exists():
        raise SystemExit(f"{rid} was never accepted, so there is nothing to revert")
    later = [x["id"] for x in log if x["image"] == e["image"] and x.get("status") == "accepted" and x.get("accepted_seq", 0) > e.get("accepted_seq", 0)]
    if later:
        raise SystemExit(f"revert {', '.join(later)} first: they were accepted on top of {rid}")
    shutil.copy2(before, src)
    e.update(status="reverted", reverted_at=time.strftime("%Y-%m-%d %H:%M"))
    log_path.write_text(json.dumps(log, indent=1, ensure_ascii=False) + "\n")
    return src


# ---- the review sheet ----

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Panel review</title><style>
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1d1f;--mute:#6b6b70;--line:#d9d7d2;--fail:#d6342c;--mark:#e08a00;--box:#1f6feb}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--card:#1e1e21;--ink:#ececee;--mute:#9a9aa1;--line:#34343a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,sans-serif}
header{position:sticky;top:0;z-index:2;background:var(--bg);border-bottom:1px solid var(--line);padding:10px 16px;display:flex;gap:12px;flex-wrap:wrap;align-items:center}
h1{font-size:16px;margin:0 12px 0 0}.n{color:var(--mute)}button{font:inherit;padding:5px 10px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink);cursor:pointer}
main{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px;padding:16px}
.c{background:var(--card);border:3px solid transparent;border-radius:8px;padding:8px}.c.fail{border-color:var(--fail)}.c.marked{border-color:var(--mark)}
.f{position:relative;user-select:none;cursor:crosshair}.f img{display:block;width:100%;border-radius:4px}
.b{position:absolute;border:2px solid var(--box);background:rgba(31,111,235,.12)}.b.chk{border-color:var(--fail);background:none;border-style:dashed}
.h{display:flex;gap:8px;align-items:baseline;margin:6px 0 2px}.num{font-weight:700}.meta{color:var(--mute);font-size:12px}
.why{color:var(--fail);font-size:12px}details{font-size:12px;color:var(--mute)}textarea{width:100%;font:12px/1.4 system-ui;margin-top:4px;background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:4px}
pre{white-space:pre-wrap;word-break:break-all;font-size:11px;background:var(--bg);padding:6px;border-radius:4px;margin:4px 0 0}
.hide-ok .c:not(.fail):not(.marked){display:none}
</style></head><body><header><h1>__TITLE__</h1><span class="n" id="count"></span>
<button id="only">Show only flagged</button><button id="dl">Download marks.json</button><button id="clear">Clear my marks</button>
<span class="n">Click a panel to mark it. Drag on it to box a region. Type the fix in the note; it becomes the repair prompt.</span></header>
<main id="grid"></main><script>
const PANELS=__PANELS__, FILM=__FILM__, KEY="panel-review:"+FILM;
let marks={};try{marks=JSON.parse(localStorage.getItem(KEY)||"{}")}catch(e){}
const save=()=>{try{localStorage.setItem(KEY,JSON.stringify(marks))}catch(e){};count()};
const m=img=>marks[img]||(marks[img]={marked:false,note:"",boxes:[]});
const q=s=>"'"+s.replace(/'/g,"'\\\\''")+"'";
function count(){const k=Object.values(marks).filter(x=>x.marked||x.boxes.length).length,f=PANELS.filter(p=>p.check&&p.check.verdict==="FAIL").length;
document.getElementById("count").textContent=PANELS.length+" panels, "+f+" flagged by the checker, "+k+" marked by you"}
function cmds(p){const x=m(p.image);return x.boxes.map(b=>"python3 longform.py repair "+q(FILM)+" --image "+q(p.image)+" --box "+b.map(v=>v.toFixed(4)).join(",")+" --prompt "+q(x.note||"Redraw the hand in image 1 with five fingers; keep the lineart, colours and everything else unchanged.")).join("\\n")}
function draw(p,el){const x=m(p.image),f=el.querySelector(".f");f.querySelectorAll(".b").forEach(b=>b.remove());
const add=(b,cls)=>{const d=document.createElement("div");d.className="b"+(cls?" "+cls:"");Object.assign(d.style,{left:b[0]*100+"%",top:b[1]*100+"%",width:b[2]*100+"%",height:b[3]*100+"%"});f.appendChild(d);return d};
((p.check||{}).boxes||[]).forEach(b=>add(b,"chk"));x.boxes.forEach((b,i)=>{const d=add(b);d.title="Click to remove";d.onclick=e=>{e.stopPropagation();x.boxes.splice(i,1);save();draw(p,el)}});
el.classList.toggle("marked",x.marked||x.boxes.length>0);const pre=el.querySelector("pre");pre.textContent=cmds(p);pre.hidden=!x.boxes.length}
const grid=document.getElementById("grid");
PANELS.forEach((p,i)=>{const el=document.createElement("div");el.className="c"+(p.check&&p.check.verdict==="FAIL"?" fail":"");
el.innerHTML='<div class="f"><img loading="lazy" alt=""></div><div class="h"><span class="num">'+(i+1)+'</span><span class="meta"></span></div>'+
(p.check&&p.check.reason?'<div class="why"></div>':'')+'<details><summary>prompt</summary><div class="pr"></div></details><textarea rows="2" placeholder="What is wrong, as the repair prompt"></textarea><pre hidden></pre>';
el.querySelector("img").src=p.src;el.querySelector(".meta").textContent=[p.beats.join(" "),p.shot,p.image].filter(Boolean).join(" · ");
if(p.check&&p.check.reason)el.querySelector(".why").textContent=p.check.verdict+": "+p.check.reason;el.querySelector(".pr").textContent=p.prompt||"(none)";
const ta=el.querySelector("textarea");ta.value=m(p.image).note;ta.oninput=()=>{m(p.image).note=ta.value;save();el.querySelector("pre").textContent=cmds(p)};
const f=el.querySelector(".f");let st=null,ghost=null;
f.onpointerdown=e=>{const r=f.getBoundingClientRect();st=[(e.clientX-r.left)/r.width,(e.clientY-r.top)/r.height,r];f.setPointerCapture(e.pointerId)};
f.onpointermove=e=>{if(!st)return;const r=st[2],x=(e.clientX-r.left)/r.width,y=(e.clientY-r.top)/r.height;if(!ghost){ghost=document.createElement("div");ghost.className="b";f.appendChild(ghost)}
Object.assign(ghost.style,{left:Math.min(st[0],x)*100+"%",top:Math.min(st[1],y)*100+"%",width:Math.abs(x-st[0])*100+"%",height:Math.abs(y-st[1])*100+"%"})};
f.onpointerup=e=>{if(!st)return;const r=st[2],cl=v=>Math.min(1,Math.max(0,v)),x=cl((e.clientX-r.left)/r.width),y=cl((e.clientY-r.top)/r.height),x0=cl(st[0]),y0=cl(st[1]);st=null;if(ghost){ghost.remove();ghost=null}
const w=Math.abs(x-x0),h=Math.abs(y-y0),it=m(p.image);if(w<0.02&&h<0.02){it.marked=!it.marked}else{it.boxes.push([Math.min(x,x0),Math.min(y,y0),w,h])}save();draw(p,el)};
grid.appendChild(el);draw(p,el)});
document.getElementById("only").onclick=e=>{document.body.classList.toggle("hide-ok");e.target.textContent=document.body.classList.contains("hide-ok")?"Show all":"Show only flagged"};
document.getElementById("clear").onclick=()=>{if(confirm("Clear every mark and note on this page?")){marks={};save();location.reload()}};
document.getElementById("dl").onclick=()=>{const out={film:FILM,marks:Object.fromEntries(Object.entries(marks).filter(([k,v])=>v.marked||v.note||v.boxes.length))};
const a=document.createElement("a");a.href=URL.createObjectURL(new Blob([JSON.stringify(out,null,1)],{type:"application/json"}));a.download="marks.json";a.click()};
count();
</script></body></html>
"""


def review_page(film, panels, checks, title):
    """panels: [{"image", "beats", "shot", "prompt"}], in film order. checks: {image: {"verdict", "reason", "boxes"}} from any
    checker (boxes as 0-1 fractions). The page reads the images from the film folder, so open it from there."""
    out = film / "out" / "review"
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for p in panels:
        src = film / p["image"]
        rows.append(dict(p, src=Path("../..", p["image"]).as_posix() if src.exists() else "", check=checks.get(p["image"])))
    page = (PAGE.replace("__TITLE__", html.escape(title))
            .replace("__PANELS__", json.dumps(rows, ensure_ascii=False).replace("</", "<\\/"))
            .replace("__FILM__", json.dumps(str(film)).replace("</", "<\\/")))
    (out / "index.html").write_text(page)
    return out / "index.html"
