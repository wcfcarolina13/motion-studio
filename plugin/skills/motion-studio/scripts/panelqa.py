"""Panel quality for illustrated films: prompt lint before generation, a review sheet after it, and region repair.

The rules come from K-factory's anatomy research (2026-10-17): small step-distilled image models break hands, limbs and
multi-person scenes, no licence-safe automatic judge agrees with people better than about 60% of the time, and mflux has
no masked inpainting for the commercially licensed edit models. So prompts are linted for the known failure patterns,
a person marks faults on a contact sheet, and a fault is repaired by redrawing a crop and pasting back only the marked
box (plus a margin) with our own mask and colour match.

  lint(items)                         warnings per panel prompt (no network, no model)
  review_page(film, panels, checks)   out/review/index.html: numbered panels, checker FAIL outlines, click and drag to mark
  repair(...)                         crop, redraw with mflux, check, paste back -> candidates in repairs/<image>/rNN/
  accept(...) / revert(...)           put a candidate into the panel, or restore the panel from before it
"""
import html
import json
import os
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
WHITE_LEVEL = 235           # owner's rule (2026-11-24): grounds are warm cream, never paper white; a pixel with every
WHITE_MAX = 0.25            # channel at or over 235 counts as white, and a still over 25% white is flagged
DARK = re.compile(r"\b(?:night\w*|midnight|after dark|dusk|nightfall|moonlit|flashlight|torch(?:light)?|headlamp|lantern|"
                  r"pitch[- ]black|darkness)\b", re.I)
SKY_SAID = re.compile(r"\b(?:night sky|dark sky|black sky|starry|stars|moonless|indigo sky|navy sky|no sky|indoors|interior|"
                      r"inside)\b", re.I)
SKY_BRIGHT = re.compile(r"\b(?:daylight|sunny|sunlit|bright sky|blue sky|midday|noon|afternoon sun|morning sun)\b", re.I)
WHITE_GROUND = re.compile(r"\b(?:pure white|white background|white paper|stark white|bright white)\b", re.I)
HIDDEN = re.compile(r"pocket|behind (?:his|her|their|the) back|under the table|below the (?:frame|panel)|out of (?:frame|shot)"
                    r"|in (?:his|her|their) lap|in shadow|hidden|cropped|off[- ]frame", re.I)
CONTACT = re.compile(r"handshake|shak\w* hands|\bhug(?:s|ged|ging)?\b|embrac\w*|holding hands|hand on (?:his|her|their) (?:shoulder|arm|back|cheek)"
                     r"|grabs? (?:his|her|their) (?:arm|wrist|hand)", re.I)
# the owner's review of 85 ChatGPT stills (picture-truth.md, rule 12)
MONEY = re.compile(r"\b(?:cash|money|bills?|banknotes?|dollar notes?|coins?|wallets?|paychecks?|paycheques?|stacks? of \$|\$\d)", re.I)
HAND_ONLY = re.compile(r"\b(?:hands? only|only (?:the |his |her |their )?hands?|close[- ]up (?:of|on) (?:the |his |her |their |a )?(?:\w+ )?hands?"
                       r"|hands? (?:entering|reaching|coming) (?:in )?(?:from|into) (?:the )?(?:frame|edge)|just (?:the |a )?hands?)\b", re.I)
BODY_SAID = re.compile(r"\b(?:waist[- ]up|from behind|shoulders?|whole (?:arm|body|figure)|full figure|torso|upper body)\b", re.I)
TIPPED = re.compile(r"\b(?:tipped|tips? over|on its side|overturned|knocked over|toppled|upside[- ]down|fallen over|lying on its)\b", re.I)
# a worn thing tipped on a head is a pose, not a spill (cel test, 2026-11-28: "hard hat tipped far back")
TIPPED_WORN = re.compile(r"\b(?:hat|cap|helmet|visor|brim|beret|hood|head|chin)\s+(?:\w+\s+){0,1}(?:tipped|tips?)\b"
                         r"|\btipped\s+(?:\w+\s+){0,2}(?:back|forward|up|sideways)\b(?=[^.;]*\b(?:hat|cap|helmet|visor|brim|beret)\b)", re.I)
# "No money or coins anywhere" asks for none (cel test, 2026-11-28): a money word after no/without/never in its clause
MONEY_NEG = re.compile(r"\b(?:no|without|never|not|nor|zero|free of)\b[^.;,]*$", re.I)
INSIDE_SAID = re.compile(r"\b(?:shut|closed|latched|now (?:on|faces|points|lies)|sideways|rotated|turned with it)\b", re.I)
XRAY = re.compile(r"\b(?:hidden (?:inside|behind|under|in)|buried|underground|beneath the (?:floor|ground|surface)|inside the walls?|behind the wall)\b", re.I)
# "face hidden in black shadow" is how a faceless hero is written, not a hidden object to show X-ray style
XRAY_FACELESS = re.compile(r"\b(?:face|faces|head|eyes|features)\s+(?:\w+\s+){0,2}hidden in\b|hidden in (?:\w+\s+)?(?:shadow|shade|darkness)", re.I)
# words that strip what a thing needs to read as itself: "blank doors" took the door handles off about ten porta-potty
# stills, the owner's most repeated note (2026-11-26). These stop a run (refused()), they don't only warn.
REFUSED = [
    (re.compile(r"\b(?:blank|featureless|handleless|plain flat|bare)\s+(?:\w+\s+)?doors?\b", re.I),
     "strips the door's hardware (the model drops the handle and latch); say what the door has instead, e.g. \"a gray "
     "pull handle and a red/green occupancy dot\", as the object sheet does"),
]
XRAY_SAID = re.compile(r"\b(?:see[- ]through|x[- ]ray|cutaway|outline|transparent|ghost(?:ed)?)\b", re.I)
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


