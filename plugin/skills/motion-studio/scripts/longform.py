#!/usr/bin/env python3
"""Long-form narrated films (YouTube explainers, 5-20 minutes) on the motion-studio engine.

A film folder holds script.json and narration/<id>.mp3 (one clip per segment, from any TTS or a human read).
script.json: {"title", "palette"?, "captions"?: true, "moods"?: {"<chapter>": "tension"}, "segments": [
  {"id", "chapter", "type", "data", "narration", "source"?, "mood"?}]}.
Anything inside a segment's data with "at": "<word or phrase>" (optionally "nth": 2, "offset": -0.1) is timed to
that word of the segment's narration once `align` has run, so beats land on what is said.

  longform.py init <film>                       copy the engine (film.html, kit.js, figure.js) into the folder
  longform.py lines <film>                      each segment's narration (feed to TTS, one file per id)
  longform.py align <film>                      word timestamps for every clip -> narration/words.json
  longform.py timeline <film> [--pad 0.45]      measure clips, resolve "at" cues -> timeline.js, narration.wav, chapters.txt
  longform.py stills <film> [--every 20]        one still per segment (or every N s) + contact sheet
  longform.py music <film>                      rank the audio library per chapter mood -> out/music_plan.json
  longform.py mix <film> [--plan | --music a.mp3,b.mp3] [--whoosh w.wav] [--bed-db -24]
  longform.py render <film> [--workers 4] [--scale 1.0]
  longform.py final <film>                      mux render + mix, verify
  longform.py check <film> [--hold 4]           critic numbers: dead air, text-only stretches, cue landings, flashes

Every file stays in the film folder; nothing is uploaded from here.
"""
import argparse
import json
import math
import shutil
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE.parent / "assets" / "templates" / "longform.html"
sys.path.insert(0, str(HERE))
FPS = 30
TEXT_ONLY = {"statement", "quote", "list", "chapter", "title"}      # scene types that are words on a background


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def load_script(film):
    return json.loads((film / "script.json").read_text())


def load_timeline(film):
    return json.loads((film / "timeline.js").read_text().split("=", 1)[1].rstrip().rstrip(";"))


def cmd_init(a):
    film = Path(a.film).resolve()
    for d in ("narration", "out", "review", "sources"):
        (film / d).mkdir(parents=True, exist_ok=True)
    shutil.copy(TEMPLATE, film / "film.html")
    for f in ("kit.js", "figure.js"):
        shutil.copy(HERE / f, film / f)
    print(film)


def cmd_lines(a):
    film = Path(a.film).resolve()
    for s in load_script(film)["segments"]:
        if s.get("narration"):
            print(f"{s['id']}\t{s['narration']}")


def cmd_align(a):
    import align
    film = Path(a.film).resolve()
    out_path = film / "narration" / "words.json"
    words = json.loads(out_path.read_text()) if out_path.exists() and not a.redo else {}
    methods = {}
    for s in load_script(film)["segments"]:
        if not s.get("narration") or (s["id"] in words and words[s["id"]].get("text") == s["narration"]):
            continue
        clip = film / "narration" / f"{s['id']}.mp3"
        if not clip.exists():
            raise SystemExit(f"missing narration clip {clip}")
        ws, method = align.align_clip(clip, s["narration"], work=film / "out")
        words[s["id"]] = {"text": s["narration"], "method": method, "words": ws}
        methods[method] = methods.get(method, 0) + 1
    out_path.write_text(json.dumps(words, indent=1, ensure_ascii=False))
    print(f"words -> {out_path} ({', '.join(f'{v} {k}' for k, v in methods.items()) or 'nothing new'})")


def norm(w):
    return "".join(ch for ch in w.lower().replace("’", "'") if ch.isalnum() or ch == "'")


def find_phrase(words, phrase, nth=1):
    """Start time of the nth occurrence of `phrase` in a segment's word list, or None."""
    target = [norm(x) for x in phrase.split()]
    seq = [norm(w[0]) for w in words]
    hits = 0
    for i in range(len(seq) - len(target) + 1):
        if all(seq[i + k] == target[k] or (k == len(target) - 1 and seq[i + k].startswith(target[k])) for k in range(len(target))):
            hits += 1
            if hits == nth:
                return words[i][1]
    return None


