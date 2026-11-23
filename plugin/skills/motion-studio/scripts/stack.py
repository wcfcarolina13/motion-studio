"""Sprite stacks: fake-3D pixel props from four flat drawings.

A sprite stack is a strip of flat top-down layers, ground layer first. Drawn bottom to top, each rotated by the same
angle, squashed for the camera's pitch and lifted one art pixel, the layers read as a solid object that turns in crisp
pixels. stack.js draws them in a film; this file makes the strip.

  stack.py carve <views.png> <strip.png> [--scale N] [--shade 0.18] [--film <film>]
      views.png holds four drawings in one row, in this order, on a transparent or flat background:
        TOP    looking straight down, the object's front at the bottom of the drawing
        SIDE   seen from the object's right side, so its front points left
        FRONT  seen standing in front of it
        BACK   seen standing behind it
      Widths and heights must agree: TOP width = FRONT width = BACK width, TOP height = SIDE width,
      SIDE height = FRONT height = BACK height. That height is the number of layers.
      --scale N  the drawings were enlarged N times (each art pixel is an N x N block); default: found from the sheet.
      Writes the strip, <strip>.json (count, layer size, warnings) and <strip>-check.png (the four views as read,
      five angles and an exploded view). Look at the check sheet before using the strip. --film copies stack.js into
      that film's folder.

How it carves: a voxel is kept only where all four drawings cover it (the object's visual hull). A voxel with nothing
above it takes the TOP drawing's colour; one that shows on a wall takes the colour of the drawing that faces that wall
at that height, so stripes, windows and labels land on the walls they were drawn on.

Trade-offs, all from the method: a hollow that no drawing can see (a truck bed, an arch seen end-on only) carves solid;
the left wall copies the SIDE drawing (there is no fifth view), so a lopsided object needs its asymmetry in TOP, FRONT and
BACK; a part one art pixel thin flickers as the stack turns (it falls between samples at some angles), so it is
reported. The idea and the four-view workflow come from danielwh2/sprite-stacking (MIT); this file is written fresh.
Standard library, numpy and scipy (for labelling); PNGs are read and written through ffmpeg, as the rest of the engine
does.
"""
import argparse
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage

HERE = Path(__file__).resolve().parent


# ── PNG in and out through ffmpeg (no imaging library needed) ─────────────────
def read_png(path):
    """RGBA uint8 array (h, w, 4)."""
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                            "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout
    w, h = (int(v) for v in probe.strip().split(",")[:2])
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt",
                          "rgba", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 4).copy()


def write_png(path, rgba):
    rgba = np.ascontiguousarray(rgba, dtype=np.uint8)
    h, w = rgba.shape[:2]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{w}x{h}", "-i", "-",
                    "-frames:v", "1", str(path)], input=rgba.tobytes(), check=True)


# ── reading the sheet ─────────────────────────────────────────────────────────
def foreground(sheet, tol=24):
    """Mask of drawn pixels. A transparent sheet uses its alpha; an opaque one loses the background colour wherever it
    connects to the sheet's border, so a white highlight inside a drawing survives."""
    a = sheet[..., 3]
    if (a < 128).mean() > 0.02:
        return a >= 128
    rgb = sheet[..., :3].astype(int)
    corners = np.array([rgb[0, 0], rgb[0, -1], rgb[-1, 0], rgb[-1, -1]])
    bg = np.median(corners, axis=0)
    near = np.abs(rgb - bg).max(-1) <= tol
    lab, _ = ndimage.label(near)
    edge = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    return ~np.isin(lab, list(edge))