ROLE = re.compile(r"^(?:Image|Picture) \d+ (?:is|shows|gives)\b", re.I)


def sentences(prompt):
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+", prompt.strip()) if x.strip()]


def scene_words(prompt, boiler, boiler_re=None):
    """Words of the scene description: sentences that only say what a reference image is ("Image 1 is the identity
    reference for ...") and the shared style ending are fixed boilerplate, not scene."""
    return sum(len(x.split()) for x in sentences(prompt) if not ROLE.match(x) and x not in boiler and
               not x.startswith("Scale:") and not (boiler_re and boiler_re.search(x)))


def lint(items, boiler_re=None, route="klein"):
    """items: [{"id", "prompt", "shot"?, "refs"?, "plate"?, "char_in_frame"?}] -> [(id, [warnings])] for panels with any.
    A last sentence shared by at least half the prompts (the style anchor) is not counted as scene words; nor is any
    sentence matching boiler_re. route "codex" (ChatGPT images) leaves out the warnings that are about the open models'
    limits (prompt length, reference count, negative phrasing, plates) and keeps the ones about what gets drawn."""
    boiler_re = re.compile(boiler_re, re.I) if boiler_re else None
    last = [sentences(it["prompt"])[-1:] for it in items if it.get("prompt")]
    counts = {}
    for x in last:
        if x:
            counts[x[0]] = counts.get(x[0], 0) + 1
    boiler = {k for k, v in counts.items() if len(last) > 1 and v * 2 >= len(last)}
    out = []
    for it in items:
        p, w = it.get("prompt") or "", []
        if not p:
            continue
        shot = (it.get("shot") or "").upper()
        refs = it.get("refs") or []
        n_img = len(refs) + (1 if it.get("plate") else 0)
        if n_img > MAX_IMAGES and route == "klein":
            w.append(f"{n_img} reference images; keep 1-2 in one style (identity mixes above {MAX_IMAGES})")
        words = scene_words(p, boiler, boiler_re)
        if words > MAX_WORDS and route == "klein":
            w.append(f"{words} scene words; keep to {MAX_WORDS} (one action, one setting; reference lines and the style ending don't count)")
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
        if it.get("plate") and shot in PLATE_SHOTS and route == "klein":
            w.append(f"plate on a {shot} shot; its framing gets copied, leave it out or pre-crop it")
        negs = [x.group(0) for x in NEG.finditer(p) if x.group(0).lower() not in NEG_OK]
        if negs and route == "klein":
            w.append(f"negative phrasing ({', '.join(repr(x) for x in negs[:3])}); klein has no negative prompt, say what is there instead")
        dark = DARK.search(p)
        if dark and SKY_BRIGHT.search(p):
            w.append(f"\"{dark.group(0)}\" with \"{SKY_BRIGHT.search(p).group(0)}\": the light contradicts itself; pick one time of day")
        elif dark and not SKY_SAID.search(p):
            w.append(f"a dark scene (\"{dark.group(0)}\") that doesn't say its sky: models default to a bright one; say "
                     "\"night, dark navy sky, the only light is the flashlight\" or set it indoors")
        mo = next((m for m in MONEY.finditer(p) if not MONEY_NEG.search(p[max(p.rfind(".", 0, m.start()), p.rfind(";", 0, m.start()), 0):m.start()])), None)
        if mo:
            w.append(f"money in the picture (\"{mo.group(0)}\"): drawn cash and wallets read as weird (owner's rule); show the "
                     "number in a stat scene and what it buys or costs in the still")
        ho = HAND_ONLY.search(p) or (shot in ("CU", "ECU", "INS") and hands_in(p)[0] and not BODY_SAID.search(p) and re.search(r"\bhands?\b", p, re.I))
        if ho and not BODY_SAID.search(p):
            w.append("hands with no body (a hand close-up or insert): bent arms and extra hands come from the frame edge; frame the "
                     "action waist-up with the arm and shoulder in shot, or from behind")
        tp = next((m for m in TIPPED.finditer(p) if not any(wm.start() <= m.start() < wm.end() for wm in TIPPED_WORN.finditer(p))), None)
        if tp and not INSIDE_SAID.search(p):
            w.append(f"a tipped object (\"{tp.group(0)}\") that doesn't say where its insides are: say it's shut (\"door shut and "
                     "latched\") or where each inside part now lies, or the model draws them upright")
        xr = next((m for m in XRAY.finditer(p) if not any(f.start() <= m.start() < f.end() for f in XRAY_FACELESS.finditer(p))), None)
        if xr and not XRAY_SAID.search(p):
            w.append(f"a hidden thing (\"{xr.group(0)}\") with no way to show it: draw it X-ray style, the cover solid and the "
                     "thing a faint see-through outline")
        wg = WHITE_GROUND.search(p)
        if wg:
            w.append(f"\"{wg.group(0)}\": grounds are warm cream, not white (owner's rule); say \"warm cream paper\"")
        for m, why in refused(p):
            w.append(f"REFUSED \"{m}\": {why}")
        if w:
            out.append((it.get("id"), w))
    return out


def refused(prompt):
    """[(matched words, why)] for phrases no still prompt may use (REFUSED); runners stop before any model call."""
    return [(m.group(0), why) for rx, why in REFUSED for m in rx.finditer(prompt or "")]


def white_share(path):
    """The share of a still's pixels that are near-white (every channel at or over WHITE_LEVEL), on a 160x90 copy."""
    a = read_rgb(path, 160, 90)
    return float((a.min(axis=2) >= WHITE_LEVEL).mean())


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


