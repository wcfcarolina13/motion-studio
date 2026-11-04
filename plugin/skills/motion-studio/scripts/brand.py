"""Brand lock: a channel's logo is drawn in its own colours wherever it appears, whatever the film's style.

A kit marks every drawing of the channel logo (an ident, a watermark, a chapter card's mark, the end screen's) with
data-logo="<where>" on the logo's group. A film names its channel with "channel" in script.json, and the channel's
palette lives outside the repo, in ~/.motion-studio/brand/<channel>.json (MS_BRAND_DIR overrides the folder):

    {"palette": {"ink": "#111111", "accent": "#3366cc", "paper": "#fafafa"}, "delta_e": 3}

brand_problems() finds each stretch where a marked logo is fully shown, shoots that moment twice (as drawn, and with
the logo hidden) to learn which pixels are the logo's, keeps the interior ones (2 px in from every edge, so
anti-aliased rims don't count), and measures each against the nearest palette colour by CIEDE2000. A logo fails when
more than `share` of its interior pixels are further than delta_e from every palette colour. The frames are the page's
own, as render draws them before encoding, so H.264's colour rounding plays no part. What is measured is what a viewer
sees, so a vignette, grain or style filter drawn over the logo counts against it: draw logos above them.
"""
import json
import os
import subprocess
from pathlib import Path

import numpy as np

BRAND_DIR = Path(os.environ.get("MS_BRAND_DIR", Path.home() / ".motion-studio" / "brand")).expanduser()


def load_brand(sc):
    """The film's channel brand ({"palette": {name: hex}, "delta_e": 3}), or None if the film names no channel. A
    script.json "brand" object is used as given (for tests); else "channel" names the file in BRAND_DIR."""
    if isinstance(sc.get("brand"), dict):
        return sc["brand"]
    ch = sc.get("channel")
    if not ch:
        return None
    p = BRAND_DIR / f"{ch}.json"
    if not p.exists():
        raise SystemExit(f'brand: the film names channel "{ch}" but {p} does not exist; write its palette there')
    return json.loads(p.read_text())


def hex_rgb(h):
    h = h.lstrip("#")
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)]


