"""Dead space in panel stills, and what the voice says over it (owner, 2026-11-27: "if there's dead space in an image
while something interesting is being said in the transcript or a good source is being used, find a way to animate that
into the shot using the engine").

quiet_map(image): the still cut into a GW x GH grid of cells, each scored 0..9 for how busy it is (ink edges plus tonal
spread), written into the panel's data by `timeline` so the page can place "auto" callouts in the quietest part of the
frame across the whole camera move (longform.html panelCallouts).

largest_quiet(...): the biggest rectangle of quiet cells inside the part of the still the camera always shows.

candidates(text): numbers, quotes and named sources in a beat's narration, as callout suggestions.

Judgement calls, stated: "busy" is measured, not understood. A large flat sky and a large flat wall both read as quiet,
and a quiet patch can still be part of the subject (a plain shirt). The panel's focus point and any keep_clear boxes are
kept free, and `pos` overrides the choice. There is no face detector (no OpenCV here, and the series is faceless);
faces in other films need a keep_clear box. Named sources are found by pattern (a capitalised name after "according
to", "data from", or one carrying a word like Census, Bureau, Survey), so an unusual source name can be missed.
"""
import re
from pathlib import Path

GW, GH = 48, 27            # cells over a 16:9 still (40 px cells at 1920 wide)
QUIET = 2                  # a cell scoring at or below this is quiet
EDGE_T = 22                # luminance step (0..255, after a light blur) that counts as an ink edge


def quiet_map(path, gw=GW, gh=GH):
    """{"w", "h", "b"}: b is one digit per cell, row by row, 0 = flat paper, 9 = dense ink."""
    import numpy as np
    from PIL import Image, ImageFilter
    im = Image.open(path).convert("L").resize((gw * 12, gh * 12), Image.LANCZOS).filter(ImageFilter.GaussianBlur(0.8))
    a = np.asarray(im, dtype=np.float32)
    gx = np.abs(np.diff(a, axis=1, append=a[:, -1:]))
    gy = np.abs(np.diff(a, axis=0, append=a[-1:, :]))
    edge = (np.maximum(gx, gy) > EDGE_T).astype(np.float32)
    cells_e = edge.reshape(gh, 12, gw, 12).mean(axis=(1, 3))              # share of edge pixels
    cells_s = a.reshape(gh, 12, gw, 12).std(axis=(1, 3))                  # tonal spread inside the cell
    # neighbouring cells count a little: a cell beside a figure's outline is not a place to put a card's edge
    busy = np.clip(cells_e * 30 + cells_s / 6, 0, 9)
    pad = np.pad(busy, 1, mode="edge")
    near = np.max(np.stack([pad[1 + dy:1 + dy + gh, 1 + dx:1 + dx + gw] for dy in (-1, 0, 1) for dx in (-1, 0, 1)]), axis=0)
    busy = np.maximum(busy, near * 0.6)
    return {"w": gw, "h": gh, "b": "".join(str(int(round(v))) for v in busy.ravel())}


def grid(qm):
    import numpy as np
    return np.array([int(c) for c in qm["b"]], dtype=np.int8).reshape(qm["h"], qm["w"])


def max_rect(ok):
    """(area, row, col, height, width) of the largest all-True rectangle in a boolean grid (histogram method)."""
    h, w = ok.shape
    best = (0, 0, 0, 0, 0)
    heights = [0] * w
    for r in range(h):
        for c in range(w):
            heights[c] = heights[c] + 1 if ok[r, c] else 0
        stack = []
        for c in range(w + 1):
            cur = heights[c] if c < w else 0
            start = c
            while stack and stack[-1][1] >= cur:
                start, hh = stack.pop()
                area = hh * (c - start)
                if area > best[0]:
                    best = (area, r - hh + 1, start, hh, c - start)
            stack.append((start, cur))
    return best


def largest_quiet(qm, views, iw, ih, frame_w, frame_h, focus=None, keep_clear=()):
    """The biggest quiet rectangle the camera shows for the whole beat. views: [(z, cx, cy)] camera samples in still
    pixels. Returns {"pos": [x, y] centre in 0..1 of the still, "box": [x, y, w, h] 0..1, "px": [w, h] on screen at the
    widest moment} or None."""
    import numpy as np
    g = grid(qm)
    H, W = g.shape
    ok = g <= QUIET
    # only cells on screen at every sample
    x0, y0, x1, y1 = 0.0, 0.0, float(iw), float(ih)
    for z, cx, cy in views:
        x0 = max(x0, cx - frame_w / (2 * z)); x1 = min(x1, cx + frame_w / (2 * z))
        y0 = max(y0, cy - frame_h / (2 * z)); y1 = min(y1, cy + frame_h / (2 * z))
    cw, ch = iw / W, ih / H
    cols = np.arange(W); rows = np.arange(H)
    ok &= ((cols * cw >= x0) & ((cols + 1) * cw <= x1))[None, :]
    ok &= ((rows * ch >= y0) & ((rows + 1) * ch <= y1))[:, None]
    if focus:
        fx, fy = focus[0] * W, focus[1] * H
        yy, xx = np.mgrid[0:H, 0:W]
        ok &= np.hypot((xx + 0.5 - fx) / W * 16 / 9, (yy + 0.5 - fy) / H) > 0.14
    for bx, by, bw, bh in keep_clear:
        ok[int(by * H):int(np.ceil((by + bh) * H)), int(bx * W):int(np.ceil((bx + bw) * W))] = False
    area, r, c, hh, ww = max_rect(ok)
    if not area:
        return None
    zmin = min(z for z, _, _ in views) if views else 1.0
    return {"pos": [round((c + ww / 2) / W, 3), round((r + hh / 2) / H, 3)],
            "box": [round(c / W, 3), round(r / H, 3), round(ww / W, 3), round(hh / H, 3)],
            "px": [round(ww * cw * zmin), round(hh * ch * zmin)]}