def context_square(box, W, H, context, min_side=0):
    """A square around the box, context times its longer side (at least min_side), moved (not shrunk) to stay inside the image."""
    x, y, w, h = box
    side = int(min(max(max(w, h) * context, min_side), W, H))
    cx, cy = x + w / 2, y + h / 2
    sx = int(min(max(cx - side / 2, 0), W - side))
    sy = int(min(max(cy - side / 2, 0), H - side))
    return sx, sy, side


def grow(box, margin, W, H):
    x, y, w, h = box
    x0, y0, x1, y1 = max(0, x - margin), max(0, y - margin), min(W, x + w + margin), min(H, y + h + margin)
    return x0, y0, x1 - x0, y1 - y0


def open_sides(region, W, H):
    """Sides of the region that lie on the panel border: there is nothing to blend into, so they get no ramp."""
    x, y, w, h = region
    return "".join(s for s, on in (("l", x == 0), ("t", y == 0), ("r", x + w == W), ("b", y + h == H)) if on)


def feather_mask(h, w, box, feather):
    """Repairs logged before 2026-10-18: 1 inside the box, falling linearly to 0 over `feather` pixels outside it. Kept so
    `accept` reproduces those candidates exactly; new repairs use blend_mask."""
    x, y, bw, bh = box
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dx = np.maximum(np.maximum(x - xx, xx - (x + bw - 1)), 0)
    dy = np.maximum(np.maximum(y - yy, yy - (y + bh - 1)), 0)
    d = np.hypot(dx, dy)
    return np.clip(1 - d / max(feather, 1e-6), 0, 1)[..., None] if feather > 0 else (d == 0).astype(np.float32)[..., None]


def blend_mask(h, w, region, feather, sides=""):
    """0 at the region's edge, rising (smoothstep, so no visible ramp edge) to 1 at `feather` pixels inside it. The whole
    blend is inside the region, so nothing outside it changes; with the default margin the marked box itself is fully replaced."""
    x, y, bw, bh = region
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    far = np.full((h, w), 1e9, np.float32)
    d = np.minimum.reduce([far if "l" in sides else xx - x + 0.5, far if "r" in sides else x + bw - 0.5 - xx,
                           far if "t" in sides else yy - y + 0.5, far if "b" in sides else y + bh - 0.5 - yy])
    if feather <= 0:
        return (d > 0).astype(np.float32)[..., None]
    t = np.clip(d / feather, 0, 1)
    return (t * t * (3 - 2 * t))[..., None]


def ring(h, w, region, width):
    """Pixels outside the region and within `width` of it: what the model redrew but the paste keeps from the original."""
    x, y, bw, bh = region
    yy, xx = np.mgrid[0:h, 0:w]
    near = (xx >= x - width) & (xx < x + bw + width) & (yy >= y - width) & (yy < y + bh + width)
    inside = (xx >= x) & (xx < x + bw) & (yy >= y) & (yy < y + bh)
    return near & ~inside


def luma(a):
    return a @ np.array([0.299, 0.587, 0.114], np.float32)


def gradient(a):
    l = luma(a)
    gx, gy = np.zeros_like(l), np.zeros_like(l)
    gx[:, 1:-1], gy[1:-1] = (l[:, 2:] - l[:, :-2]) / 2, (l[2:] - l[:-2]) / 2
    return np.hypot(gx, gy)


def sharpness_at(a, where):
    """Variance of the Laplacian over a set of pixels: how much fine detail (line edges, grain) they hold."""
    l = luma(a)
    lap = np.zeros_like(l)
    lap[1:-1, 1:-1] = 4 * l[1:-1, 1:-1] - l[:-2, 1:-1] - l[2:, 1:-1] - l[1:-1, :-2] - l[1:-1, 2:]
    return float(lap[where].var())


EDGE = 40           # luma gradient of an ink line (a black line on white reads about 120)
RING = 20           # px outside the paste used to match colour (K-factory's measurement band)
NO_EDIT = 60        # levels a pixel must move to count as changed


def crossings(base, region):
    """Strong lines in the original that cross each edge of the paste: where the redraw ends and the old drawing goes on."""
    g = gradient(base) > EDGE
    H, W = g.shape
    x, y, w, h = region
    lines = {"left": g[y:y + h, x] if x > 0 else None, "right": g[y:y + h, x + w - 1] if x + w < W else None,
             "top": g[y, x:x + w] if y > 0 else None, "bottom": g[y + h - 1, x:x + w] if y + h < H else None}
    return {k: int((v & ~np.concatenate([[False], v[:-1]])).sum()) for k, v in lines.items() if v is not None}


def blur(a, r=2):
    """A (2r+1)-pixel box blur, so a line the model moved by a pixel or two does not count as new content."""
    out = a.copy()
    for ax in (0, 1):
        src = out.copy()
        out = sum(np.roll(src, k, ax) for k in range(-r, r + 1)) / (2 * r + 1)
    return out


def color_match(new, old, band):
    """Per-channel gain and offset that make the redraw's colours match the original on the band around the paste
    (klein shifts a whole crop's colour, red by -6 to -18 in K-factory's runs). Fitted twice: the second fit leaves out
    band pixels the model actually redrew, so new content there does not skew it. The gain is held to 0.8-1.25 so a
    band full of lineart cannot stretch the contrast."""
    ident = [1.0, 1.0, 1.0], [0.0, 0.0, 0.0]
    use = band & (np.abs(blur(new) - blur(old)).max(-1) < 50)      # a colour cast is under 50; drawn content is not
    for _ in range(2):
        if use.sum() < 200:
            return ident
        g, o = new[use], old[use]
        so, sg = o.std(0), g.std(0)
        gain = np.where(np.minimum(so, sg) > 4, np.clip(so / np.maximum(sg, 1e-6), 0.8, 1.25), 1.0)    # flat band: shift only
        off = o.mean(0) - gain * g.mean(0)
        use = band & (np.abs(blur(new * gain + off) - blur(old)).max(-1) < 30)
    return [round(float(v), 4) for v in gain], [round(float(v), 2) for v in off]


