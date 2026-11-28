"""Beat landings measured on the finished video, not on the page.

`check` and the page-level cue checks look at the film before it is rendered, so they can't see a cue that drifts in
the render, the chunk joins or the mux. This reads the video itself: for every word cue in timeline.js (picture flips,
callouts, counters, sfx, lettered screens) and every cut between segments, it finds the frame where the picture starts
to change near the word and reports how far that lands from the word. It also lines the video's soundtrack up with
out/mix.wav, so an audio offset in the export shows up.

Built from the render-layer test (2026-11-28), where the same measure read our engine, Remotion and Motion Canvas cuts
of the Survive Year 1 intro and found every picture flip within a frame of its word. It needs nothing from the
renderer, only the video and the timeline.

    python3 cuecheck.py <film> [video]      # prints one line per cue; exit 1 when a clear landing is off
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

FPS = 30
W, H = 192, 108
EARLY, LATE = 0.25, 0.20     # a clear change this far before or after its word fails (the engine lands callouts about
                             # 2 frames early on purpose, "anticipate"; 0.2 s is 6 frames, past any rounding)
CLEAR = 3.0                  # the change must stand this far (luma levels, 12x12 block) above the scene's own motion
CROWD = 0.35                 # a cue this close after another can't be told apart from it: it is listed, not judged
OFFSET_MS = 45               # soundtrack against the mix: more than this is more than a frame and a bit


def signal(video, fps=FPS):
    """Per frame, the largest mean luma change in any 12x12 block (so a small callout counts as well as a cut)."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vf", f"fps={fps},scale={W}:{H},format=gray",
                          "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, H, W).astype(np.float32)
    if len(fr) < 2:
        return np.zeros(len(fr))
    diff = np.abs(np.diff(fr, axis=0))
    blocks = diff[:, :H // 12 * 12, :W // 12 * 12].reshape(len(diff), H // 12, 12, W // 12, 12).mean(axis=(2, 4))
    return np.r_[0.0, blocks.max(axis=(1, 2))]        # index k: the change that frame k shows


def cues(tl):
    """[(t, label)] for every word cue and every cut, one per frame (several cues on one word are one landing)."""
    out = [(float(c["t"]), f"\"{c.get('at', '')}\" in {c.get('seg', '?')}") for c in tl.get("cues", []) if c.get("t") is not None]
    segs = tl.get("segments", [])
    for prev, sg in zip(segs, segs[1:]):
        if sg.get("t0") is not None and sg.get("t0") > 0:
            out.append((float(sg["t0"]), f"cut to {sg['id']}"))
    seen, uniq = set(), []
    for t, lab in sorted(out):
        k = int(round(t * FPS))
        if k not in seen:
            seen.add(k)
            uniq.append((t, lab))
    return uniq


def landings(sig, cue_list, fps=FPS):
    """[{t, label, ms, strength, state}] with state "ok", "early", "late", "unseen" (nothing clear changes near the word)
    or "crowded" (too close after another cue to tell apart)."""
    rows, last = [], None
    for t, lab in cue_list:
        row = {"t": round(t, 3), "label": lab, "ms": None, "strength": 0.0}
        if last is not None and t - last < CROWD:
            rows.append({**row, "state": "crowded"}); last = t; continue
        last = t
        a, b = max(0, int((t - 0.3) * fps)), min(len(sig), int((t + 0.6) * fps) + 1)
        if b - a < 3:
            rows.append({**row, "state": "unseen"}); continue
        before = sig[max(0, a - 15):a]
        base = float(np.median(before)) if len(before) else 0.0       # the scene's own motion just before the cue
        w = sig[a:b] - base
        pk = float(w.max())
        row["strength"] = round(pk, 1)
        if pk < CLEAR:
            rows.append({**row, "state": "unseen"}); continue
        k = a + int(np.argmax(w > 0.4 * pk))                          # onset: the first frame above 40% of the peak
        ms = (k / fps - t) * 1000
        state = "early" if ms < -EARLY * 1000 else "late" if ms > LATE * 1000 else "ok"
        rows.append({**row, "ms": round(ms), "state": state})
    return rows


def audio_offset_ms(video, wav, secs=30):
    """Soundtrack lag of the video against the mix (positive: the video's sound is late), by cross-correlation."""
    def pcm(p):
        return np.frombuffer(subprocess.run(["ffmpeg", "-v", "error", "-i", str(p), "-ac", "1", "-ar", "8000", "-t", str(secs),
                                             "-f", "s16le", "-"], capture_output=True, check=True).stdout, np.int16).astype(np.float32)
    x, y = pcm(video), pcm(wav)
    n = min(len(x), len(y))
    if n < 8000 or not x[:n].any() or not y[:n].any():
        return None
    c = np.fft.irfft(np.fft.rfft(x[:n], 2 * n) * np.conj(np.fft.rfft(y[:n], 2 * n)))
    lag = int(np.argmax(c))
    lag = lag - 2 * n if lag > n else lag
    return lag / 8.0


def run(film, video=None, wav=None):
    """(lines, problems) for a film's finished video."""
    film = Path(film)
    tl = json.loads((film / "timeline.js").read_text().split("=", 1)[1].rstrip().rstrip(";"))
    if video is None:
        raw = film / "out" / "silent-raw.mp4"            # a finish's grain and print mottle are not landings
        video = raw if tl.get("finish") and raw.exists() else film / "out" / "silent.mp4"
    rows = landings(signal(video), cues(tl))
    judged = [r for r in rows if r["ms"] is not None]
    lines = [f"beat landings on the video ({Path(video).name}): {len(rows)} cue(s), {len(judged)} measured, "
             f"{sum(r['state'] == 'unseen' for r in rows)} with no clear change, {sum(r['state'] == 'crowded' for r in rows)} too close to tell apart"]
    if judged:
        ms = [r["ms"] for r in judged]
        lines.append(f"  measured landings from {min(ms):+d} to {max(ms):+d} ms of the word (one frame is {1000 // FPS} ms)")
    problems = [f"{r['label']} at {r['t']:.2f} s lands {abs(r['ms'])} ms {'after' if r['ms'] > 0 else 'before'} its word"
                for r in rows if r["state"] in ("early", "late")]
    wav = Path(wav) if wav else film / "out" / "mix.wav"
    final = film / "out" / f"{film.name}.mp4"
    if wav.exists() and final.exists():
        off = audio_offset_ms(final, wav)
        if off is not None:
            lines.append(f"  soundtrack against out/mix.wav: {off:+.1f} ms")
            if abs(off) > OFFSET_MS:
                problems.append(f"the finished video's sound is {abs(off):.0f} ms {'late' if off > 0 else 'early'} against "
                                f"out/mix.wav: run final again, and if it stays, the mux is shifting it")
    return lines, problems


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    lines, probs = run(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    print("\n".join(lines))
    for p in probs:
        print("FAIL", p)
    sys.exit(1 if probs else 0)