def resolve_cues(node, words, seg, problems, cues):
    """Give every {"at": "..."} inside a segment's data an absolute "t"."""
    if isinstance(node, dict):
        if isinstance(node.get("at"), str):
            t = find_phrase(words, node["at"], node.get("nth", 1)) if words else None
            if t is None:
                problems.append(f"{seg['id']}: cue word \"{node['at']}\" not in its narration" if words else
                                f"{seg['id']}: cue \"{node['at']}\" needs `align` first")
                t = seg["t0"] + 0.5
            node["t"] = round(t + node.get("offset", 0.0), 3)
            cues.append({"seg": seg["id"], "at": node["at"], "t": node["t"]})
        for v in node.values():
            resolve_cues(v, words, seg, problems, cues)
    elif isinstance(node, list):
        for v in node:
            resolve_cues(v, words, seg, problems, cues)


def cmd_timeline(a):
    film = Path(a.film).resolve()
    sc = load_script(film)
    wj = film / "narration" / "words.json"
    allwords = json.loads(wj.read_text()) if wj.exists() else {}
    t, segs, clips, chapters, problems, cues = a.lead, [], [], [], [], []
    moods = sc.get("moods", {})
    for s in sc["segments"]:
        clip = film / "narration" / f"{s['id']}.mp3"
        if s.get("narration"):
            if not clip.exists():
                raise SystemExit(f"missing narration clip {clip}")
            d = duration(clip)
            clips.append((t, clip))
            dur = d + a.pad + s.get("extra", 0.0)
        else:
            dur = s.get("hold", 3.0)
        seg = {k: s[k] for k in ("id", "type", "data", "source", "chapter", "cast") if k in s}
        seg.update(t0=round(t, 3), t1=round(t + dur, 3))
        w = allwords.get(s["id"])
        if w and w.get("text") == s.get("narration"):
            seg["words"] = [[x["w"], round(t + x["t0"], 3), round(t + x["t1"], 3)] for x in w["words"]]
        elif s.get("narration") and allwords:
            problems.append(f"{s['id']}: narration changed since `align`; run align again")
        resolve_cues(seg.get("data", {}), seg.get("words"), seg, problems, cues)
        segs.append(seg)
        if s.get("chapter") and (not chapters or chapters[-1]["title"] != s["chapter"]):
            if chapters:
                chapters[-1]["t1"] = round(t, 3)
            chapters.append({"title": s["chapter"], "t0": round(t, 3), "mood": s.get("mood") or moods.get(s["chapter"])})
        t += dur
    total = round(t + a.tail, 3)
    if chapters:
        chapters[-1]["t1"] = total
    tl = {"title": sc["title"], "palette": sc.get("palette", {}), "duration": total, "segments": segs,
          "chapters": chapters, "captions": bool(sc.get("captions", False)) and any("words" in s for s in segs),
          "grade": sc.get("grade", {"vignette": 0.35, "grain": 0}), "cues": cues}
    (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps(tl, indent=1, ensure_ascii=False) + ";\n")
    # narration bed: every clip placed at its segment start
    sr = 48000
    inputs, filt = [], []
    for i, (t0, clip) in enumerate(clips):
        inputs += ["-i", str(clip)]
        filt.append(f"[{i}:a]aresample={sr},aformat=channel_layouts=mono,adelay={int(t0 * 1000)}|{int(t0 * 1000)}[n{i}]")
    graph = ";".join(filt) + ";" + "".join(f"[n{i}]" for i in range(len(clips))) + \
        f"amix=inputs={len(clips)}:normalize=0:dropout_transition=0,apad=whole_dur={total}[out]"
    script = film / "out" / "narration.filter"
    script.write_text(graph)
    run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex_script", str(script), "-map", "[out]",
         "-t", str(total), "-ar", str(sr), "-ac", "1", str(film / "out" / "narration.wav")])
    # YouTube chapters: first must be 0:00, each at least 10 s
    lines = []
    for k, ch in enumerate(chapters):
        s0 = 0 if k == 0 else int(ch["t0"])
        lines.append(f"{s0 // 60}:{s0 % 60:02d} {ch['title']}")
        if k and ch["t0"] - chapters[k - 1]["t0"] < 10:
            problems.append(f"chapter \"{chapters[k - 1]['title']}\" is under 10 s; YouTube will ignore the chapter list")
    (film / "out" / "chapters.txt").write_text("\n".join(lines) + "\n")
    if any("words" in s for s in segs):
        write_vtt(segs, film / "out" / "captions.vtt")
    print(f"{len(segs)} segments, {len(clips)} narration clips, {len(cues)} word cues, {total:.1f} s ({total / 60:.1f} min)")
    print("\n".join(lines))
    for p in problems:
        print("PROBLEM:", p)