def paste(base, new, crop, region, feather, sides):
    """Blend a redrawn crop (panel scale, colour already matched) into a panel array inside the region only."""
    sx, sy, side = crop
    mask = blend_mask(side, side, (region[0] - sx, region[1] - sy, region[2], region[3]), feather, sides)
    res = base.copy()
    res[sy:sy + side, sx:sx + side] = res[sy:sy + side, sx:sx + side] * (1 - mask) + new * mask
    return res


def redrawn(out_png, side, gain=(1, 1, 1), off=(0, 0, 0)):
    return read_rgb(out_png, side, side) * np.array(gain, np.float32) + np.array(off, np.float32)


SHARPEN_BELOW = 0.85                    # a redraw under this share of the panel's detail is sharpened to match
SHARPEN_STEPS = (0.5, 1.0, 1.5, 2.0, 3.0)


def unsharp(a, amount, r=1):
    """Unsharp mask: add back `amount` times the detail a (2r+1)-pixel blur removes."""
    return np.clip(a + amount * (a - blur(a, r)), 0, 255) if amount else a


def sharpen_to(new, detail):
    """The unsharp amount that brings a soft redraw's fine detail closest to the panel's (detail(a) is the ratio to the
    panel on the paste margin). The two A/Bs of 2026-11-27 pasted at 0.51-0.57 of the panel's detail, which reads soft."""
    d0 = detail(new)
    if d0 >= SHARPEN_BELOW:
        return 0.0, d0
    best = min(((abs(detail(unsharp(new, k)) - 1.0), k) for k in SHARPEN_STEPS))
    k = best[1]
    return k, detail(unsharp(new, k))


def composite(base, out_png, crop, box, feather):
    """The paste used for repairs logged before 2026-10-18 (feather outside the box, no colour match)."""
    sx, sy, side = crop
    new = read_rgb(out_png, side, side)
    mask = feather_mask(side, side, (box[0] - sx, box[1] - sy, box[2], box[3]), feather)
    res = base.copy()
    res[sy:sy + side, sx:sx + side] = res[sy:sy + side, sx:sx + side] * (1 - mask) + new * mask
    return res


# Repair routes. ChatGPT images through codex is the default: the owner's rule (2026-11-26, picture-truth.md rule 15) is
# that local image models are not good enough for finished pictures, so the mflux edit models below are drafts only.
# They run only with draft=True (`repair --draft-local`), and a panel with an accepted local repair is refused at
# render (longform.draft_stills reads repairs/log.json), so the default can't slip back to them.
CHATGPT = "chatgpt"
ROUTES = (CHATGPT, "klein", "qwen")
CHATGPT_TRIES = 2   # calls per take: a non-square answer is asked again once
GENERATORS = {      # mflux 0.23 CLIs; neither takes a mask, so the crop is redrawn whole and the paste confines it
    "klein": ["mflux-generate-flux2-edit", "--model", "flux2-klein-4b"],
    "qwen": ["mflux-generate-qwen-edit", "--model", "qwen-image-edit-2511", "-q", "6", "--low-ram"],
}
CROP = 1024         # largest generation size; both models are trained around one megapixel
MIN_SIDE = 640      # smallest crop; a smaller box gets more context rather than an enlarged crop


def config():
    path = Path.home() / ".motion-studio.json"
    return json.loads(path.read_text()) if path.exists() else {}


def generator(model):
    """The command for one edit model: the mflux binary (from "mflux": {"bin": "<venv>/bin"} in ~/.motion-studio.json or
    $MFLUX_BIN, else PATH) and its model arguments ("mflux": {"klein": ["--model", "<local q8 folder>", "--base-model",
    "flux2-klein-4b"]} replaces the defaults, so a local quantized build is used instead of downloading the full weights)."""
    cfg = config().get("mflux") or {}
    name, *args = GENERATORS[model]
    bindir = os.environ.get("MFLUX_BIN") or cfg.get("bin")
    exe = Path(bindir).expanduser() / name if bindir else shutil.which(name)
    if not exe or not Path(exe).exists():
        where = f"{bindir}/{name}" if bindir else f"{name} on PATH"
        raise SystemExit(f"no {where}: install mflux 0.23 or later (pip install -U mflux, MIT), or set \"mflux\": "
                         "{\"bin\": \"<venv>/bin\"} in ~/.motion-studio.json")
    return [str(exe), *cfg.get(model, args)]


def esrgan():
    """realesrgan-ncnn-vulkan (BSD-3), the same binary `upscale` uses: $REALESRGAN_BIN, "realesrgan" in
    ~/.motion-studio.json, or PATH. None if absent."""
    b = os.environ.get("REALESRGAN_BIN") or config().get("realesrgan") or shutil.which("realesrgan-ncnn-vulkan")
    return str(Path(b).expanduser()) if b and Path(b).expanduser().exists() else None


def make_crop(src, crop, gen, dest):
    """The square the model sees, at gen x gen. A crop smaller than that is enlarged with Real-ESRGAN first when it is
    installed: a Lanczos-enlarged crop is soft, and the model copies the softness into its redraw."""
    sx, sy, side = crop
    cut = f"crop={side}:{side}:{sx}:{sy}"
    if side == gen:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", cut, "-frames:v", "1", str(dest)], check=True)
        return "none"
    binary = esrgan() if side * 1.2 < gen else None
    if binary:
        native, big = dest.with_name("crop-native.png"), dest.with_name("crop-x4.png")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", cut, "-frames:v", "1", str(native)], check=True)
        subprocess.run([binary, "-i", str(native), "-o", str(big), "-n", "realesrgan-x4plus-anime", "-s", "4"],
                       capture_output=True, check=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(big), "-vf", f"scale={gen}:{gen}:flags=lanczos",
                        "-frames:v", "1", str(dest)], check=True)
        native.unlink(); big.unlink()
        return "realesrgan"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", f"{cut},scale={gen}:{gen}:flags=lanczos",
                    "-frames:v", "1", str(dest)], check=True)
    return "lanczos"


