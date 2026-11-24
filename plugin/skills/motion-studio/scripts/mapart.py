"""Engraved paper maps and collage pieces for the `map` scene (references/maps.md).

  python3 mapart.py build <film>                     draw the film's map from script.json "map" into maps/
  python3 mapart.py cutout <in.png> <out.png> [--doc] [--width 600]
                                                     a collage piece: halftone, scissor edge, drop shadow
  python3 mapart.py fonts <film>                     fetch the four map fonts into <film>/fonts/

The map is drawn once, as a raster: coastlines and borders from Natural Earth (public domain), projected
equirectangular with longitude scaled by the cosine of the middle latitude. The sea gets coast-parallel lines and
wave lines, land a diagonal hatch, the featured region a cross-hatch; coastlines are solid, land borders dashed, the
graticule dotted. Each line is drawn from a distance field, so it is antialiased and keeps an even weight however
the coast turns. Four sizes (mip levels) are written, so the film shows the one nearest its zoom and the hatching
doesn't shimmer. Each featured region also gets a yellow paint layer, which the film multiplies over the engraving.

Data and fonts are downloaded on first use into ~/.motion-studio/ (MS_CACHE overrides it):
  Natural Earth 1:50m or 1:10m vectors (public domain), from github.com/nvkelso/natural-earth-vector
  Libre Caslon Text and Anton (SIL OFL 1.1); Permanent Marker and Special Elite (Apache 2.0), from github.com/google/fonts
"""
import json
import math
import os
import shutil
import sys
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

BOARD, INK, YELLOW, BEIGE = "#EFEBE3", "#1D1C20", "#EDBD2E", "#F1EDE4"
CACHE = Path(os.environ.get("MS_CACHE", Path.home() / ".motion-studio"))
NE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_{res}_{layer}.geojson"
GF = "https://raw.githubusercontent.com/google/fonts/main/"
# file in the film's fonts/ folder: (folder in google/fonts, file there, licence, licence file). Each family's own
# licence file travels with it, since an OFL file carries that family's copyright line
FONTS = {
    "LibreCaslonText.ttf": ("ofl/librecaslontext", "LibreCaslonText%5Bwght%5D.ttf", "OFL-1.1", "OFL.txt"),
    "LibreCaslonText-Italic.ttf": ("ofl/librecaslontext", "LibreCaslonText-Italic%5Bwght%5D.ttf", "OFL-1.1", "OFL.txt"),
    "Anton-Regular.ttf": ("ofl/anton", "Anton-Regular.ttf", "OFL-1.1", "OFL.txt"),
    "PermanentMarker-Regular.ttf": ("apache/permanentmarker", "PermanentMarker-Regular.ttf", "Apache-2.0", "LICENSE.txt"),
    "SpecialElite-Regular.ttf": ("apache/specialelite", "SpecialElite-Regular.ttf", "Apache-2.0", "LICENSE.txt"),
}
LEVELS = 4
TILE = 1024        # images are cut into tiles: Chrome's software raster re-decodes a 7200 px image every frame (1.3 s), a few tiles it keeps


def rgb(h):
    return np.array([int(h[i:i + 2], 16) for i in (1, 3, 5)], np.float32)