def write_vtt(segs, dest, max_words=9, max_chars=42):
    """A caption track from the aligned words (upload it to YouTube instead of burning captions into 16:9)."""
    def ts(x):
        h, m, sec = int(x // 3600), int(x % 3600 // 60), x % 60
        return f"{h:02d}:{m:02d}:{sec:06.3f}"
    cues = []
    for s in segs:
        page = []
        for w in s.get("words", []):
            page.append(w)
            text = " ".join(x[0] for x in page)
            if w[0][-1:] in ".!?" or len(page) >= max_words or len(text) > max_chars:
                cues.append((page[0][1], page[-1][2], text)); page = []
        if page:
            cues.append((page[0][1], page[-1][2], " ".join(x[0] for x in page)))
    out = ["WEBVTT", ""]
    for i, (a, b, text) in enumerate(cues):
        b = min(b + 0.3, cues[i + 1][0]) if i + 1 < len(cues) else b + 0.3
        out += [f"{ts(a)} --> {ts(b)}", text, ""]
    Path(dest).write_text("\n".join(out))


def cmd_stills(a):
    import render
    film = Path(a.film).resolve()
    tl = load_timeline(film)
    if a.every:
        ts = [round(x, 2) for x in frange(1.0, tl["duration"], a.every)]
    else:
        ts = [round(s["t0"] + (s["t1"] - s["t0"]) * a.at, 2) for s in tl["segments"]]
    out = film / "review" / ("stills-every" if a.every else "stills-segments")
    out, n, errors = render.stills(film, ts, out)
    print(f"{n} stills -> {out}" + (f" ({len(errors)} page errors)" if errors else ""))
    print("sheet ->", render.sheet(out, out.with_suffix(".png"), cols=6))


def frange(a, b, step):
    x = a
    while x < b:
        yield x
        x += step


def _render_chunk(args):
    film, i0, i1, dest, scale = args
    import render
    with render.Film(Path(film)) as f:
        vf = f"scale=trunc(iw*{scale}/2)*2:trunc(ih*{scale}/2)*2:out_color_matrix=bt709:out_range=tv,format=yuv420p"
        ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "image2pipe", "-framerate", str(FPS), "-i", "-",
                               "-vf", vf, "-r", str(FPS), "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                               "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
                               str(dest)], stdin=subprocess.PIPE)
        for i in range(i0, i1):
            ff.stdin.write(f.shot(i / FPS))
            if (i - i0) % 900 == 0:
                print(f"  chunk {i0}: frame {i - i0}/{i1 - i0}", flush=True)
        ff.stdin.close()
        if ff.wait() != 0:
            raise RuntimeError("ffmpeg failed")
        return dest, len(f.errors)


def cmd_render(a):
    film = Path(a.film).resolve()
    tl = load_timeline(film)
    n = math.ceil(tl["duration"] * FPS)
    parts = film / "out" / "parts"
    shutil.rmtree(parts, ignore_errors=True)
    parts.mkdir(parents=True, exist_ok=True)
    size = math.ceil(n / a.workers)
    jobs = [(str(film), k, min(n, k + size), str(parts / f"p{k:07d}.mp4"), a.scale) for k in range(0, n, size)]
    with ProcessPoolExecutor(a.workers) as ex:
        results = list(ex.map(_render_chunk, jobs))
    lst = parts / "list.txt"
    lst.write_text("".join(f"file '{Path(d).name}'\n" for d, _ in results))
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(film / "out" / "silent.mp4")])
    errs = sum(e for _, e in results)
    print(f"{n} frames -> {film / 'out' / 'silent.mp4'}" + (f" ({errs} page errors)" if errs else ""))