def repair_ask(prompt, side, ref=False, faceless=False):
    """The ChatGPT edit instruction for one crop: keep the picture, change only the fault, same square size."""
    import codex_stills
    parts = ["Edit the first attached picture. It is a square crop of a larger illustration, and it will be pasted back "
             "into that illustration, so keep it the same picture: the same composition, framing, line work, colours, "
             "paper texture and light, with everything outside the part to fix in the same place. Change only this: "
             + prompt.strip().rstrip(".") + "."]
    if ref:
        parts.append("The second attached picture is a reference for how that part should look. Do not copy it in whole.")
    parts.append(f"Make exactly one square image, {side}x{side}, the same size as the first attached picture, and save it "
                 "as image.png in the current folder.")
    parts.append("No writing, letters, numbers, labels or signage anywhere in the picture, and no border or frame.")
    parts.append(codex_stills.HANDS)
    if faceless:
        parts.append("No human face may be visible anywhere: show people from behind or cropped at the shoulders. This is a hard rule.")
    return " ".join(parts)


MARK_RGB = (255, 0, 0)   # the outline drawn on the panel for a markup repair
MARK_W = 6


def repair_markup_ask(prompt, W, H, ref=False, faceless=False):
    """The ChatGPT instruction for a markup repair: the whole panel with the fault outlined in red, plus the clean panel."""
    import codex_stills
    # the clean panel goes first: only the first input gets the model's extra fidelity (OpenAI's image guide), and the
    # finished picture must not carry the marker; image 2 only says where
    parts = ["Image 1 is the illustration to edit. Image 2 is the same illustration with a red rectangle drawn on it to "
             "show where the change goes. Edit image 1: change only what lies inside the area the red rectangle marks, and "
             "keep everything outside it exactly as it is, with the same composition, framing, line work, colours, paper "
             "texture and light. The change: " + prompt.strip().rstrip(".") + ". The finished picture has no red rectangle."]
    if ref:
        parts.append("Image 3 is a reference for how that part should look. Do not copy it in whole.")
    parts.append(f"Make exactly one image, {W}x{H}, the same size and shape as image 1, and save it as image.png in the "
                 "current folder.")
    parts.append("No writing, letters, numbers, labels or signage anywhere in the picture, and no border or frame.")
    parts.append(codex_stills.HANDS)
    if faceless:
        parts.append("No human face may be visible anywhere: show people from behind or cropped at the shoulders. This is a hard rule.")
    return " ".join(parts)


def draw_mark(base, region, dest):
    """The panel with a red rectangle around the paste region (drawn just outside it, so it never lands in the paste)."""
    a = np.clip(base + 0.5, 0, 255).astype(np.uint8).copy()
    H, W = a.shape[:2]
    x, y, w, h = region
    x0, y0, x1, y1 = max(0, x - MARK_W), max(0, y - MARK_W), min(W, x + w + MARK_W), min(H, y + h + MARK_W)
    a[y0:y0 + MARK_W, x0:x1] = MARK_RGB; a[y1 - MARK_W:y1, x0:x1] = MARK_RGB
    a[y0:y1, x0:x0 + MARK_W] = MARK_RGB; a[y0:y1, x1 - MARK_W:x1] = MARK_RGB
    write_rgb(a.astype(np.float32), dest)


def outside_likeness(a, b, region, step=4):
    """Correlation of two panels' gray values outside the region (sampled every `step` px): 1.0 when nothing outside moved."""
    g = [(x[::step, ::step] @ np.array([0.299, 0.587, 0.114], np.float32)) for x in (a, b)]
    m = np.ones(g[0].shape, bool)
    x, y, w, h = region
    m[y // step:(y + h) // step + 1, x // step:(x + w) // step + 1] = False
    u, v = g[0][m] - g[0][m].mean(), g[1][m] - g[1][m].mean()
    den = float(np.sqrt((u * u).sum() * (v * v).sum()))
    return round(float((u * v).sum() / den), 3) if den else 0.0


def fit_full(out, W, H, base, region):
    """A whole-panel answer mapped onto the panel: resized when it came back at the panel's shape; otherwise the better
    aligned of a stretch and a centre crop (by likeness outside the region). Returns (array, how, likeness)."""
    w, h = size(out)
    tries = [("resized", read_rgb(out, W, H))]
    if abs(w / h - W / H) > 0.01:
        cw, ch = (round(h * W / H), h) if w / h > W / H else (w, round(w * H / W))
        tmp = Path(str(out) + ".fit.png")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(out), "-vf", f"crop={cw}:{ch},scale={W}:{H}:flags=lanczos",
                        "-frames:v", "1", str(tmp)], check=True)
        tries = [("stretched", tries[0][1]), ("centre-cropped", read_rgb(tmp))]
        tmp.unlink()
    scored = [(outside_likeness(a, base, region), how, a) for how, a in tries]
    k, how, a = max(scored, key=lambda x: x[0])
    return a, how, k


def red_left(new, old, mask):
    """Share of the masked pixels that came back marker-red where the panel was not (the outline left in)."""
    def red(a):
        return (a[..., 0] > 170) & (a[..., 1] < 80) & (a[..., 2] < 80)
    return round(float((red(new) & ~red(old))[mask].mean()), 4) if mask.any() else 0.0


