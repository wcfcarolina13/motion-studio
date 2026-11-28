"""Finish passes: a look applied to each composed frame after the page draws it and before the burned-in captions go on
(script.json "finish": "cel"). The page itself is never finished, so check, the dashboard and contact sheets show the
clean picture; `longform.py finish <film> --at <s>...` writes before/after stills to look at, and render applies it.

"cel" (cel style test, 2026-11-27): an old-cartoon print. Ink softened about 0.8 px, then an unsharp mask (radius 5,
60%) for a faint pale rim on strokes; chroma blurred more than luma and shifted 2 px off register; a slow low-frequency
luma mottle (+-2.5%) on fills; bloom from highlights; a warm tone with blacks lifted toward #2a1710; a light vignette;
grain in brightness only. Constants are set for a 1920 px long side and scale with the frame.

Time: the mottle drifts, blending between two fixed fields over MOTTLE frames (0.8 s), and the grain is held for two
frames (on twos, like the house overlays), so it moves at 15 fields a second rather than 30. Every value is a pure function of the frame number
and the seed, so a re-rendered chunk matches its neighbours.
"""
import numpy as np
from PIL import Image, ImageFilter

FINISHES = ("cel",)
MOTTLE = 24           # frames per mottle field
GRAIN = 2             # frames per grain field (on twos, like the house overlays)
FLOOR = np.array([0x2a, 0x17, 0x10], dtype=np.float32) / 255 * 0.55


def _blur(a, r):
    """Gaussian blur of a float32 0-255 plane (PIL, so it is fast)."""
    if r <= 0:
        return a
    return np.asarray(Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(r)), dtype=np.float32)


def _field(seed, k, shape, cell):
    """A fixed low-frequency noise field, -1..1, for field number k."""
    H, W = shape
    rng = np.random.default_rng((seed * 1_000_003 + k) & 0xFFFFFFFF)
    lo = rng.standard_normal((max(2, H // cell), max(2, W // cell))).astype(np.float32)
    lo = np.asarray(Image.fromarray(lo).resize((W, H), Image.BICUBIC), dtype=np.float32)
    return lo / (np.abs(lo).max() + 1e-6)


def _grain(seed, k, shape):
    rng = np.random.default_rng((seed * 7_919 + k * 104_729 + 17) & 0xFFFFFFFF)
    return rng.standard_normal(shape).astype(np.float32)


def _drift(fn, frame, period):
    """fn(k) blended from field k to k+1 across `period` frames (smoothstep), so it moves without popping. Divided by the
    blend's own spread, so the noise is as strong midway as at a field (a plain mix is 0.71 of it halfway)."""
    k, u = divmod(frame, period)
    u = u / period
    u = u * u * (3 - 2 * u)
    return (fn(k) * (1 - u) + fn(k + 1) * u) / np.sqrt((1 - u) ** 2 + u ** 2)


def cel(rgb, frame, seed=0):
    """rgb: HxWx3 uint8 -> HxWx3 uint8, the cel finish for this frame number."""
    H, W = rgb.shape[:2]
    k = max(W, H) / 1920.0
    im = Image.fromarray(rgb, "RGB").filter(ImageFilter.GaussianBlur(0.8 * k))
    im = im.filter(ImageFilter.UnsharpMask(radius=5 * k, percent=60, threshold=0))
    y, cb, cr = [np.asarray(c, dtype=np.float32) for c in im.convert("YCbCr").split()]
    s = int(round(2 * k))
    cb = np.roll(_blur(cb, 2.0 * k), (s, s), (0, 1))
    cr = np.roll(_blur(cr, 2.0 * k), (-s, s // 2), (0, 1))
    cell = max(8, int(90 * k))
    y = y * (1 + 0.025 * _drift(lambda n: _field(seed, n, (H, W), cell), frame, MOTTLE))
    rgb = np.asarray(Image.merge("YCbCr", [Image.fromarray(np.clip(c, 0, 255).astype(np.uint8)) for c in (y, cb, cr)])
                     .convert("RGB"), dtype=np.float32) / 255
    # bloom, worked at a quarter size: it is a wide blur, so nothing is lost and it is 16x cheaper
    q = (max(1, W // 4), max(1, H // 4))
    hi = np.clip(rgb - 0.72, 0, 1) / 0.28
    small = Image.fromarray((hi * 255).astype(np.uint8), "RGB").resize(q, Image.BILINEAR).filter(ImageFilter.GaussianBlur(18 * k / 4))
    bloom = np.asarray(small.resize((W, H), Image.BILINEAR), dtype=np.float32) / 255
    rgb = 1 - (1 - rgb) * (1 - 0.22 * bloom)
    rgb = rgb * np.array([1.03, 1.0, 0.94], dtype=np.float32)
    rgb = FLOOR + rgb * (1 - FLOOR)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    d = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
    rgb *= (1 - 0.16 * np.clip(d - 0.55, 0, 1) ** 1.6)[..., None]
    g = _grain(seed, frame // GRAIN, (H, W)) * 0.018          # held for two frames: on twos
    rgb = rgb + g[..., None]
    return (np.clip(rgb, 0, 1) * 255).astype(np.uint8)


def apply(spec, rgb, frame):
    """spec: "cel" or {"kind": "cel", "seed": n}."""
    kind, seed = (spec, 0) if isinstance(spec, str) else (spec.get("kind"), int(spec.get("seed", 0)))
    if kind == "cel":
        return cel(rgb, frame, seed)
    raise ValueError(f"finish \"{kind}\": use one of {', '.join(FINISHES)}")


def kind_of(spec):
    return spec if isinstance(spec, str) else (spec or {}).get("kind") if isinstance(spec, dict) else None
