"""Four-view drawings of two Shoestring props, drawn in code: a wheelie bin and a smoothie stand.

  python3 draw_props.py <out_dir>      writes bin-views.png and stand-views.png (8x enlarged, transparent)

Then carve each with `stack.py carve`. This is the "no image model" route: every art pixel is placed here, so the four
views agree exactly. The palette is the Shoestring pixel kit's.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugin/skills/motion-studio/scripts"))
from stack import write_png  # noqa: E402

HEX = {"ink": "#1f1f2a", "navy": "#24408e", "navyd": "#1a2f6b", "navyl": "#3c5cb3", "mustard": "#e3b23c",
       "mustardd": "#b98a22", "red": "#c7372f", "redd": "#97261f", "cream": "#f1e9d3", "creamd": "#d8ccad",
       "metal": "#ddd7c7", "metald": "#a9a291", "purple": "#6b4a9b", "wood": "#9a6a3c", "woodd": "#74502c",
       "green": "#4f8a4a", "greend": "#3a6838", "white": "#fbf7ee"}
RGB = {k: tuple(int(v[i:i + 2], 16) for i in (1, 3, 5)) for k, v in HEX.items()}


def canvas(h, w):
    return np.zeros((h, w, 4), np.uint8)


def fill(a, rows, cols, name):
    a[rows, cols, :3] = RGB[name]
    a[rows, cols, 3] = 255


def sheet(views, scale=8, gap=4):
    h = max(v.shape[0] for v in views)
    w = sum(v.shape[1] for v in views) + gap * (len(views) + 1)
    out = canvas(h + 2 * gap, w)
    x = gap
    for v in views:
        out[gap + h - v.shape[0]:gap + h, x:x + v.shape[1]] = v      # bottoms aligned; empty rows are transparent
        x += v.shape[1] + gap
    return out.repeat(scale, 0).repeat(scale, 1)


def wheelie_bin():
    W, L, H = 14, 16, 22           # width (x), length front-to-back (y), height (layers)
    top, side, front, back = canvas(L, W), canvas(H, L), canvas(H, W), canvas(H, W)
    # TOP: the lid, front at the bottom; hinge and handle across the back
    fill(top, slice(0, L), slice(0, W), "navyl")
    fill(top, slice(0, 2), slice(0, W), "ink")          # handle bar
    fill(top, slice(2, 4), slice(0, W), "navyd")        # hinge
    fill(top, slice(L - 2, L), slice(2, W - 2), "navy") # lid lip at the front
    fill(top, slice(6, 10), slice(5, 9), "mustard")     # lid sticker
    # SIDE (from the left, front pointing left = column 0 is the front)
    for r in range(H):
        if r < 3:                       # lid: the full length
            fill(side, r, slice(0, L), "navyl" if r == 0 else "navy")
        elif r < 19:                    # body: the front slants in toward the bottom
            inset = (r - 3) // 6
            fill(side, r, slice(1 + inset, L - 1), "navy")
            fill(side, r, slice(L - 3, L - 1), "navyd")     # back corner shadow
        else:                           # base and wheels at the back
            fill(side, r, slice(2, L - 1), "navyd")
    fill(side, slice(0, 3), slice(L - 2, L), "ink")     # handle seen side-on
    fill(side, slice(17, 22), slice(L - 6, L - 1), "ink")   # wheel
    fill(side, slice(18, 21), slice(L - 5, L - 2), "metald")
    fill(side, slice(7, 9), slice(3, L - 4), "navyd")   # moulded rib
    # FRONT and BACK: same silhouette (the body tapers a little toward the base)
    for v, is_front in ((front, True), (back, False)):
        for r in range(H):
            if r < 3:
                fill(v, r, slice(0, W), "navyl" if r == 0 else "navy")
            elif r < 19:
                inset = (r - 3) // 8
                fill(v, r, slice(1 + inset, W - 1 - inset), "navy")
            else:
                fill(v, r, slice(2, W - 2), "navyd")
        if is_front:
            fill(v, slice(9, 14), slice(3, W - 3), "white")     # label
            fill(v, slice(10, 13), slice(5, W - 5), "green")    # recycling mark
            fill(v, slice(3, 4), slice(1, W - 1), "navyd")      # lid shadow
        else:
            fill(v, slice(0, 3), slice(0, W), "ink")            # handle
            fill(v, slice(17, 22), slice(1, 4), "ink")          # wheels
            fill(v, slice(17, 22), slice(W - 4, W - 1), "ink")
            fill(v, slice(18, 21), slice(2, 3), "metald")
            fill(v, slice(18, 21), slice(W - 3, W - 2), "metald")
    return [top, side, front, back]


def smoothie_stand():
    W, L, H = 26, 16, 28
    top, side, front, back = canvas(L, W), canvas(H, L), canvas(H, W), canvas(H, W)
    # TOP: striped awning over the front half, flat roof behind, a cup sign in the middle
    fill(top, slice(0, L), slice(0, W), "creamd")
    for x in range(0, W, 4):
        fill(top, slice(6, L), slice(x, x + 2), "red")
        fill(top, slice(6, L), slice(x + 2, x + 4), "cream")
    fill(top, slice(2, 6), slice(10, 16), "mustard")    # sign top
    fill(top, slice(3, 5), slice(12, 14), "red")        # straw
    # SIDE: counter body, awning sloping out over the front, sign on the roof
    for r in range(H):
        if r < 6:                                    # sign, toward the back half
            fill(side, r, slice(10, 14), "mustard" if r > 1 else "red")
        elif r < 10:                                 # awning: reaches further forward as it drops
            fill(side, r, slice(0 if r >= 8 else 2, L), "red" if r % 2 else "cream")
        elif r < 24:                                 # counter
            fill(side, r, slice(3, L), "wood")
            fill(side, r, slice(L - 2, L), "woodd")
        else:                                        # base
            fill(side, r, slice(3, L), "woodd")
    fill(side, slice(12, 15), slice(5, L - 3), "woodd")  # side panel line
    # FRONT: awning stripes, serving window, counter, a price board
    for v, is_front in ((front, True), (back, False)):
        for r in range(H):
            if r < 6:
                fill(v, r, slice(10, 16), "mustard" if r > 1 else "red")
            elif r < 10:
                for x in range(0, W, 4):
                    fill(v, r, slice(x, x + 2), "red")
                    fill(v, r, slice(x + 2, x + 4), "cream")
            elif r < 24:
                fill(v, r, slice(0, W), "wood")
            else:
                fill(v, r, slice(0, W), "woodd")
        if is_front:
            fill(v, slice(10, 16), slice(3, W - 3), "ink")        # serving window
            fill(v, slice(12, 16), slice(5, 9), "purple")         # cups on the counter
            fill(v, slice(12, 16), slice(11, 15), "mustard")
            fill(v, slice(12, 16), slice(17, 21), "green")
            fill(v, slice(16, 18), slice(1, W - 1), "creamd")     # counter top edge
            fill(v, slice(19, 23), slice(8, 18), "cream")         # price board
            fill(v, slice(20, 22), slice(10, 16), "red")
        else:
            fill(v, slice(12, 20), slice(10, 16), "woodd")        # back door
            fill(v, slice(15, 17), slice(14, 15), "metal")
    return [top, side, front, back]


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    write_png(out / "bin-views.png", sheet(wheelie_bin()))
    write_png(out / "stand-views.png", sheet(smoothie_stand()))
    print(out / "bin-views.png", out / "stand-views.png")