def draft_repairs(film):
    """{image: model} for panels whose accepted repair came from a local (draft-only) model."""
    log_path = Path(film) / "repairs" / "log.json"
    log = json.loads(log_path.read_text()) if log_path.exists() else []
    return {e["image"]: e["model"] for e in log if e.get("status") == "accepted" and e.get("model") in GENERATORS}


METHODS = ("crop", "markup", "whole")
# paste margin and feather per method: a whole-panel answer is redrawn everywhere, so its paste gets a wider blend to hide
# the seam (the first A/B, 2026-11-27: the whole edit fixed merged roofs; pasted at 16 px it left a faint seam at the roof)
PASTE = {"crop": (32, 16), "markup": (32, 16), "whole": (64, 48)}


def repair(film, img, box, prompt, *, ref=None, model=CHATGPT, seeds=(1,), context=2.25, margin=None, feather=None,
           color=True, min_change=0.10, gen_size=None, gen_args=(), beats=(), draft=False, allow_codex=False, faceless=False,
           method="crop", sharpen=True):
    """Redraw one region of a panel. The paste covers the box grown by `margin`, blends over `feather` px inside that, and
    matches the redraw's colour to the original around it. Writes repairs/<image path>/rNN/: crop.png (the square sent
    to the model), out_<seed>.png (what came back), compare_<seed>.png (the crop before and after) and, unless the seed
    changed almost nothing, candidate_<seed>.png (the whole panel with only the region changed). Logs the run, with
    per-seed checks, in repairs/log.json. The panel itself is untouched until `accept`."""
    src = film / img
    if not src.exists():
        raise SystemExit(f"no panel image {img}")
    if model not in ROUTES:
        raise SystemExit(f"no repair route {model}: {', '.join(ROUTES)}")
    if method not in METHODS:
        raise SystemExit(f"no repair method {method}: {', '.join(METHODS)}")
    if method != "crop" and model != CHATGPT:
        raise SystemExit(f"the {method} method sends the whole panel; only the ChatGPT route takes it")
    margin = PASTE[method][0] if margin is None else margin
    feather = PASTE[method][1] if feather is None else feather
    if model in GENERATORS and not draft:
        raise SystemExit(f"{model} is a local image model, and local models are drafts only (owner's rule, picture-truth.md "
                         "rule 15): repair with ChatGPT (the default), or pass --draft-local for a draft the render will refuse")
    if model == CHATGPT:
        import codex_stills
        if not codex_stills.allowed(allow_codex):
            raise SystemExit("the ChatGPT repair spends the owner's Codex allowance (one image per take): pass --allow-codex "
                             "once he has said yes")
        stop = [f'"{m}" {why}' for m, why in refused(prompt)]
        if stop:
            raise SystemExit("refused prompt words, nothing run:\n  " + "\n  ".join(stop))
        if gen_args:
            raise SystemExit("--gen-args are mflux arguments; the ChatGPT route takes none")
    gen_cmd = generator(model) if model in GENERATORS else None
    W, H = size(src)
    b = parse_box(box, W, H)
    region = grow(b, margin, W, H)
    sides = open_sides(region, W, H)
    # The crop holds the region and the colour band around it. It is generated at its own size when it fits (any resize
    # there and back halves the fine detail: a Lanczos round trip alone measured 0.48 on a klein panel), so a small box
    # gets a wider crop instead of an enlarged one. Only a crop over CROP, or a forced gen_size, is resized.
    sx, sy, side = context_square(b, W, H, context, max(max(region[2:]) + 2 * (RING + 4), MIN_SIDE))
    if side <= CROP and not gen_size:
        side = side // 16 * 16          # mflux sizes are multiples of 16; trimming a few pixels keeps the crop native
        sx, sy = min(sx, W - side), min(sy, H - side)
    gen = gen_size or min(CROP, side)
    key = Path(img).with_suffix("").as_posix()
    root = film / "repairs" / key
    n = 1 + max([int(p.name[1:]) for p in root.glob("r[0-9]*") if p.name[1:].isdigit()] or [0])
    rdir = root / f"r{n:02d}"
    rdir.mkdir(parents=True)
    base = read_rgb(src)
    cross = crossings(base, region)
    crop = rdir / "crop.png"
    enlarged = make_crop(src, (sx, sy, side), gen, crop)
    images = [str(crop)] + ([str(Path(ref).resolve())] if ref else [])
    t0 = time.time()
    failures = {}
    whole = {}
    if model == CHATGPT and method == "whole":
        # the whole panel goes with "change only ..." (the first A/B's winner); the answer is mapped back onto the panel
        # and only the same crop is kept, so the paste (wider feather), colour match and checks are the crop route's
        import codex_stills
        ask = codex_stills.ask_edit({"change": prompt}, faceless, W, H)
        for x in seeds:
            full = rdir / f"full_{x}.png"
            why = codex_stills.edit_image(ask, [str(src)] + ([str(Path(ref).resolve())] if ref else []), full)
            if why:
                print(f"  take {x}: {why}"); failures[str(x)] = why
                continue
            arr, how, k = fit_full(full, W, H, base, region)
            whole[str(x)] = {"fit": how, "outside_likeness": k, "size": list(size(full))}
            write_rgb(arr[sy:sy + side, sx:sx + side], rdir / f"out_{x}.png")
    elif model == CHATGPT and method == "markup":
        # the whole panel goes, with the paste region outlined in red, plus the clean panel; the answer is mapped back onto
        # the panel and only the same crop is kept, so the paste, colour match and checks below are the crop route's
        mark = rdir / "markup.png"
        draw_mark(base, region, mark)
        ask = repair_markup_ask(prompt, W, H, ref=bool(ref), faceless=faceless)
        for x in seeds:
            full = rdir / f"full_{x}.png"
            why = codex_stills.edit_image(ask, [str(src), str(mark)] + ([str(Path(ref).resolve())] if ref else []), full)
            if why:
                print(f"  take {x}: {why}"); failures[str(x)] = why
                continue
            arr, how, k = fit_full(full, W, H, base, region)
            whole[str(x)] = {"fit": how, "outside_likeness": k, "size": list(size(full))}
            write_rgb(arr[sy:sy + side, sx:sx + side], rdir / f"out_{x}.png")
    elif model == CHATGPT:
        # the crop itself is the edit base; ChatGPT has no seed, so each "seed" is one independent take
        ask = repair_ask(prompt, gen, ref=bool(ref), faceless=faceless)
        for x in seeds:
            out = rdir / f"out_{x}.png"
            for t in range(CHATGPT_TRIES):
                why = codex_stills.edit_image(ask, images, out)
                if why is None:
                    w, h = size(out)
                    if abs(w / h - 1) <= 0.02:
                        failures.pop(str(x), None)
                        break
                    why = f"came back {w}x{h}, not square"
                    out.unlink()
                print(f"  take {x} try {t + 1}: {why}")
                failures[str(x)] = why
        with open(film / "repairs" / "usage.log", "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\trepair {key}\tchatgpt via codex\t{len(seeds)} takes\n")
    else:
        cmd = [*gen_cmd, "--image-paths", *images, "--prompt", prompt, "--seed", *[str(s) for s in seeds],
               "--width", str(gen), "--height", str(gen), "--output", str(rdir / "out.png"), *gen_args]
        subprocess.run(cmd, check=True)
        for x in seeds:     # mflux writes out.png for one seed and out_seed_<n>.png for several; name them out_<n>.png
            for got in ([rdir / "out.png"] if len(seeds) == 1 else []) + [rdir / f"out_seed_{x}.png"]:
                if got.exists():
                    got.rename(rdir / f"out_{x}.png")
                    break
    secs = round(time.time() - t0, 1)
    old = base[sy:sy + side, sx:sx + side]
    rc = (region[0] - sx, region[1] - sy, region[2], region[3])
    band = ring(side, side, rc, RING)
    near = ring(side, side, rc, 12)
    bx, by = b[0] - sx, b[1] - sy
    inbox = (slice(by, by + b[3]), slice(bx, bx + b[2]))
    # detail is compared on the margin (pasted, but outside the fault, so old and new should show the same drawing)
    rim = ring(side, side, (bx, by, b[2], b[3]), margin) & ~ring(side, side, rc, side) if margin >= 8 else np.zeros((side, side), bool)
    rim = rim if rim.sum() >= 400 else np.zeros((side, side), bool)
    rim[inbox] |= not rim.any()

    def detail(a):
        return round(sharpness_at(a, rim) / max(sharpness_at(old, rim), 1e-6), 2)

    trip = None if gen == side else detail(read_rgb(crop, side, side))
    made, checks = [], {}
    for s in seeds:
        out = rdir / f"out_{s}.png"
        if not out.exists():
            print(f"  seed {s}: the generator wrote no {out.name}")
            continue
        gain, off = color_match(read_rgb(out, side, side), old, band) if color else ([1.0] * 3, [0.0] * 3)
        new = redrawn(out, side, gain, off)
        amount, _ = sharpen_to(new, detail) if sharpen else (0.0, None)
        new = unsharp(new, amount)
        change = float((np.abs(new[inbox] - old[inbox]).max(-1) > NO_EDIT).mean())
        # what the model drew just outside the paste: the paste drops it, so a shape there ends in a cut-off edge
        overflow = float((np.abs(blur(new) - blur(old)).max(-1) > 50)[near].mean()) if near.any() else 0.0
        sharp = detail(new)
        notes = []
        if method == "whole" and whole[str(s)]["outside_likeness"] < 0.9:
            notes.append(f"the model redrew the panel well outside the fix (likeness {whole[str(s)]['outside_likeness']}); "
                         "only the region is pasted, so check the seam")
        if method == "markup":
            pm = np.zeros((side, side), bool)
            pm[max(0, rc[1]):rc[1] + rc[3], max(0, rc[0]):rc[0] + rc[2]] = True
            whole[str(s)]["red_left"] = red_left(new, old, pm)
            if whole[str(s)]["red_left"] > 0.002:
                notes.append(f"the red outline is left in ({whole[str(s)]['red_left']:.1%} of the paste is marker red)")
            if whole[str(s)]["outside_likeness"] < 0.9:
                notes.append(f"the model redrew the panel outside the outline (likeness {whole[str(s)]['outside_likeness']}); "
                             "only the outlined region is pasted, so check the seam")
        if min_change and change < min_change:
            notes.append(f"no edit ({change:.0%} of the box changed)")
        if overflow > 0.03:
            notes.append(f"the redraw runs past the paste ({overflow:.0%} of the band just outside it is new): grow --box or --margin")
        if not 0.7 <= sharp <= 1.4:
            notes.append(f"{'softer' if sharp < 0.7 else 'sharper'} than the panel (detail ratio {sharp}"
                         + (f"; the resize alone gives {trip}" if trip is not None else "") + ")")
        checks[str(s)] = {"change": round(change, 3), "overflow": round(overflow, 3), "sharpness": sharp, "sharpen": amount,
                          "gain": gain, "offset": off, "notes": notes}
        if min_change and change < min_change:     # kept for a look (the raw redraw beside the crop), not offered
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-i", str(out), "-filter_complex",
                            f"[0]crop={side}:{side}:{sx}:{sy},scale=768:768[a];[1]scale=768:768[b];[a][b]hstack",
                            "-frames:v", "1", str(rdir / f"compare_{s}.png")], check=True)
            continue
        cand = paste(base, new, (sx, sy, side), region, feather, sides)
        write_rgb(cand, rdir / f"candidate_{s}.png")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-i", str(rdir / f"candidate_{s}.png"), "-filter_complex",
                        f"[0]crop={side}:{side}:{sx}:{sy},scale=768:768[a];[1]crop={side}:{side}:{sx}:{sy},scale=768:768[b];"
                        "[a][b]hstack", "-frames:v", "1", str(rdir / f"compare_{s}.png")], check=True)
        made.append(s)
    log_path = film / "repairs" / "log.json"
    log = json.loads(log_path.read_text()) if log_path.exists() else []
    entry = {"id": f"{key}/r{n:02d}", "image": img, "beats": list(beats), "box": list(b), "region": list(region),
             "margin": margin, "feather": feather, "open_sides": sides, "crop": [sx, sy, side], "gen_size": gen, "resize_detail": trip,
             "enlarged_with": enlarged, "color_match": color, "crossings": cross, "prompt": prompt, "model": model,
             "ref": ref, "seeds": list(seeds), "candidates": made, "checks": checks, "seconds": secs,
             "draft": model in GENERATORS, "failures": failures, "method": method, "full": whole,
             "at": time.strftime("%Y-%m-%d %H:%M"), "status": "candidate" if made else "failed"}
    log.append(entry)
    log_path.write_text(json.dumps(log, indent=1, ensure_ascii=False) + "\n")
    return rdir, made, secs, entry


