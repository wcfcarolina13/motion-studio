"""The take gate (picture-truth.md rule 16.4): before the owner sees a take, someone looks at it at full size against
the note's own question, with a scale ruler where size matters, and records the verdict. The review dashboard shows
a take made after the rule only once it has passed; a take redrawn under the same name needs a new check.

    studio.py gate <film> <take path> --question "Is the gate as tall as the fence?" --pass|--fail [--why "..."]
                  [--ruler x,ground_y,ref_y,ref_ft]       draws a foot ruler on a full-size copy to look at first
    studio.py gate <film> --list                          takes still waiting for the check

Gates live in review/gates.json: {path: {"ok", "question", "why", "mtime", "at", "ruler"?}}. The ruler is drawn at
column x from the ground line up, scaled so ref_y (a thing of known height, e.g. the top of a 7 ft door) sits at ref_ft;
ticks every half foot, labelled every foot, so a handle can be read against 3.5 ft. Ruler images go to review/gates/.
"""
import calendar, hashlib, json, time
from pathlib import Path

# takes made before the rule are shown as before; the rule binds takes made from here on (2026-11-27 02:10 UTC)
SINCE = calendar.timegm((2026, 11, 27, 2, 10, 0))
FILE = "review/gates.json"


def load(film):
    p = Path(film) / FILE
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def status(film, path, mtime, gates=None):
    """'old' (made before the rule), 'pass', 'fail', or 'unchecked' (never checked, or redrawn since)."""
    if mtime and mtime < SINCE:
        return "old"
    g = (gates if gates is not None else load(film)).get(path)
    if not g or abs(g.get("mtime", 0) - (mtime or 0)) > 0.01:
        return "unchecked"
    return "pass" if g["ok"] else "fail"


def ruler(film, path, x, ground, ref_y, ref_ft):
    """A full-size copy of the take with a foot ruler at column x. Returns its path."""
    from PIL import Image, ImageDraw
    src = Path(film) / path
    im = Image.open(src).convert("RGB")
    d = ImageDraw.Draw(im)
    px = (ground - ref_y) / float(ref_ft)
    if px <= 0:
        raise SystemExit("--ruler: ref_y must be above the ground line (smaller y)")
    top = max(0, int(ground - px * (ref_ft + 1)))
    for xx, col in ((x - 1, (0, 0, 0)), (x, (255, 255, 0)), (x + 1, (0, 0, 0))):
        d.line((xx, ground, xx, top), fill=col, width=1)
    k = 0
    while ground - k * px / 2 >= top:
        y = ground - k * px / 2
        w = 18 if k % 2 == 0 else 9
        d.line((x - w, y, x + w, y), fill=(255, 255, 0), width=3)
        if k % 2 == 0:
            d.rectangle((x + 22, y - 10, x + 62, y + 10), fill=(0, 0, 0)); d.text((x + 26, y - 6), f"{k // 2} ft", fill=(255, 255, 0))
        k += 1
    out = Path(film) / "review" / "gates" / (hashlib.sha1(path.encode()).hexdigest()[:12] + ".png")
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out)
    return out


def record(film, path, question, ok, why="", ruler_args=None):
    film = Path(film)
    src = film / path
    if not src.exists():
        raise SystemExit(f"no take {path} in {film}")
    if not question or not question.strip():
        raise SystemExit("--question: the note's own question, e.g. \"Is the handle at 3.5 ft?\"")
    gates = load(film)
    e = {"ok": bool(ok), "question": question.strip(), "why": why or "", "mtime": round(src.stat().st_mtime, 3),
         "at": time.strftime("%Y-%m-%d %H:%M")}
    if ruler_args:
        e["ruler"] = list(ruler_args)
    gates[path] = e
    p = film / FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(gates, indent=1, ensure_ascii=False) + "\n")
    return e


def waiting(film, groups):
    gates = load(film)
    return [(g["shot"], t["path"]) for g in groups for t in g["takes"] if status(film, t["path"], t.get("mtime"), gates) == "unchecked"]
