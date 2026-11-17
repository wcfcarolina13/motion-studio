"""Motion gate: a video must move from its first frame and never sit still for long.

Mean absolute frame-to-frame difference (grayscale levels 0-255, frames scaled to 1/4) per window. Fails when
  - any window in the first `first` seconds, or
  - the mean of any `stretch`-second run of windows
falls under the floor. Defaults: 0.5 s windows at 10 fps, first 3 s, 2 s stretches, and
floor = max(0.02, 0.25 x the film's median window) unless `floor` is given.
A loop frame may be still only AT the seam: ramp motion up from 0 there instead of holding the frame.
Deliberate holds are excused with `stills` [(t0, t1), ...] (seconds), e.g. from the board's "stills" list.
`mask` [(y0, y1), ...] in source pixels blanks rows before measuring, e.g. a caption band, so changing captions
don't count as motion.

  python3 motioncheck.py <video.mp4> [--floor X] [--ratio 0.25] [--first 3] [--stretch 2] [--still 57.6-58.2] [--mask 1380-1545] [--report]
"""
import subprocess
import sys

import numpy as np

WINDOW, FPS = 0.5, 10


def windows(video, mask=(), fps=FPS, window=WINDOW):
    """Per-window mean motion, in grayscale levels."""
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                            "-of", "csv=p=0", str(video)], capture_output=True, text=True, check=True).stdout.strip()
    W, H = (int(x) for x in probe.split(",")[:2])
    w, h = max(2, W // 4 // 2 * 2), max(2, H // 4 // 2 * 2)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vf", f"fps={fps},scale={w}:{h},format=gray",
                          "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    f = np.frombuffer(raw, np.uint8).reshape(-1, h, w).astype(np.int16)
    for y0, y1 in mask:
        f[:, int(y0 * h / H):int(y1 * h / H), :] = 0
    d = np.abs(np.diff(f, axis=0)).mean(axis=(1, 2))
    per = max(1, int(round(fps * window)))
    return [float(d[i:i + per].mean()) for i in range(0, len(d) - per + 1, per)]


def check(win, floor=None, ratio=0.25, first=3.0, stretch=2.0, stills=(), window=WINDOW):
    """(floor, faults) for per-window motion values."""
    med = float(np.median(win)) if win else 0.0
    floor = floor if floor is not None else max(0.02, ratio * med)
    excused = lambda t0, t1: any(a <= t0 + 1e-6 and t1 <= b + 1e-6 for a, b in stills)
    faults = []
    for i, v in enumerate(win[:int(round(first / window))]):
        t0 = i * window
        if v < floor and not excused(t0, t0 + window):
            faults.append(f"{t0:.1f}-{t0 + window:.1f} s: {v:.3f} under {floor:.3f} in the first {first:g} s")
    n = max(1, int(round(stretch / window)))
    for i in range(len(win) - n + 1):
        m, t0 = sum(win[i:i + n]) / n, i * window
        if m < floor and not excused(t0, t0 + stretch):
            faults.append(f"{t0:.1f}-{t0 + stretch:.1f} s: {stretch:g} s mean {m:.3f} under {floor:.3f}")
    return floor, faults


def _spans(args, flag):
    return [tuple(float(x) for x in args[i + 1].split("-")) for i, a in enumerate(args) if a == flag]


def main(argv):
    video = argv[0]
    val = lambda k, d: float(argv[argv.index(k) + 1]) if k in argv else d
    win = windows(video, mask=_spans(argv, "--mask"))
    floor, faults = check(win, floor=val("--floor", None), ratio=val("--ratio", 0.25), first=val("--first", 3.0),
                          stretch=val("--stretch", 2.0), stills=_spans(argv, "--still"))
    if "--report" in argv:
        for i, v in enumerate(win):
            print(f"{i * WINDOW:6.1f} s {v:8.3f}")
    print(f"motioncheck: {len(win)} windows; median {np.median(win) if win else 0:.3f}; floor {floor:.3f}; {len(faults)} fault(s)")
    for f in faults[:20]:
        print("  " + f)
    return 1 if faults else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