# ── music ─────────────────────────────────────────────────
def cmd_music(a):
    """Pick a library track per chapter from its mood; runs of chapters with one mood share one track."""
    import audio
    film = Path(a.film).resolve()
    tl = load_timeline(film)
    chapters = tl["chapters"]
    default = a.default
    runs = []
    for ch in chapters:
        m = ch.get("mood") or default
        if runs and runs[-1]["mood"] == m:
            runs[-1]["t1"] = ch["t1"]; runs[-1]["chapters"].append(ch["title"])
        else:
            runs.append({"mood": m, "t0": ch["t0"], "t1": ch["t1"], "chapters": [ch["title"]]})
    used, misses = [], []
    for r in runs:
        ranked = audio.rank_tracks(r["mood"])
        if not ranked:
            raise SystemExit("the audio library has no tracks: `studio.py fetch music <id>` first")
        # prefer a track not used by the previous run, so a mood change is heard as a change
        pick = next((x for x in ranked if not used or x[1] != used[-1]), ranked[0])
        r.update(track=pick[1], title=pick[2], score=pick[0], alternatives=[[x[0], x[1], x[2]] for x in ranked[:4]])
        used.append(pick[1])
        if pick[0] < a.fit:
            misses.append(r)
        print(f"{r['t0']:7.1f}-{r['t1']:7.1f}s  {r['mood']:<8} {pick[2]} ({pick[1]}, fit {pick[0]:.2f})  [{', '.join(r['chapters'])}]")
    (film / "out" / "music_plan.json").write_text(json.dumps({"runs": runs}, indent=1))
    for r in misses:
        print(f"NO GOOD FIT for {r['mood']} ({', '.join(r['chapters'])}): best is {r['score']:.2f}. "
              f"Search the library source: {', '.join(audio.MOOD_SEARCH.get(r['mood'], [r['mood']]))}")
    print("plan ->", film / "out" / "music_plan.json", "(edit `track` per run to override)")