def split_views(sheet, fg):
    """The four drawings, left to right, each cropped to its own pixels (RGBA, background cleared)."""
    cols = fg.any(0)
    runs, start = [], None
    for x, c in enumerate(list(cols) + [False]):
        if c and start is None:
            start = x
        elif not c and start is not None:
            runs.append((start, x)); start = None
    # drop specks (stray pixels, a signature) that are tiny next to the biggest drawing
    big = max(int(fg[:, a:b].sum()) for a, b in runs) if runs else 0
    runs = [(a, b) for a, b in runs if fg[:, a:b].sum() >= 0.02 * big]
    if len(runs) != 4:
        raise SystemExit(f"found {len(runs)} drawings side by side, expected 4 (TOP, SIDE, FRONT, BACK) in one row "
                         "with a clear gap between them")
    views = []
    for a, b in runs:
        m = fg[:, a:b]
        ys = np.nonzero(m.any(1))[0]
        v = sheet[ys[0]:ys[-1] + 1, a:b].copy()
        v[..., 3] = np.where(m[ys[0]:ys[-1] + 1], 255, 0)
        views.append(v)
    return views


def find_scale(views):
    """The largest N for which every view is made of N x N blocks of one colour (an enlarged pixel drawing)."""
    def ok(v, n):
        h, w = v.shape[:2]
        if h % n or w % n:
            return False
        blocks = v.reshape(h // n, n, w // n, n, 4)
        return bool((blocks == blocks[:, :1, :, :1]).all())
    for n in range(16, 1, -1):
        if all(ok(v, n) for v in views):
            return n
    return 1


def shrink(v, n):
    return v[::n, ::n].copy() if n > 1 else v


# ── carving ───────────────────────────────────────────────────────────────────
def check_sizes(top, side, front, back):
    (L, W), (H, Ls), (Hf, Wf), (Hb, Wb) = top.shape[:2], side.shape[:2], front.shape[:2], back.shape[:2]
    bad = []
    if not W == Wf == Wb:
        bad.append(f"widths differ: TOP {W}, FRONT {Wf}, BACK {Wb}")
    if L != Ls:
        bad.append(f"lengths differ: TOP is {L} tall, SIDE is {Ls} wide")
    if not H == Hf == Hb:
        bad.append(f"heights differ: SIDE {H}, FRONT {Hf}, BACK {Hb}")
    if bad:
        raise SystemExit("the four drawings don't agree (art pixels): " + "; ".join(bad) +
                         ". Redraw them at one scale; a mismatch carves the object wrong.")
    return L, W, H


def carve(top, side, front, back, shade=0.18):
    """Volume (H, L, W, 4): z = 0 is the ground layer, y runs back to front, x left to right."""
    L, W, H = check_sizes(top, side, front, back)
    # every drawing re-indexed to the object's axes. Rows of SIDE/FRONT/BACK run from the top of the object down.
    side_y = side[:, ::-1]          # seen from the right with the front pointing left: column 0 is the front (y = L-1)
    front_x = front                 # seen from the front, the viewer's left is the object's left (x = 0), as in TOP
    back_x = back[:, ::-1]          # seen from behind, the viewer's left is the object's right
    t_on, s_on, f_on, b_on = (v[..., 3] > 0 for v in (top, side_y, front_x, back_x))

    vol = np.zeros((H, L, W, 4), np.uint8)
    occ = np.zeros((H, L, W), bool)
    for z in range(H):
        r = H - 1 - z
        occ[z] = t_on & s_on[r][:, None] & (f_on[r] | b_on[r])[None, :]

    xs, ys = np.arange(W)[None, :].repeat(L, 0), np.arange(L)[:, None].repeat(W, 1)
    for z in range(H):
        o = occ[z]
        if not o.any():
            continue
        r = H - 1 - z
        above = occ[z + 1] if z + 1 < H else np.zeros_like(o)
        # distance to the slice's edge in each direction; the nearest one is the wall this voxel shows on
        inf = 10 ** 6
        left = xs - np.maximum.accumulate(np.where(~o, xs, -1), axis=1)
        right = np.minimum.accumulate(np.where(~o, xs, W)[:, ::-1], axis=1)[:, ::-1] - xs
        backd = ys - np.maximum.accumulate(np.where(~o, ys, -1), axis=0)
        frontd = np.minimum.accumulate(np.where(~o, ys, L)[::-1], axis=0)[::-1] - ys
        d = np.stack([left, right, backd, frontd]).astype(float)
        d[0][~s_on[r][:, None].repeat(W, 1)] = inf        # no SIDE colour on this row: don't pick that wall
        d[1][~s_on[r][:, None].repeat(W, 1)] = inf
        wall = d.argmin(0)
        rgb = top[..., :3].copy()
        sc = side_y[r, :, :3][:, None, :].repeat(W, 1)     # (L, W, 3)
        fc = front_x[r, :, :3][None, :, :].repeat(L, 0)
        bc = back_x[r, :, :3][None, :, :].repeat(L, 0)
        fc = np.where(f_on[r][None, :, None], fc, bc)      # a row only one of FRONT/BACK covers takes the other's colour
        bc = np.where(b_on[r][None, :, None], bc, fc)
        walls = np.select([wall[..., None] < 2, wall[..., None] == 2], [sc, bc], fc)
        covered = above & o
        rgb = np.where(covered[..., None], walls, rgb)
        if shade:
            # walls darken a little toward the ground, so the stack's sides read as sides
            f = 1 - shade * (0.6 + 0.4 * (1 - z / max(H - 1, 1)))
            rgb = np.where(covered[..., None], np.clip(rgb * f, 0, 255), rgb)
        vol[z, o, :3] = rgb[o]
        vol[z, o, 3] = 255
    return vol


def thin_layers(vol):
    """Layer numbers holding a part with no 2 x 2 solid block anywhere in it (a 1 px post, wire or rail)."""
    out = []
    for z, s in enumerate(vol[..., 3] > 0):
        if not s.any():
            continue
        quad = s[:-1, :-1] & s[1:, :-1] & s[:-1, 1:] & s[1:, 1:]      # top-left corners of solid 2 x 2 blocks
        inblock = np.zeros_like(s)
        for dy in (0, 1):
            for dx in (0, 1):
                inblock[dy:dy + quad.shape[0], dx:dx + quad.shape[1]] |= quad
        lab, n = ndimage.label(s)
        if any(not (inblock & (lab == i)).any() for i in range(1, n + 1)):
            out.append(z)
    return out


def runs_of(zs):
    out = []
    for z in zs:
        if out and z == out[-1][1] + 1:
            out[-1][1] = z
        else:
            out.append([z, z])
    return out


# ── a still of the stack, the same sampling stack.js uses ─────────────────────
def draw(vol, turn_deg, pitch=0.75, spacing=1.0, outline=(31, 31, 42, 255)):
    """One frame at one art pixel per pixel, as stack.js draws it: each layer rotated about its true centre
    ((w - 1) / 2, (h - 1) / 2), squashed by `pitch`, lifted round(z * spacing), sampled nearest."""
    n, h, w = vol.shape[:3]
    r = math.ceil(math.hypot(w, h) / 2) + 2
    lift = round((n - 1) * spacing)
    bw, bh = 2 * r + 3, math.ceil(2 * r * pitch) + lift + 4
    gx, gy = r + 1, bh - math.ceil(r * pitch) - 2       # ground centre in the buffer
    out = np.zeros((bh, bw, 4), np.uint8)
    v, u = np.mgrid[0:bh, 0:bw]
    a = math.radians(turn_deg)
    ca, sa = math.cos(a), math.sin(a)
    cx, cy = (w - 1) / 2, (h - 1) / 2
    for z in range(n):
        dx = u - gx
        dy = (v - (gy - round(z * spacing))) / pitch
        sx = np.rint(cx + dx * ca + dy * sa).astype(int)
        sy = np.rint(cy - dx * sa + dy * ca).astype(int)
        ok = (sx >= 0) & (sx < w) & (sy >= 0) & (sy < h)
        px = np.zeros((bh, bw, 4), np.uint8)
        px[ok] = vol[z, sy[ok], sx[ok]]
        hit = px[..., 3] > 0
        out[hit] = px[hit]
    if outline is not None:
        a_ = out[..., 3] > 0
        ring = ndimage.binary_dilation(a_, [[0, 1, 0], [1, 1, 1], [0, 1, 0]]) & ~a_
        out[ring] = outline
    return out


def check_sheet(vol, views, dest, scale=6):
    """The four drawings as read, five angles and an exploded view, on a light ground."""
    tiles = [("as read", None)] + [(f"{d} deg", draw(vol, d)) for d in (0, 45, 135, 225, 315)]
    tiles.append(("exploded", draw(vol, 30, spacing=2.4)))
    frames = [t for _, t in tiles[1:]]
    th = max(f.shape[0] for f in frames) * scale
    vh = max(v.shape[0] for v in views) * scale
    pad = 2 * scale
    row1 = sum(v.shape[1] * scale + pad for v in views) + pad
    row2 = sum(f.shape[1] * scale + pad for f in frames) + pad
    W_, H_ = max(row1, row2), vh + th + 3 * pad
    sheet = np.zeros((H_, W_, 4), np.uint8)
    sheet[...] = (239, 231, 214, 255)
    x = pad
    for vv in views:
        up = vv.repeat(scale, 0).repeat(scale, 1)
        m = up[..., 3] > 0
        sheet[pad:pad + up.shape[0], x:x + up.shape[1]][m] = up[m]
        x += up.shape[1] + pad
    x = pad
    for f in frames:
        up = f.repeat(scale, 0).repeat(scale, 1)
        m = up[..., 3] > 0
        y0 = vh + 2 * pad + (th - up.shape[0])
        sheet[y0:y0 + up.shape[0], x:x + up.shape[1]][m] = up[m]
        x += up.shape[1] + pad
    write_png(dest, sheet)
    return dest


def strip_of(vol):
    """Layers left to right, ground layer first: (h, w * count, 4)."""
    return np.concatenate(list(vol), axis=1)


def cmd_carve(a):
    sheet = read_png(a.views)
    views = split_views(sheet, foreground(sheet, a.bg_tol))
    n = a.scale or find_scale(views)
    views = [shrink(v, n) for v in views]
    top, side, front, back = views
    vol = carve(top, side, front, back, shade=a.shade)
    out = Path(a.strip)
    write_png(out, strip_of(vol))
    warnings = []
    for z0, z1 in runs_of(thin_layers(vol)):
        warnings.append(f"layers {z0}-{z1} hold a part 1 art pixel thin: it will flicker as the stack turns. "
                        "Draw posts, handles and wires 2 px wide.")
    meta = {"count": int(vol.shape[0]), "w": int(vol.shape[2]), "h": int(vol.shape[1]), "scale_read": n,
            "source": Path(a.views).name, "warnings": warnings}
    out.with_suffix(".json").write_text(json.dumps(meta, indent=1) + "\n")
    chk = check_sheet(vol, [top, side, front, back], out.with_name(out.stem + "-check.png"))
    print(f"{meta['count']} layers of {meta['w']} x {meta['h']} art px (drawings read at {n}x) -> {out}")
    print(f"check sheet -> {chk}  (look at it before using the strip)")
    for w_ in warnings:
        print("WARN", w_)
    if a.film:
        dest = Path(a.film).expanduser() / "stack.js"
        shutil.copy(HERE / "stack.js", dest)
        print("stack.js ->", dest)


def main(argv=None):
    p = argparse.ArgumentParser(description="Sprite stacks: fake-3D pixel props from four flat drawings.")
    sp = p.add_subparsers(dest="cmd", required=True)
    c = sp.add_parser("carve", help="four drawings -> a layer strip, its .json and a check sheet")
    c.add_argument("views"); c.add_argument("strip")
    c.add_argument("--scale", type=int, default=0, help="the drawings' enlargement; 0 finds it")
    c.add_argument("--shade", type=float, default=0.18, help="how much walls darken (0 = none)")
    c.add_argument("--bg-tol", type=int, default=24, help="colour tolerance for an opaque sheet's background")
    c.add_argument("--film", help="copy stack.js into this film folder")
    c.set_defaults(fn=cmd_carve)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