NUMWORD = r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion)"
MONEY = re.compile(r"[-−]?\$\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:k|K|m|M|bn|million|billion|thousand)\b)?(?:\s(?:a|per)\s\w+)?")
PCT = re.compile(r"\b\d+(?:\.\d+)?\s?(?:%|percent\b)")
COUNT = re.compile(r"\b(?:\d[\d,]*(?:\.\d+)?|" + NUMWORD + r"(?:[\s-]" + NUMWORD + r")*)\s+(?:[a-z][a-z-]*\s)?"
                   r"(?:units?|toilets?|trucks?|customers?|jobs?|events?|weeks?|months?|years?|days?|hours?|miles?|people|"
                   r"workers?|sites?|rentals?|gallons?|businesses|companies|times|calls?|bookings?|contracts?)\b", re.I)
QUOTE = re.compile(r"[\"“]([^\"”]{6,140})[\"”]")
SOURCE_LEAD = re.compile(r"\b(?:according to|data from|figures from|numbers from|a (?:report|survey|study) (?:by|from)|per)\s+"
                         r"((?:the\s)?[A-Z][\w&.'-]*(?:\s(?:of|for|and|on|the|[A-Z][\w&.'-]*))*)")
SOURCE_WORDS = re.compile(r"\b((?:the\s)?(?:[A-Z][\w&.'-]*\s){0,4}(?:Census|Bureau|Survey|Association|Institute|Department|"
                          r"Administration|Agency|Commission|Council|Board|Report|Index|University|IBISWorld|OSHA|BLS|SBA|IRS)"
                          r"(?:\s(?:of|for|on)(?:\s[A-Z][\w&.'-]*)+)?)")


def _cue(m_text):
    """The cue word for a match: its first word carrying a digit, else its first word."""
    words = m_text.split()
    for w in words:
        if re.search(r"\d", w):
            return w.strip(".,;:")
    return words[0].strip(".,;:\"“")


def candidates(text, seg_source=None):
    """[{"kind", "text"/"value", "at"}] callout suggestions from one beat's narration, in the order spoken."""
    out, seen, spans = [], set(), []

    def add(pos, item, end=None):
        key = item.get("value") or item.get("text")
        end = pos + 1 if end is None else end
        if key and key.lower() not in seen and not any(a < end and pos < b for a, b in spans):
            seen.add(key.lower()); out.append((pos, item)); spans.append((pos, end))
    for m in MONEY.finditer(text):
        add(m.start(), {"kind": "stat", "value": m.group(0).strip(), "at": _cue(m.group(0))}, m.end())
    for m in PCT.finditer(text):
        v = m.group(0).replace(" percent", "%").replace("percent", "%")
        add(m.start(), {"kind": "bar" if float(re.match(r"[\d.]+", v).group(0)) <= 100 else "stat", "value": v, "at": _cue(m.group(0))}, m.end())
    for m in COUNT.finditer(text):
        add(m.start(), {"kind": "stat", "value": m.group(0).strip(), "at": _cue(m.group(0))}, m.end())
    for m in QUOTE.finditer(text):
        add(m.start(), {"kind": "note", "text": "“" + m.group(1).strip() + "”", "at": m.group(1).split()[0].strip(".,;:")})
    for rx in (SOURCE_LEAD, SOURCE_WORDS):
        for m in rx.finditer(text):
            name = re.sub(r"^the\s", "", m.group(1).strip().rstrip(".,"))
            if len(name) > 2:
                add(m.start(1), {"kind": "source", "text": name, "at": name.split()[0]})
    if seg_source and not any(i["kind"] == "source" for _, i in out):
        out.append((len(text), {"kind": "source", "text": str(seg_source), "at": None, "from": "the segment's source line"}))
    return [i for _, i in sorted(out, key=lambda x: x[0])]


def shown(seg):
    """Lower-cased text already in the shot for this beat: its counter, tag, caption card, screen lines and callouts."""
    d = seg.get("data") or {}
    bits = []
    for k in ("counter", "tag"):
        v = seg.get(k)
        if isinstance(v, dict):
            bits += [str(v.get("value", "")), str(v.get("text", "")), str(v.get("label", ""))]
    cc = d.get("caption_card")
    bits.append(cc.get("text", "") if isinstance(cc, dict) else str(cc or ""))
    for l in ((d.get("screen") or {}).get("lines") or []):
        bits.append(l.get("text", "") if isinstance(l, dict) else str(l))
    for c in d.get("callouts") or []:
        bits += [str(c.get("text", "")), str(c.get("value", ""))]
    return " ".join(bits).lower()


def covered(item, seg):
    have = re.sub(r"[\s,]", "", shown(seg))
    key = re.sub(r"[\s,]", "", str(item.get("value") or item.get("text") or "").lower())
    digits = re.sub(r"[^\d.]", "", key)
    nums = re.sub(r"[^\d.]", " ", shown(seg).replace(",", "")).split()
    return bool(key) and (key in have or (len(digits) >= 2 and digits.strip(".") in [n.strip(".") for n in nums]))