def fetch(url, dest):
    dest = Path(dest)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  downloading {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    tmp.rename(dest)
    return dest


def lic_name(name):
    return f"LICENSE-{FONTS[name][0].split('/')[1]}.txt"


def cached_font(name):
    folder, file, _, licfile = FONTS[name]
    f = fetch(f"{GF}{folder}/{file}", CACHE / "fonts" / name)
    fetch(f"{GF}{folder}/{licfile}", CACHE / "fonts" / lic_name(name))
    return f


def cmd_fonts(film):
    dest = Path(film) / "fonts"
    dest.mkdir(parents=True, exist_ok=True)
    for name, (_, _, lic, _) in FONTS.items():
        shutil.copy2(cached_font(name), dest / name)
        shutil.copy2(CACHE / "fonts" / lic_name(name), dest / lic_name(name))
    (dest / "LICENCES.md").write_text("".join(f"- {n}: {l}, {lic_name(n)} (github.com/google/fonts)\n" for n, (_, _, l, _) in FONTS.items()))
    print(dest)


def layer(res, name):
    return json.loads(fetch(NE.format(res=res, layer=name), CACHE / "naturalearth" / f"ne_{res}_{name}.geojson").read_text())


def rings(geom):
    """Outer and inner rings of a (Multi)Polygon as lists of (lon, lat)."""
    if geom["type"] == "Polygon":
        return [geom["coordinates"]]
    if geom["type"] == "MultiPolygon":
        return geom["coordinates"]
    return []


def lines(geom):
    if geom["type"] == "LineString":
        return [geom["coordinates"]]
    if geom["type"] == "MultiLineString":
        return geom["coordinates"]
    return []


class Proj:
    """Equirectangular, longitude scaled by cos(middle latitude); x, y in map pixels, y down."""

    def __init__(self, bounds, width):
        self.lon0, self.lat0, self.lon1, self.lat1 = bounds
        self.k = math.cos(math.radians((self.lat0 + self.lat1) / 2))
        self.s = width / ((self.lon1 - self.lon0) * self.k)
        self.w = int(width)
        self.h = int(round((self.lat1 - self.lat0) * self.s))

    def __call__(self, lon, lat):
        return (np.asarray(lon) - self.lon0) * self.k * self.s, (self.lat1 - np.asarray(lat)) * self.s

    def json(self):
        return {"lon0": self.lon0, "lat0": self.lat0, "lon1": self.lon1, "lat1": self.lat1, "k": self.k, "s": self.s, "w": self.w, "h": self.h}


def chaikin(a, passes=3, closed=True):
    """Corner cutting: each pass replaces every edge by points at 1/4 and 3/4 along it, which rounds the straight runs
    of simplified data into a drawn line. Natural Earth rings are global, so no ring closes along the map's edge."""
    for _ in range(passes):
        if len(a) < 3:
            return a
        b = np.roll(a, -1, axis=0) if closed else a[1:]
        a0 = a if closed else a[:-1]
        q, r = 0.75 * a0 + 0.25 * b, 0.25 * a0 + 0.75 * b
        out = np.empty((2 * len(a0), 2))
        out[0::2], out[1::2] = q, r
        a = out if closed else np.vstack([a[:1], out, a[-1:]])
    return a


def poly_mask(P, polys, scale=1):
    """Binary mask of polygons (even-odd for holes), drawn at `scale` times the map size."""
    im = Image.new("1", (P.w * scale, P.h * scale), 0)
    d = ImageDraw.Draw(im)
    for poly in polys:
        for k, ring in enumerate(poly):
            a = np.asarray(ring, np.float64)
            if len(a) < 3:
                continue
            a = chaikin(a[:-1] if np.allclose(a[0], a[-1]) else a)
            x, y = P(a[:, 0], a[:, 1])
            pts = list(zip((x * scale).tolist(), (y * scale).tolist()))
            d.polygon(pts, fill=1 if k == 0 else 0)
    return np.asarray(im, bool)


def line_ink(dist, width):
    """Antialiased ink (0..1) of a line `width` px wide, from each pixel's distance to its centre line."""
    return np.clip(width / 2 + 0.5 - np.abs(dist), 0, 1)


def edt(mask):
    from scipy.ndimage import distance_transform_edt
    return distance_transform_edt(mask).astype(np.float32)


def stroke_layer(P, paths, width, dash=None, dot=None, scale=2):
    """Polylines (lists of (lon, lat)) drawn at `scale` times the size then reduced, which antialiases them.
    dash=(on, off) in map px; dot=(radius, spacing) draws dots along the line instead."""
    W, H = P.w * scale, P.h * scale
    im = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(im)
    for path in paths:
        a = np.asarray(path, np.float64)
        if len(a) < 2:
            continue
        x, y = P(a[:, 0], a[:, 1])
        pts = np.stack([x, y], 1) * scale
        if dash is None and dot is None:
            d.line([tuple(p) for p in pts], fill=255, width=int(round(width * scale)), joint="curve")
            continue
        seg = np.hypot(*np.diff(pts, axis=0).T)
        cum = np.concatenate([[0], np.cumsum(seg)])
        if cum[-1] == 0:
            continue
        if dot:
            r, gap = dot[0] * scale, dot[1] * scale
            for u in np.arange(0, cum[-1], gap):
                px, py = np.interp(u, cum, pts[:, 0]), np.interp(u, cum, pts[:, 1])
                d.ellipse([px - r, py - r, px + r, py + r], fill=255)
            continue
        on, off = dash[0] * scale, dash[1] * scale
        u = 0.0
        while u < cum[-1]:
            us = np.linspace(u, min(u + on, cum[-1]), 8)
            q = np.stack([np.interp(us, cum, pts[:, 0]), np.interp(us, cum, pts[:, 1])], 1)
            d.line([tuple(p) for p in q], fill=255, width=int(round(width * scale)), joint="curve")
            u += on + off
    return np.asarray(im.reduce(scale), np.float32) / 255


def font(name, size):
    return ImageFont.truetype(str(cached_font(name)), int(size))


def label_layer(P, labels, default_size):
    """Engraved place names, letter-spaced, centred on their lon/lat. Returns (ink, knockout) layers: the
    knockout clears the hatching behind the words, as an engraver leaves a gap for a name."""
    ink = Image.new("L", (P.w, P.h), 0)
    d = ImageDraw.Draw(ink)
    for lb in labels:
        size = lb.get("size", default_size * (1.1 if lb.get("sea") else 1))
        f = font("LibreCaslonText-Italic.ttf" if lb.get("sea") else "LibreCaslonText.ttf", size)
        text = lb["text"] if lb.get("sea") else lb["text"].upper()
        track = size * lb.get("tracking", 0.32 if not lb.get("sea") else 0.18)
        rows = text.split("\n")
        x0, y0 = P(lb["lon"], lb["lat"])
        lh = size * 1.25
        for r, row in enumerate(rows):
            widths = [d.textlength(c, font=f) for c in row]
            total = sum(widths) + track * (len(row) - 1)
            x = float(x0) - total / 2
            y = float(y0) - lh * len(rows) / 2 + r * lh
            for c, w in zip(row, widths):
                d.text((x, y), c, font=f, fill=255)
                x += w + track
    a = np.asarray(ink, np.float32) / 255
    knock = np.asarray(ink.filter(ImageFilter.MaxFilter(13)).filter(ImageFilter.GaussianBlur(2)), np.float32) / 255
    return a, np.clip(knock * 1.6, 0, 1)


def compass(P, lon, lat, r=150):
    """An eight-point compass rose: alternate points filled and outlined, a ring, an N above."""
    sc = 2
    im = Image.new("L", (P.w * sc, P.h * sc), 0)
    d = ImageDraw.Draw(im)
    cx, cy = (float(v) * sc for v in P(lon, lat))
    R = r * sc
    d.ellipse([cx - R * 0.78, cy - R * 0.78, cx + R * 0.78, cy + R * 0.78], outline=255, width=3 * sc)
    d.ellipse([cx - R * 0.70, cy - R * 0.70, cx + R * 0.70, cy + R * 0.70], outline=255, width=1 * sc)
    for k in range(8):
        a = k * math.pi / 4 - math.pi / 2
        L = R if k % 2 == 0 else R * 0.62
        wdt = R * (0.13 if k % 2 == 0 else 0.1)
        tip = (cx + L * math.cos(a), cy + L * math.sin(a))
        l = (cx + wdt * math.cos(a - math.pi / 2), cy + wdt * math.sin(a - math.pi / 2))
        rr = (cx + wdt * math.cos(a + math.pi / 2), cy + wdt * math.sin(a + math.pi / 2))
        d.polygon([tip, l, (cx, cy)], fill=255)
        d.polygon([tip, rr, (cx, cy)], outline=255, width=2 * sc)
    f = font("LibreCaslonText.ttf", R * 0.34)
    d.text((cx, cy - R * 1.02), "N", font=f, fill=255, anchor="ms")
    return np.asarray(im.reduce(sc), np.float32) / 255


def pick(features, want):
    """Countries matching a name or ISO code (A2 or A3), case-insensitive."""
    w = str(want).lower()
    keys = ("NAME", "NAME_LONG", "ADMIN", "SOVEREIGNT", "ISO_A2", "ISO_A3", "ADM0_A3", "ISO_A2_EH", "ISO_A3_EH")
    return [f for f in features if any(str(f["properties"].get(k, "")).lower() == w for k in keys)]


def save_tiles(im, out, name, keep=None):
    """Cut an image into TILE px tiles, <name>/<row>_<col>.png; returns [[col, row, w, h], ...]. keep(box) can skip tiles."""
    (out / name).mkdir(parents=True, exist_ok=True)
    for old in (out / name).glob("*.png"):     # a rebuild replaces every tile
        old.unlink()
    tiles = []
    for r in range(0, im.height, TILE):
        for c in range(0, im.width, TILE):
            box = (c, r, min(c + TILE, im.width), min(r + TILE, im.height))
            t = im.crop(box)
            if keep and not keep(t):
                continue
            t.save(out / name / f"{r // TILE}_{c // TILE}.png", compress_level=3)
            tiles.append([c // TILE, r // TILE, box[2] - box[0], box[3] - box[1]])
    return tiles


def cmd_build(film):
    film = Path(film).resolve()
    sc = json.loads((film / "script.json").read_text())
    spec = sc.get("map")
    if not spec:
        raise SystemExit("script.json has no \"map\": see references/maps.md")
    res = spec.get("detail", "50m")
    P = Proj(spec["bounds"], spec.get("width", 7200))
    print(f"map {P.w}x{P.h} px, {res} detail, bounds {spec['bounds']}")
    out = film / "maps"
    out.mkdir(exist_ok=True)
    cmd_fonts(film)

    land = poly_mask(P, [r for f in layer(res, "land")["features"] for r in rings(f["geometry"])])
    countries = layer(res, "admin_0_countries")["features"]
    feat = {}
    for fe in spec.get("featured", []):
        hits = pick(countries, fe.get("country", fe["id"]))
        if not hits:
            raise SystemExit(f"featured {fe}: no Natural Earth country by that name or code")
        feat[fe["id"]] = poly_mask(P, [r for h in hits for r in rings(h["geometry"])]) & land
    anyfeat = np.zeros_like(land)
    for m in feat.values():
        anyfeat |= m
    sea = ~land
    dsea, dland = edt(sea), edt(land)
    yy, xx = np.mgrid[0:P.h, 0:P.w].astype(np.float32)

    # The sea lines are close detail: the two finest sizes carry them, the two coarser ones leave the sea plain, so a
    # wide shot reads as clean paper instead of a grey mush of lines (as in the reference frames).
    fine = np.zeros((P.h, P.w), np.float32)
    for k in spec.get("coast_lines", [8, 19, 32, 48]):
        fine = np.maximum(fine, line_ink(dsea - k, 1.7) * sea)
    phase = yy + 3.5 * np.sin(xx * (2 * math.pi / 140))
    wave = np.abs(np.mod(phase + 11, 22) - 11)
    far = np.clip((dsea - 58) / 10, 0, 1)
    fine = np.maximum(fine, line_ink(wave, 1.4) * far * sea)
    del phase, wave, far
    # land: the featured regions are cross-hatched; other land stays plain paper unless "land_hatch" asks for a diagonal hatch
    u = (xx - yy) / math.sqrt(2)
    v = (xx + yy) / math.sqrt(2)
    cross = np.maximum(line_ink(np.abs(np.mod(u + 6.5, 13) - 6.5), 1.3), line_ink(np.abs(np.mod(v + 6.5, 13) - 6.5), 1.3))
    ink = cross * anyfeat
    if spec.get("land_hatch"):
        ink = np.maximum(ink, line_ink(np.abs(np.mod(u + 11, 22) - 11), 1.5) * (land & ~anyfeat))
    del u, v, cross, xx, yy
    # graticule: dotted, every 10 degrees, over the sea only
    step = spec.get("graticule", 10)
    grat = []
    for lon in np.arange(math.ceil(P.lon0 / step) * step, P.lon1 + 1e-9, step):
        grat.append([(lon, la) for la in np.linspace(P.lat0, P.lat1, 200)])
    for lat in np.arange(math.ceil(P.lat0 / step) * step, P.lat1 + 1e-9, step):
        grat.append([(lo, lat) for lo in np.linspace(P.lon0, P.lon1, 400)])
    ink = np.maximum(ink, stroke_layer(P, grat, 0, dot=(1.8, 11)) * np.clip((dsea - 12) / 6, 0, 1))
    # coastline 6 px on the land/sea edge; land borders 4 px, dashed 22 on, 18 off
    coast = np.where(land, dland, dsea) - 0.5
    ink = np.maximum(ink, line_ink(coast, 6))
    del coast
    borders = [chaikin(np.asarray(ln, np.float64), closed=False) for f in layer(res, "admin_0_boundary_lines_land")["features"] for ln in lines(f["geometry"])]
    ink = np.maximum(ink, stroke_layer(P, borders, 4, dash=(22, 18)))
    if spec.get("compass"):
        ink = np.maximum(ink, compass(P, *spec["compass"]))
    lab, knock = label_layer(P, spec.get("labels", []), spec.get("label_size", 60))
    fine *= 1 - knock
    ink = ink * (1 - knock)
    del knock

    base, col = rgb(BOARD), rgb(INK)
    tone = paper_tone(P.h, P.w)[..., None] * np.array([1.0, 0.995, 0.985], np.float32)

    def paint_ink(a):
        img = base[None, None] * (1 - a[..., None]) + col[None, None] * a[..., None]
        img *= tone
        return Image.fromarray(np.clip(img + 0.5, 0, 255).astype(np.uint8))

    tiles = {}
    plain = paint_ink(np.maximum(ink, lab)).reduce(2)
    im = paint_ink(np.maximum(np.maximum(ink, fine), lab))
    del ink, fine, lab, tone
    for L in range(LEVELS):
        if L == 2:
            im = plain.reduce(2)
        tiles[f"map-{L}"] = save_tiles(im, out, f"map-{L}")
        print(f"  maps/map-{L}/ {im.size[0]}x{im.size[1]}, {len(tiles[f'map-{L}'])} tiles")
        im = im.reduce(2)
    regions = {}
    for rid, m in feat.items():
        ys, xs = np.nonzero(m)
        regions[rid] = {"box": [int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)]}
        a = Image.fromarray((m * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.8))
        paint = Image.new("RGBA", a.size, YELLOW)
        paint.putalpha(a)
        for L in range(LEVELS):
            tiles[f"paint-{rid}-{L}"] = save_tiles(paint, out, f"paint-{rid}-{L}", keep=lambda t: t.getchannel("A").getextrema()[1] > 0)
            paint = paint.reduce(2)
    meta = {"proj": P.json(), "levels": LEVELS, "tile": TILE, "tiles": tiles, "regions": regions,
            "credit": "Map data: Natural Earth (public domain). Coastlines are simplified."}
    (out / "map.json").write_text(json.dumps(meta, indent=1) + "\n")
    print(out / "map.json")


def noise(h, w, rng, octaves=((8, 1.0), (24, 0.5), (80, 0.25), (260, 0.12))):
    """Cloud noise: random grids of several sizes, upsampled smoothly and summed, scaled to 0..1."""
    acc = np.zeros((h, w), np.float32)
    for cells, amp in octaves:
        g = rng.random((max(2, h * cells // w + 2), cells + 2)).astype(np.float32)
        acc += amp * np.asarray(Image.fromarray(g, "F").resize((w, h), Image.BICUBIC), np.float32)
    acc -= acc.min()
    return acc / max(acc.max(), 1e-6)


def paper_tone(h, w, seed=5):
    """The paper the map is printed on, as a multiplier (about 0.85 to 1): cloud noise, fibres, a few creases and grain.
    It is baked into the map, so it travels with the map under the camera. A full-frame texture multiplied over every
    frame cost about 0.5 s a frame in the renderer's software raster, against under 0.1 s for the whole map."""
    rng = np.random.default_rng(seed)
    v = 1 - 0.05 * noise(h, w, rng)
    fib = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(fib)
    for _ in range(int(900 * w * h / (1920 * 1080))):
        x, y, a, L = rng.random() * w, rng.random() * h, rng.random() * math.pi, 8 + rng.random() * 30
        pts = [(x + t * L * math.cos(a) + 2 * math.sin(t * 5), y + t * L * math.sin(a)) for t in np.linspace(0, 1, 6)]
        d.line(pts, fill=int(40 + rng.random() * 50), width=1)
    v -= 0.06 * np.asarray(fib.filter(ImageFilter.GaussianBlur(0.6)), np.float32) / 255
    del fib
    for _ in range(4):      # creases: a dark line with a light edge beside it
        cr = Image.new("L", (w, h), 0)
        d = ImageDraw.Draw(cr)
        x0, y0, a = rng.random() * w, rng.random() * h, rng.random() * math.pi
        L = max(w, h) * 1.5
        d.line([(x0 - L * math.cos(a), y0 - L * math.sin(a)), (x0 + L * math.cos(a), y0 + L * math.sin(a))], fill=255, width=3)
        c = np.asarray(cr.filter(ImageFilter.GaussianBlur(2)), np.float32) / 255
        v -= 0.05 * c
        v += 0.03 * np.roll(c, 4, axis=0)
    v += 0.025 * (rng.random((h, w)).astype(np.float32) - 0.5)
    return np.clip(v, 0, 1)


def cmd_cutout(src, dest, doc=False, width=600, seed=3):
    """A collage piece from a picture with transparency (or a white background, keyed out). Not a document: grey,
    contrast 1.25, a 45-degree dot screen mixed at 34%, grain. A document: kept as line art on cream paper. Both get
    a white scissor edge about 7 px wide with a wobble, and a soft shadow offset (5, 7) px."""
    im = Image.open(src).convert("RGBA")
    if im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    a = np.asarray(im, np.float32) / 255
    alpha = a[..., 3]
    if alpha.min() > 0.99 and not doc:      # no transparency: key out a white or near-white background (a document keeps its paper)
        alpha = np.clip((0.97 - a[..., :3].min(axis=2)) / 0.06, 0, 1)
    lum = a[..., :3] @ np.array([0.299, 0.587, 0.114], np.float32)
    rng = np.random.default_rng(seed)
    pad = 24
    h, w = lum.shape
    H, Wd = h + 2 * pad, w + 2 * pad
    if doc:
        cream = rgb(BEIGE) / 255
        art = cream[None, None] * np.clip(lum * 1.1, 0, 1)[..., None] + rgb(INK)[None, None] / 255 * (1 - np.clip(lum * 1.1, 0, 1))[..., None]
    else:
        g = np.clip((lum - 0.5) * 1.25 + 0.5, 0, 1)
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        u, v = (xx + yy) / math.sqrt(2), (xx - yy) / math.sqrt(2)
        cell = 5.0
        du, dv = np.mod(u, cell) - cell / 2, np.mod(v, cell) - cell / 2
        rdot = np.hypot(du, dv) / (cell / math.sqrt(2))       # 0 at a dot's centre
        dots = np.clip((rdot - np.sqrt(1 - g) * 0.92) * 6 + 0.5, 0, 1)   # darker tone, bigger dot
        g = g * 0.66 + dots * 0.34
        g = np.clip(g + (rng.random(g.shape).astype(np.float32) - 0.5) * 0.08, 0, 1)
        art = np.repeat(g[..., None], 3, 2) * (rgb("#F4F1EA") / 255)[None, None]
    A = np.zeros((H, Wd), np.float32)
    A[pad:pad + h, pad:pad + w] = alpha
    from scipy.ndimage import distance_transform_edt
    dist = distance_transform_edt(A < 0.5).astype(np.float32)
    wob = 7 + 2.2 * (noise(H, Wd, rng, ((6, 1.0), (18, 0.5))) - 0.5) * 2
    edge = np.clip(wob - dist + 0.5, 0, 1)
    out = np.ones((H, Wd, 3), np.float32) * (rgb("#FBFAF6") / 255)
    out[pad:pad + h, pad:pad + w] = out[pad:pad + h, pad:pad + w] * (1 - alpha[..., None]) + art * alpha[..., None]
    sh = Image.fromarray((edge * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(6))
    sha = np.zeros((H + 8, Wd + 6), np.float32)
    sha[7:7 + H, 5:5 + Wd] = np.asarray(sh, np.float32) / 255 * 0.32
    canvas = np.zeros((H + 8, Wd + 6, 4), np.float32)
    canvas[..., :3] = rgb(INK) / 255
    canvas[..., 3] = sha
    top = np.zeros_like(canvas)
    top[:H, :Wd, :3] = out
    top[:H, :Wd, 3] = edge
    al = top[..., 3:] + canvas[..., 3:] * (1 - top[..., 3:])
    col = (top[..., :3] * top[..., 3:] + canvas[..., :3] * canvas[..., 3:] * (1 - top[..., 3:])) / np.maximum(al, 1e-6)
    res = np.concatenate([col, al], 2)
    Image.fromarray(np.clip(res * 255 + 0.5, 0, 255).astype(np.uint8), "RGBA").save(dest)
    print(f"{dest} {res.shape[1]}x{res.shape[0]}")


def main(argv):
    if len(argv) >= 2 and argv[0] == "build":
        cmd_build(argv[1])
    elif len(argv) >= 2 and argv[0] == "fonts":
        cmd_fonts(argv[1])
    elif len(argv) >= 3 and argv[0] == "cutout":
        w = int(argv[argv.index("--width") + 1]) if "--width" in argv else 600
        cmd_cutout(argv[1], argv[2], doc="--doc" in argv, width=w)
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