def build_bed(film, plan, total, sr=48000, xfade=3.0):
    """The music bed from a plan: each run plays its track from the top (looping on a crossfade if the run is longer);
    the last run is back-timed so the track's own ending lands on the film's end."""
    import numpy as np
    import audio
    cat = audio.catalog()["tracks"]
    bed = np.zeros((int(total * sr) + sr, 2))
    runs = plan["runs"]
    for k, r in enumerate(runs):
        x = audio.load(audio.library_dir() / cat[r["track"]]["file"], stereo=True, sr=sr)
        # level-match tracks to one loudness, so a quiet piano run and a loud score sit at the same depth under the voice
        e = (cat[r["track"]].get("mood") or {}).get("energy")
        if e is not None:
            x = x * 10 ** ((-20.0 - e) / 20)
        a0 = max(0.0, r["t0"] - (xfade / 2 if k else 0))
        a1 = min(total, r["t1"] + (xfade / 2 if k < len(runs) - 1 else 0))
        need = int((a1 - a0) * sr)
        last = k == len(runs) - 1
        if last and len(x) >= need:
            # back-time: start late enough that the track ends with the film (trim trailing silence first)
            env = np.abs(x).max(axis=1)
            end = len(env) - np.argmax(env[::-1] > 0.003)
            seg = x[max(0, end - need):end]
        else:
            reps, seg = [], np.zeros((0, 2))
            n_x = int(xfade * sr)
            while len(seg) < need:
                piece = x.copy()
                if len(seg):
                    ramp = np.linspace(0, 1, n_x)[:, None]
                    seg[-n_x:] = seg[-n_x:] * (1 - ramp) + piece[:n_x] * ramp
                    piece = piece[n_x:]
                seg = np.concatenate([seg, piece])
            seg = seg[:need]
        n = len(seg)
        ramp_n = min(int(xfade * sr), n // 2)
        env = np.ones(n)
        if k:
            env[:ramp_n] = np.linspace(0, 1, ramp_n)
        if not last:
            env[-ramp_n:] = np.minimum(env[-ramp_n:], np.linspace(1, 0, ramp_n))
        i0 = int(a0 * sr)
        bed[i0:i0 + n] += seg * env[:, None]
    bed = bed[:int(total * sr)]
    import wave
    dest = film / "out" / "bed.wav"
    pcm = (np.clip(bed, -1, 1) * 32767).astype("<i2")
    with wave.open(str(dest), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr); w.writeframes(pcm.tobytes())
    return dest


def cmd_mix(a):
    """Narration on top; the music bed ducked under the voice; an optional whoosh at each chapter start."""
    film = Path(a.film).resolve()
    tl = load_timeline(film)
    total = tl["duration"]
    narr = film / "out" / "narration.wav"
    inputs = ["-i", str(narr)]
    filt = ["[0:a]aformat=sample_rates=48000:channel_layouts=stereo,asplit=2[voice][key]"]
    if a.plan:
        bed = build_bed(film, json.loads((film / "out" / "music_plan.json").read_text()), total)
        inputs += ["-i", str(bed)]
        filt.append(f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={a.bed_db}dB,afade=t=in:d=1.0[bed]")
        filt.append("[bed][key]sidechaincompress=threshold=0.02:ratio=6:attack=40:release=600[ducked]")
        mix_in = ["[voice]", "[ducked]"]
    elif a.music:
        tracks = [Path(p) for p in a.music.split(",")]
        seq, k = [], 0
        while sum(duration(t) for t in seq) < total + 5:
            seq.append(tracks[k % len(tracks)]); k += 1
        for t in seq:
            inputs += ["-i", str(t)]
        filt.append("[1:a]aformat=sample_rates=48000:channel_layouts=stereo[m0]")
        for i in range(1, len(seq)):
            filt.append(f"[{i + 1}:a]aformat=sample_rates=48000:channel_layouts=stereo[r{i}]")
            filt.append(f"[m{i - 1}][r{i}]acrossfade=d=4:c1=tri:c2=tri[m{i}]")
        filt.append(f"[m{len(seq) - 1}]atrim=0:{total},volume={a.bed_db}dB,afade=t=in:d=1.5,afade=t=out:st={max(0, total - 4)}:d=4[bed]")
        filt.append("[bed][key]sidechaincompress=threshold=0.02:ratio=6:attack=40:release=600[ducked]")
        mix_in = ["[voice]", "[ducked]"]
    else:
        mix_in = ["[voice]"]
    if a.whoosh and len(tl["chapters"]) > 1:
        base = len(inputs) // 2
        inputs += ["-i", a.whoosh]
        cuts = [ch["t0"] for ch in tl["chapters"][1:]]
        filt.append(f"[{base}:a]aformat=sample_rates=48000:channel_layouts=stereo,volume=-10dB,asplit={len(cuts)}" +
                    "".join(f"[w{i}]" for i in range(len(cuts))))
        for i, c in enumerate(cuts):
            ms = int(max(0, c - 0.25) * 1000)
            filt.append(f"[w{i}]adelay={ms}|{ms}[wd{i}]")
            mix_in.append(f"[wd{i}]")
    filt.append("".join(mix_in) + f"amix=inputs={len(mix_in)}:normalize=0:dropout_transition=0,atrim=0:{total}[pre]")
    script = film / "out" / "mix.filter"
    script.write_text(";".join(filt))
    pre = film / "out" / "premix.wav"
    run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex_script", str(script), "-map", "[pre]", "-ar", "48000", str(pre)])
    # two-pass loudness to -14 LUFS; -2.5 dBTP leaves room for the AAC encode's overshoot
    meas = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(pre), "-af", "loudnorm=I=-14:TP=-2.5:LRA=11:print_format=json", "-f", "null", "-"],
                          capture_output=True, text=True).stderr
    j = json.loads(meas[meas.rfind("{"):])
    ln = (f"loudnorm=I=-14:TP=-2.5:LRA=11:measured_I={j['input_i']}:measured_TP={j['input_tp']}:measured_LRA={j['input_lra']}:"
          f"measured_thresh={j['input_thresh']}:offset={j['target_offset']}:linear=true")
    run(["ffmpeg", "-v", "error", "-y", "-i", str(pre), "-af", ln, "-ar", "48000", str(film / "out" / "mix.wav")])
    print("mix ->", film / "out" / "mix.wav")


def cmd_final(a):
    import verify
    film = Path(a.film).resolve()
    out = film / "out" / f"{film.name}.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-i", str(film / "out" / "silent.mp4"), "-i", str(film / "out" / "mix.wav"),
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest", "-movflags", "+faststart", str(out)])
    # scene wipes and stamps are meant to hit: don't call them pops
    tl = load_timeline(film)
    allow = [(sg["t0"], sg["t0"] + 0.5) for sg in tl["segments"] if sg["type"] in ("stage", "board", "crowd")]
    allow += [(sg["t1"], sg["t1"] + 0.5) for sg in tl["segments"] if sg["type"] in ("stage", "board", "crowd")]
    allow += [(sg["data"]["stamp"]["t"], sg["data"]["stamp"]["t"] + 0.25) for sg in tl["segments"]
              if sg["type"] == "board" and sg.get("data", {}).get("stamp", {}).get("t") is not None]
    ok = verify.verify(out, bpm=None, sheet_dest=None, bg=(11, 11, 11), allow=allow)
    print(out)
    sys.exit(0 if ok else 1)


