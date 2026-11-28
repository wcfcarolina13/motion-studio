"""Where a one-frame pop comes from: the page, the finish, the chunk join or the encode.

final's pop check names frames that differ from both neighbours. This takes those frames and, for each, measures the
same frame four ways, so the cause is read off the numbers rather than guessed:

  video      out/silent.mp4 (finished) and out/silent-raw.mp4 (unfinished, when the film has a finish), k-1, k, k+1
  sequence   one page draws k-2..k+1 in order, raw and finished
  fresh      a new page draws only k, raw and finished

and which render chunk k falls in, and how far into it. A pop in silent.mp4 but not in silent-raw.mp4 is the finish.
One in the raw video but in neither page run is the chunk join or the encode. One where fresh differs from sequence is
the page's own state.

For a pop at a chunk's first frame it also reads the two chunk files on their own (out/parts: the last frame of the
chunk before, the first of this one) and gives each frame's mean level against the page, so the side that is off is
named. And it encodes the page's own frames k-3..k+2 with this machine's ffmpeg and the render's encoder settings, once
as one chunk and once cut at k, so an encoder that shifts a chunk's colours shows here without a render.

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
    """{k: H x W x 3 uint8} for frames k of a video (by exact frame number; -1 is the last frame)."""
    ks = sorted(set(ks))
    if ks == [-1]:
        n = int(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-count_frames", "-show_entries",
                                "stream=nb_read_frames", "-of", "csv=p=0", str(video)], capture_output=True, text=True, check=True).stdout.strip())
        return {-1: video_frames(video, [n - 1], w, h)[n - 1]}
    sel = "+".join(f"eq(n\\,{k})" for k in ks)
    for keep in (["-fps_mode", "passthrough"], ["-vsync", "0"]):     # -vsync is gone from ffmpeg 7+, -fps_mode new in 5.1
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vf", f"select='{sel}',scale={w}:{h},format=rgb24",
                            *keep, "-f", "rawvideo", "-"], capture_output=True)
        if r.returncode == 0:
            break
    else:
        raise SystemExit(f"ffmpeg could not read {video}: {r.stderr.decode()[-300:]}")
    arr = np.frombuffer(r.stdout, np.uint8).reshape(-1, h, w, 3)
    return dict(zip(ks, arr))


def decode(b, w, h):
    from PIL import Image
    return np.asarray(Image.open(io.BytesIO(b)).convert("RGB").resize((w, h)))


def d(a, b):
    return round(float(np.abs(a.astype(int) - b.astype(int)).mean()), 2)


def level(a, b):
    """Mean of a - b over the picture (signed: how much brighter a is)."""
    return round(float((a.astype(int) - b.astype(int)).mean()), 2)


def encode_test(frames, cut, w, h, tmp):
    """Encode a run of frame images (bytes) the way render does (lossless chunks, joined and encoded once), whole and
    cut in two at index cut. (step at the cut, whole; step at the cut, cut in two) in mean levels."""
    import longform
    out = {}
    for name, groups in (("whole", [frames]), ("cut", [frames[:cut], frames[cut:]])):
        files = []
        for i, g in enumerate(groups):
            f = tmp / f"{name}{i}.mp4"
            p = subprocess.Popen(longform.encoder_cmd(f, None, None), stdin=subprocess.PIPE)
            for b in g:
                p.stdin.write(b)
            p.stdin.close()
            if p.wait():
                raise SystemExit("ffmpeg failed in the encode test")
            files.append(f)
        lst = tmp / f"{name}.txt"
        lst.write_text("".join(f"file '{f}'\n" for f in files))
        joined = tmp / f"{name}.mp4"
        longform.join_parts(lst, joined)
        fr = video_frames(joined, list(range(len(frames))), w, h)
        out[name] = d(fr[cut], fr[cut - 1])
    return out["whole"], out["cut"]


def pop_row(fr, k):
    """(k vs k-1, k vs k+1, k-1 vs k+1): a pop is the first two high and the third low."""
    return d(fr[k], fr[k - 1]), d(fr[k], fr[k + 1]), d(fr[k - 1], fr[k + 1])


def main(film, ks, out=None):
    import tempfile
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
    lines = [f"film {film.name}: {n} frames, {len(ranges)} chunks; each cell is k vs k-1 / k vs k+1 / k-1 vs k+1 (mean "
             f"levels); 'against page' is each video frame's mean level minus the page's, for k-1, k, k+1"]
    for k in ks:
        part = next(((a, b) for a, b in ranges if a <= k < b), None)
        seg = next((s["id"] for s in tl["segments"] if s["t0"] <= k / FPS < s["t1"]), "?")
        row = [f"frame {k} ({k / FPS:.2f} s, {seg}); chunk {part[0]}-{part[1]}, {k - part[0]} frame(s) in" if part else f"frame {k}"]
        with render.Film(film, video=True) as f:
            f.frame((k - 4) / FPS, FPS)
            seq = {j: f.frame(j / FPS, FPS) for j in range(k - 3, k + 3)}
        with render.Film(film, video=True) as f:
            fresh = f.frame(k / FPS, FPS)
        page = {}
        for which, idx in (("finished", 0), ("raw", 1)):
            if seq[k][idx] is None:
                continue
            page[which] = {j: decode(seq[j][idx], w, h) for j in seq}
        for name, fr in got.items():
            if all(j in fr for j in (k - 1, k, k + 1)):
                pg = page.get("raw" if name == "video raw" else "finished") or page.get("finished")
                vs = f"   against page {tuple(level(fr[j], pg[j]) for j in (k - 1, k, k + 1))}" if pg else ""
                row.append(f"  {name:<14} {pop_row(fr, k)}{vs}")
                Image.fromarray(fr[k]).save(out / f"{k}-{name.replace(' ', '-')}.png")
                Image.fromarray(fr[k - 1]).save(out / f"{k - 1}-{name.replace(' ', '-')}.png")
        for which, idx in (("finished", 0), ("raw", 1)):
            if which not in page:
                continue
            s_ = page[which]
            fz = decode(fresh[idx], w, h)
            row.append(f"  page {which:<9} {pop_row(s_, k)}   fresh vs sequence {d(fz, s_[k])}")
            Image.fromarray(s_[k]).save(out / f"{k}-page-{which}-sequence.png")
            Image.fromarray(fz).save(out / f"{k}-page-{which}-fresh.png")
        prev = next(((a, b) for a, b in ranges if b == k), None)
        if part and part[0] == k and prev:
            for tag, suffix in (("", ".mp4"), (" raw", ".raw.mp4")):
                pa = film / "out" / "parts" / f"p{prev[0]:07d}-{prev[1]:07d}{suffix}"
                pb = film / "out" / "parts" / f"p{part[0]:07d}-{part[1]:07d}{suffix}"
                pg = page.get("raw" if tag else "finished")
                if pa.exists() and pb.exists() and pg:
                    last, first = video_frames(pa, [-1], w, h)[-1], video_frames(pb, [0], w, h)[0]
                    row.append(f"  chunk files{tag:<4} last of {pa.name} against page {level(last, pg[k - 1])}, first of "
                               f"{pb.name} against page {level(first, pg[k])}, step {d(first, last)}")
        idx = 1 if seq[k][1] is not None else 0
        with tempfile.TemporaryDirectory() as tmp:
            whole, cut = encode_test([seq[j][idx] for j in range(k - 3, k + 3)], 3, w, h, Path(tmp))
        row.append(f"  encode test    page frames k-3..k+2 through this ffmpeg: step at k {whole} as one chunk, {cut} cut at k")
        lines += row
    (out / "popdiag.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"-> {out}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    sys.path.insert(0, str(Path(__file__).parent))
    main(sys.argv[1], [int(x) for x in sys.argv[2].split(",") if x.strip()])
