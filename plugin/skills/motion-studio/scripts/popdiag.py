"""Where a one-frame pop comes from: the page, the finish, the chunk join or the encode.

final's pop check names frames that differ from both neighbours. This takes those frames and, for each, measures the
same frame four ways, so the cause is read off the numbers rather than guessed:

  video      out/silent.mp4 (finished) and out/silent-raw.mp4 (unfinished, when the film has a finish), k-1, k, k+1
  sequence   one page draws k-2..k+1 in order, raw and finished
  fresh      a new page draws only k, raw and finished

and which render chunk k falls in, and how far into it. A pop in silent.mp4 but not in silent-raw.mp4 is the finish.
One in the raw video but in neither page run is the chunk join or the encode. One where fresh differs from sequence is
the page's own state.

    python3 popdiag.py <film> 1436,1724,1940        # prints a table; PNGs go to <film>/review/pops/

Built for the first cel render (2026-11-28), where pops sat at chunk starts on the Mac but couldn't be made here.
"""
import io
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

FPS = 30


def video_frames(video, ks, w, h):
    """{k: H x W x 3 uint8} for frames k of a video (by exact frame number)."""
    sel = "+".join(f"eq(n\\,{k})" for k in sorted(set(ks)))
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vf", f"select='{sel}',scale={w}:{h},format=rgb24",
                          "-vsync", "0", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    arr = np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3)
    return dict(zip(sorted(set(ks)), arr))


def decode(b, w, h):
    from PIL import Image
    return np.asarray(Image.open(io.BytesIO(b)).convert("RGB").resize((w, h)))


def d(a, b):
    return round(float(np.abs(a.astype(int) - b.astype(int)).mean()), 2)


def pop_row(fr, k):
    """(k vs k-1, k vs k+1, k-1 vs k+1): a pop is the first two high and the third low."""
    return d(fr[k], fr[k - 1]), d(fr[k], fr[k + 1]), d(fr[k - 1], fr[k + 1])


def main(film, ks, out=None):
    import longform
    import render
    from PIL import Image
    film = Path(film).resolve()
    out = Path(out or film / "review" / "pops"); out.mkdir(parents=True, exist_ok=True)
    tl = longform.load_timeline(film)
    n = int(round(tl["duration"] * FPS))
    ranges = longform.part_ranges(tl, n)
    w, h = 480, 270
    vids = {"video": film / "out" / "silent.mp4", "video raw": film / "out" / "silent-raw.mp4"}
    want = [j for k in ks for j in (k - 1, k, k + 1)]
    got = {name: video_frames(v, want, w, h) for name, v in vids.items() if v.exists()}
    lines = [f"film {film.name}: {n} frames, {len(ranges)} chunks; each cell is k vs k-1 / k vs k+1 / k-1 vs k+1 (mean levels)"]
    for k in ks:
        part = next(((a, b) for a, b in ranges if a <= k < b), None)
        seg = next((s["id"] for s in tl["segments"] if s["t0"] <= k / FPS < s["t1"]), "?")
        row = [f"frame {k} ({k / FPS:.2f} s, {seg}); chunk {part[0]}-{part[1]}, {k - part[0]} frame(s) in" if part else f"frame {k}"]
        for name, fr in got.items():
            if all(j in fr for j in (k - 1, k, k + 1)):
                row.append(f"  {name:<14} {pop_row(fr, k)}")
                Image.fromarray(fr[k]).save(out / f"{k}-{name.replace(' ', '-')}.png")
        with render.Film(film, video=True) as f:
            f.frame((k - 2) / FPS, FPS)
            seq = {j: f.frame(j / FPS, FPS) for j in (k - 1, k, k + 1)}
        with render.Film(film, video=True) as f:
            fresh = f.frame(k / FPS, FPS)
        for which, idx in (("finished", 0), ("raw", 1)):
            if seq[k][idx] is None:
                continue
            s_ = {j: decode(seq[j][idx], w, h) for j in seq}
            fz = decode(fresh[idx], w, h)
            row.append(f"  page {which:<9} {pop_row(s_, k)}   fresh vs sequence {d(fz, s_[k])}")
            Image.fromarray(s_[k]).save(out / f"{k}-page-{which}-sequence.png")
            Image.fromarray(fz).save(out / f"{k}-page-{which}-fresh.png")
        lines += row
    (out / "popdiag.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"-> {out}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    sys.path.insert(0, str(Path(__file__).parent))
    main(sys.argv[1], [int(x) for x in sys.argv[2].split(",") if x.strip()])