# ── check: the numbers a critic should start from ─────────
def frame_diffs(video, fps=10, w=192, h=108, local=False):
    """Mean absolute luma change between consecutive frames (0..1), sampled at `fps`. With local=True, also the
    largest change in any 12x12 block above the caption band, so a small reveal (a spoke label) still counts."""
    import numpy as np
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(video), "-vf", f"fps={fps},scale={w}:{h},format=gray", "-f", "rawvideo", "-"],
                         stdout=subprocess.PIPE)
    prev, d, loc = None, [], []
    band = int(h * 0.84) // 12 * 12                      # captions and the source line live below this
    while True:
        b = p.stdout.read(w * h)
        if len(b) < w * h:
            break
        f = np.frombuffer(b, np.uint8).astype(np.int16)
        if prev is not None:
            diff = np.abs(f - prev)
            d.append(float(diff.mean()) / 255)
            if local:
                blocks = diff.reshape(h, w)[:band].reshape(band // 12, 12, w // 12, 12).mean(axis=(1, 3))
                loc.append(float(blocks.max()) / 255)
        prev = f
    p.wait()
    return (np.array(d), np.array(loc)) if local else np.array(d)


def cmd_check(a):
    """Dead air, text-only stretches, cue landings and one-frame flashes, measured from the timeline and the render."""
    import numpy as np
    film = Path(a.film).resolve()
    tl = load_timeline(film)
    segs, total = tl["segments"], tl["duration"]
    report, flags = [], []
    # 1. picture variety from the timeline: how much of the film is words on a background
    text_time = sum(s["t1"] - s["t0"] for s in segs if s["type"] in TEXT_ONLY)
    run_t, run0, worst = 0.0, None, (0.0, None, None)
    for s in segs:
        if s["type"] in TEXT_ONLY:
            run0 = run0 or s; run_t += s["t1"] - s["t0"]
            if run_t > worst[0]:
                worst = (run_t, run0["id"], s["id"])
        else:
            run_t, run0 = 0.0, None
    share = text_time / total
    report.append(f"text-only scenes: {share:.0%} of the film; longest run {worst[0]:.0f} s ({worst[1]}..{worst[2]})")
    if share > a.max_text:
        flags.append(f"{share:.0%} of the film is text on a background (limit {a.max_text:.0%}): turn statements into stage, board or crowd scenes")
    if worst[0] > a.max_text_run:
        flags.append(f"text-only run of {worst[0]:.0f} s from {worst[1]} to {worst[2]} (limit {a.max_text_run:.0f} s)")
    types = [s["type"] for s in segs]
    repeats = [(types[i], segs[i]["id"]) for i in range(2, len(types)) if types[i] == types[i - 1] == types[i - 2]]
    for ty, sid in repeats:
        flags.append(f"three {ty} scenes in a row ending at {sid}")
    video = film / "out" / "silent.mp4"
    if not video.exists():
        report.append("no render yet: run `render` for dead-air, cue and flash checks")
    else:
        fps = 10
        d, loc = frame_diffs(video, fps, local=True)
        ev = d > a.event
        times = np.nonzero(ev)[0] / fps
        # 2. dead air: stretches with no visible event
        gaps = np.diff(np.concatenate([[0.0], times, [total]]))
        starts = np.concatenate([[0.0], times])
        seg_at = lambda t: next((s["id"] for s in segs if s["t0"] <= t < s["t1"]), "end")
        holds = [(starts[i], gaps[i]) for i in range(len(gaps)) if gaps[i] >= a.hold]
        n_ev = int((ev[1:] & ~ev[:-1]).sum() + (1 if len(ev) and ev[0] else 0))     # a run of changing frames is one event
        report.append(f"visible events: {n_ev} ({n_ev / total * 60:.0f}/min); holds over {a.hold:.0f} s: {len(holds)}")
        for t0, g in holds:
            flags.append(f"nothing new on screen for {g:.1f} s from {t0:.1f} s ({seg_at(t0 + 0.1)})")
        # per chapter: events per minute and stillness, so a flat chapter stands out
        for ch in tl["chapters"]:
            k0, k1 = int(ch["t0"] * fps), int(ch["t1"] * fps)
            sl = d[k0:k1]
            if len(sl) < fps:
                continue
            e = sl > a.event
            epm = (e[1:] & ~e[:-1]).sum() / (len(sl) / fps) * 60
            still = float((sl < a.event / 4).mean())
            report.append(f"  {ch['title'][:28]:<28} {epm:4.0f} events/min, {still:.0%} still")
        # 3. cue landings: something visible should change within -0.2..+0.8 s of each cued word
        missed = []
        for c in tl.get("cues", []):
            k0, k1 = int(max(0, c["t"] - 0.2) * fps), int((c["t"] + 0.8) * fps) + 1
            if not ((d[k0:k1] > a.event) | (loc[k0:k1] > a.local)).any():
                missed.append(c)
        report.append(f"word cues: {len(tl.get('cues', []))}, {len(missed)} with no visible change near the word")
        for c in missed:
            flags.append(f"cue \"{c['at']}\" in {c['seg']} at {c['t']:.1f} s: nothing changes on screen")
        # 4. one-frame flashes at full rate: frame n differs from both neighbours while they match each other
        p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(video), "-vf", "scale=160:90,format=gray", "-f", "rawvideo", "-"], stdout=subprocess.PIPE)
        win, n, flashes = [], 0, []
        while True:
            b = p.stdout.read(160 * 90)
            if len(b) < 160 * 90:
                break
            win.append(np.frombuffer(b, np.uint8).astype(np.int16)); n += 1
            if len(win) == 3:
                a0, a1, a2 = win
                d01, d12, d02 = np.abs(a1 - a0).mean(), np.abs(a2 - a1).mean(), np.abs(a2 - a0).mean()
                if min(d01, d12) > 6 and d02 < 0.35 * min(d01, d12):
                    flashes.append((n - 2) / FPS)
                win.pop(0)
        p.wait()
        report.append(f"one-frame flashes: {len(flashes)}")
        for t in flashes:
            flags.append(f"one-frame flash at {t:.2f} s ({seg_at(t)})")
    print("\n".join(report))
    print(f"\n{len(flags)} flag(s)" + (":" if flags else ""))
    for f in flags:
        print("FLAG:", f)
    (film / "review" / "check.txt").write_text("\n".join(report + [""] + ["FLAG: " + f for f in flags]) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("init"); p.add_argument("film"); p.set_defaults(fn=cmd_init)
    p = sp.add_parser("lines"); p.add_argument("film"); p.set_defaults(fn=cmd_lines)
    p = sp.add_parser("align"); p.add_argument("film"); p.add_argument("--redo", action="store_true"); p.set_defaults(fn=cmd_align)
    p = sp.add_parser("timeline"); p.add_argument("film"); p.add_argument("--pad", type=float, default=0.45)
    p.add_argument("--lead", type=float, default=0.3); p.add_argument("--tail", type=float, default=1.5); p.set_defaults(fn=cmd_timeline)
    p = sp.add_parser("stills"); p.add_argument("film"); p.add_argument("--every", type=float); p.add_argument("--at", type=float, default=0.75)
    p.set_defaults(fn=cmd_stills)
    p = sp.add_parser("render"); p.add_argument("film"); p.add_argument("--workers", type=int, default=4); p.add_argument("--scale", type=float, default=1.0)
    p.set_defaults(fn=cmd_render)
    p = sp.add_parser("music"); p.add_argument("film"); p.add_argument("--default", default="serious"); p.add_argument("--fit", type=float, default=0.6)
    p.set_defaults(fn=cmd_music)
    p = sp.add_parser("mix"); p.add_argument("film"); p.add_argument("--plan", action="store_true"); p.add_argument("--music")
    p.add_argument("--whoosh"); p.add_argument("--bed-db", type=float, default=-24); p.set_defaults(fn=cmd_mix)
    p = sp.add_parser("final"); p.add_argument("film"); p.set_defaults(fn=cmd_final)
    p = sp.add_parser("check"); p.add_argument("film"); p.add_argument("--hold", type=float, default=4.0)
    p.add_argument("--event", type=float, default=0.0008, help="mean luma change that counts as something happening")
    p.add_argument("--local", type=float, default=0.02, help="change in one small block that counts as a cue landing (small reveals)")
    p.add_argument("--max-text", type=float, default=0.4); p.add_argument("--max-text-run", type=float, default=25.0)
    p.set_defaults(fn=cmd_check)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
