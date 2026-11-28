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
W, H = 384, 216              # a chart's start dot (about 8 px at 960) is a clear 12x12 block here; at 192x108 it was not
EARLY, LATE = 0.25, 0.20     # a clear change this far before or after its word fails (the engine lands callouts about
                             # 2 frames early on purpose, "anticipate"; 0.2 s is 6 frames, past any rounding)
CLEAR = 3.0                  # the change must stand this far (luma levels, 12x12 block) above the scene's own motion
CROWD = 0.35                 # a cue this close after another can't be told apart from it: it is listed, not judged
GRADUAL = {"camera", "series"}   # cues that start something easing in from rest and overlapping its neighbours (a map
                             # camera move, a chart line's 1.6 s draw): no single frame is their landing. The first cel
                             # film's last four beat FAILs were all these. They are timed on the page; render drift, the
                             # thing this check is for, shows on the crisp cues around them
OFFSET_MS = 45               # soundtrack against the mix: more than this is more than a frame and a bit


def signal(video, fps=FPS):
    """Per frame, the mean luma change in each 12x12 block (frames x rows x columns), so a small callout counts as well as
    a cut, and each block's own motion can be taken away on its own. Read as a stream: a 12-minute film is 7 GB of
    frames at this size, and only the blocks are kept (50 MB)."""
    by, bx, size = H // 12, W // 12, W * H
    out, prev = [np.zeros((1, by, bx), np.float32)], None
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(video), "-vf", f"fps={fps},scale={W}:{H},format=gray",
                          "-f", "rawvideo", "-"], stdout=subprocess.PIPE)
    while True:
        buf = p.stdout.read(size * 300)
        if len(buf) < size:
            break
        fr = np.frombuffer(buf[:len(buf) // size * size], np.uint8).reshape(-1, H, W).astype(np.float32)
        if prev is not None:
            fr = np.concatenate([prev[None], fr])
        diff = np.abs(np.diff(fr, axis=0))
        if len(diff):
            out.append(diff[:, :by * 12, :bx * 12].reshape(len(diff), by, 12, bx, 12).mean(axis=(2, 4)))
        prev = fr[-1]
    if p.wait():
        raise subprocess.CalledProcessError(p.returncode, "ffmpeg")
    if prev is None:
        return np.zeros((0, by, bx), np.float32)
    return np.concatenate(out)                       # index k: the change frame k shows


def cues(tl):
    """[(t, label)] for every word cue and every cut, one per frame (several cues on one word are one landing). Gradual
    cues (GRADUAL) are left out; run() counts them."""
    out = [(float(c["t"]), f"\"{c.get('at', '')}\" in {c.get('seg', '?')}") for c in tl.get("cues", [])
           if c.get("t") is not None and c.get("kind") not in GRADUAL]
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
    times = [t for t, _ in cue_list]
    for i, (t, lab) in enumerate(cue_list):
        row = {"t": round(t, 3), "label": lab, "ms": None, "strength": 0.0}
        if last is not None and t - last < CROWD:
            rows.append({**row, "state": "crowded"}); last = t; continue
        last = t
        a, b = max(0, int((t - 0.3) * fps)), min(len(sig), int((t + 0.6) * fps) + 1)
        if b - a < 3:
            rows.append({**row, "state": "unseen"}); continue
        # novelty: per 12x12 block, how far this frame's change goes past the most that block changed in the half
        # second before the cue, and the largest of that over the picture. Steady motion (a push-in, a boil, a map
        # camera on twos, a line already drawing) stays inside each block's own range; a mark that wasn't there does not.
        env = sig[max(0, a - 15):a].max(axis=0) if a > 0 else np.zeros(sig.shape[1:], np.float32)
        w = (sig[a:b] - env).max(axis=(1, 2))
        # the same measure on the frames before the cue, each against its own half second: how much "new" the scene
        # makes by itself. Over hundreds of blocks some always step a little past their range, and timing the first
        # frame above a fixed level read every cue of the first cel film 0.3 s early (#199, #200)
        amb = [float((sig[j] - sig[max(0, j - 15):j].max(axis=0)).max()) for j in range(max(1, a - 15), a)]
        thr = CLEAR + (float(np.percentile(amb, 90)) if amb else 0.0)
        pk = float(w.max())
        row["strength"] = round(pk, 1)
        if pk < thr:
            rows.append({**row, "state": "unseen"}); continue
        # onset: the first frame past both the scene's own level and 40% of the peak. A chart's start dot is a block
        # that never changed before, so it stands out on its own and isn't lost under the bigger line drawn after it
        k = a + int(np.argmax(w >= max(thr, 0.4 * pk)))
        nxt = times[i + 1] if i + 1 < len(times) else None
        if nxt is not None and k / fps >= nxt - 1 / fps:
            # the first change comes on the next cue's word, so it is that cue's landing, not this one's: a cut into a
            # scene that holds the same picture until its first word (Survive Year 1 v38c, "The strong paths...")
            rows.append({**row, "state": "unseen"}); continue
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
    gradual = sum(c.get("kind") in GRADUAL for c in tl.get("cues", []))
    lines = [f"beat landings on the video ({Path(video).name}): {len(rows)} cue(s), {len(judged)} measured, "
             f"{sum(r['state'] == 'unseen' for r in rows)} with no clear change, {sum(r['state'] == 'crowded' for r in rows)} too close to tell apart"
             + (f"; {gradual} camera move(s) and line draw(s) not timed here" if gradual else "")]
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
