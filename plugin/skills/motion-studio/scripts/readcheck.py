"""Readability measures: text contrast against the pixels actually behind each glyph, and the flash rate.

Contrast (WCAG 2 relative luminance and contrast ratio): two pictures of the same region, one with the text drawn and
one with it hidden, are compared. Pixels that change are the text. Its core (the pixels that change most, so
antialiased edges don't count) is split into glyphs at blank columns. Each glyph's contrast is the median, over its
core pixels, of the ratio between the text's colour there and the colour behind it there. The block's contrast is its
worst glyph. A text drawn in nearly the colour behind it barely changes any pixel, and gets a ratio of 1.

Flash rate (WCAG 2.3.1, general flash): a flash is a pair of opposing changes in relative luminance of at least 0.1
where the darker state is under 0.8. A frame fails when, in some block the size of a 10-degree field (a third of the
frame's width, 4:3), at least a quarter of the pixels changed more than 6 times (more than 3 flashes) in the second
up to that frame. Every frame is read, at a reduced size.

  python3 readcheck.py flash <video.mp4> [--width 96]
"""
import subprocess
import sys

import numpy as np

_LIN = np.array([(c / 255) / 12.92 if c / 255 <= 0.04045 else (((c / 255) + 0.055) / 1.055) ** 2.4 for c in range(256)])


def luminance(rgb):
    """WCAG relative luminance (0-1) of an H x W x 3 uint8 sRGB array."""
    lin = _LIN[rgb]
    return 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]


def ratio(l1, l2):
    """WCAG contrast ratio (1-21), elementwise."""
    hi, lo = np.maximum(l1, l2), np.minimum(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def glyph_contrasts(drawn, behind, min_diff=8, core_share=0.5, min_core=12):
    """Per-glyph contrast of a text against what is behind it. drawn, behind: H x W x 3 uint8 pictures of one region,
    with the text and with it hidden. Returns [(x0, x1, ratio)] per glyph, left to right, or [] if the text changed
    no pixel at all (then it cannot be seen: treat it as ratio 1)."""
    diff = np.abs(drawn.astype(np.int16) - behind.astype(np.int16)).max(axis=2)
    mask = diff > min_diff
    cols = mask.any(axis=0)
    runs, x = [], 0
    while x < len(cols):
        if cols[x]:
            x0 = x
            while x < len(cols) and cols[x]:
                x += 1
            runs.append((x0, x))
        x += 1
    if not runs:
        return []
    ld, lb = luminance(drawn), luminance(behind)
    out = []
    for x0, x1 in runs:
        d = diff[:, x0:x1]
        core = d >= max(min_diff + 1, core_share * d.max())
        if core.sum() < min_core:           # an antialiasing sliver (a box edge composited a shade differently): too small to judge alone. A full stop at the 56 px floor has about 30 core pixels
            continue
        out.append((x0, x1, float(np.median(ratio(ld[:, x0:x1][core], lb[:, x0:x1][core])))))
    if not out:                             # only small marks (punctuation, a thin line of text): judge them together
        core = diff >= max(min_diff + 1, core_share * diff.max())
        out.append((runs[0][0], runs[-1][1], float(np.median(ratio(ld[core], lb[core])))))
    return out


def worst_contrast(drawn, behind):
    """(the lowest glyph ratio, glyph count) of a text; (1.0, 0) when the text changed no pixel."""
    g = glyph_contrasts(drawn, behind)
    return (min(r for _, _, r in g), len(g)) if g else (1.0, 0)


def flash_frames(frames_iter, fps, w, h, delta=0.1, dark=0.8, per_second=3, area=0.25):
    """[(t, flashes, share)] for every frame that fails: frames_iter yields H x W x 3 uint8 frames in order."""
    fw = max(1, round(w / 3)); fh = max(1, min(h, round(fw * 3 / 4)))
    win = max(1, int(round(fps)))
    ring, total = [], np.zeros((h, w), np.int16)
    ext = d = None
    out = []
    for n, rgb in enumerate(frames_iter):
        L = luminance(rgb)
        if ext is None:
            ext, d = L.copy(), np.zeros((h, w), np.int8)
            ring.append(np.zeros((h, w), np.bool_))
            continue
        # follow the current direction's extreme, then look for a reversal of at least delta from it
        rising, falling = d > 0, d < 0
        ext = np.where(rising & (L > ext), L, ext)
        ext = np.where(falling & (L < ext), L, ext)
        low = np.minimum(L, ext) < dark
        up = (L - ext >= delta) & low & ~rising
        down = (ext - L >= delta) & low & ~falling
        change = up | down
        d = np.where(up, 1, np.where(down, -1, d)).astype(np.int8)
        ext = np.where(change, L, ext)
        ring.append(change); total += change
        if len(ring) > win:
            total -= ring.pop(0)
        hot = (total > 2 * per_second).astype(np.int32)
        if not hot.any():
            continue
        ii = np.pad(hot.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
        box = ii[fh:, fw:] - ii[:-fh, fw:] - ii[fh:, :-fw] + ii[:-fh, :-fw]
        share = float(box.max()) / (fw * fh)
        if share >= area:
            y, x = np.unravel_index(int(box.argmax()), box.shape)    # the flash count of the worst field, not one pixel's
            out.append((n / fps, int(np.median(total[y:y + fh, x:x + fw][hot[y:y + fh, x:x + fw] > 0])) // 2, share))
    return out


def video_frames(video, width=96):
    """(frames iterator, fps, w, h) of a video at `width` px wide, every frame, RGB."""
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate",
                            "-of", "csv=p=0", str(video)], capture_output=True, text=True, check=True).stdout.strip().split(",")
    W, H = int(probe[0]), int(probe[1])
    num, den = (probe[2].split("/") + ["1"])[:2]
    fps = float(num) / float(den or 1)
    w = width; h = max(2, round(H * w / W))

    def frames():
        p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(video), "-vf", f"scale={w}:{h}:flags=area,format=rgb24",
                              "-f", "rawvideo", "-"], stdout=subprocess.PIPE)
        size = w * h * 3
        while True:
            b = p.stdout.read(size)
            if len(b) < size:
                break
            yield np.frombuffer(b, np.uint8).reshape(h, w, 3)
        p.stdout.close(); p.wait()
    return frames(), fps, w, h


def flash_spans(video, width=96, gap=0.5):
    """[(t0, t1, most flashes in a second, largest share of a field)] where the video breaks the flash limit."""
    it, fps, w, h = video_frames(video, width)
    spans = []
    for t, n, share in flash_frames(it, fps, w, h):
        if spans and t - spans[-1][1] <= gap:
            a, _, m, s = spans[-1]
            spans[-1] = (a, t, max(m, n), max(s, share))
        else:
            spans.append((t, t, n, share))
    return spans


def main(argv):
    if not argv or argv[0] != "flash" or len(argv) < 2:
        print(__doc__); return 2
    width = int(argv[argv.index("--width") + 1]) if "--width" in argv else 96
    spans = flash_spans(argv[1], width)
    print(f"flash rate: {len(spans)} stretch(es) over 3 flashes a second")
    for t0, t1, n, s in spans:
        print(f"  {t0:.2f}-{t1:.2f} s: {n} flashes in a second over {s:.0%} of a 10-degree field")
    return 1 if spans else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