def rgb_lab(rgb):
    """sRGB (0..255, shape (..., 3)) to CIE Lab under D65."""
    c = np.asarray(rgb, float) / 255
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    xyz = c @ np.array([[0.4124564, 0.2126729, 0.0193339], [0.3575761, 0.7151522, 0.1191920], [0.1804375, 0.0721750, 0.9503041]])
    xyz /= np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > (6 / 29) ** 3, np.cbrt(xyz), xyz / (3 * (6 / 29) ** 2) + 4 / 29)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def delta_e2000(a, b):
    """CIEDE2000 between Lab arrays that broadcast together."""
    L1, a1, b1 = np.moveaxis(np.asarray(a, float), -1, 0)
    L2, a2, b2 = np.moveaxis(np.asarray(b, float), -1, 0)
    C1, C2 = np.hypot(a1, b1), np.hypot(a2, b2)
    Cm = (C1 + C2) / 2
    G = 0.5 * (1 - np.sqrt(Cm ** 7 / (Cm ** 7 + 25 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p, h2p = np.degrees(np.arctan2(b1, a1p)) % 360, np.degrees(np.arctan2(b2, a2p)) % 360
    dLp, dCp = L2 - L1, C2p - C1p
    dh = h2p - h1p
    dh = np.where(C1p * C2p == 0, 0, np.where(dh > 180, dh - 360, np.where(dh < -180, dh + 360, dh)))
    dHp = 2 * np.sqrt(C1p * C2p) * np.sin(np.radians(dh / 2))
    Lm, Cmp = (L1 + L2) / 2, (C1p + C2p) / 2
    hs = h1p + h2p
    hm = np.where(C1p * C2p == 0, hs, np.where(np.abs(h1p - h2p) <= 180, hs / 2, np.where(hs < 360, (hs + 360) / 2, (hs - 360) / 2)))
    T = 1 - 0.17 * np.cos(np.radians(hm - 30)) + 0.24 * np.cos(np.radians(2 * hm)) + 0.32 * np.cos(np.radians(3 * hm + 6)) \
        - 0.20 * np.cos(np.radians(4 * hm - 63))
    dth = 30 * np.exp(-((hm - 275) / 25) ** 2)
    Rc = 2 * np.sqrt(Cmp ** 7 / (Cmp ** 7 + 25 ** 7))
    Sl = 1 + 0.015 * (Lm - 50) ** 2 / np.sqrt(20 + (Lm - 50) ** 2)
    Sc, Sh = 1 + 0.045 * Cmp, 1 + 0.015 * Cmp * T
    Rt = -np.sin(np.radians(2 * dth)) * Rc
    return np.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2 + Rt * (dCp / Sc) * (dHp / Sh))


def decode(png, w, h):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", "-", "-vf", f"scale={w}:{h},format=rgb24", "-f", "rawvideo", "-"],
                         input=png, capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 3).astype(np.int16)


# every element marked data-logo that is fully shown now (0.95 opacity or more after its parents, not hidden), with
# its box on the frame
LOGOS = """() => {
  const shown = n => { let o = 1; for (; n && n.nodeType === 1 && n.id !== 'svg'; n = n.parentNode) {
      const cs = getComputedStyle(n); if (cs.display === 'none' || cs.visibility === 'hidden') return 0; o *= +cs.opacity; } return o; };
  const st = document.getElementById('stage').getBoundingClientRect();
  return [...document.querySelectorAll('#svg [data-logo]')].map((e, i) => [i, e.getAttribute('data-logo') || 'logo', shown(e), e.getBoundingClientRect()])
    .filter(([, , o, r]) => o >= 0.95 && r.width >= 8 && r.height >= 8 && r.right > st.left && r.left < st.right && r.bottom > st.top && r.top < st.bottom)
    .map(([i, n, , r]) => [i, n, r.left - st.left, r.top - st.top, r.width, r.height]);
}"""
HIDE = "([i, on]) => { const e = document.querySelectorAll('#svg [data-logo]')[i]; e.style.visibility = on ? '' : 'hidden'; }"


def logo_moments(f, step=0.5):
    """[(t, index, where, box)]: the middle of each stretch in which a marked logo is fully shown, sampled every `step`
    seconds (a logo up for less than one step may be missed)."""
    runs, live = [], {}
    n = int(f.dur / step)
    for k in range(n + 1):
        t = min(k * step, f.dur - 1e-3)
        f.seek(t)
        now = {r[0]: r for r in f.page.evaluate(LOGOS)}
        for i in list(live):
            if i not in now:
                runs.append(live.pop(i) + [t - step])
        for i, r in now.items():
            live.setdefault(i, [t, r])
    runs += [v + [f.dur] for v in live.values()]
    return [(round((t0 + t1) / 2, 3), r[0], r[1], r[2:]) for t0, r, t1 in runs]


def brand_problems(film, sc, share=0.02, step=0.5):
    """(report lines, problems) for every marked logo moment: what share of its interior pixels lies within delta_e of
    the channel palette. No channel or no marked logo: nothing to check."""
    import render
    try:
        brand = load_brand(sc)
    except SystemExit as e:          # a channel with no palette file: a problem to fix, not a crash of check
        return [], [str(e).removeprefix("brand: ")]
    report, problems = [], []
    with render.Film(film) as f:
        moments = logo_moments(f, step)
        if not moments:
            return ["brand: no logo marked data-logo in this film"], []
        if not brand:
            return [], [f'brand: {len(moments)} logo moment(s) but the film names no "channel" with a palette']
        pal = np.array([hex_rgb(c) for c in brand["palette"].values()])
        names = list(brand["palette"])
        lab_pal, lim = rgb_lab(pal), float(brand.get("delta_e", 3))
        for t, i, where, (x, y, w, h) in moments:
            on = decode(f.shot(t), f.w, f.h)
            f.page.evaluate(HIDE, [i, False]); off = decode(f.shot(t), f.w, f.h); f.page.evaluate(HIDE, [i, True])
            x0, y0, x1, y1 = max(0, int(x)), max(0, int(y)), min(f.w, int(x + w) + 1), min(f.h, int(y + h) + 1)
            m = np.abs(on - off)[y0:y1, x0:x1].sum(-1) > 6
            core = m.copy()      # 2 px in from every edge of the logo's own pixels
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    core &= np.roll(np.roll(m, dy, 0), dx, 1)
            core[:2], core[-2:], core[:, :2], core[:, -2:] = False, False, False, False
            px = on[y0:y1, x0:x1][core]
            if len(px) < 20:
                report.append(f"brand: {where} at {t:.1f} s: too small to measure ({len(px)} interior pixels)")
                continue
            de = delta_e2000(rgb_lab(px)[:, None, :], lab_pal[None, :, :])
            near, best = de.min(1), de.argmin(1)
            off_share = float((near > lim).mean())
            used = ", ".join(f"{names[k]} {np.mean(best == k):.0%}" for k in range(len(names)) if np.mean(best == k) >= 0.01)
            report.append(f"brand: {where} at {t:.1f} s: {1 - off_share:.0%} of {len(px)} logo pixels within dE {lim:g} ({used})")
            if off_share > share:
                bad = px[near > lim]
                med = "#" + "".join(f"{int(v):02x}" for v in np.median(bad, 0))
                k = int(np.median(best[near > lim]))
                problems.append(f"{where} logo at {t:.1f} s: {off_share:.0%} of its pixels are off the channel palette "
                                f"(typically {med}, dE {float(np.median(near[near > lim])):.1f} from {names[k]}): a style, grade "
                                "or vignette is recolouring the logo; draw it in the palette colours, above the style's filters and the vignette")
    return report, problems