def _entry(film, rid):
    log_path = film / "repairs" / "log.json"
    log = json.loads(log_path.read_text()) if log_path.exists() else []
    for e in log:
        if e["id"] == rid:
            return log, log_path, e
    raise SystemExit(f"no repair {rid} in {log_path}")


def png_stamps(path):
    """A PNG panel's text entries (model, original, anatomy verdict), which an ffmpeg rewrite would drop."""
    if Path(path).suffix.lower() != ".png":
        return {}
    from PIL import Image
    with Image.open(path) as im:
        return dict(im.text or {})


def restamp(path, stamps, draft=None):
    """Put a panel's text entries back after a paste; a local-model repair also marks it a draft (render refuses it)."""
    if Path(path).suffix.lower() != ".png" or not (stamps or draft):
        return
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo
    stamps = dict(stamps)
    if draft:
        stamps.update({"motion-studio-draft": "1", "motion-studio-model": f"{draft} repair (local, drafts only)"})
    info = PngInfo()
    for k, v in stamps.items():
        info.add_text(k, v)
    im = Image.open(path); im.load()
    im.save(str(path) + ".part", format="PNG", pnginfo=info); os.replace(str(path) + ".part", path)


def accept(film, rid, seed=None):
    """Paste a repair's region into the panel as it is now (so two repairs of one panel both survive, in either order).
    The panel as it was goes to before.<ext> in the repair folder first, so `revert` can restore it. The upscaled copy is
    now older than the panel, so `timeline` asks for `upscale` again."""
    log, log_path, e = _entry(film, rid)
    if e.get("status") == "accepted":
        raise SystemExit(f"{rid} is already in the panel; `revert` it first to accept another seed")
    rdir, src = film / "repairs" / rid, film / e["image"]
    if e.get("whole"):          # a whole-panel edit (the Gemini route): the candidate replaces the panel as it is
        out = next((p for p in sorted(rdir.glob("out_1.*")) if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")), None)
        if not out or (seed or 1) != 1:
            raise SystemExit(f"{rid} has no output to accept")
        shutil.copy2(src, rdir / ("before" + src.suffix))
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(out), "-frames:v", "1",
                        *(["-q:v", "2"] if src.suffix.lower() in (".jpg", ".jpeg") else []), str(src)], check=True)
        seq = 1 + max([x.get("accepted_seq", 0) for x in log] or [0])
        e.update(status="accepted", accepted_seed=1, accepted_seq=seq, accepted_at=time.strftime("%Y-%m-%d %H:%M"))
        log_path.write_text(json.dumps(log, indent=1, ensure_ascii=False) + "\n")
        return src
    have = [x for x in e["seeds"] if (rdir / f"out_{x}.png").exists()]     # also outputs renamed by hand after a run
    offered = [x for x in e.get("candidates", have) if x in have]
    seed = seed if seed is not None else (offered[0] if len(offered) == 1 else None)
    if seed is None or seed not in have:
        raise SystemExit(f"{rid} has outputs for seeds {have} (candidates {offered}): pass --seed")
    shutil.copy2(src, rdir / ("before" + src.suffix))
    tmp = rdir / "accepted.png"
    base, out = read_rgb(src), rdir / f"out_{seed}.png"
    if "region" not in e:       # logged before the inside feather
        write_rgb(composite(base, out, e["crop"], e["box"], e["feather"]), tmp)
    else:
        sx, sy, side = e["crop"]
        c = e.get("checks", {}).get(str(seed))
        if c:
            gain, off = c["gain"], c["offset"]
        elif e.get("color_match"):      # an output added by hand: match it now
            x, y, w, h = e["region"]
            gain, off = color_match(read_rgb(out, side, side), base[sy:sy + side, sx:sx + side],
                                    ring(side, side, (x - sx, y - sy, w, h), RING))
        else:
            gain, off = [1.0] * 3, [0.0] * 3
        new = unsharp(redrawn(out, side, gain, off), (c or {}).get("sharpen", 0.0))
        write_rgb(paste(base, new, e["crop"], e["region"], e["feather"], e.get("open_sides", "")), tmp)
    keep = png_stamps(src)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(tmp), "-frames:v", "1",
                    *(["-q:v", "2"] if src.suffix.lower() in (".jpg", ".jpeg") else []), str(src)], check=True)
    restamp(src, keep, draft=e.get("model") in GENERATORS and e.get("model"))
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
