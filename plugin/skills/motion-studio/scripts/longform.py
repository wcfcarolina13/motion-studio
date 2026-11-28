#!/usr/bin/env python3
"""Long-form narrated films (YouTube explainers, 5-20 minutes) on the motion-studio engine.

A film folder holds script.json and narration/<id>.mp3 (one clip per segment, from any TTS or a human read).
script.json: {"title", "voice"?: {"engine", "pack", "name", "speed"}, "palette"?, "theme"? (stage colours, longform.md), "captions"?: false, "loop"?: false, "type_scale"?: 1, "say"?: {"John Does": "John Doughs"}, "moods"?: {"<chapter>": "tension"}, "segments": [
  {"id", "chapter", "type", "data", "narration", "source"?, "mood"?}]}.
Anything inside a segment's data with "at": "<word or phrase>" (optionally "nth": 2, "offset": -0.1) is timed to
that word of the segment's narration once `align` has run, so beats land on what is said.

  longform.py init <film>                       copy the engine (film.html, kit.js, house.js, figure.js, closeup.js, mapscene.js) into the folder
  longform.py lines <film> [--json]             what the voice says per segment, "say" map applied (feed to TTS, one file per id;
                                                --json adds speaker, mono); likely misreads go to stderr as SAY: lines
  longform.py voiced <film> [ids] [--speed 1.0]  after voicing: record each clip's voice, pack, speed, words and hash
                                                (narration/voice.json; timeline stops on a clip that doesn't match the film)
  longform.py panels <film>                     the unique panel images the film needs -> out/panels.json (prompts for the generator)
  longform.py lint <film | prompts.json>        warn on prompt patterns that break hands, limbs and busy scenes
  longform.py review <film> [--checks c.json]   out/review/index.html: numbered panels to mark by click, regions by drag
  longform.py repair <film> --beat b07 --box x,y,w,h --prompt "..." --allow-codex   redraw one region (markup + whole) -> repairs/<image>/rNN/
  longform.py accept|revert <film> panels/p07/r02 [--seed 2]   put a repair into the panel, or take it back out
  longform.py upscale <film> [--size 2304x1296]   Real-ESRGAN 4x then Lanczos to cover the size -> panels-hd/ (timeline uses it)
  longform.py align <film>                      word timestamps for every clip -> narration/words.json
  longform.py short <film> <short> --from s05 --to s09   a 9:16 film from those segments: same voice and sound, its own layout
  longform.py timeline <film> [--pad 0.45]      measure clips, resolve "at" cues -> timeline.js, narration.wav, chapters.txt
  longform.py pace <film> [--apply]             review/pace.md: narration with nothing new landing, reveals not on a word (--apply cues them)
  longform.py tsheet <film> [--all]             review/intro-tsheet.md (or tsheet.md): audio | visual rows, numbers marked
  longform.py captions <film> --lang es --text es.json   a caption track in another language -> out/captions.es.vtt
  longform.py finish <film> [--at 4 12]          the film's finish ("finish": "cel") before | after -> review/finish-<t>.png
  longform.py stills <film> [--every 20]        one still per segment (or every N s) + contact sheet
  longform.py cast-sheet <film>                 set3d cast design sheet (review/cast-sheet.png); render needs it current
  longform.py music <film>                      rank the audio library per chapter mood -> out/music_plan.json
  longform.py mix <film> [--plan | --music a.mp3,b.mp3] [--whoosh id|w.wav | --no-whoosh] [--bed-db DB | --bed-gap LU]
                  [--bed-profile auto|short|long] [--bed-dip 3] [--bed-exempt REASON] [--no-sfx]
  longform.py credits <film>                    out/credits.txt: the attribution lines for every sound the mix used
  longform.py render <film> [--workers 4] [--scale 1.0] [--fresh]
  longform.py final <film>                      mux render + mix, verify
  longform.py check <film> [--hold 4]           critic numbers: dead air, text-only stretches, cue landings, flashes, text size
  longform.py deadspace <film> [--json]         quiet room in each panel beat + numbers, quotes, sources said over it -> review/deadspace.json
  longform.py share <film>                      out/share/: a page that streams the cut (publish it as a private artifact)

Every file stays in the film folder; nothing is uploaded from here.
"""
import argparse
import json
import math
import os
import re
import shutil
import tempfile
import time
import subprocess
import sys
import hashlib
from concurrent.futures import CancelledError, ProcessPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE.parent / "assets" / "templates" / "longform.html"
sys.path.insert(0, str(HERE))
import panelqa  # noqa: E402
import scalecheck  # noqa: E402
FPS = 30
TEXT_ONLY = {"statement", "quote", "list", "chapter", "title"}      # scene types that are words on a background, unless a kit draws a picture into one (see picture_share)


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


_FILTER_FLAG = None


def filter_file(path):
    """ffmpeg args that read a filtergraph from a file: -filter_complex_script up to ffmpeg 8,
    -/filter_complex on builds that dropped it (9.x). Decided from the help listing, so git builds work too."""
    global _FILTER_FLAG
    if _FILTER_FLAG is None:
        helptext = subprocess.run(["ffmpeg", "-hide_banner", "-h", "long"], capture_output=True, text=True).stdout
        _FILTER_FLAG = "-filter_complex_script" if "-filter_complex_script" in helptext else "-/filter_complex"
    return [_FILTER_FLAG, str(path)]


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def load_script(film):
    return json.loads((film / "script.json").read_text())


def load_timeline(film):
    return json.loads((film / "timeline.js").read_text().split("=", 1)[1].rstrip().rstrip(";"))


def script_sha(film):
    sp = film / "script.json"
    return hashlib.sha256(sp.read_bytes()).hexdigest()[:16] if sp.exists() else None


def stale_timeline(film, tl=None):
    """Why timeline.js does not match script.json, or None. timeline writes the script's hash into timeline.js, so a
    timeline that stopped (and left the last one in place) or a script edited since is caught; a timeline.js from before
    the hash existed falls back to file times. Episode thread, 2026-11-27: timeline stopped on stills that weren't
    upscaled, and check read the previous cut and reported its numbers as current."""
    sha = script_sha(film)
    if sha is None or not (film / "timeline.js").exists():
        return None
    tl = tl if tl is not None else load_timeline(film)
    if tl.get("script_sha"):
        return None if tl["script_sha"] == sha else "script.json has changed since timeline.js was written"
    if (film / "script.json").stat().st_mtime > (film / "timeline.js").stat().st_mtime + 1:
        return "script.json is newer than timeline.js"
    return None


# ── the soundtrack must be the cut's own ──
# 2026-11-28: the studio dashboard played a Nov 25 mix.wav (664.8 s, the old hook order) under a Nov 27 picture
# (752.7 s) for weeks of review, and every cue check read word times, never the served sound. mix writes out/mix.json,
# what the mix was made from (the script's hash, the timeline's length, the narration's hash); preview, check and final
# compare it with the film now.
MIX_TOL = 0.5      # s a mix may differ from its stamped length (encoder padding)


def _sha_file(path, _cache={}):
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    if key not in _cache:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for b in iter(lambda: fh.read(1 << 20), b""):
                h.update(b)
        _cache[key] = h.hexdigest()[:16]
    return _cache[key]


def mix_sources(film, tl=None):
    tl = tl if tl is not None else load_timeline(film)
    narr = film / "out" / "narration.wav"
    return {"script_sha": tl.get("script_sha"), "duration": round(float(tl["duration"]), 3),
            "narration_sha": _sha_file(narr) if narr.exists() else None}


def write_mix_stamp(film, tl=None):
    mix = film / "out" / "mix.wav"
    st = dict(mix_sources(film, tl), length=round(duration(mix), 3), made=time.strftime("%Y-%m-%dT%H:%M:%S"))
    (film / "out" / "mix.json").write_text(json.dumps(st, indent=1) + "\n")


def stale_mix(film, tl=None):
    """Why out/mix.wav is not this cut's sound, or None (also None when there is no mix yet)."""
    mix = film / "out" / "mix.wav"
    if not mix.exists() or not (film / "timeline.js").exists():
        return None
    tl = tl if tl is not None else load_timeline(film)
    stamp = film / "out" / "mix.json"
    if not stamp.exists():                  # a mix made before the stamp: file time and length decide
        if mix.stat().st_mtime < (film / "timeline.js").stat().st_mtime - 1:
            return "out/mix.wav is older than timeline.js"
        n = duration(mix)
        if n < float(tl["duration"]) - MIX_TOL:
            return f"out/mix.wav is {n:.1f} s and the cut is {float(tl['duration']):.1f} s"
        return None
    was, now = json.loads(stamp.read_text()), mix_sources(film, tl)
    if was.get("script_sha") != now["script_sha"]:
        return f"out/mix.wav was made for another version of script.json (mixed {was.get('made', '?')})"
    if abs(float(was.get("duration", 0)) - now["duration"]) > 0.01:
        return f"out/mix.wav was made for a {was.get('duration')} s cut; the cut is now {now['duration']} s"
    if was.get("narration_sha") != now["narration_sha"]:
        return "out/narration.wav has changed since out/mix.wav was made"
    n = duration(mix)
    if abs(n - float(was.get("length", n))) > MIX_TOL:
        return f"out/mix.wav is {n:.1f} s, not the {was.get('length')} s that mix wrote"
    return None


def require_fresh_mix(film, what, tl=None):
    why = stale_mix(film, tl)
    if why:
        raise SystemExit(f"{what} refused: {why}, so it would play another cut's sound. Run `longform.py mix {film}` "
                         "(a Short: `timeline` cuts its sound again).")


def listen_mix(film, tl, windows=3, span=12.0):
    """(report line, flags): spot-transcribe the served mix at a few points and compare with the timeline's words.
    Whisper only (align.py); without it the line says so and nothing is flagged."""
    import difflib, statistics, align
    mix = film / "out" / "mix.wav"
    if not mix.exists():
        return "listen: no out/mix.wav yet", []
    words = sorted((w for s in tl["segments"] for w in (s.get("words") or [])), key=lambda w: w[1])
    if not words:
        return "listen: no timed words", []
    try:
        align._whisper_python()
    except RuntimeError as e:
        return f"listen: not run ({e})", []
    norm = lambda w: re.sub(r"[^a-z0-9]", "", str(w).lower())
    total, flags, offs, parts = float(tl["duration"]), [], [], []
    with tempfile.TemporaryDirectory() as d:
        for k in range(windows):
            a = min(max(0.0, total * (k + 1) / (windows + 1) - span / 2), max(0.0, total - span))
            clip = Path(d) / f"w{k}.wav"
            run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.3f}", "-t", f"{span:.3f}", "-i", str(mix), "-ar", "16000", "-ac", "1", str(clip)])
            try:
                heard = align.whisper_words(clip)
            except RuntimeError as e:
                return f"listen: whisper failed ({e})", []
            ours = [w for w in words if a + 0.5 <= w[1] <= a + span - 0.5]
            if len(ours) < 4:
                continue
            A, B = [norm(w[0]) for w in ours], [norm(h[0]) for h in heard]
            sm = difflib.SequenceMatcher(a=A, b=B, autojunk=False)
            pairs = [(ours[b.a + i], heard[b.b + i]) for b in sm.get_matching_blocks() for i in range(b.size)]
            share = len(pairs) / len(ours)
            off = statistics.median(h[1] + a - w[1] for w, h in pairs) if pairs else None
            parts.append(f"{a:.0f} s: {share:.0%} heard" + (f", {off * 1000:+.0f} ms" if off is not None else ""))
            if off is not None:
                offs.append(off)
            if share < 0.5 or off is None or abs(off) > 0.25:
                flags.append(f"listen: the served mix at {a:.0f}-{a + span:.0f} s doesn't say the timeline's words there "
                             f"({share:.0%} heard" + (f", {off * 1000:+.0f} ms off" if off is not None else "") +
                             "): the sound is from another cut. Run `longform.py mix`")
    return "listen: " + ("; ".join(parts) if parts else "no window had enough words"), flags


def require_fresh_timeline(film, what):
    tl = load_timeline(film)
    why = stale_timeline(film, tl)
    if why:
        raise SystemExit(f"{what} refused: {why}, so it would read an old cut. Run `longform.py timeline {film}` and fix "
                         "what stops it first.")
    return tl


# ── real things on screen need a real capture ───────────────
# A scene that shows a real thing (a scam text, a company site, a headline, an agency page) points at a capture:
# "real": "<id>" in sources/captures.json, {"<id>": {"file", "url", "captured": "YYYY-MM-DD", "licence", "credit"?}}.
# Elements that look real but are made up say so with "illustrative": true. Unmarked, they stop timeline and render.
CAPTURE_FIELDS = ("file", "url", "captured", "licence")
REAL_ICONS = {"web", "doc", "phone", "bank", "card"}     # board cards drawn as evidence (a plain note is exempt)


def load_captures(film):
    m = film / "sources" / "captures.json"
    return json.loads(m.read_text()) if m.exists() else {}


def real_things(seg):
    """(where, what, element) for everything in a segment that names a capture or looks like a real artifact."""
    d = seg.get("data") or {}
    out = []
    if seg.get("type") in ("sms", "capture"):
        out.append((seg["id"], "text message" if seg["type"] == "sms" else "screenshot", d))
    for k, b in enumerate(d.get("beats") or []):
        if isinstance(b, dict) and (b.get("notify") or b.get("sms") or "real" in b):
            out.append((f"{seg['id']} beat {k + 1}", "text message" if b.get("sms") else "phone notification", b))
    for c in d.get("cards") or []:
        if isinstance(c, dict) and ((c.get("icon") in REAL_ICONS and c.get("kind") != "note") or "real" in c):
            out.append((f"{seg['id']} card {c.get('id', '?')}", f"{c.get('icon') or 'evidence'} card", c))
    return out


def real_asset_problems(film, segs):
    caps, missing = load_captures(film), []
    for seg in segs:
        made_up = seg.get("illustrative") or (seg.get("data") or {}).get("illustrative")
        for where, what, el in real_things(seg):
            if seg.get("type") != "capture" and (made_up or el.get("illustrative")):
                continue                        # a capture scene shows a captured file by definition: it can't be made up
            rid = el.get("real")
            if not rid:
                missing.append(f"{where}: a real-looking {what} with no \"real\" capture id; "
                               "point it at sources/captures.json or mark it \"illustrative\": true")
                continue
            entry = caps.get(rid)
            if not entry:
                missing.append(f"{where}: capture \"{rid}\" is not listed in sources/captures.json")
                continue
            gaps = [f for f in CAPTURE_FIELDS if not str(entry.get(f) or "").strip()]
            if gaps:
                missing.append(f"{where}: capture \"{rid}\" has no {', '.join(gaps)}")
            elif not (film / entry["file"]).exists():
                missing.append(f"{where}: capture \"{rid}\" file {entry['file']} is missing")
            elif not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(entry["captured"])):
                missing.append(f"{where}: capture \"{rid}\" date {entry['captured']!r} is not YYYY-MM-DD")
    return missing


def png_text(path):
    """A PNG's tEXt entries as a dict ({} for anything else or a damaged file)."""
    import struct
    out = {}
    try:
        with open(path, "rb") as f:
            if f.read(8) != b"\x89PNG\r\n\x1a\n":
                return out
            while True:
                head = f.read(8)
                if len(head) < 8:
                    break
                n, kind = struct.unpack(">I4s", head)
                if kind == b"IDAT" or kind == b"IEND":
                    break
                data = f.read(n); f.read(4)
                if kind == b"tEXt" and b"\0" in data:
                    k, v = data.split(b"\0", 1); out[k.decode("latin-1")] = v.decode("latin-1")
    except OSError:
        pass
    return out


def draft_stills(film, segs):
    """Panel stills stamped as drafts by the stills route (a step-distilled 'turbo' model): layout only, never shipped
    (owner's rule, 2026-11-25). An upscaled copy loses the stamp, so its source is read too. A panel whose accepted repair
    came from a local edit model is a draft too (rule 15, 2026-11-26), read from repairs/log.json, so a JPEG counts."""
    out = []
    local = panelqa.draft_repairs(film)
    for sg in segs:
        if sg["type"] != "panel":
            continue
        for img in panel_images(sg.get("data")):
            cands = [film / img] + ([film / img.split("panels-hd/", 1)[1]] if "panels-hd/" in img else [])
            hit = next((png_text(p) for p in cands if png_text(p).get("motion-studio-draft") == "1"), None)
            if hit is not None:
                out.append(f'{sg["id"]}: {img} is a draft still ({hit.get("motion-studio-model", "draft model")}, layout only); '
                           f'redraw it with a full-quality model before render')
            elif local.get(img.split("panels-hd/", 1)[-1]):
                out.append(f'{sg["id"]}: {img} has an accepted {local[img.split("panels-hd/", 1)[-1]]} repair (local model, drafts '
                           f'only); revert it and repair with ChatGPT before render')
    return out


def gate_real_assets(film, segs, step):
    missing = real_asset_problems(film, segs)
    if missing:
        raise SystemExit(f"{step} stopped: {len(missing)} real-looking element(s) without a listed capture\n  "
                         + "\n  ".join(missing))


# what `init` writes to a new film's engine.json: behaviour that is on for new films but would change an older film's
# re-render. "anticipate": figures lean back before a big pose change, overshoot and settle (figure.js F.at; 2026-10-25).
# "map_drift": a map camera pushes in slowly across every hold instead of sitting still (mapscene.js; 2026-11-25)
# "stat_drift": a stat scene rises slowly from its count on, so a long stat never holds still (longform.html; 2026-11-25)
ENGINE_DEFAULTS = {"anticipate": True, "map_drift": True, "stat_drift": True}
CAPTION_STYLES = ("pages", "chunk")
SCREEN_FONTS = {"caslon": "LibreCaslonText.ttf", "marker": "PermanentMarker-Regular.ttf", "elite": "SpecialElite-Regular.ttf",
                "anton": "Anton-Regular.ttf", "nunito": "Nunito.ttf"}
HOUSE_FONTS = ("LibreCaslonText.ttf", "LibreCaslonText-Italic.ttf", "PermanentMarker-Regular.ttf", "SpecialElite-Regular.ttf", "Anton-Regular.ttf")


def cmd_init(a):
    film = Path(a.film).resolve()
    for d in ("narration", "out", "review", "sources"):
        (film / d).mkdir(parents=True, exist_ok=True)
    shutil.copy(TEMPLATE, film / "film.html")
    for f in ENGINE_FILES:
        shutil.copy(HERE / f, film / f)
    # defaults a film made from today on gets, where an older film keeps the old behaviour so its re-renders don't change.
    # script.json overrides each ("anticipate": false)
    eng = film / "engine.json"
    if not eng.exists():
        eng.write_text(json.dumps(ENGINE_DEFAULTS, indent=1) + "\n")
    print(film)


def say_map(sc, s):
    import say
    return say.merged(sc.get("say"), s.get("say"))


def spoken_lines(sc):
    """(segment, the text the voice gets) for every narrated segment: the narration with the film's "say" map applied."""
    import say
    return [(s, say.apply(s["narration"], say_map(sc, s))) for s in sc["segments"] if s.get("narration")]


def say_problems(sc):
    """What the voice is likely to misread, in the text it will be given (so a "say" entry that fixes it clears it)."""
    import say
    return [f"{s['id']}: \"{w}\": {why}" + (f"; try \"say\": {{\"{w}\": \"{fix}\"}}" if fix else "")
            for s, text in spoken_lines(sc) for w, why, fix in say.lint(text)] \
        + [f"{s['id']}: \"{w}\": {why} ({fix})" for s, text in spoken_lines(sc) for w, tier, why, fix in say.homographs(text) if tier == "BLOCK"]


def homograph_listen(sc):
    """WARN-tier homographs ("record", "use", "close"...): usually read right, so listed for the listen-through, not flagged."""
    import say
    return [f"{s['id']}: \"{w}\"" for s, text in spoken_lines(sc) for w, tier, why, fix in say.homographs(text) if tier == "WARN"]


def homograph_blocks(sc, ids=None):
    """Lines the voice must not be given yet: a BLOCK-tier homograph that no "say" entry has pinned (say.HOMOGRAPHS)."""
    import say
    return [f"{s['id']}: \"{w}\" in \"{text[:80]}\": {why}; pin it with \"say\": {{\"{w}\": \"{fix.split(' or ')[0]}\"}} (or the other reading)"
            for s, text in spoken_lines(sc) if not ids or s["id"] in ids for w, tier, why, fix in say.homographs(text) if tier == "BLOCK"]


# ── one voice per film ───────────────────────────────────────
# A film is voiced by one voice, one model pack and one speed (a drama: one per speaker, "voices": {speaker: {...}}).
# Whoever voices a clip records it with `voiced`, in narration/voice.json: {id: {engine, pack, name, speed, text, sha1}}.
# timeline stops when a clip is unrecorded, was changed after it was recorded, was voiced for other words, or was voiced
# with another voice, pack or speed: a re-voice at 1.17 slipped into the toll-text film (2026-10-23).
VOICE = {"engine": "kokoro", "pack": "v1.0", "name": "am_michael", "speed": 1.0}   # the owner's pick (2026-10-23)
VOICE_KEYS = ("engine", "pack", "name", "speed")
# Paid voices wait for the final animation pass (owner's rule, 2026-11-24): drafts and animatics are voiced with a free
# engine (Kokoro, or an open model on Colab), and a paid engine is used once, for the final read, after the owner signs
# off the last animation pass. That sign-off is recorded in script.json as "animation_signed_off": {"by", "on"}, written
# only on the owner's word. Until then `lines` won't hand over lines for a paid voice and `voiced` won't record one.
PAID_VOICE_ENGINES = ("elevenlabs", "eleven")


def paid_voice_block(sc, v):
    """Why a paid voice may not be used yet, or None."""
    if not str(v.get("engine", "")).lower().startswith(PAID_VOICE_ENGINES):
        return None
    so = sc.get("animation_signed_off") or {}
    if so.get("by") and so.get("on"):
        return None
    return (f"{v['engine']} is a paid voice, and paid voices wait until the owner signs off the final animation pass "
            "(script.json \"animation_signed_off\": {\"by\", \"on\"}, set only on their word). Voice drafts with a free "
            "engine: set \"voice\" back to Kokoro (the default) or an open model on Colab.")


def voice_for(sc, s):
    v = dict(VOICE, **(sc.get("voice") or {}))
    sp = s.get("speaker")
    if sp and sp != "narrator":
        v = dict(v, **((sc.get("voices") or {}).get(sp) or {}))
    return {k: v[k] for k in VOICE_KEYS}


def sha1(path):
    import hashlib
    return hashlib.sha1(Path(path).read_bytes()).hexdigest()


def voice_problems(film, sc):
    vj = film / "narration" / "voice.json"
    rec = json.loads(vj.read_text()) if vj.exists() else {}
    out, used = [], {}
    for s, text in spoken_lines(sc):
        clip, want, e = film / "narration" / f"{s['id']}.mp3", voice_for(sc, s), rec.get(s["id"])
        if not clip.exists():
            continue                                    # timeline reports missing clips itself
        if not e:
            out.append(f"{s['id']}: no record of how it was voiced (run `voiced` after voicing it)")
            continue
        got = {k: e.get(k) for k in VOICE_KEYS}
        blk = paid_voice_block(sc, got)
        if blk:
            out.append(f"{s['id']}: voiced before sign-off. {blk}")
        if got["engine"] == want["engine"] == "human":
            continue
        diff = [f"{k} {got[k]!r} (film: {want[k]!r})" for k in VOICE_KEYS if got[k] != want[k]]
        if diff:
            out.append(f"{s['id']}: voiced with " + ", ".join(diff))
        if e.get("sha1") != sha1(clip):
            out.append(f"{s['id']}: the clip changed after it was recorded as voiced (re-voiced by hand?); voice it again and run `voiced`")
        elif e.get("text") != text:
            out.append(f"{s['id']}: voiced for other words than the script now has; voice it again")
        used.setdefault((s.get("speaker") or "narrator", got["pack"], got["speed"]), []).append(s["id"])
    by_speaker = {}
    for (sp, pack, speed), ids in used.items():
        by_speaker.setdefault(sp, []).append(f"pack {pack} at speed {speed}: {', '.join(ids[:6])}" + (" …" if len(ids) > 6 else ""))
    for sp, mixes in by_speaker.items():
        if len(mixes) > 1:
            out.append(f"{sp} is voiced with more than one pack or speed: " + "; ".join(mixes))
    return out


def cmd_voiced(a):
    """Record how clips were voiced (after voicing them): the film's voice unless overridden, the words, the file's hash."""
    film = Path(a.film).resolve()
    sc = load_script(film)
    vj = film / "narration" / "voice.json"
    rec = json.loads(vj.read_text()) if vj.exists() else {}
    blocks = homograph_blocks(sc, a.ids)
    if blocks and not a.allow_homographs:
        raise SystemExit("voiced stopped: these clips were given words with two pronunciations; pin each in \"say\", "
                         "re-voice the clip, then run voiced again\n  " + "\n  ".join(blocks))
    over = {k: v for k, v in (("engine", a.engine), ("pack", a.pack), ("name", a.name), ("speed", a.speed)) if v is not None}
    n = 0
    for s, text in spoken_lines(sc):
        if a.ids and s["id"] not in a.ids:
            continue
        clip = film / "narration" / f"{s['id']}.mp3"
        if not clip.exists():
            print(f"  {s['id']}: no clip yet, skipped")
            continue
        v = dict(voice_for(sc, s), **over)
        blk = paid_voice_block(sc, v)
        if blk:
            raise SystemExit(f"voiced stopped at {s['id']}: {blk}")
        rec[s["id"]] = dict(v, text=text, sha1=sha1(clip))
        n += 1
    vj.write_text(json.dumps(rec, indent=1, ensure_ascii=False) + "\n")
    print(f"{n} clip(s) recorded -> {vj}")
    for p in voice_problems(film, sc):
        print("VOICE:", p)


def speed_outliers(film, segs, tol=0.15, drift=0.05):
    """Clips spoken noticeably faster or slower than the film's median (spoken letters per second, from the word
    times): a speed slip shows here even without a record. Sentences vary by about ±10% on their own (more with spread
    timing), so only a larger gap is flagged; a 1.17 re-voice showed as +37% on the test film."""
    import statistics
    import align
    rates = {}
    for s in segs:
        ws = s.get("words") or []
        if len(ws) >= 6:
            speech = ws[-1][2] - ws[0][1]                # first word on to last word off: pauses inside count, as speed stretches them too
            if speech > 1.5:
                rates[s["id"]] = sum(len("".join(align.spoken(w[0]))) for w in ws) / speech   # spoken letters per second
    if len(rates) < 4:
        return []
    med = statistics.median(rates.values())
    out = [f"{sid}: spoken {r / med - 1:+.0%} against the film's median pace; check its voice speed"
           for sid, r in rates.items() if abs(r / med - 1) > tol]
    # drift: one clip 8% fast is a sentence; three or more in a row all off the same way is a passage that sounds
    # rushed or dragging (toll-text's opening ran 6-11% fast across its first scenes, under the single-clip limit)
    ids, run = list(rates), []
    for i, sid in enumerate(ids + [None]):
        dev = rates[sid] / med - 1 if sid else 0.0
        if run and (sid is None or abs(dev) < drift or (dev > 0) != (rates[run[0]] / med > 1)):
            if len(run) >= 3:
                devs = [rates[r] / med - 1 for r in run]
                out.append(f"{run[0]}..{run[-1]}: {len(run)} clips in a row spoken {min(devs, key=abs):+.0%} to {max(devs, key=abs):+.0%} "
                           f"against the film's median pace; the passage will sound {'rushed' if devs[0] > 0 else 'slow'}")
            run = []
        if sid and abs(dev) >= drift:
            run.append(sid)
    return out


def cmd_lines(a):
    film = Path(a.film).resolve()
    sc = load_script(film)
    lines = spoken_lines(sc)
    blocks = homograph_blocks(sc)
    if blocks and not a.allow_homographs:
        raise SystemExit("lines stopped: words with two pronunciations the voice will guess at; rewrite or pin each one\n  "
                         + "\n  ".join(blocks))
    for p in say_problems(sc):
        print("SAY:", p, file=sys.stderr)
    listen = homograph_listen(sc)
    if listen:
        print(f"LISTEN: {len(listen)} word(s) with two readings the voice usually gets right; check them in the listen-through: "
              + ", ".join(listen), file=sys.stderr)
    v = voice_for(sc, {})
    paid = sorted({blk for s, _ in lines if (blk := paid_voice_block(sc, voice_for(sc, s)))})
    if paid:
        raise SystemExit("lines stopped: " + "\n  ".join(paid))
    print(f"VOICE: {v['engine']} {v['name']}, pack {v['pack']}, speed {v['speed']}; run `voiced` after voicing", file=sys.stderr)
    if a.json:      # a drama: one voice per speaker, inner monologue read closer and quieter
        print(json.dumps([{"id": s["id"], "speaker": s.get("speaker", "narrator"), "mono": bool(s.get("mono")), "voice": voice_for(sc, s), "text": text,
                           **({"shown": s["narration"]} if text != s["narration"] else {})} for s, text in lines], indent=1, ensure_ascii=False))
        return
    for s, text in lines:
        print(f"{s['id']}\t{text}")


def panel_list(film, sc, problems=None):
    """Every panel image the script's panel scenes use, once each and in film order, with what the generator needs: the
    shot, who is in frame, the prompt, reference images and plate. The first beat to give a field sets it."""
    panels = {}
    for s in sc["segments"]:
        if s.get("type") != "panel":
            continue
        d = s.get("data") or {}
        img = (panel_images(d) or [None])[0]       # a flipbook panel is listed by its first frame (its key drawing)
        if not img:
            if problems is not None:
                problems.append(f"{s['id']}: panel scene with no data.image or data.frames")
            continue
        p = panels.setdefault(img, {"image": img, "beats": [], "shot": d.get("shot"), "char_in_frame": d.get("char_in_frame", []),
                                    "flashback": bool(d.get("flashback")), "prompt": d.get("prompt", ""), "seed": d.get("seed"),
                                    "refs": d.get("refs", []), "plate": d.get("plate"), "size": d.get("size"),
                                    "props": d.get("props", [])})
        p["beats"].append(s["id"])
        for k in ("prompt", "refs", "plate", "size", "props"):
            if d.get(k) and not p[k]:
                p[k] = d[k]
    return list(panels.values())


def cmd_panels(a):
    """The unique panel images and their prompts -> out/panels.json for the image runner. Beats that reuse a panel
    (another crop or camera move) list it once."""
    film = Path(a.film).resolve()
    sc = load_script(film)
    problems = []
    panels = panel_list(film, sc, problems)
    sizes = sc.get("sizes") or {}
    problems += scalecheck.sheet_problems(sizes)
    for p in panels:
        p["exists"] = (film / p["image"]).exists()
        p["upscaled"] = bool(hd_path(film, p["image"]))
        if not p["prompt"]:
            problems.append(f"{p['image']} (beats {', '.join(p['beats'])}): no prompt")
        unknown = [k for k in p.get("props") or [] if k not in sizes]
        if unknown:
            problems.append(f"{p['image']}: props with no entry in \"sizes\": {', '.join(unknown)}")
        p["scale"] = scalecheck.scale_sentence(p, sizes)
        p["prompt"] = scalecheck.with_scale(p["prompt"], p["scale"])
        if p["exists"]:
            white = panelqa.white_share(film / p["image"])
            if white > panelqa.WHITE_MAX:
                problems.append(f"{p['image']}: {white:.0%} of the picture is near-white; ground and paper should be warm "
                                f"cream (no channel over {panelqa.WHITE_LEVEL}); redraw or grade it down")
    for x in problems:
        print("PROBLEM:", x)
    out = film / "out" / "panels.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"panels": panels, "characters": sc.get("characters", {})}, indent=1, ensure_ascii=False) + "\n")
    missing = [p for p in panels if not p["exists"]]
    print(f"{len(panels)} unique panels for {sum(len(p['beats']) for p in panels)} panel beats; {len(missing)} still to generate -> {out}")
    warned = panelqa.lint([dict(p, id=p["image"]) for p in panels])
    if warned:
        print(f"{len(warned)} panel prompts have lint warnings; run `longform.py lint {a.film}`")


ENGINE_FILES = ("kit.js", "house.js", "figure.js", "closeup.js", "mapscene.js", "three.min.js", "set3d.js")


def engine_drift(film, segs):
    """(problems, warnings) about the film's own copy of the engine. A film keeps the film.html and scripts `init` copied
    in, so a scene type added to the engine later is unknown to an older film, and its page draws it as a statement of
    the word "undefined" with no error (two cutaways in a full film, 2026-11-25). A segment whose type the film's page
    doesn't define is a problem; any engine file that differs from the engine's own copy is a warning."""
    page = film / "film.html"
    if not page.exists():
        return [], []
    html = page.read_text(errors="replace")
    known = set(re.findall(r"^B\.(\w+)\s*=", html, re.M))
    if "B.map = MAP.build" in html and (film / "mapscene.js").exists():
        known.add("map")
    if "B.set3d = " in html and all((film / f).exists() for f in ("set3d.js", "three.min.js")):
        known.add("set3d")
    probs = [f'{sg["id"]}: scene type "{sg["type"]}" is not in this film\'s film.html (it would draw as "undefined"); '
             f'run `longform.py init {film}` to refresh the engine files (script.json is kept)'
             for sg in segs if sg["type"] not in known]
    stale = [n for n, src in [("film.html", TEMPLATE)] + [(f, HERE / f) for f in ENGINE_FILES]
             if (film / n).exists() and (film / n).read_bytes() != src.read_bytes()]
    warns = [f"engine files differ from the engine's: {', '.join(stale)}; run `longform.py init {film}` to refresh them "
             f"(keep a copy first if you edited one by hand)"] if stale else []
    return probs, warns


def cutaway_problems(segs, sizes):
    """A cutaway is accurate by construction only if its subject and person are in the size sheet and every part fits
    inside the subject (2 cm of slack for line weight)."""
    out = []
    for sg in segs:
        if sg.get("type") != "cutaway":
            continue
        d = sg.get("data") or {}
        sub = sizes.get(d.get("subject"))
        if not sub:
            out.append(f'{sg["id"]}: cutaway subject "{d.get("subject")}" is not in "sizes"')
            continue
        if d.get("person") and d["person"] not in sizes:
            out.append(f'{sg["id"]}: cutaway person "{d["person"]}" is not in "sizes"')
        w = (sub.get("w") if d.get("view") == "front" else sub.get("d") or sub.get("w")) or sub["h"]
        for p in d.get("parts") or []:
            if "rect" in p:
                x, y, pw, ph = p["rect"]; xs, ys = [x, x + pw], [y, y + ph]
            elif "circle" in p:
                x, y, r = p["circle"]; xs, ys = [x - r, x + r], [y - r, y + r]
            else:
                nums = [float(v) for v in re.findall(r"-?\d*\.?\d+", str(p.get("path", "")))]
                xs, ys = nums[0::2] or [0], nums[1::2] or [0]
            if min(xs) < -0.02 or max(xs) > w + 0.02 or min(ys) < -0.02 or max(ys) > sub["h"] + 0.02:
                out.append(f'{sg["id"]}: cutaway part "{p.get("label")}" reaches outside the {d["subject"]} '
                           f'({w:g} m by {sub["h"]:g} m)')
    return out


def scale_verdicts(film):
    f = film / "out" / "scale.json"
    return json.loads(f.read_text()) if f.exists() else {}


def gate_scale(film, sc, step):
    """A film with a "sizes" sheet renders only once every panel with two or more sized things has passed the
    proportion check on its current image (scalecheck.py)."""
    sizes = sc.get("sizes") or {}
    if not sizes:
        return
    probs = scalecheck.sheet_problems(sizes) + scalecheck.gate_problems(film, panel_list(film, sc), sizes, scale_verdicts(film))
    if probs:
        raise SystemExit(f"{step} stopped: {len(probs)} proportion problem(s); run `longform.py scale {film.name}`, mark "
                         "the stills, then `--marks marks.json`\n  " + "\n  ".join(probs))


def cmd_scale(a):
    """Proportions: without --marks, writes out/scale/index.html to mark each panel with two or more sized things; with
    --marks, judges them into out/scale.json and exits 1 on any failure."""
    film = Path(a.film).resolve()
    sc = load_script(film)
    sizes = sc.get("sizes") or {}
    if not sizes:
        raise SystemExit('no "sizes" in script.json: add each person and prop with its real height in metres and a source')
    for x in scalecheck.sheet_problems(sizes):
        print("PROBLEM:", x)
    panels = panel_list(film, sc)
    if not a.marks:
        f, n = scalecheck.page(film / "out" / "scale", film, panels, sizes, sc.get("title", film.name), a.tol)
        print(f"{n} panels to mark -> {f}")
        return
    old = scale_verdicts(film)
    new = scalecheck.check_all(film, panels, sizes, json.loads(Path(a.marks).read_text()), a.tol)
    (film / "out").mkdir(exist_ok=True)
    (film / "out" / "scale.json").write_text(json.dumps({**old, **new}, indent=1) + "\n")
    if scalecheck.report(new):
        sys.exit(1)


def cmd_lint(a):
    """Warn on prompt patterns that break anatomy or blur a scene on small image models. Takes a film folder (its panel
    scenes) or a JSON list of panel records with prompt, shot, refs and plate (an image runner's prompt file)."""
    src = Path(a.source).resolve()
    if src.is_dir():
        items = [dict(p, id=f"{p['image']} ({', '.join(p['beats'])})") for p in panel_list(src, load_script(src))]
    else:
        data = json.loads(src.read_text())
        data = data.get("panels", data) if isinstance(data, dict) else data
        items = [dict(x, id=str(x.get("id") or x.get("beat") or x.get("image") or i + 1)) for i, x in enumerate(data)]
    warned = panelqa.lint(items, a.boiler)
    for pid, ws in warned:
        print(pid)
        for w in ws:
            print("   ", w)
    n = sum(len(w) for _, w in warned)
    print(f"{len(items)} prompts, {len(warned)} with warnings ({n} in all). Warnings are heuristics from the 2026-10-17 anatomy "
          "research; a person decides.")


def cmd_review(a):
    """out/review/index.html: every panel numbered in film order. Click to mark, drag to box a region, type the fix; the
    page keeps marks in this browser, downloads them as marks.json, and prints a `repair` command for each box.
    --checks is a checker's verdicts, {image: {"verdict": "FAIL", "reason", "boxes": [[x, y, w, h] in 0-1]}}."""
    film = Path(a.film).resolve()
    sc = load_script(film)
    checks = json.loads(Path(a.checks).read_text()) if a.checks else {}
    page = panelqa.review_page(film, panel_list(film, sc), checks, sc.get("title", film.name))
    print(f"review sheet -> {page} (open it in a browser; it reads the panels from the film folder)")


def image_for(film, a):
    """The panel image to repair and its panel record. --image works on a bare folder of panels with no script.json."""
    sc = load_script(film) if (film / "script.json").exists() else {"segments": []}
    if a.image:
        return a.image, [p for p in panel_list(film, sc) if p["image"] == a.image][:1]
    for p in panel_list(film, sc):
        if a.beat in p["beats"]:
            return p["image"], [p]
    raise SystemExit(f"no panel beat {a.beat}")


def cmd_repair(a):
    """Redraw one region of a panel (a broken hand, a stray limb): crop a square around --box, send the crop to ChatGPT
    (through codex) as the edit base, and paste back only the box plus --margin, blended inside that edge and colour-matched
    to the panel around it. Neither route takes a mask, so the model redraws the whole crop and our mask confines the
    change. The local mflux models are drafts only (--draft-local). Candidates land in repairs/<image>/rNN/; nothing
    touches the panel until `accept`."""
    film = Path(a.film).resolve()
    img, found = image_for(film, a)
    beats = found[0]["beats"] if found else []
    if a.feather is not None and a.margin is not None and a.feather > a.margin:
        print(f"  note: --feather {a.feather} is wider than --margin {a.margin}, so the edge of the box itself keeps some of the old drawing")
    model = a.model or (panelqa.CHATGPT if not a.draft_local else "qwen")
    if model in panelqa.GENERATORS:
        print(f"  DRAFT ONLY: {model} is a local model; the render refuses this panel while the repair is accepted")
    # the default on the ChatGPT route runs markup and whole together, one take each, and the gate keeps whichever fixes
    # it: each won one of the two A/Bs of 2026-11-27 (whole on merged roofs where markup failed, markup on a liftgate where
    # it was the sharpest), so one call of each beats three of either (picture-truth.md rule 16)
    method = a.method or ("pair" if model == panelqa.CHATGPT else "crop")
    methods = ["markup", "whole"] if method == "pair" else [method]
    seeds = a.seeds or ([1] if method == "pair" else [1, 2, 3])
    for m in methods:
        rdir, made, secs, e = panelqa.repair(film, img, a.box, a.prompt, ref=a.ref, model=model, seeds=seeds, context=a.context,
                                             draft=a.draft_local, allow_codex=a.allow_codex, faceless=a.faceless, method=m,
                                             margin=a.margin, feather=a.feather, color=not a.no_color_match, min_change=a.min_change,
                                             gen_size=a.gen_size, gen_args=a.gen_args.split() if a.gen_args else [], beats=beats,
                                             sharpen=not a.no_sharpen)
        rid = e["id"]
        cross = {k: v for k, v in e["crossings"].items() if v}
        if cross:
            print("  lines crossing the paste edge (they stay as drawn outside it; if the redraw moves them, they break there): "
                  + ", ".join(f"{k} {v}" for k, v in cross.items()))
        print(f"  {m}: crop {e['crop'][2]} px, generated at {e['gen_size']} ({e['enlarged_with'] if e['enlarged_with'] != 'none' else 'native size'})")
        for s_, c in e["checks"].items():
            print(f"  {m} take {s_}: {c['change']:.0%} of the box changed, detail ratio {c['sharpness']}"
                  + (f" (sharpened {c['sharpen']})" if c.get("sharpen") else "")
                  + (f"; {'; '.join(c['notes'])}" if c["notes"] else ""))
        print(f"{rid} ({m}): {len(made)} candidate(s) in {secs} s -> {rdir.relative_to(film)}/compare_<seed>.png")
        if made:
            print(f"  gate it at full size, then: longform.py accept {a.film} {rid}" + (" --seed N" if len(made) > 1 else ""))


def cmd_accept(a):
    film = Path(a.film).resolve()
    src = panelqa.accept(film, a.repair, a.seed)
    if panelqa.draft_repairs(film).get(str(src.relative_to(film))):
        print("  DRAFT ONLY: this repair came from a local model, so the render refuses this panel until it is reverted")
    print(f"{src.relative_to(film)} replaced (the previous version is in repairs/{a.repair}/); run `upscale` again for this panel")


def cmd_revert(a):
    film = Path(a.film).resolve()
    src = panelqa.revert(film, a.repair)
    print(f"{src.relative_to(film)} restored from before {a.repair}; run `upscale` again for this panel")


def cmd_gemini_draw(a):
    """Draw panels with Gemini (Nano Banana): the prompt plus role-labelled references, 16:9 at 2K, every call fresh.
    Batch by default (half price, results within 24 h; fetch them with `gemini-collect`); --now draws interactively.
    The run is priced first and refused whole past the cap in ~/.motion-studio.json ("gemini": {"cap_usd": 15})."""
    import gemini
    film = Path(a.film).resolve()
    if a.jobs:
        data = json.loads(Path(a.jobs).read_text())
        items = data.get("panels", data) if isinstance(data, dict) else data
    else:
        items = panel_list(film, load_script(film))
    if a.beats:
        items = [p for p in items if set(p.get("beats", [])) & set(a.beats) or p.get("image") in a.beats]
    if not a.redo:
        items = [p for p in items if not (film / p["image"]).exists()]
    if not items:
        raise SystemExit("nothing to draw (every panel exists; --redo draws them again and moves the old ones to history/)")
    jobs = gemini.make_jobs(film, items, model=a.model, size=a.size)
    if a.dry_run:
        cost = sum(gemini.estimate(j, not a.now) for j in jobs)
        for j in jobs:
            print(f"--- {j['image']} ({j['model']}, {len(j['refs'])} refs, ${gemini.estimate(j, not a.now):.4f})\n{gemini.prompt_text(j)}")
        print(f"{len(jobs)} images, about ${cost:.2f} {'interactive' if a.now else 'batch'}; nothing sent")
        return
    run, cost, done, problems = gemini.draw(film, jobs, batch=not a.now)
    for x in problems:
        print("PROBLEM:", x)
    if a.now:
        print(f"run {run}: {len(done)} of {len(jobs)} panels drawn, about ${cost:.2f} committed; sidecars beside each panel")
    else:
        print(f"run {run}: {len(jobs)} panels in {len(done)} batch job(s), about ${cost:.2f} committed; "
              f"`longform.py gemini-collect {a.film}` fetches them once done (target 24 h)")
    print("spend: run ${:.2f}, this month ${:.2f}".format(*gemini.totals(run)))


def cmd_gemini_collect(a):
    import time
    import gemini
    film = Path(a.film).resolve()
    while True:
        written, problems, running = gemini.collect(film)
        for p in written:
            print(f"  {p.relative_to(film)}")
        for x in problems:
            print("PROBLEM:", x)
        if not a.wait or not running:
            break
        time.sleep(a.every)
    print(f"{len(written)} panels written" + (f"; still running: {', '.join(running)}" if running else "; no batch still running"))
    if written:
        print("run `upscale` for the new panels if they are smaller than the frame")


def cmd_gemini_edit(a):
    """Edit a whole panel with one instruction and a keep-list (for Gemini panels this replaces the crop repair):
    "Move him up the bed against the headboard" --keep "the room" --keep "every face and outfit". The result is a
    candidate in repairs/<image>/rNN/; `accept` puts it in the panel and `revert` undoes that."""
    import gemini
    film = Path(a.film).resolve()
    img, found = image_for(film, a)
    refs = [dict(zip(("path", "role"), r.split(":", 1))) if ":" in r else r for r in a.ref or []]
    rdir, e, problem = gemini.edit(film, img, a.instruction, keep=a.keep or [], refs=refs, model=a.model, size=a.size,
                                   beats=found[0]["beats"] if found else [])
    if problem:
        print("PROBLEM:", problem)
    if e["candidates"]:
        print(f"{e['id']}: candidate in {rdir.relative_to(film)}/ (${e['usd']:.3f}, {e['tries']} tr{'y' if e['tries'] == 1 else 'ies'}); "
              f"look at compare_1.png, then: longform.py accept {a.film} {e['id']}")


def cmd_gemini_spend(a):
    import gemini
    rows = gemini.ledger()
    runs = sorted({r["run"] for r in rows if r.get("kind") == "estimate"})
    for run in runs[-a.last:]:
        n = sum(1 for r in rows if r.get("kind") == "estimate" and r["run"] == run)
        print(f"  run {run}: {n} calls, ${gemini.totals(run)[0]:.2f}")
    cfg = gemini.config()
    month_cap = cfg.get("month_cap_usd", gemini.MONTH_CAP_USD)
    print(f"this month ${gemini.totals()[1]:.2f}" + (f" of a ${float(month_cap):.2f} cap" if month_cap is not None else " (no monthly cap)")
          + f"; per-run cap ${float(cfg.get('cap_usd', gemini.CAP_USD)):.2f}; ledger {gemini.ledger_path()}")


PANEL_HD = "panels-hd"      # upscaled copies of the panel images, same relative paths; timeline prefers them


def image_size(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout.strip()
    w, h = out.split(",")[:2]
    return int(w), int(h)


def hd_path(film, img):
    """The upscaled copy of a panel image, if `upscale` made one and it is newer than its source."""
    src, hd = film / img, film / PANEL_HD / Path(img).with_suffix(".png")
    return hd if hd.exists() and (not src.exists() or hd.stat().st_mtime >= src.stat().st_mtime) else None


def cmd_upscale(a):
    """Panels are drawn at the image model's size (1344x768 for klein) and the frame is 1920x1080. Upscale each with
    Real-ESRGAN (realesrgan-ncnn-vulkan, BSD-3; the anime model keeps ink lines clean), then Lanczos down to cover --size
    and centre-crop. The default size is the frame times the deepest camera push (ECU 1.2), so a push-in still shows
    one image pixel per screen pixel. A native large render was tested slower (1,541 s against 7.5 s) and softer."""
    import shutil as sh
    import tempfile
    film = Path(a.film).resolve()
    W, H = (int(x) for x in a.size.lower().split("x"))
    binary = sh.which(a.bin) or (a.bin if Path(a.bin).exists() else None)
    if not binary:
        raise SystemExit(f"{a.bin} not found: install realesrgan-ncnn-vulkan (github.com/xinntao/Real-ESRGAN releases, BSD-3) "
                         "or pass --bin /path/to/realesrgan-ncnn-vulkan")
    imgs = upscale_list(film, load_script(film)["segments"])
    done = skipped = 0
    for img in imgs:
        src = film / img
        if not src.exists():
            print(f"  missing {img}"); continue
        if hd_path(film, img) and not a.redo:
            skipped += 1; continue
        dest = film / PANEL_HD / Path(img).with_suffix(".png")
        dest.parent.mkdir(parents=True, exist_ok=True)
        w, h = image_size(src)
        with tempfile.TemporaryDirectory() as td:
            big = Path(td) / "x4.png"
            if w < W or h < H:
                run([binary, "-i", str(src), "-o", str(big), "-n", a.model, "-s", "4"], capture_output=True)
            else:
                big = src                                   # already large enough: only fit it
            run(["ffmpeg", "-v", "error", "-y", "-i", str(big), "-vf",
                 f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H}", str(dest)])
        done += 1
        print(f"  {img}: {w}x{h} -> {dest.relative_to(film)} ({W}x{H})")
    print(f"{done} upscaled, {skipped} already current, {len(imgs)} panels")


def cmd_align(a):
    import align
    film = Path(a.film).resolve()
    out_path = film / "narration" / "words.json"
    words = json.loads(out_path.read_text()) if out_path.exists() and not a.redo else {}
    methods, sc = {}, load_script(film)
    for s in sc["segments"]:
        sm = say_map(sc, s)
        # kept when its text is unchanged and it was really aligned: a spread (estimated) segment is tried again, so
        # installing an aligner fixes an old film on its next `align`
        if not s.get("narration") or (s["id"] in words and words[s["id"]].get("text") == s["narration"]
                                      and (words[s["id"]].get("say") or {}) == sm and words[s["id"]].get("method") != "spread"):
            continue
        clip = film / "narration" / f"{s['id']}.mp3"
        if not clip.exists():
            raise SystemExit(f"missing narration clip {clip}")
        ws, method = align.align_clip(clip, s["narration"], work=film / "out", say_map=sm)
        words[s["id"]] = {"text": s["narration"], "method": method, "words": ws, **({"say": sm} if sm else {})}
        methods[method] = methods.get(method, 0) + 1
    out_path.write_text(json.dumps(words, indent=1, ensure_ascii=False))
    print(f"words -> {out_path} ({', '.join(f'{v} {k}' for k, v in methods.items()) or 'nothing new'})")
    allm = {}
    for v in words.values():
        allm[v.get("method", "?")] = allm.get(v.get("method", "?"), 0) + 1
    print("aligners, whole film: " + ", ".join(f"{k} {v}" for k, v in sorted(allm.items())))
    if allm.get("spread"):
        print(f"WARN {allm['spread']} segment(s) spread by weight (estimated times, off by up to 0.5 s): install an aligner "
              f"(pipx install mlx-whisper on the Mac, or pip install pocketsphinx) and run align again; check fails on them")


def alignment_flags(film, sc):
    """(report line, flags): which aligner timed each narrated segment, and a flag for every one left "spread" (word
    times estimated, not heard) or never aligned. Cue words, callouts, map tags, cuts and counters all key off these
    times, so an estimate moves them all (owner's out-of-sync notes, Survive Year 1, 2026-11-27)."""
    wp = film / "narration" / "words.json"
    words = json.loads(wp.read_text()) if wp.exists() else {}
    narrated = [x for x in sc.get("segments", []) if x.get("narration")]
    if not narrated:
        return None, []
    count, bad = {}, []
    for x in narrated:
        m = (words.get(x["id"]) or {}).get("method", "none")
        count[m] = count.get(m, 0) + 1
        if m in ("spread", "none"):
            bad.append(x["id"])
    line = "word timing: " + ", ".join(f"{k} {v}" for k, v in sorted(count.items())) + f" of {len(narrated)} narrated segment(s)"
    flags = []
    if bad:
        flags.append(f"word timing: {len(bad)} narrated segment(s) have estimated word times ({', '.join(bad[:8])}"
                     f"{', ...' if len(bad) > 8 else ''}), so every cue on them can land up to 0.5 s off. Install an aligner "
                     f"(pipx install mlx-whisper on the Mac, or pip install pocketsphinx), then `align` and `timeline` again")
    return line, flags


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


TRANSITION_HOLD = 0.3   # s a transition beat waits beyond a continuation before it starts (owner's note, 2026-11-03)


def transition_holds(film, sc, hold, pad, allwords):
    """{segment id: (how it joins the one before, seconds held after it)}. A segment's "join" is "transition" (a new
    place, subject or chapter) or "continue"; unset, it is a transition where a new chapter starts and a continuation
    elsewhere. The segment before a transition holds longer, so its last picture and the voice gap both run longer:
    long enough that the voice gap into the transition is the film's usual continuation gap (the median, from the
    aligned words) plus `hold`. Clips start and end with different amounts of silence, so a flat `hold` added to
    every transition left some under that; without word times it is a flat `hold`. A Short takes its holds from its
    long-form film's timeline, so each segment lasts as long as it does there and the sound cut still lines up."""
    def edges(s):   # silence before the first word and after the last, from the aligned words, or None
        w = allwords.get(s["id"])
        clip = film / "narration" / f"{s['id']}.mp3"
        if not (s.get("narration") and w and w.get("text") == s.get("narration") and w.get("words") and clip.exists()):
            return None
        return w["words"][0]["t0"], duration(clip) - w["words"][-1]["t1"]
    segs = sc["segments"]
    ed = {s["id"]: edges(s) for s in segs}
    natural = {s["id"]: ed[p["id"]][1] + pad + p.get("extra", 0.0) + ed[s["id"]][0]
               for p, s in zip(segs, segs[1:]) if ed[p["id"]] and ed[s["id"]]}
    out, chapter, prev = {}, None, None
    for s in sc["segments"]:
        j = s.get("join")
        if j not in (None, "continue", "transition"):
            raise SystemExit(f'{s["id"]}: "join" must be "continue" or "transition", not {j!r}')
        new_ch = bool(s.get("chapter")) and s.get("chapter") != chapter
        if s.get("chapter"):
            chapter = s["chapter"]
        j = j or ("transition" if new_ch and prev else "continue")
        out[s["id"]] = (j, 0.0)
        prev = s["id"]
    cont = sorted(natural[k] for k, (j, _) in out.items() if j == "continue" and k in natural)
    base = cont[len(cont) // 2] if cont else None
    for p, s in zip(segs, segs[1:]):
        if out[s["id"]][0] == "transition" and hold:
            add = max(0.0, base + hold - natural[s["id"]]) if base is not None and s["id"] in natural else hold
            out[p["id"]] = (out[p["id"]][0], round(add, 3))
    if sc.get("short_of"):
        parent = (film / sc["short_of"]["film"]).resolve()
        if (parent / "timeline.js").exists():
            pseg = {x["id"]: x for x in load_timeline(parent)["segments"]}
            out = {k: (j, pseg[k].get("hold_after", 0.0) if k in pseg else h) for k, (j, h) in out.items()}
    return out


def cmd_timeline(a):
    film = Path(a.film).resolve()
    sc = load_script(film)
    wj = film / "narration" / "words.json"
    allwords = json.loads(wj.read_text()) if wj.exists() else {}
    t, segs, clips, chapters, problems, cues = a.lead, [], [], [], [], []
    moods = sc.get("moods", {})
    holds = transition_holds(film, sc, a.transition_hold, a.pad, allwords)
    import review_state
    picks = review_state.load_picks(film)
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
        dur += holds[s["id"]][1]
        seg = {k: s[k] for k in ("id", "type", "data", "source", "chapter", "cast", "speaker", "mono", "sfx", "bed", "illustrative", "payoff", "steady", "counter", "tag", "tally", "source_plate", "deadspace", "drift", "intro", "shows", "unshown") if k in s}
        seg.update(t0=round(t, 3), t1=round(t + dur, 3), join=holds[s["id"]][0], hold_after=holds[s["id"]][1])
        w = allwords.get(s["id"])
        if w and w.get("text") == s.get("narration"):
            seg["words"] = [[x["w"], round(t + x["t0"], 3), round(t + x["t1"], 3)] for x in w["words"]]
        elif s.get("narration") and allwords:
            problems.append(f"{s['id']}: narration changed since `align`; run align again")
        if seg.get("type") == "panel" and seg.get("data"):
            # the owner's picks among takes (<shot>_s<seed> siblings), made in the studio dashboard: review_state.py
            seg["data"] = dict(seg["data"])
            review_state.apply_picks(film, seg["data"], picks, problems, s["id"])
        if seg.get("type") == "panel" and panel_images(seg.get("data")):
            # a flipbook's frames are drawn over one composition, so every frame must be the first one's size, or a flip jumps
            srcs = panel_images(seg["data"])
            sizes = [image_size(film / x) if (film / x).exists() else None for x in srcs]
            if sizes[0] and any(z and z != sizes[0] for z in sizes[1:]):
                problems.append(f"{s['id']}: flipbook frames differ in size from the first ({sizes[0][0]}x{sizes[0][1]}): " +
                                ", ".join(f"{x} {z[0]}x{z[1]}" for x, z in zip(srcs, sizes) if z and z != sizes[0]))
            use = []
            for x, z in zip(srcs, sizes):
                hd = hd_path(film, x)
                if not hd and z and z[0] < 1920:
                    problems.append(f"{s['id']}: panel {x} is under 1920 wide and has no upscale; run `upscale`")
                use.append(str(hd.relative_to(film)) if hd else x)
            seg["data"] = dict(seg["data"], image=use[0])
            if seg["data"].get("frames"):
                seg["data"]["frames"] = [u if isinstance(f, str) else dict(f, image=u) for f, u in zip(seg["data"]["frames"], use)]
        if seg.get("type") == "capture":        # a captured screenshot: the file, its size and its credit come from the manifest
            cap = load_captures(film).get((seg.get("data") or {}).get("real"), {})
            if cap.get("file") and (film / cap["file"]).exists():
                iw, ih = image_size(film / cap["file"])
                seg["data"] = dict(seg["data"], image=cap["file"], iw=iw, ih=ih, url=cap.get("url", ""))
                seg.setdefault("source", cap.get("credit") or re.sub(r"^https?://(www\.)?([^/]+).*", r"\2", cap.get("url", "")))
        resolve_cues(seg.get("data", {}), seg.get("words"), seg, problems, cues)
        if seg.get("type") == "panel" and (seg.get("data") or {}).get("callouts") and seg["data"].get("image"):
            # the still's quiet map, for callouts placed "auto" (deadspace.py; longform.md Panel callouts)
            import deadspace
            im = film / seg["data"]["image"]
            if im.exists():
                seg["data"]["quiet"] = deadspace.quiet_map(im)
            else:
                problems.append(f"{s['id']}: callouts need the panel's image to place them; {seg['data']['image']} is missing")
        if seg.get("type") == "panel" and (seg.get("data") or {}).get("clip"):
            if (seg["data"].get("frames") or []):
                problems.append(f"{s['id']}: a panel takes a clip or flipbook frames, not both")
            else:
                clip_prepare(film, seg, sc.get("format", "16x9"), problems)
        if seg.get("type") == "panel" and len((seg.get("data") or {}).get("frames") or []) > 1:
            flips, fp = panel_flips(seg)
            problems += fp
            seg["data"]["flips"] = flips
        if seg.get("type") == "panel":
            problems += screen_spans(seg, cues)
        if seg.get("sfx"):      # drama foley: "sfx": ["paper slap", {"kind": "low hit", "at": "envelope"}]
            seg["sfx"] = [dict(x, kind=kind_key(x["kind"])) if isinstance(x, dict) else {"kind": kind_key(x)} for x in seg["sfx"]]
            resolve_cues(seg["sfx"], seg.get("words"), seg, problems, cues)
        for k in ("counter", "tag"):    # the persistent HUD cards (longform.md): a string is shorthand for {"value"/"text"}
            v = seg.get(k)
            if isinstance(v, (str, int, float)) and v is not None:
                seg[k] = {"value" if k == "counter" else "text": str(v)}
            if isinstance(seg.get(k), dict):
                resolve_cues(seg[k], seg.get("words"), seg, problems, cues)
        if isinstance(seg.get("tally"), dict):      # napkin math (longform.md): each line lands on its word
            resolve_cues(seg["tally"], seg.get("words"), seg, problems, cues)
        if seg.get("bed"):      # ambience under the whole segment: "bed": "rain glass" (or a list)
            seg["bed"] = [kind_key(b) for b in ([seg["bed"]] if isinstance(seg["bed"], str) else seg["bed"])]
        segs.append(seg)
        if s.get("chapter") and (not chapters or chapters[-1]["title"] != s["chapter"]):
            if chapters:
                chapters[-1]["t1"] = round(t, 3)
            chapters.append({"title": s["chapter"], "t0": round(t, 3), "mood": s.get("mood") or moods.get(s["chapter"])})
        t += dur
    gate_real_assets(film, segs, "timeline")
    vp = voice_problems(film, sc)
    if vp:
        raise SystemExit("timeline stopped: the narration is not one consistent voice\n  " + "\n  ".join(vp))
    fmt = sc.get("format", "16x9")
    short = sc.get("short_of")
    total = round(t + (SHORT_TAIL if short else a.tail), 3)
    if chapters:
        chapters[-1]["t1"] = total
    eng = json.loads((film / "engine.json").read_text()) if (film / "engine.json").exists() else {}
    tl = {"title": sc["title"], "palette": sc.get("palette", {}), "theme": sc.get("theme", {}), "duration": total, "segments": segs, "format": fmt,
          "chapters": chapters, "captions": (fmt == "9x16" or bool(sc.get("captions", False))) and any("words" in s for s in segs),
          "grade": sc.get("grade", {"vignette": 0.35, "grain": 0}), "cues": cues, "type_scale": sc.get("type_scale", 1),
          "anticipate": bool(sc.get("anticipate", eng.get("anticipate", False))),
          "map_drift": sc.get("map_drift", eng.get("map_drift", False)),
          "stat_drift": sc.get("stat_drift", eng.get("stat_drift", False))}
    # the house collage style (house.js; longform.md "Collage style"): film-wide, off unless script.json says "collage"
    if sc.get("style"):
        tl["style"] = sc["style"]
        if sc["style"] not in ("collage", "plain"):
            problems.append(f"style \"{sc['style']}\": use \"collage\" or \"plain\"")
    if sc.get("paper_grain"):
        tl["paper_grain"] = True
    if sc.get("finish"):                     # a look on the composed frames at render time (finish.py), under the captions
        import finish as finish_
        if finish_.kind_of(sc["finish"]) not in finish_.FINISHES:
            problems.append(f"finish {sc['finish']!r}: use one of {', '.join(finish_.FINISHES)} (or {{\"kind\": ..., \"seed\": n}})")
        else:
            tl["finish"] = sc["finish"]
    if sc.get("caption_style"):              # burned captions: "pages" (the default) or "chunk" (Shorts, 1 to 3 words)
        if sc["caption_style"] not in CAPTION_STYLES:
            problems.append(f"caption_style {sc['caption_style']!r}: use one of {', '.join(CAPTION_STYLES)}")
        else:
            tl["caption_style"] = sc["caption_style"]
    need = HOUSE_FONTS if sc.get("style") == "collage" else ()
    need += ("Nunito.ttf",) if tl.get("caption_style") == "chunk" else ()
    for seg in tl.get("segments", []):                 # a screen lettered in a house face (a hand-written price: "marker")
        scr = (seg.get("data") or {}).get("screen") if isinstance(seg.get("data"), dict) else None
        for one in (scr if isinstance(scr, list) else [scr] if scr else []):
            if isinstance(one, dict) and one.get("font") in SCREEN_FONTS:
                need += (SCREEN_FONTS[one["font"]],)
    if need and not all((film / "fonts" / f).exists() for f in need):
        import mapart                                   # the house typefaces (OFL, Apache-2.0), with their licences
        mapart.cmd_fonts(film)
    if sc.get("sizes"):
        tl["sizes"] = sc["sizes"]
    if sc.get("set3d"):                                 # the 2.5D pack's film-wide settings (palette), references/set3d.md
        tl["set3d"] = sc["set3d"]
    problems += cutaway_problems(segs, sc.get("sizes") or {})
    for w in palette_warnings(tl["palette"]):
        print("WARN", w)
    # adjacent panels on one still play as one camera move; a camera object on the later one restarts it, a cut back to
    # the same picture (owner's note, 2026-11-27)
    for p_, q_ in zip(segs, segs[1:]):
        dp, dq = p_.get("data") or {}, q_.get("data") or {}
        if p_["type"] == q_["type"] == "panel" and dp.get("image") and dp.get("image") == dq.get("image") \
                and not dp.get("clip") and not dq.get("clip") and isinstance(dq.get("camera"), dict):
            print("WARN", f"{q_['id']}: same still as {p_['id']} with its own camera: it cuts back to the same picture. "
                          f"Drop its camera to carry {p_['id']}'s move on, or use another still")
    drift, drift_warn = engine_drift(film, segs)
    problems += drift
    for w in drift_warn:
        print("WARN", w)
    if any(sg.get("type") == "map" for sg in segs):     # the map scene's projection and regions (mapart.py build)
        mj = film / "maps" / "map.json"
        if mj.exists():
            tl["map"] = json.loads(mj.read_text())
        else:
            problems.append("map segments but no maps/map.json: run mapart.py build first")
    if short:
        problems += short_audio(film, short, segs, a.lead)
    tl["script_sha"] = script_sha(film)      # check and pace refuse a timeline.js that no longer matches script.json
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
    if not clips:       # nothing spoken yet (a silent cut or a panel test): a silent bed of the right length
        inputs, graph = ["-f", "lavfi", "-i", f"anullsrc=r={sr}:cl=mono"], "[0:a]anull[out]"
    script.write_text(graph)
    run(["ffmpeg", "-v", "error", "-y", *inputs, *filter_file(script), "-map", "[out]",
         "-t", str(total), "-ar", str(sr), "-ac", "1", str(film / "out" / "narration.wav")])
    # YouTube chapters: first must be 0:00, each at least 10 s
    lines = []
    for k, ch in enumerate(chapters):
        s0 = 0 if k == 0 else int(ch["t0"])
        lines.append(f"{s0 // 60}:{s0 % 60:02d} {ch['title']}")
        if k and ch["t0"] - chapters[k - 1]["t0"] < 10 and fmt != "9x16":      # Shorts have no chapter list
            problems.append(f"chapter \"{chapters[k - 1]['title']}\" is under 10 s; YouTube will ignore the chapter list")
    (film / "out" / "chapters.txt").write_text("\n".join(lines) + "\n")
    if any("words" in s for s in segs):
        write_vtt(segs, film / "out" / "captions.vtt")
    print(f"{len(segs)} segments, {len(clips)} narration clips, {len(cues)} word cues, {total:.1f} s ({total / 60:.1f} min)")
    print("\n".join(lines))
    for p in problems:
        print("PROBLEM:", p)


# ── Shorts: a 9:16 film re-laid-out from a run of a long-form film's segments ──
SHORT_TAIL = 0.8        # seconds of silence after a Short's last line (the long-form tail would carry the next line)


def cmd_short(a):
    """A new 9:16 film folder from segments --from..--to of a long-form film. It reuses their script, voice clips,
    word times and (at `timeline`) a cut of the long-form mix, and gets its own layout, burned-in captions and review."""
    import copy
    film, dest = Path(a.film).resolve(), Path(a.dest).resolve()
    sc = load_script(film)
    ids = [x["id"] for x in sc["segments"]]
    if a.first not in ids or a.last not in ids or ids.index(a.first) > ids.index(a.last):
        raise SystemExit(f"--from {a.first} --to {a.last}: both must be segment ids of {film.name}, in film order")
    if (dest / "script.json").exists():
        raise SystemExit(f"{dest}/script.json exists; a Short's script is edited by hand, so it is never overwritten")
    segs = copy.deepcopy(sc["segments"][ids.index(a.first):ids.index(a.last) + 1])
    for d in ("narration", "out", "review", "sources"):
        (dest / d).mkdir(parents=True, exist_ok=True)
    shutil.copy(TEMPLATE, dest / "film.html")
    for f in ENGINE_FILES:
        shutil.copy(HERE / f, dest / f)
    if (film / "engine.json").exists():     # the Short moves like its long-form film
        shutil.copy2(film / "engine.json", dest / "engine.json")
    keep = {x["id"] for x in segs}
    for x in segs:
        clip = film / "narration" / f"{x['id']}.mp3"
        if clip.exists():
            shutil.copy2(clip, dest / "narration" / clip.name)
    wj = film / "narration" / "words.json"
    if wj.exists():
        words = {k: v for k, v in json.loads(wj.read_text()).items() if k in keep}
        (dest / "narration" / "words.json").write_text(json.dumps(words, indent=1, ensure_ascii=False) + "\n")
    vj = film / "narration" / "voice.json"           # the clips are the same files, so their voice record carries over
    if vj.exists():
        rec = {k: v for k, v in json.loads(vj.read_text()).items() if k in keep}
        (dest / "narration" / "voice.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False) + "\n")
    if (film / "sources").is_dir():
        shutil.copytree(film / "sources", dest / "sources", dirs_exist_ok=True)
    for x in segs:                          # panel stills and their upscaled copies, at the same relative paths
        for src in [p for img in panel_images(x.get("data")) for p in (film / img, hd_path(film, img))]:
            if src and src.exists():
                (dest / src.relative_to(film)).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest / src.relative_to(film))
    out = {k: sc[k] for k in ("palette", "theme", "grade", "moods", "ui_sounds", "type_scale", "voice", "voices", "say", "set3d", "style", "paper_grain", "finish", "caption_style") if k in sc}
    out.update(title=a.title or sc["title"], format="9x16", captions=True,
               short_of={"film": os.path.relpath(film, dest), "from": a.first, "to": a.last}, segments=segs)
    (dest / "script.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    print(f"{len(segs)} segments -> {dest}")
    tlp = film / "timeline.js"
    if tlp.exists():
        span = {x["id"]: x for x in load_timeline(film)["segments"]}
        secs = span[a.last]["t1"] - span[a.first]["t0"] + SHORT_TAIL
        print(f"length about {secs:.0f} s" + ("; over 3 minutes, YouTube won't treat it as a Short" if secs > 180 else ""))
    print("Now lay it out for 9:16 in its script.json: fewer things on screen at once, larger type, its own camera "
          "(panel focus and camera, board card x/y, stage cast x). Then timeline, stills (look at the sheet), render, final.")


def short_audio(film, short, segs, lead):
    """Cut the Short's sound from its long-form mix: lead-in, its segments, a fade, then SHORT_TAIL of silence.
    Returns problems; the cut is only right while every segment lasts exactly as long as it does in the long-form film."""
    parent = (film / short["film"]).resolve()
    problems = []
    if not (parent / "timeline.js").exists():
        return [f"long-form film {parent} has no timeline.js; run timeline there first"]
    ptl = load_timeline(parent)
    pseg = {x["id"]: x for x in ptl["segments"]}
    for x in segs:
        p = pseg.get(x["id"])
        if not p:
            problems.append(f"{x['id']} is not in the long-form film any more")
        elif abs((p["t1"] - p["t0"]) - (x["t1"] - x["t0"])) > 0.02:
            problems.append(f"{x['id']} lasts {x['t1'] - x['t0']:.2f} s here and {p['t1'] - p['t0']:.2f} s in the long-form "
                            "film; run timeline with the long-form film's --pad and --lead, or its sound won't line up")
    mix = parent / "out" / "mix.wav"
    if not mix.exists() or mix.stat().st_mtime < (parent / "timeline.js").stat().st_mtime:
        return problems + [f"{mix} is missing or older than its timeline; run mix on the long-form film, then timeline here again"]
    if problems or not segs:
        return problems
    p0, p1 = pseg[segs[0]["id"]]["t0"], pseg[segs[-1]["id"]]["t1"]
    start, delay = p0 - lead, 0.0
    if start < 0:
        start, delay = 0.0, -start
    span = p1 - start
    af = (f"atrim=start={start:.3f}:duration={span:.3f},asetpts=PTS-STARTPTS,afade=t=in:d=0.08,"
          f"afade=t=out:st={max(0.0, span - 0.4):.3f}:d=0.4,adelay={int(delay * 1000)}:all=1,apad=pad_dur={SHORT_TAIL}")
    run(["ffmpeg", "-v", "error", "-y", "-i", str(mix), "-af", af, "-ar", "48000", str(film / "out" / "mix.wav")])
    write_mix_stamp(film)
    used = parent / "out" / "sounds_used.json"
    if used.exists():
        shutil.copy2(used, film / "out" / "sounds_used.json")
    print(f"sound: {p0 - lead:.2f}-{p1:.2f} s of {parent.name}'s mix -> out/mix.wav (credits copied)")
    return problems


def write_vtt(segs, dest, max_words=9, max_chars=42):
    """A caption track from the aligned words (upload it to YouTube instead of burning captions into 16:9)."""
    cues = []
    for s in segs:
        page, mono = [], s.get("mono")
        for w in s.get("words", []):
            page.append(w)
            text = " ".join(x[0] for x in page)
            if w[0][-1:] in ".!?" or len(page) >= max_words or len(text) > max_chars:
                cues.append((page[0][1], page[-1][2], text, mono)); page = []
        if page:
            cues.append((page[0][1], page[-1][2], " ".join(x[0] for x in page), mono))
    vtt_out(cues, dest)


def vtt_ts(x):
    h, m, sec = int(x // 3600), int(x % 3600 // 60), x % 60
    return f"{h:02d}:{m:02d}:{sec:06.3f}"


def vtt_out(cues, dest):
    """cues: (t0, t1, text, mono). Each cue holds 0.3 s past its last word unless the next one starts; inner
    monologue is italic (<i>, which YouTube shows), and speakers carry no label, as no badge is burned in either."""
    out = ["WEBVTT", ""]
    for i, (a, b, text, mono) in enumerate(cues):
        b = min(b + 0.3, cues[i + 1][0]) if i + 1 < len(cues) else b + 0.3
        out += [f"{vtt_ts(a)} --> {vtt_ts(b)}", f"<i>{text}</i>" if mono else text, ""]
    Path(dest).write_text("\n".join(out))


def pages(text, max_chars=42):
    """Split a line into caption pages of at most max_chars: a page never runs past a sentence end unless the
    next sentence fits whole on it, and a long sentence wraps at spaces."""
    out = []
    for sent in re.findall(r"\S.*?(?:[.!?…]+[\"'»”)]*(?=\s|$)|$)", text):
        cur = out.pop() if out and len(out[-1]) + 1 + len(sent.strip()) <= max_chars and out[-1][-1:] in ".!?…»”\"')" else ""
        for w in sent.split():
            nxt = (cur + " " + w).strip()
            if cur and len(nxt) > max_chars:
                out.append(cur); cur = w
            else:
                cur = nxt
        if cur:
            out.append(cur)
    return out


def cmd_captions(a):
    """A caption track in another language from translated lines, timed to the English read: each segment's
    translation spans that segment's spoken words (first word to last), its pages sharing the time by length."""
    film = Path(a.film).resolve()
    tl = load_timeline(film)
    tr = json.loads(Path(a.text).read_text())
    spoken = [s for s in tl["segments"] if s.get("words")]
    missing = [s["id"] for s in spoken if not str(tr.get(s["id"], "")).strip()]
    if missing:
        raise SystemExit(f"{len(missing)} spoken segments have no {a.lang} line: {', '.join(missing[:12])}")
    extra = sorted(set(tr) - {s["id"] for s in spoken})
    cues = []
    for s in spoken:
        t0, t1 = s["words"][0][1], s["words"][-1][2]
        pg = pages(tr[s["id"]])
        total = sum(len(x) for x in pg)
        t = t0
        for x in pg:
            d = (t1 - t0) * len(x) / total
            cues.append((round(t, 3), round(t + d, 3), x, s.get("mono")))
            t += d
    dest = film / "out" / f"captions.{a.lang}.vtt"
    vtt_out(cues, dest)
    print(f"{len(cues)} cues -> {dest}")
    if extra:
        print(f"ignored {len(extra)} ids not spoken in this film: {', '.join(extra[:12])}")


def cmd_stills(a):
    import render
    film = Path(a.film).resolve()
    tl = load_timeline(film)
    if a.every:
        ts = [round(x, 2) for x in frange(1.0, tl["duration"], a.every)]
    else:
        ts = [round(s["t0"] + (s["t1"] - s["t0"]) * a.at, 2) for s in tl["segments"]]
    out = film / "review" / ("stills-every" if a.every else "stills-segments")
    try:
        out, n, _ = render.stills(film, ts, out)
    except render.PageError as e:
        raise SystemExit(f"stills stopped: {e}")
    print(f"{n} stills -> {out}")
    print("sheet ->", render.sheet(out, out.with_suffix(".png"), cols=6))


CAST_POSES = ("stand", "point", "present", "think", "shrug", "worried", "cheer", "happy")


def set3d_cast(segs):
    """Every distinct cast member of the film's set3d segments: [{id, look, back}] in order of first appearance."""
    seen, out = set(), []
    for sg in segs:
        if sg.get("type") != "set3d":
            continue
        for c in (sg.get("data") or {}).get("cast", []):
            key = json.dumps([c.get("id"), c.get("look"), bool(c.get("back"))], sort_keys=True)
            if key not in seen:
                seen.add(key); out.append({"id": c.get("id"), "look": c.get("look"), "back": bool(c.get("back"))})
    return out


def cast_stamp(segs, palette):
    return hashlib.sha256(json.dumps([set3d_cast(segs), palette], sort_keys=True).encode()).hexdigest()[:16]


CAST_PAGE = """<!doctype html><meta charset="utf-8"><body style="margin:0;background:#fff">
<canvas id="c"></canvas>
<script src="../kit.js"></script><script src="../figure.js"></script><script src="../three.min.js"></script><script src="../set3d.js"></script>
<script>
(() => {
const CAST = %s, POSES = %s, PAL = Object.assign({}, S3.PALETTE, %s), cw = 200, ch = 300, TOP = 64, left = 150;
const c = document.getElementById("c"), cols = POSES.length + 2;
c.width = left + cols * cw + 20; c.height = TOP + CAST.length * (ch + 24) + 20;
const x = c.getContext("2d"); x.fillStyle = PAL.paper; x.fillRect(0, 0, c.width, c.height);
x.fillStyle = PAL.ink; x.font = "600 22px sans-serif";
["facing left", "back"].concat(POSES.map(p => p + (p === "stand" ? " (right)" : ""))).forEach((h, i) => x.fillText(h, left + i * cw + 12, 40));
const cell = document.createElement("canvas"); cell.width = 512; cell.height = 768; const cx = cell.getContext("2d");
CAST.forEach((m, r) => {
  const L = typeof m.look === "object" ? Object.assign({}, S3.LOOKS.ada, m.look) : (S3.LOOKS[m.look] || Object.values(S3.LOOKS)[r %% 5]);
  const y = TOP + r * (ch + 24);
  x.fillStyle = PAL.ink; x.font = "700 26px sans-serif"; x.fillText(m.id || "?", 16, y + ch / 2);
  x.font = "18px sans-serif"; x.fillText(typeof m.look === "string" ? m.look : "custom", 16, y + ch / 2 + 28);
  if (m.back) x.fillText("(faceless)", 16, y + ch / 2 + 52);
  const draw = (i, pose, f, back) => { S3.drawCutout(cx, L, F.POSES[pose] || {}, f, { back, ink: PAL.ink, paper: PAL.paper });
    x.drawImage(cell, left + i * cw, y, cw, ch); };
  draw(0, "stand", -1, m.back); draw(1, "stand", 1, true);
  POSES.forEach((p, i) => draw(i + 2, p, 1, m.back));
});
window.DONE = true;
})();
</script>"""


def cmd_cast_sheet(a):
    """review/cast-sheet.png: every set3d cast member, facing each way, from the back and in the stock poses, so the
    designs are agreed before any scene is animated (a cutout film's design sheet comes first). Its stamp, in
    review/cast-sheet.json, is what render's set3d gate compares with the film's cast."""
    from playwright.sync_api import sync_playwright
    import render
    film = Path(a.film).resolve()
    sc = load_script(film)
    segs = sc.get("segments", [])
    cast = set3d_cast(segs) or [{"id": k, "look": k, "back": False} for k in ("ada", "bo", "cy", "dee", "eli")]
    pal = (sc.get("set3d") or {}).get("palette", {})
    page = film / "review" / "cast-sheet.html"
    page.parent.mkdir(exist_ok=True)
    page.write_text(CAST_PAGE % (json.dumps(cast), json.dumps(list(CAST_POSES)), json.dumps(pal)))
    kw = {"executable_path": os.environ["MS_CHROMIUM"]} if os.environ.get("MS_CHROMIUM") else {"channel": "chrome"}
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, args=["--allow-file-access-from-files", *render.RASTER], **kw)
        pg = b.new_page(); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(page.as_uri())
        try:
            pg.wait_for_function("window.DONE === true", timeout=20000)
        except Exception:
            raise SystemExit(f"cast-sheet: the sheet did not finish drawing: {errs[0] if errs else 'no page error'}")
        size = pg.evaluate("[document.getElementById('c').width, document.getElementById('c').height]")
        pg.set_viewport_size({"width": size[0], "height": size[1]})
        out = film / "review" / "cast-sheet.png"
        pg.locator("#c").screenshot(path=str(out))
        b.close()
    if errs:
        raise SystemExit(f"cast-sheet: page error: {errs[0]}")
    (film / "review" / "cast-sheet.json").write_text(json.dumps({"stamp": cast_stamp(segs, pal), "cast": cast}, indent=1))
    print(f"{len(cast)} cast -> {out}")


def frange(a, b, step):
    x = a
    while x < b:
        yield x
        x += step


def _render_chunk(args):
    """One chunk of frames to its own mp4. A page error raises render.PageError (never retried); a chunk stops early
    when another chunk's page error has written the stop file."""
    film, i0, i1, dest, scale = args
    import render
    stop = Path(dest).parent / "stop"
    tmp = Path(dest).with_suffix(".part.mp4")   # renamed when whole, so a finished chunk is one whose name exists
    with render.Film(Path(film), video=True) as f:
        vf = f"scale=trunc(iw*{scale}/2)*2:trunc(ih*{scale}/2)*2:out_color_matrix=bt709:out_range=tv,format=yuv420p"
        enc = lambda out, crf, preset: subprocess.Popen(
            ["ffmpeg", "-v", "error", "-y", "-f", "image2pipe", "-framerate", str(FPS), "-i", "-",
             "-vf", vf, "-r", str(FPS), "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
             "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
             str(out)], stdin=subprocess.PIPE)
        ff = enc(tmp, 18, "medium")
        # with a finish, the unfinished frames go to a proxy too: final's motion check reads it, since grain and mottle
        # would count as motion on every frame and hide a frozen shot
        proxy_dest, proxy_tmp = Path(dest).with_suffix(".raw.mp4"), Path(dest).with_suffix(".raw.part.mp4")
        px = enc(proxy_tmp, 23, "veryfast") if f.finish else None
        pipes = [p for p in (ff, px) if p]
        try:
            for i in range(i0, i1):
                if (i - i0) % 30 == 0 and stop.exists():
                    raise ChunkStopped(f"chunk {i0} stopped: another chunk hit a page error")
                png, raw = f.frame(i / FPS, FPS)
                ff.stdin.write(png)
                if px:
                    px.stdin.write(raw)
                if (i - i0) % 900 == 0:
                    print(f"  chunk {i0}: frame {i - i0}/{i1 - i0}", flush=True)
        except BaseException:
            for p in pipes:
                p.stdin.close(); p.wait()
            raise
        for p in pipes:
            p.stdin.close()
        if any(p.wait() != 0 for p in pipes):
            raise RuntimeError("ffmpeg failed")
        if px:
            proxy_tmp.rename(proxy_dest)
        tmp.rename(dest)
        return dest


HEAD = 0.6            # seconds at a segment's start kept as their own part: the frames the scene before can still draw on
                      # (its outgoing wipe, WIPE = 0.45 s in the template), so a partial render redoes them with it


def _frame(t):
    return math.ceil(t * FPS - 1e-6)


def part_ranges(tl, n):
    """Frame ranges the render is split into: cut at every segment's start and HEAD seconds after it, and no part longer
    than CHUNK, so a partial render redoes whole parts and nothing else."""
    cuts = {0, n}
    for sg in tl["segments"]:
        cuts.update(c for c in (_frame(sg["t0"]), _frame(sg["t0"] + HEAD)) if 0 < c < n)
    cuts, out = sorted(cuts), []
    for a, b in zip(cuts, cuts[1:]):
        out += [(k, min(b, k + CHUNK)) for k in range(a, b, CHUNK)]
    return out


def segment_pick(tl, only, seg_from, seg_to):
    """Segment ids named by --only (comma-separated) and/or --from/--to (inclusive), in film order. A map segment brings
    its whole run: the run shares one camera and its pieces can stay on across segments."""
    ids = [sg["id"] for sg in tl["segments"]]
    want = set(x.strip() for x in (only or "").split(",") if x.strip())
    if seg_from or seg_to:
        for x in (seg_from, seg_to):
            if x and x not in ids:
                raise SystemExit(f"render: no segment {x}")
        i0, i1 = ids.index(seg_from or ids[0]), ids.index(seg_to or ids[-1])
        want.update(ids[min(i0, i1):max(i0, i1) + 1])
    if not want:
        return []
    bad = sorted(want - set(ids))
    if bad:
        raise SystemExit(f"render: no segment {', '.join(bad)}")
    segs = tl["segments"]
    for i, sg in enumerate(segs):
        if sg["id"] in want and sg["type"] == "map":
            j = i
            while j > 0 and segs[j - 1]["type"] == "map":
                j -= 1
            while j < len(segs) and segs[j]["type"] == "map":
                want.add(segs[j]["id"]); j += 1
    return [i for i in ids if i in want]


def _code_and_files(film):
    """The page's own code by content, and every other file it can load by size and time (the stamp's rules)."""
    code, files = {}, {}
    for p in sorted(film.rglob("*")):
        rel = p.relative_to(film)
        if not p.is_file() or rel.parts[0] in STAMP_SKIP_DIRS or p.name.startswith(".") or p.suffix.lower() in STAMP_SKIP_TYPES \
                or p.name.endswith("~") or (len(rel.parts) == 1 and p.name in STAMP_SKIP_NAMES):
            continue
        if p.suffix in (".html", ".js", ".css", ".json") and len(rel.parts) == 1:
            if rel.name not in ("timeline.js", "script.json"):
                code[str(rel)] = hashlib.sha256(p.read_bytes()).hexdigest()
        else:
            st = p.stat(); files[str(rel)] = f"{st.st_size} {st.st_mtime_ns}"
    return code, files


def write_manifest(film, tl, parts, scale):
    """out/parts/manifest.json: what the kept parts were rendered from, which a partial render compares against."""
    code, files = _code_and_files(film)
    (parts / "manifest.json").write_text(json.dumps({"scale": scale, "fps": FPS, "timeline": tl, "code": code,
                                                      "files": files}) + "\n")


def partial_problems(film, tl, parts, only, scale, ranges):
    """The frame ranges to redo for --only, after making sure nothing outside the named segments can have changed: the
    same page code, the same timing, the same other segments and film settings, and every changed file used only by the
    named segments. Anything else stops with what differs, since a spliced film would mix two versions."""
    mf = parts / "manifest.json"
    if not mf.exists():
        raise SystemExit("render --only needs a finished render of this film to splice into (out/parts/manifest.json); "
                         "render the whole film once first")
    old = json.loads(mf.read_text())
    probs = []
    only = list(only)
    code, files = _code_and_files(film)
    ot = old["timeline"]
    # the map (maps/map.json in the timeline, its tiles and fonts, map_drift) is shared by every map segment and used by
    # nothing else: a change there redoes every map segment instead of refusing the splice (each map fix used to cost a
    # whole render, 2026-11-25)
    maps = [sg["id"] for sg in tl["segments"] if sg["type"] == "map"]
    map_keys = ("map", "map_drift")
    map_files = [k for k in sorted(set(files) | set(old["files"])) if files.get(k) != old["files"].get(k)
                 and k.split("/", 1)[0] in ("maps", "fonts")]
    if maps and (any(ot.get(k) != tl.get(k) for k in map_keys) or map_files):
        add = [m for m in maps if m not in only]
        if add:
            print(f"render --only: the map changed ({', '.join([k for k in map_keys if ot.get(k) != tl.get(k)] + map_files[:3])}), "
                  f"so every map segment is redone too: {', '.join(add)}")
            only += add
    else:
        map_keys, map_files = (), []
    if old.get("scale") != scale or old.get("fps") != FPS:
        probs.append(f"render settings changed (scale {old.get('scale')} -> {scale})")
    probs += [f"page code changed: {k}" for k in sorted(set(code) | set(old["code"])) if code.get(k) != old["code"].get(k)]
    if [(s["id"], round(s["t0"], 4), round(s["t1"], 4)) for s in ot["segments"]] != \
            [(s["id"], round(s["t0"], 4), round(s["t1"], 4)) for s in tl["segments"]] or ot["duration"] != tl["duration"]:
        probs.append("segment timing changed (a re-voice, a new segment or a moved cut)")
    else:
        for so, sn in zip(ot["segments"], tl["segments"]):
            if sn["id"] not in only and so != sn:
                probs.append(f"{sn['id']} changed but is not named")
            elif sn["id"] in only and {k: v for k, v in so.items() if k != "data"} != {k: v for k, v in sn.items() if k != "data"}:
                probs.append(f"{sn['id']} changed outside its data (a counter, tag or caption carries into later segments)")
    rest = lambda t: {k: v for k, v in t.items() if k != "segments" and k not in map_keys}
    if rest(ot) != rest(tl):
        probs.append("film-wide timeline settings changed (captions, chapters, cues, grade, map)")
    named = json.dumps([sg for sg in tl["segments"] if sg["id"] in only])
    for k in sorted(set(files) | set(old["files"])):
        if files.get(k) != old["files"].get(k) and k not in named and not (k.startswith(CLIP_DIR + "/") and k.rsplit("/", 1)[0] in named) and k not in map_files:
            probs.append(f"{k} changed and is not used by the named segments")
    missing = [Path(f"p{i0:07d}-{i1:07d}.mp4") for i0, i1 in ranges if not (parts / f"p{i0:07d}-{i1:07d}.mp4").exists()]
    if missing:
        probs.append(f"{len(missing)} part(s) of the last render are missing ({missing[0]} ...)")
    if probs:
        raise SystemExit("render --only stopped: the rest of the film may not match the last render, so a splice would "
                         "mix two versions. Render the whole film instead (or fix these):\n  " + "\n  ".join(probs[:12]))
    segs, dirty = tl["segments"], []
    for i, sg in enumerate(segs):
        if sg["id"] in only:      # the last segment owns the tail after its last word (a map holds to the end)
            dirty.append((_frame(sg["t0"]), _frame(tl["duration"] if i == len(segs) - 1 else sg["t1"])))
            if i + 1 < len(segs):         # the next scene's first moments carry this one's outgoing wipe
                dirty.append((_frame(segs[i + 1]["t0"]), _frame(segs[i + 1]["t0"] + HEAD)))
    return [(i0, i1) for i0, i1 in ranges if any(i0 < b and a < i1 for a, b in dirty)], [i for i in (sg["id"] for sg in segs) if i in only]


class ChunkStopped(RuntimeError):
    """A chunk that gave up because another chunk's page error stopped the render."""


CHUNK = 900            # frames per render chunk (30 s): a failure loses at most this much, and chunks outnumber workers
WORKER_GB = 1.5        # memory one render worker (a Chromium page plus its ffmpeg) may take; ~1 GB measured on a plain film
HEADROOM_GB = 2.0      # left free for the system while rendering


def available_gb():
    """Memory the system can hand out now, in GB (Linux MemAvailable; macOS free + inactive + speculative + purgeable
    pages), or None if it can't tell."""
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1e6
    except OSError:
        pass
    try:
        vm = subprocess.run(["vm_stat"], capture_output=True, text=True, check=True).stdout
        page = int(re.search(r"page size of (\d+)", vm).group(1))
        pages = sum(int(m.group(2)) for m in re.finditer(r"Pages (free|inactive|speculative|purgeable):\s+(\d+)", vm))
        return pages * page / 1e9
    except (OSError, subprocess.CalledProcessError, AttributeError):
        return None


def render_stamp(film, scale):
    """What the frames depend on: the page, its scripts and timeline (by content), every other file the page can load
    (by size and time; see _stamp for what is left out) and the render settings. Kept chunks are reused only while
    this is unchanged."""
    return _stamp(film, hashlib.sha256(f"{scale} {FPS} {CHUNK} {HEAD} segment-parts".encode()))


def film_stamp(film):
    """The film as the page sees it (render_stamp without the render settings): what a passing check is tied to."""
    return _stamp(film, hashlib.sha256(b"film"))


# Files that never reach a frame, left out of the film's stamp: the folders a run writes or mixes from (renders, review
# sheets, logs, voice and music clips) and file types the page never loads (logs, notes, sheets, editor and lock
# files). Everything else counts, so an image or script a kit adds in a subfolder still ties the check to its version.
# A kit's log appended after each step made every film "changed since check" (Longtok, episode 2, 2026-10-26).
STAMP_SKIP_DIRS = {"out", "review", "narration", "music", "logs"}
STAMP_SKIP_TYPES = {".log", ".md", ".txt", ".tsv", ".csv", ".bak", ".tmp", ".swp", ".pid", ".lock"}
STAMP_SKIP_NAMES = {"notes.json"}      # the owner's review notes never reach a frame, so a new note doesn't make check stale


def _stamp(film, h):
    for p in sorted(film.rglob("*")):
        rel = p.relative_to(film)
        if not p.is_file() or rel.parts[0] in STAMP_SKIP_DIRS or p.name.startswith(".") or p.suffix.lower() in STAMP_SKIP_TYPES \
                or p.name.endswith("~") or (len(rel.parts) == 1 and p.name in STAMP_SKIP_NAMES):
            continue
        h.update(str(rel).encode())
        if p.suffix in (".html", ".js", ".css", ".json") and len(rel.parts) == 1:
            h.update(p.read_bytes())
        else:
            st = p.stat(); h.update(f"{st.st_size} {st.st_mtime_ns}".encode())
    return h.hexdigest()


class JobStatus:
    """out/<job>.status.json for a long run, so a detached job's state can be read without a live session: state
    (running, done, failed), when it started and last changed, progress, the last error, and anything noted (a skipped
    gate). The run ends with one line, DONE or FAILED, naming the job and the film. Use as a context manager."""

    def __init__(self, film, job):
        self.path, self.job, self.film = film / "out" / f"{job}.status.json", job, film
        self.d = {"job": job, "film": str(film), "state": "running", "pid": os.getpid(), "started": self._now(),
                  "progress": "", "last_error": "", "notes": []}

    @staticmethod
    def _now():
        import datetime
        return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    def update(self, **kw):
        self.d.update(kw, updated=self._now())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.d, indent=1) + "\n")
        tmp.replace(self.path)

    def note(self, text):
        self.update(notes=self.d["notes"] + [text])

    def __enter__(self):
        self.update()
        return self

    def __exit__(self, kind, err, tb):
        if kind is None or (kind is SystemExit and err.code in (None, 0)):
            self.update(state="done")
            print(f"DONE {self.job} {self.film.name}", flush=True)
            return False
        msg = "interrupted" if kind is KeyboardInterrupt else str(err) if kind is SystemExit else f"{kind.__name__}: {err}"
        self.update(state="failed", last_error=msg)
        print(f"FAILED {self.job} {self.film.name}: {msg.splitlines()[0] if msg else kind.__name__}", flush=True)
        return False


def page_gate(film, what):
    """Stop `what` if the page throws anywhere (render.page_problems); returns the problems for check's report."""
    import render
    found = render.page_problems(film)
    lines = [f"  {'load' if t is None else f'{t:.2f} s'}{f' ({seg})' if seg else ''}: {m}" for t, seg, m in found]
    if found and what:
        raise SystemExit(f"{what} stopped: the page is not clean ({len(found)} problem(s)):\n" + "\n".join(lines))
    return lines


def render_workers(tl, platform):
    """Render workers when --workers isn't given: 4, but 1 for a film with set3d scenes on a machine whose Chrome draws
    WebGL on the GPU (a Mac). On an M2 Pro a set3d film took 0.16 s a frame with one worker and about 11x longer in
    wall time with four, the pages fighting over the GPU (2026-11-25). Linux (the cloud's software WebGL, on the CPU)
    keeps 4."""
    has3d = any(sg.get("type") == "set3d" for sg in tl.get("segments", []))
    return 1 if has3d and platform == "darwin" else 4


def cmd_render(a):
    film = Path(a.film).resolve()
    with JobStatus(film, "render") as st:
        _render(a, film, st)


def _render(a, film, st):
    tl = load_timeline(film)
    gate_real_assets(film, tl["segments"], "render")
    gate_scale(film, load_script(film), "render")
    drafts = draft_stills(film, tl["segments"])
    if drafts:
        raise SystemExit("render stopped: draft stills can't ship:\n" + "\n".join(drafts))
    # estimated word times move every cue keyed to a word, so a film isn't rendered on them
    spread_ = alignment_flags(film, load_script(film))[1]
    if spread_ and not getattr(a, "allow_spread", False):
        raise SystemExit("render stopped: " + spread_[0] + " (or --allow-spread for a rough draft)")
    # a render is only started on a version of the film that a check passed (its page ran clean), and on a page that
    # is clean now: a JavaScript error never heals on a retry, and a render is the most expensive place to find one
    passed = film / "review" / "check-gate.json"
    rec = json.loads(passed.read_text()) if passed.exists() else {}
    if rec.get("stamp") != film_stamp(film) or not rec.get("page_clean"):
        why = ("no check has been run" if not rec else "the last check found page errors" if not rec.get("page_clean")
               else "the film changed since the last check")
        if not a.skip_gate:
            raise SystemExit(f"render stopped: {why}. Run `check` on this version of the film first (or --skip-gate)")
        print(f"render: --skip-gate: {why}; rendering anyway", flush=True)
        st.note(f"gate skipped: {why}")
    page_gate(film, "render")
    has3d = any(sg.get("type") == "set3d" for sg in tl["segments"])
    if tl.get("format") == "9x16" or has3d:   # a Short is a new layout, and a 3D set's camera can frame anything: look at a contact sheet before spending a render
        made = (film / "timeline.js").stat().st_mtime
        sheets = [p for p in (film / "review").glob("stills-*.png") if p.stat().st_mtime >= made]
        if not sheets:
            raise SystemExit(f"render stopped: a {'9:16 film' if not has3d else 'film with set3d scenes'} needs its own contact "
                             "sheet first. Run `stills` (set3d: `stills --every 1`) and look at review/stills-segments.png (or "
                             "stills-every.png); it must be newer than timeline.js")
    if has3d:                                 # the cutout designs are agreed on their own sheet before anything is animated
        rec = film / "review" / "cast-sheet.json"
        sc = load_script(film)
        if not rec.exists() or json.loads(rec.read_text()).get("stamp") != cast_stamp(sc.get("segments", []), (sc.get("set3d") or {}).get("palette", {})):
            raise SystemExit("render stopped: the set3d cast changed since its design sheet (or has none). Run "
                             f"`longform.py cast-sheet {film}` and look at review/cast-sheet.png")
    n = math.ceil(tl["duration"] * FPS)
    # finished chunks survive a failed or interrupted render and are reused while the film is unchanged (render_stamp);
    # --fresh, or any change to the page, timeline, assets or settings, starts over, unless --only names what changed
    parts, stamp = film / "out" / "parts", render_stamp(film, a.scale)
    ranges = part_ranges(tl, n)
    jobs = [(str(film), i0, i1, str(parts / f"p{i0:07d}-{i1:07d}.mp4"), a.scale) for i0, i1 in ranges]
    only = segment_pick(tl, a.only, a.seg_from, a.seg_to)
    if only:
        dirty, only = partial_problems(film, tl, parts, only, a.scale, ranges)
        print(f"render --only: {', '.join(only)}; re-rendering {sum(i1 - i0 for i0, i1 in dirty)} of {n} frames "
              f"and keeping the rest from the last render")
        st.note(f"partial render: {', '.join(only)}; other frames kept from the previous render")
        for i0, i1 in dirty:
            (parts / f"p{i0:07d}-{i1:07d}.mp4").unlink(missing_ok=True)
    elif a.fresh or not (parts / "stamp.txt").exists() or (parts / "stamp.txt").read_text() != stamp:
        shutil.rmtree(parts, ignore_errors=True)
    parts.mkdir(parents=True, exist_ok=True)
    (parts / "stamp.txt").write_text(stamp)
    for stale in parts.glob("*.part.mp4"):
        stale.unlink()
    todo = [j for j in jobs if not Path(j[3]).exists()]
    if len(todo) < len(jobs) and not only:
        print(f"resuming: {len(jobs) - len(todo)} of {len(jobs)} chunks already rendered")
    # each worker is a browser page: more than memory allows and the system starts killing them mid-chunk
    asked = a.workers if a.workers is not None else render_workers(tl, sys.platform)
    workers, gb = asked, available_gb()
    if gb is not None:
        workers = max(1, min(workers, int((gb - HEADROOM_GB) / WORKER_GB)))
        if workers < asked:
            print(f"workers: {workers} (asked for {asked}; {gb:.1f} GB free at {WORKER_GB} GB each, {HEADROOM_GB} GB kept free)")
    import render
    stop = parts / "stop"
    stop.unlink(missing_ok=True)
    done = len(jobs) - len(todo)
    st.update(progress=f"{done}/{len(jobs)} chunks")
    for attempt in range(1 + a.retries):
        if not todo:
            break
        failed, page_err = [], None
        with ProcessPoolExecutor(min(workers, len(todo))) as ex:
            futures = {ex.submit(_render_chunk, j): j for j in todo}
            for fu in as_completed(futures):
                j = futures[fu]
                try:
                    fu.result()
                    done += 1
                    st.update(progress=f"{done}/{len(jobs)} chunks")
                except render.PageError as e:              # the film's own code threw: stop everything, no retry
                    if not page_err:
                        page_err = e
                        stop.write_text(str(e))
                        for other in futures:
                            other.cancel()
                        st.update(last_error=str(e))
                except (ChunkStopped, CancelledError):
                    pass
                except Exception as e:                      # a crashed page or worker (TargetClosedError, a broken pool, a timeout)
                    failed.append(j)
                    st.update(last_error=f"chunk {j[1]}-{j[2]}: {type(e).__name__}: {str(e)[:200]}")
                    print(f"  chunk {j[1]}-{j[2]} failed: {type(e).__name__}: {str(e)[:120]}", flush=True)
        if page_err:
            stop.unlink(missing_ok=True)
            raise SystemExit(f"render stopped: {page_err}. A page error is the film's own code and fails the same way "
                             f"every time, so it is not retried. Fix it and run check (the fix changes the page, so render starts over)")
        todo = failed
        if todo and attempt < a.retries:
            workers = max(1, workers // 2)                  # fewer pages at once on the retry, in case memory was the cause
            print(f"retrying {len(todo)} chunk(s) with {workers} worker(s)")
    if todo:
        raise SystemExit(f"render stopped: {len(todo)} chunk(s) still failing after {1 + a.retries} tries; the "
                         f"{len(jobs) - len(todo)} finished ones are kept, so running render again picks up from here")
    lst = parts / "list.txt"
    lst.write_text("".join(f"file '{Path(j[3]).name}'\n" for j in jobs))
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(film / "out" / "silent.mp4")])
    raw = film / "out" / "silent-raw.mp4"
    raw.unlink(missing_ok=True)
    if tl.get("finish"):                      # the unfinished picture, for final's motion check (see _render_chunk)
        rl = parts / "list-raw.txt"
        miss = [j for j in jobs if not Path(j[3]).with_suffix(".raw.mp4").exists()]
        if miss:
            raise SystemExit(f"render stopped: {len(miss)} chunk(s) have no unfinished copy for the motion check; run render --fresh")
        rl.write_text("".join(f"file '{Path(j[3]).with_suffix('.raw.mp4').name}'\n" for j in jobs))
        run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(rl), "-c", "copy", str(raw)])
    write_manifest(film, tl, parts, a.scale)
    print(f"{n} frames -> {film / 'out' / 'silent.mp4'}")


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


def plan_problems(tl, plan):
    """Ways a music plan no longer matches the film: chapters added, dropped or renamed, or the chapters retimed."""
    out, chs, runs = [], tl["chapters"], plan.get("runs") or []
    have, want = [c for r in runs for c in r.get("chapters", [])], [c["title"] for c in chs]
    if have != want:
        out.append(f"it covers chapters {have}, the film has {want}")
    if runs and chs and abs(runs[-1]["t1"] - chs[-1]["t1"]) > 0.5:
        out.append(f"its music ends at {runs[-1]['t1']:.1f} s, the last chapter at {chs[-1]['t1']:.1f} s")
    return out


def build_bed(film, plan, total, sr=48000, xfade=3.0):
    """The music bed from a plan: each run plays its track from the top (looping on a crossfade if the run is longer);
    the last run reaches the film's end (past its last chapter, under the tail or an end screen) and is back-timed so the
    track's own ending lands there."""
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
        a1 = min(total, r["t1"] + xfade / 2) if k < len(runs) - 1 else total
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
    # the bed must still be playing at the end: a plan made before the film grew, or a track that dies early, leaves the
    # ending (often the end screen) bare, which the owner heard as a flat outro
    # ("silent" = 30 dB under the bed's typical level; a track's own ending may decay over its last 3 s)
    hop = int(0.05 * sr)
    rms = np.sqrt((bed[:len(bed) // hop * hop] ** 2).mean(axis=1).reshape(-1, hop).mean(axis=1))
    loud = np.nonzero(rms > np.median(rms[rms > 1e-5]) * 10 ** (-30 / 20))[0] if (rms > 1e-5).any() else []
    last_heard = (loud[-1] + 1) * hop / sr if len(loud) else 0.0
    if total - last_heard > 3.0:
        raise SystemExit(f"music bed goes quiet at {last_heard:.1f} s, {total - last_heard:.1f} s before the film ends "
                         f"({runs[-1].get('title') or runs[-1]['track']} is the last track): pick a longer track for the last run "
                         f"in out/music_plan.json, or run `music` again")
    import wave
    dest = film / "out" / "bed.wav"
    pcm = (np.clip(bed, -1, 1) * 32767).astype("<i2")
    with wave.open(str(dest), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr); w.writeframes(pcm.tobytes())
    return dest


SFX_GAP = {"buzz": 0.6, "ping": 0.35, "pin": 0.12, "thud": 0.5, "tick": 0.08}    # same-kind sounds closer than this are dropped
SFX_MAX = {"buzz": 0.9, "ping": 1.2, "pin": 0.4, "thud": 0.8, "whoosh": 1.0,    # seconds a cue may ring; longer clips are cut
           "gavel": 3.0, "footsteps-marble": 3.0, "monitor-beep": 3.0, "low-hit": 2.5, "glass-break": 2.0, "door": 2.0,
           "pen-roll": 1.5, "paper-slide": 1.5, "pen-scratch": 1.5, "keys-lock": 1.5, "cutlery": 1.5, "dripping-water": 1.5,
           "seal-stamp": 1.0, "paper-slap": 0.8, "slap": 0.8}
BED_GAIN = 0.3        # an ambience bed sits about 10 dB under the foley hits (the whole sfx bus is then --sfx-db)
BED_FADE = 0.8        # seconds a bed fades in and out at the edges of its run of segments


def kind_key(k):
    """One spelling per kind: "paper slap", "Paper_Slap" and "paper-slap" are the same role."""
    return "-".join(str(k).lower().replace("_", " ").split())


def trim_clip(x, kind, sr=48000, fade=0.06):
    """Cut a recording to the time its kind may ring (a 3 s vibration loop is one buzz, not three), fading the cut."""
    import numpy as np
    n = int(SFX_MAX.get(kind, 1.5) * sr)
    if len(x) <= n:
        return x
    x = x[:n].copy()
    f = int(fade * sr)
    x[-f:] *= np.linspace(1, 0, f)
    return x


def shape_clip(x, gain=1.0, lowpass=None, sr=48000):
    """A film's own take on a kind: scale it and roll off its top (a buzz under a voice wants to be felt, not heard)."""
    if lowpass:
        from scipy.signal import butter, sosfilt
        x = sosfilt(butter(4, float(lowpass) / (sr / 2), output="sos"), x)
    return x * float(gain)


def recorded_sfx(kind, trim=True):
    """Library clips catalogued with "role": <kind> (studio.py role <id> <kind>); trim=False keeps a bed's full length."""
    import audio
    try:
        lib, cat = audio.library_dir(), audio.catalog()
    except SystemExit:
        return []
    kind = kind_key(kind)
    return [(k, trim_clip(audio.load(lib / v["file"]), kind) if trim else audio.load(lib / v["file"]))
            for k, v in sorted(cat["sfx"].items()) if v.get("role") and kind_key(v["role"]) == kind and (lib / v["file"]).exists()]


def bed_runs(segs):
    """[(kind, t0, t1)]: a bed over each run of back-to-back segments that ask for it, so it doesn't restart at a cut."""
    runs, open_ = [], {}
    for sg in segs:
        here = set(sg.get("bed") or [])
        for k in list(open_):
            if k not in here:
                runs.append((k, *open_.pop(k)))
        for k in here:
            if k in open_:
                open_[k][1] = sg["t1"]
            else:
                open_[k] = [sg["t0"], sg["t1"]]
    return sorted(runs + [(k, a, b) for k, (a, b) in open_.items()], key=lambda r: r[1])


def lay_bed(x, n, sr=48000, xfade=1.0):
    """Loop a clip to n samples with a crossfade at each seam, and fade the ends of the run."""
    import numpy as np
    f = min(int(xfade * sr), len(x) // 3)
    out = x[:n].copy() if len(x) >= n else None
    if out is None:
        out = x.copy()
        ramp = np.linspace(0, 1, f)
        while len(out) < n:
            out[-f:] = out[-f:] * (1 - ramp) + x[:f] * ramp
            out = np.concatenate([out, x[f:]])
        out = out[:n]
    e = min(int(BED_FADE * sr), n // 2)
    out[:e] *= np.linspace(0, 1, e)
    out[n - e:] *= np.linspace(1, 0, e)
    return out


def build_sfx(film, tl, sr=48000, drawn_ok=False):
    """Small UI sounds under the picture, placed from the timeline: a buzz when a phone lights, a soft ping when a
    message lands, a paper tick when a card is pinned, a thud when a stamp hits. Each kind uses recorded clips from
    the library when any carry that role (cycled, gain varied, transient on the cue, as sound.md asks). A kind with no
    recording is left silent, or drawn in code with drawn_ok (a placeholder: the channel owner found them amateur)."""
    import numpy as np
    import wave

    def tt(d):
        return np.arange(int(d * sr)) / sr

    def thump(d, f0, f1, decay):
        t = tt(d)
        f = f1 + (f0 - f1) * np.exp(-t * 25)
        return np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-t * decay) * np.minimum(1, t / 0.002)

    rng = np.random.default_rng(3)

    def noise(d, lo, hi, decay):
        from scipy.signal import butter, sosfilt
        t = tt(d)
        return sosfilt(butter(2, [lo / (sr / 2), hi / (sr / 2)], "band", output="sos"), rng.standard_normal(len(t))) * np.exp(-t * decay)

    def buzz():
        t = tt(0.42)
        on = ((t % 0.21) < 0.13).astype(float)
        v = np.sign(np.sin(2 * np.pi * 165 * t)) * 0.5 + np.sin(2 * np.pi * 330 * t) * 0.3
        from scipy.signal import butter, sosfilt
        return sosfilt(butter(2, 900 / (sr / 2), output="sos"), v * on) * 0.5

    def ping():
        t = tt(0.5)
        return (np.sin(2 * np.pi * 1318.5 * t) * np.exp(-t * 9) + 0.6 * np.sin(2 * np.pi * 1975.5 * t) * np.exp(-t * 12) * (t > 0.07)) * np.minimum(1, t / 0.003) * 0.35

    def pin():
        return np.concatenate([noise(0.05, 2000, 6000, 80) * 0.5 + thump(0.05, 180, 120, 60)[:int(0.05 * sr)] * 0.4, np.zeros(10)])

    def thud():
        x = thump(0.4, 90, 45, 9) * 0.9
        x[:int(0.08 * sr)] += noise(0.08, 200, 2500, 40) * 0.4
        return x

    drawn = {"buzz": buzz, "ping": ping, "pin": pin, "thud": thud}
    kinds = {k: recorded_sfx(k) or ([(f"drawn-{k}", f())] if drawn_ok else []) for k, f in drawn.items()}
    for sg in tl["segments"]:           # foley a segment asks for by name: recorded clips only, never drawn
        for x in sg.get("sfx", []):
            kinds.setdefault(kind_key(x["kind"]), recorded_sfx(x["kind"]))
    if any(sg["type"] == "map" for sg in tl["segments"]):    # the map scene's sounds: recordings only, like named foley
        for k in ("marker", "typewriter", "whoosh"):
            kinds.setdefault(k, recorded_sfx(k))
    if any(isinstance(sg.get("counter"), dict) for sg in tl["segments"]):
        kinds.setdefault("tick", recorded_sfx("tick"))
    # script.json "ui_sounds": {"ping": false, "buzz": {"gain": 0.2, "lowpass": 2500}} sets a film's default per kind
    tuned = load_script(film).get("ui_sounds") or {}
    unknown = [k for k in tuned if kind_key(k) not in kinds]
    if unknown:                         # a misspelt kind would otherwise leave the sound playing with no word said
        print(f"  ui_sounds: no such sound kind {', '.join(map(repr, unknown))} (kinds here: {', '.join(sorted(kinds))})")
    off = {kind_key(k) for k, v in tuned.items() if v is False and kind_key(k) in kinds}
    for k, v in tuned.items():
        k = kind_key(k)
        if isinstance(v, dict) and kinds.get(k):
            kinds[k] = [(c, shape_clip(x, v.get("gain", 1.0), v.get("lowpass"), sr)) for c, x in kinds[k]]
    for k in off:
        kinds[k] = []
    missing = [k for k, c in kinds.items() if not c and k not in off]
    events = []
    map_seen = False
    for sg in tl["segments"]:
        d = sg.get("data") or {}
        if sg["type"] == "stage":
            for b in d.get("beats", []):
                if b.get("t") is None:
                    continue
                if b.get("glow"):
                    events.append((b["t"], "buzz"))
                if b.get("notify") or b.get("sms"):
                    events.append((b["t"] + (0.12 if b.get("glow") else 0.0), "ping"))
        elif sg["type"] == "board":
            events += [(c["t"] - 0.05, "pin") for c in d.get("cards", []) if c.get("t") is not None]
            if d.get("stamp", {}).get("t") is not None:
                events.append((d["stamp"]["t"] - 0.05 + 0.12, "thud"))
        elif sg["type"] == "sms":
            events.append(((d.get("t") or sg["t0"] + 0.55) - 0.2, "ping"))
        elif sg["type"] == "map":       # references/maps.md: a paper tick per piece (and per cut-out letter), marker per ink mark,
            for x in d.get("pieces", []):       # typewriter under a typed title, a whoosh on each camera move
                t = x.get("t", sg["t0"] + 0.25) - 0.05
                if x.get("kind") in ("ransom", "sheet"):
                    n = len(re.sub(r"\s", "", x.get("word" if x.get("kind") == "sheet" else "text", "")))
                    start = t + (10 / 30 if x.get("kind") == "sheet" else 0)
                    events += [(start + i * 3.5 / 30, "pin") for i in range(n)]
                if x.get("kind") != "ransom":
                    events.append((t, "pin"))
                if x.get("kind") == "title":
                    events.append((t + 4 / 30, "typewriter"))
            events += [(x.get("t", sg["t0"] + 0.3) - 0.05, "marker") for x in d.get("ink", [])]
            cams = d.get("camera") or []
            for k, c in enumerate(cams if isinstance(cams, list) else [cams]):
                if map_seen or k:
                    events.append((c.get("t", sg["t0"]) - 0.05, "whoosh"))
            map_seen = True
        if sg["type"] != "map":
            map_seen = False
        events += [(x.get("t", sg["t0"] + 0.1), kind_key(x["kind"])) for x in sg.get("sfx", [])]
        if isinstance(sg.get("counter"), dict):      # the HUD counter ticks as it counts to a new value (recordings only)
            t = sg["counter"].get("t", sg["t0"]) - 0.05
            events += [(t + k * 0.1, "tick") for k in range(6)]
    skipped = sum(1 for _, k in events if k in missing)
    muted = sum(1 for _, k in events if k in off)
    events = sorted(e for e in events if e[1] not in missing and e[1] not in off)
    kept, last = [], {}
    for t, k in events:
        if t - last.get(k, -9) >= SFX_GAP.get(k, 0.3):
            kept.append((t, k)); last[k] = t
    dropped, events = len(events) - len(kept), kept
    total = tl["duration"]
    out = np.zeros(int(total * sr) + sr)
    used = {k: 0 for k in kinds}
    for t, k in events:
        _, x = kinds[k][used[k] % len(kinds[k])]
        if kinds[k][0][0].startswith("drawn"):                 # drawn sounds start on their cue
            gain, peak = 1.0, 0.0
        else:                                                   # recordings: vary the level, land the transient on the cue
            gain, peak = rng.uniform(0.85, 1.15), np.argmax(np.abs(x[:int(0.6 * sr)])) / sr
        i = int((t - peak) * sr)
        if i < 0:
            x, i = x[-i:], 0
        n = min(len(x), len(out) - i)
        out[i:i + n] += gain * x[:n]
        used[k] += 1
    beds, bed_used, bed_silent = {}, {}, set()
    for k, a, b in bed_runs(tl["segments"]):
        clips = beds.setdefault(k, recorded_sfx(k, trim=False))
        if not clips:
            bed_silent.add(k); continue
        done = bed_used.setdefault(k, [])
        key, x = clips[len(done) % len(clips)]
        i, n = int(a * sr), int((b - a) * sr)
        n = min(n, len(out) - i)
        out[i:i + n] += BED_GAIN * lay_bed(x, n, sr)
        bed_used[k].append(key)
        print(f"  bed {k}: {key} under {a:.1f}-{b:.1f} s")
    if bed_silent:
        print(f"  beds silent: no library clip has role {', '.join(repr(k) for k in sorted(bed_silent))}")
    out = np.stack([out, out], 1)[:int(total * sr)]
    dest = film / "out" / "sfx.wav"
    with wave.open(str(dest), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((np.clip(out, -1, 1) * 32767).astype("<i2").tobytes())
    (film / "out").mkdir(exist_ok=True)
    (film / "out" / "sfx_events.json").write_text(json.dumps([[round(t, 3), k] for t, k in events]))     # check's pace profile
    print(f"sfx: {len(events)} sounds ({', '.join(f'{k} {used[k]}' for k in kinds)})" + (f", {dropped} too close to the last one dropped" if dropped else ""))
    if muted:
        print(f"  {muted} cues off by script.json ui_sounds: {', '.join(sorted(off))}")
    if skipped:
        print(f"  {skipped} cues silent: no library clip has role {', '.join(repr(k) for k in missing)} (sound.md, long-form UI sounds)")
    for k, clips in kinds.items():
        if used[k]:
            print(f"  {k}: " + (", ".join(c for c, _ in clips) if not clips[0][0].startswith("drawn")
                               else "drawn in code (placeholder)"))
    sounds = {k: [c for c, _ in kinds[k][:used[k]]] for k in kinds if used[k] and not kinds[k][0][0].startswith("drawn")}
    for k, keys in bed_used.items():
        sounds.setdefault(k, []).extend(c for c in dict.fromkeys(keys) if c not in sounds.get(k, []))
    return dest, sounds


def library_whoosh():
    """The first library clip catalogued with "role": "whoosh", as (key, path), or None."""
    import audio
    try:
        lib, cat = audio.library_dir(), audio.catalog()
    except SystemExit:
        return None
    hits = [(k, lib / v["file"]) for k, v in sorted(cat["sfx"].items()) if v.get("role") == "whoosh" and (lib / v["file"]).exists()]
    return hits[0] if hits else None


# How far the ducked music bed sits under the voice, in LU (integrated loudness of the voice stem minus the ducked bed
# stem, both measured by ebur128 before the final mix). A Short's bed sits closer than a long film's: a phone in the
# hand, a shorter piece (the owner's picks, 2026-11-08, after a bed 28 LU under the voice couldn't be heard).
# profile: (target, note below, note above)
BED_PROFILES = {"short": (16.0, 14.0, 18.0), "long": (18.0, 16.0, 20.0)}
BED_GAP_MAX = 22.0     # further under the voice than this, the bed can't be heard on a phone at a comfortable level
BED_GAP_MIN = 10.0     # closer than this, it fights the words
BED_BAND_MIN = 10.0    # in 1-4 kHz, where consonants carry, the bed must stay at least this far under the voice
BED_START_DB = -14.7   # first guess when the gap sets the level; a library bed lands near 18 LU here


def lufs(wav, pre=""):
    """Integrated loudness (LUFS) of a file by ebur128, after an optional filter chain; None when it's silent."""
    af = (pre + "," if pre else "") + "ebur128"
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(wav), "-af", af, "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    tail = err[err.rfind("Summary:"):]
    try:
        v = float(tail.split("I:")[1].split("LUFS")[0])
    except (IndexError, ValueError):
        return None
    return None if v < -69 else v


BAND = "highpass=f=1000:poles=2,lowpass=f=4000:poles=2"     # 12 dB/octave each side


def bed_gaps(voice, bed):
    """(full-band gap, 1-4 kHz gap) in LU between a voice stem and a ducked bed stem. The full gap is None when either
    stem is silent. The band gap is None when the voice has nothing there to mask, and inf when the bed has nothing
    there (it can't mask what it doesn't play)."""
    v, b, vb, bb = lufs(voice), lufs(bed), lufs(voice, BAND), lufs(bed, BAND)
    gap = None if v is None or b is None else round(v - b, 2)
    band = None if vb is None else float("inf") if bb is None else round(vb - bb, 2)
    return gap, band


def bed_verdict(gap, band, profile):
    """(problems, notes) for a measured gap: problems stop the mix, notes are printed."""
    target, lo, hi = BED_PROFILES[profile]
    probs, notes = [], []
    if gap is None:
        probs.append("the bed or the voice measured silent, so the gap couldn't be measured")
        return probs, notes
    if gap > BED_GAP_MAX:
        probs.append(f"the bed sits {gap:.1f} LU under the voice, over {BED_GAP_MAX:.0f}: it can't be heard. Raise it "
                     f"(--bed-gap {target:.0f} sets it for you)")
    elif gap < BED_GAP_MIN:
        probs.append(f"the bed sits only {gap:.1f} LU under the voice, under {BED_GAP_MIN:.0f}: it fights the words. Lower it")
    elif not lo <= gap <= hi:
        notes.append(f"the bed sits {gap:.1f} LU under the voice, outside the {profile} range {lo:.0f}-{hi:.0f} (target {target:.0f})")
    if band is not None and band < BED_BAND_MIN:
        probs.append(f"in 1-4 kHz the bed comes within {band:.1f} LU of the voice (needs {BED_BAND_MIN:.0f}): it masks consonants. "
                     "Dip the bed there (--bed-dip 3 cuts 3 dB at 2.5 kHz) rather than lowering the whole bed")
    return probs, notes


def cmd_mix(a):
    """Narration on top; the music bed ducked under the voice; an optional whoosh at each chapter start. With a bed, the
    gap between voice and ducked bed is measured, and a bed too quiet, too loud or crowding the words stops the mix."""
    film = Path(a.film).resolve()
    if load_script(film).get("short_of") and not a.remix:
        raise SystemExit("this is a Short: `timeline` already cut its sound from the long-form mix (out/mix.wav). "
                         "Pass --remix to build a separate mix for it instead")
    tl = require_fresh_timeline(film, "mix")
    total = tl["duration"]
    narr = film / "out" / "narration.wav"
    sounds = {}                                          # what the mix used, for `credits`
    inputs = ["-i", str(narr)]
    dip = f",equalizer=f=2500:t=q:w=1:g={-abs(a.bed_dip)}" if a.bed_dip else ""   # a presence dip where consonants carry
    filt = []
    if a.plan:
        plan = json.loads((film / "out" / "music_plan.json").read_text())
        stale = plan_problems(tl, plan)
        if stale:
            raise SystemExit("out/music_plan.json is out of date with the timeline: " + "; ".join(stale) +
                             ". Run `music` again (redo any track you picked by hand in the plan)")
        sounds["music"] = list(dict.fromkeys(r["track"] for r in plan["runs"]))
        bed = build_bed(film, plan, total)
        inputs += ["-i", str(bed)]
        filt.append("[1:a]aformat=sample_rates=48000:channel_layouts=stereo,volume=@BED_DB@dB" + dip + ",afade=t=in:d=1.0[bed]")
        filt.append("[bed][key]sidechaincompress=threshold=0.02:ratio=6:attack=40:release=600,asplit=2[ducked][bstem]")
        mix_in = ["[voice]", "[ducked]"]
    elif a.music:
        tracks = [Path(p) for p in a.music.split(",")]
        sounds["music"] = [str(t) for t in tracks]
        seq, k = [], 0
        while sum(duration(t) for t in seq) < total + 5:
            seq.append(tracks[k % len(tracks)]); k += 1
        for t in seq:
            inputs += ["-i", str(t)]
        filt.append("[1:a]aformat=sample_rates=48000:channel_layouts=stereo[m0]")
        for i in range(1, len(seq)):
            filt.append(f"[{i + 1}:a]aformat=sample_rates=48000:channel_layouts=stereo[r{i}]")
            filt.append(f"[m{i - 1}][r{i}]acrossfade=d=4:c1=tri:c2=tri[m{i}]")
        filt.append(f"[m{len(seq) - 1}]atrim=0:{total},volume=@BED_DB@dB" + dip + f",afade=t=in:d=1.5,afade=t=out:st={max(0, total - 4)}:d=4[bed]")
        filt.append("[bed][key]sidechaincompress=threshold=0.02:ratio=6:attack=40:release=600,asplit=2[ducked][bstem]")
        mix_in = ["[voice]", "[ducked]"]
    else:
        mix_in = ["[voice]"]
    # the voice also keys the ducking, so it is split only when there is a bed to duck: an unused split output fails the graph
    has_bed = "[ducked]" in mix_in
    key = ",asplit=3[voice][key][vstem]" if has_bed else "[voice]"
    filt.insert(0, "[0:a]aformat=sample_rates=48000:channel_layouts=stereo" + key)
    whoosh = None
    if a.whoosh and Path(a.whoosh).exists():
        whoosh = (a.whoosh, Path(a.whoosh))
    elif a.whoosh:                                       # a catalog id
        import audio
        e = audio.catalog()["sfx"].get(a.whoosh)
        if e is None:
            raise SystemExit(f"--whoosh {a.whoosh}: not a file or a catalogued sfx id")
        whoosh = (a.whoosh, audio.library_dir() / e["file"])
    elif a.whoosh is None:                               # default: the library's whoosh, if one carries the role
        whoosh = library_whoosh()
    if whoosh and len(tl["chapters"]) > 1:
        sounds["whoosh"] = whoosh[0]
        base = len(inputs) // 2
        inputs += ["-i", str(whoosh[1])]
        cuts = [ch["t0"] for ch in tl["chapters"][1:]]
        wmax = SFX_MAX["whoosh"]
        filt.append(f"[{base}:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=0:{wmax},afade=t=out:st={wmax - 0.06}:d=0.06,"
                    f"volume=-10dB,asplit={len(cuts)}" +
                    "".join(f"[w{i}]" for i in range(len(cuts))))
        for i, c in enumerate(cuts):
            ms = int(max(0, c - 0.25) * 1000)
            filt.append(f"[w{i}]adelay={ms}|{ms}[wd{i}]")
            mix_in.append(f"[wd{i}]")
    if a.sfx:
        base = len(inputs) // 2
        sfx_wav, sounds["sfx"] = build_sfx(film, tl, drawn_ok=a.sfx_drawn)
        inputs += ["-i", str(sfx_wav)]
        filt.append(f"[{base}:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={a.sfx_db}dB[sfx]")
        mix_in.append("[sfx]")
    filt.append("".join(mix_in) + f"amix=inputs={len(mix_in)}:normalize=0:dropout_transition=0,atrim=0:{total}[pre]")
    script = film / "out" / "mix.filter"
    pre = film / "out" / "premix.wav"
    vstem, bstem = film / "out" / "voice-stem.wav", film / "out" / "bed-stem.wav"

    def premix(db):
        script.write_text(";".join(filt).replace("@BED_DB@", f"{db:.2f}"))
        f32 = ["-ar", "48000", "-c:a", "pcm_f32le"]     # float, so a bed raised above 0 dB can't clip before loudnorm
        stems = ["-map", "[vstem]", *f32, str(vstem), "-map", "[bstem]", *f32, str(bstem)] if has_bed else []
        run(["ffmpeg", "-v", "error", "-y", *inputs, *filter_file(script), "-map", "[pre]", *f32, str(pre), *stems])

    if has_bed:
        profile = a.bed_profile if a.bed_profile != "auto" else ("short" if tl.get("format") == "9x16" else "long")
        target = a.bed_gap if a.bed_gap is not None else BED_PROFILES[profile][0]
        auto = a.bed_db is None
        db = BED_START_DB if auto else a.bed_db
        premix(db)
        gap, band = bed_gaps(vstem, bstem)
        # the ducking's gain reduction follows the voice (the sidechain key), not the bed, so the bed's loudness moves
        # one for one with its gain: one corrected pass lands the gap; a second only if rounding or the -70 LUFS gate
        # left it off
        for _ in range(2):
            if not auto or gap is None or abs(gap - target) <= 0.3:
                break
            db = round(db + gap - target, 2)
            premix(db)
            gap, band = bed_gaps(vstem, bstem)
        probs, notes = bed_verdict(gap, band, profile)
        check = {"profile": profile, "target_lu": target, "bed_db": db, "set_by": "gap" if auto else "--bed-db",
                 "gap_lu": gap, "band_gap_lu": "bed silent there" if band == float("inf") else band, "dip_db": a.bed_dip or 0, "problems": probs, "notes": notes,
                 "exempt": a.bed_exempt or None}
        (film / "out" / "bed_check.json").write_text(json.dumps(check, indent=1) + "\n")
        print(f"bed: {db:.2f} dB ({check['set_by']}), {gap if gap is not None else '?'} LU under the voice "
              f"({profile} target {target:.0f}), {'?' if band is None else 'bed silent' if band == float('inf') else f'{band} LU'} in 1-4 kHz -> out/bed_check.json")
        for n in notes:
            print("  note:", n)
        for p_ in probs:
            print(("  EXEMPT: " if a.bed_exempt else "  STOP: ") + p_)
        if probs and not a.bed_exempt:
            old = film / "out" / "mix.wav"
            if old.exists():
                old.unlink()                         # a stale mix must not reach `final`
            raise SystemExit("mix stopped: the music bed fails its level check (sound.md, The bed under the voice). "
                             "Fix the level, or for a build already published pass --bed-exempt \"<why>\" with its old --bed-db")
        if a.bed_exempt:
            print(f"  exempt from the bed check: {a.bed_exempt}")
    else:
        premix(0)
    # two-pass loudness to -14 LUFS; -2.5 dBTP leaves room for the AAC encode's overshoot
    meas = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(pre), "-af", "loudnorm=I=-14:TP=-2.5:LRA=11:print_format=json", "-f", "null", "-"],
                          capture_output=True, text=True).stderr
    import audio
    j = audio.loudnorm_stats(meas)
    ln = (f"loudnorm=I=-14:TP=-2.5:LRA=11:measured_I={j['input_i']}:measured_TP={j['input_tp']}:measured_LRA={j['input_lra']}:"
          f"measured_thresh={j['input_thresh']}:offset={j['target_offset']}:linear=true")
    run(["ffmpeg", "-v", "error", "-y", "-i", str(pre), "-af", ln, "-ar", "48000", str(film / "out" / "mix.wav")])
    write_mix_stamp(film, tl)
    (film / "out" / "sounds_used.json").write_text(json.dumps(sounds, indent=1) + "\n")
    print("mix ->", film / "out" / "mix.wav", "(sounds used -> out/sounds_used.json; run `credits` for the description)")


def attribution_needed(licence):
    """CC BY needs a credit line; CC0, Mixkit and Kenney do not. NC/ND/SA never enter the library (studio.py import-sfx)."""
    return "CC BY" in licence.upper().replace("-", " ") or "/LICENSES/BY/" in licence.upper()


def cmd_credits(a):
    """The attribution lines for every catalogued sound the last mix used -> out/credits.txt, for the video description.
    A used sound with no licence in the catalog stops here: it doesn't ship (sound.md)."""
    import audio
    film = Path(a.film).resolve()
    used = film / "out" / "sounds_used.json"
    if not used.exists():
        raise SystemExit(f"no {used}: run `mix` first")
    u = json.loads(used.read_text())
    cat = audio.catalog()
    keys = [("tracks", k) for k in u.get("music", [])] + ([("sfx", u["whoosh"])] if u.get("whoosh") else [])
    keys += [("sfx", k) for ks in (u.get("sfx") or {}).values() for k in ks]
    lines, free, unknown = [], [], []
    for table, k in dict.fromkeys(keys):
        e = cat[table].get(k)
        if e is None:
            unknown.append(k)                            # a raw file path given to --music / --whoosh
            continue
        lic = e.get("licence", "")
        if not lic:
            raise SystemExit(f"{k} has no licence in catalog.json: it can't ship (sound.md)")
        if attribution_needed(lic):
            lines.append(e.get("attribution") or
                         f'"{e.get("title") or k}" by {e.get("creator", "unknown")} ({e.get("url", "")}), {e.get("licence_name", lic)}')
        else:
            free.append(k)
    out = film / "out" / "credits.txt"
    out.write_text(("Sound credits:\n" + "\n".join(lines) + "\n") if lines else "")
    print(f"credits -> {out} ({len(lines)} attribution lines; {len(free)} sounds need none: {', '.join(free) or '-'})")
    if unknown:
        print(f"  not in the catalog, check their licence by hand: {', '.join(unknown)}")


def film_frame0(film):
    """Frame 0 as the browser draws it (H x W x 3), the reference final's video is judged against, so a textured or
    light-paper ground is compared with itself rather than with a flat palette colour."""
    import numpy as np
    import render
    with render.Film(film) as f:
        png = f.shot(0)
        w, h = f.w, f.h
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", "-", "-vf", f"scale={w}:{h},format=rgb24", "-f", "rawvideo", "-"],
                         input=png, capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 3)


def upscale_list(film, segs):
    """Every panel still to upscale: the ones the script names and every take sibling of them (<shot>_s<seed>.png). The
    script names one take; the owner may pick another in the dashboard, and `timeline` then needs that one upscaled
    too (it stopped with "under 1920 wide and has no upscale" on each pick, 2026-11-26)."""
    import review_state
    out = set()
    for s in segs:
        if s.get("type") != "panel":
            continue
        for x in panel_images(s.get("data")):
            out.add(x)
            g = review_state.takes_of(film, x)
            out.update(p for _, p in (g[1] if g else []))
    return sorted(out)


def panel_images(d):
    """Every still a panel shows: its frames (a flipbook panel) or its one image."""
    fr = [f if isinstance(f, str) else (f or {}).get("image") for f in (d or {}).get("frames") or []]
    return [x for x in fr if x] or ([d["image"]] if (d or {}).get("image") else [])


CLIP_DIR = "clips/frames"     # a panel clip's frames, extracted once by `timeline` (clip_prepare)
CLIP_RETIME = ("blend", "hold", "interp")
# a clip plays once, forward, and holds its last frame. Never ping-pong (forward then reversed) and never loop (a visible
# restart): the owner's rule since 2026-11-22, which lived only in a notes file, so an episode shipped with "pingpong" on
# 2026-11-25 and he caught it. Both are refused by timeline; cyclic ambient motion (rain, steam, flicker) is panel fx.
CLIP_REFUSED = {"pingpong": "plays the clip forward and then reversed", "loop": "restarts the clip, a visible jump back"}


CLIP_FIT_FLOOR, CLIP_FIT_LONG = 0.45, 0.75


def clip_prepare(film, seg, fmt, problems):
    """A panel's video clip (data.clip, an mp4 in the film) as numbered JPEG frames at the panel's own size, plus the
    frame schedule the page draws from: data.clip_frames {dir, n, fps} and data.clip_sched, one [frame a, frame b, weight
    of b] per video frame of the segment. Frames, not a <video> element: a video seeks to the nearest keyframe and decodes
    asynchronously, so a render frame can catch the wrong picture or none; a numbered still is exact and the same on
    every machine. The frames are cut to cover the panel (Lanczos), so the camera and fx work on them as on a still.
      The clip plays once, forward, then holds its last frame while the camera keeps moving. "clip_mode" "pingpong" and
                   "loop" are refused (owner's rule: no forward-reverse, no visible restart; CLIP_REFUSED).
      "clip_retime": blend (default; each 30 fps frame mixes the two nearest clip frames), hold (each clip frame held,
                   16 fps cadence 2-2-2-1, the steps allowed by the pop check) or interp (ffmpeg motion interpolation to
                   30 fps; smoother, can warp edges)
      "clip_speed": 1 (0.5 plays at half speed, stretching a short clip); "clip_start": seconds into the clip.
      "clip_fit": true sets both so the clip's last frame lands on the cut (CLIP_FIT_FLOOR, CLIP_FIT_LONG).
    The extraction is cached under clips/frames/<name>-<hash of the clip and settings>/."""
    d = seg["data"]
    src = film / d["clip"]
    mode, retime = d.get("clip_mode", "hold_last"), d.get("clip_retime", "blend")
    if mode in CLIP_REFUSED:
        problems.append(f"{seg['id']}: clip_mode \"{mode}\" {CLIP_REFUSED[mode]}, which the owner's rule forbids: a clip plays "
                        f"once, forward, and holds its last frame. For a clip shorter than its segment, give the panel a camera "
                        f"move, slow it with \"clip_speed\", or shorten the segment")
        return
    if float(d.get("clip_speed", 1)) <= 0:
        problems.append(f"{seg['id']}: clip_speed must be above 0 (a negative speed plays the clip reversed, which the owner's rule forbids)")
        return
    if mode != "hold_last" or retime not in CLIP_RETIME:
        problems.append(f"{seg['id']}: clip_mode is hold_last (or left out) and clip_retime one of {', '.join(CLIP_RETIME)}")
        return
    if not src.exists():
        problems.append(f"{seg['id']}: clip {d['clip']} not found; the panel shows its still" + ("" if d.get("image") else ", and it has none"))
        return
    iw, ih = (1920, 1080) if fmt != "9x16" else (3414, 1920)      # the panel's composition size (longform.html B.panel)
    st = src.stat()
    key = hashlib.sha256(f"{d['clip']} {st.st_size} {st.st_mtime_ns} {retime == 'interp'} {iw}x{ih}".encode()).hexdigest()[:10]
    out = film / CLIP_DIR / f"{src.stem}-{key}"
    meta = out / "meta.json"
    if not meta.exists():
        fps_in = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate",
                                 "-of", "csv=p=0", str(src)], capture_output=True, text=True, check=True).stdout.strip()
        num, den = (fps_in.split("/") + ["1"])[:2]
        fps = 30.0 if retime == "interp" else float(num) / float(den)
        tmp = out.with_name(out.name + ".part")
        shutil.rmtree(tmp, ignore_errors=True); tmp.mkdir(parents=True)
        vf = (["minterpolate=fps=30:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1"] if retime == "interp" else []) + \
             [f"scale={iw}:{ih}:force_original_aspect_ratio=increase:flags=lanczos", f"crop={iw}:{ih}"]
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(src), "-vf", ",".join(vf), "-q:v", "2", "-start_number", "0",
                            str(tmp / "%04d.jpg")], capture_output=True, text=True)
        n = len(list(tmp.glob("*.jpg")))
        if r.returncode or n < 2:
            problems.append(f"{seg['id']}: couldn't read frames from {d['clip']}: {(r.stderr or 'under 2 frames').strip()[:200]}")
            shutil.rmtree(tmp, ignore_errors=True)
            return
        (tmp / "meta.json").write_text(json.dumps({"n": n, "fps": fps}) + "\n")
        shutil.rmtree(out, ignore_errors=True); tmp.rename(out)
    m = json.loads(meta.read_text())
    if d.get("clip_fit"):
        # fit the clip to its beat so its last frame lands on the cut (Survive Year 1, 2026-11-27): a clip shorter than the
        # beat is slowed to fill it, but never under CLIP_FIT_FLOOR (slower reads as slow motion; below it the clip ends
        # early and holds); a longer clip plays its end at CLIP_FIT_LONG speed, starting where that end needs it to
        if "clip_speed" in d or "clip_start" in d:
            problems.append(f"{seg['id']}: clip_fit sets clip_speed and clip_start itself; drop one or the other")
        length, beat = (m["n"] - 1) / m["fps"], seg["t1"] - seg["t0"]
        if length <= beat:
            sp, st = max(CLIP_FIT_FLOOR, length / beat), 0.0
        else:
            sp, st = CLIP_FIT_LONG, max(0.0, length - beat * CLIP_FIT_LONG)
        d["clip_speed"], d["clip_start"] = round(sp, 4), round(st, 3)
        if length / beat < CLIP_FIT_FLOOR:
            problems.append(f"{seg['id']}: clip_fit: the clip ({length:.1f} s) can't fill its {beat:.1f} s beat even at "
                            f"{CLIP_FIT_FLOOR}x; it ends {beat - length / CLIP_FIT_FLOOR:.1f} s early and holds. Shorten the beat or use a longer take")
    for e in d.get("fx") or []:
        if isinstance(e, dict) and e.get("type") in ("rain", "snow") and not e.get("over_clip"):
            problems.append(f"{seg['id']}: panel fx {e['type']} over a clip: a clip that already has weather gets it twice. "
                            f"Drop the fx, or set \"over_clip\": true on it after checking the clip has none")
    d["clip_frames"] = {"dir": str(out.relative_to(film)), "n": m["n"], "fps": m["fps"]}
    d["clip_sched"] = clip_schedule(seg, m["n"], m["fps"])
    d.setdefault("image", f"{d['clip_frames']['dir']}/0000.jpg")     # no still given: the clip's first frame is the poster


def clip_schedule(seg, n, fps, vfps=FPS):
    """[[a, b, w], ...]: for each video frame from the segment's first to its last, the clip frames to draw (b over a at
    opacity w): the clip once, forward, then its last frame, following clip_retime, clip_speed and clip_start."""
    d = seg["data"]
    retime = d.get("clip_retime", "blend")
    speed, start = float(d.get("clip_speed", 1)), float(d.get("clip_start", 0))
    out = []
    jr = lambda x: int(math.floor(x + 0.5))         # JavaScript's Math.round, which the page uses (Python's round goes to even)
    for k in range(jr(seg["t0"] * vfps), jr(seg["t1"] * vfps) + 1):
        p = max(0.0, (k / vfps - seg["t0"]) * speed + start) * fps
        if retime == "hold":
            p = math.floor(p + 1e-6)
        p = min(p, n - 1)                           # once, forward, then the last frame holds
        a = min(n - 1, int(math.floor(p + 1e-6)))
        out.append([a, min(n - 1, a + 1), round(p - a, 3) if retime == "blend" else 0])
    return out


def clip_backwards(segs):
    """Panels whose clip schedule ever steps back to an earlier frame: a clip played reversed or restarted, which the
    owner's rule forbids (CLIP_REFUSED). Read from the timeline itself, so a film timed before the rule was enforced is
    caught by check and final too, whatever its clip_mode says."""
    return [sg["id"] for sg in segs if sg["type"] == "panel" and (sg.get("data") or {}).get("clip_sched")
            and any(b[0] < a[0] for a, b in zip(sg["data"]["clip_sched"], sg["data"]["clip_sched"][1:]))]


def clip_steps(segs, fps=FPS):
    """Video frames where a held-frame clip (clip_retime "hold") steps to its next frame, for verify's allow_frames, like
    a flipbook's flips: a clip at 16 fps holds each frame for 2 or 1 video frames, so its motion comes in steps that
    the pop check would read as pops. Blended and interpolated clips change every frame and need no allowance."""
    out = set()
    for sg in segs:
        d = sg.get("data") or {}
        if sg["type"] != "panel" or not d.get("clip_sched") or d.get("clip_retime", "blend") != "hold":
            continue
        k0 = int(math.floor(sg["t0"] * fps + 0.5))
        out.update(k0 + i for i in range(1, len(d["clip_sched"])) if d["clip_sched"][i][0] != d["clip_sched"][i - 1][0])
    return out


def frame_spans(seg, k):
    """[[a, b], ...]: the times frame k of a flipbook panel is on screen (more than one span when it loops)."""
    d = seg.get("data") or {}
    flips = d.get("flips") or [[seg["t0"], 0]]
    ends = [f[0] for f in flips[1:]] + [seg["t1"]]
    return [[a, b] for (a, i), b in zip(flips, ends) if i == k and b > a]


def screen_spans(seg, cues):
    """A panel screen's time on screen, written as screen["spans"] for the page: the panel's whole length, or only while
    its "frame" (an index into data.frames) is showing, cut off at its "until" word. A line cued before its frame shows
    lands when the frame does, so its "t" (and the cue check sees) moves to the flip; the word's own time stays in
    "t_word". Survive Year 1 cel, 2026-11-28: writing on one drawing stayed up over the next drawings. Returns problems."""
    d, out = seg.get("data") or {}, []
    scr = d.get("screen")
    for j, sc in enumerate(scr if isinstance(scr, list) else [scr]):
        if not isinstance(sc, dict):
            continue
        name = f"{seg['id']}: screen" + (f" {j}" if isinstance(scr, list) else "")
        if "frame" in sc:
            k, n = sc["frame"], len(d.get("frames") or [])
            if not n:
                out.append(f"{name} names frame {k}, but the panel has no flipbook \"frames\""); continue
            if not isinstance(k, int) or isinstance(k, bool) or not 0 <= k < n:
                out.append(f"{name}: \"frame\" must be a whole number from 0 to {n - 1} (an index into data.frames)"); continue
            spans = frame_spans(seg, k)
            if not spans:
                out.append(f"{name}: frame {k} never shows in this segment, so its writing never would"); continue
        else:
            spans = [[seg["t0"], seg["t1"]]]
        u = sc.get("until")
        if isinstance(u, dict) and u.get("t") is not None:
            spans = [[a, min(b, u["t"])] for a, b in spans if a < u["t"]]
            if not spans:
                out.append(f"{name}: its \"until\" word comes before it is ever on screen"); continue
        elif u is not None:
            out.append(f"{name}: \"until\" takes a word cue, {{\"at\": \"word\"}}"); continue
        sc["spans"] = [[round(a, 4), round(b, 4)] for a, b in spans]
        for ln in sc.get("lines") or []:
            if not isinstance(ln, dict) or ln.get("t") is None:
                continue
            live = next(([a, b] for a, b in spans if b > ln["t"]), None)
            if live is None:
                out.append(f"{name}: line \"{str(ln.get('text', ''))[:30]}\" is cued after its screen has gone"); continue
            if live[0] > ln["t"]:
                for c in cues:
                    if c["seg"] == seg["id"] and c.get("at") == ln.get("at") and abs(c["t"] - ln["t"]) < 1e-6:
                        c["t"] = round(live[0], 3)
                ln["t_word"], ln["t"] = ln["t"], round(live[0], 3)
    return out


def panel_flips(seg, fps=FPS):
    """A flipbook panel's schedule, [[t, frame index], ...] in absolute seconds, every t on a whole video frame. With
    any "at" cue on a frame, every frame after the first needs one and shows from its word (hold-on-cue). Otherwise the
    frames cycle at data.fps (4: the limited animation of a flipbook, each drawing held for about 7 video frames),
    looping unless "loop": false or "hold_last": true, which plays them once and holds the last. Returns (flips, problems)."""
    d, t0, t1 = seg.get("data") or {}, seg["t0"], seg["t1"]
    frames = d.get("frames") or []
    q = lambda t: round(round(t * fps) / fps, 4)
    if len(frames) < 2:
        return [[q(t0), 0]], []
    cued = [isinstance(f, dict) and "t" in f for f in frames]
    if any(cued):
        miss = [i for i, c in enumerate(cued) if i and not c]
        if miss:
            return [[q(t0), 0]], [f"{seg['id']}: flipbook frames {miss} have no \"at\" cue; give every frame after the first one, or none"]
        ts = [t0] + [f["t"] for f in frames[1:]]
        if any(b <= a for a, b in zip(ts, ts[1:])):
            return [[q(t0), 0]], [f"{seg['id']}: flipbook frame cues are out of order ({', '.join(f'{t:.2f}' for t in ts)} s)"]
        return [[q(t), i] for i, t in enumerate(ts)], []
    step, once = 1 / float(d.get("fps", 4)), bool(d.get("hold_last")) or d.get("loop") is False
    out, k, t = [], 0, t0
    while t < t1 - 1e-6:
        i = min(k, len(frames) - 1) if once else k % len(frames)
        if not out or out[-1][1] != i:
            out.append([q(t), i])
        if once and k >= len(frames) - 1:
            break
        k += 1; t = t0 + k * step
    return out, []


def flip_frames(segs, fps=FPS):
    """Video frames where a flipbook panel changes drawing, for verify's allow_frames, like the map's boil: exact
    frames, so a real pop anywhere else in the panel is still caught."""
    return {int(round(t * fps)) for sg in segs if sg["type"] == "panel" for t, _ in ((sg.get("data") or {}).get("flips") or [])[1:]}


PUSH_MAX = 1.3     # a panel camera zoomed past this reads as "zooming at nothing" (owner's review, 2026-11-26)


def strong_pushes(segs):
    """[(segment id, deepest zoom)] for panels whose own camera zooms past PUSH_MAX. The engine's shot defaults stop at
    1.2 (ECU) and "slow-push" at 1.14; only a hand-written camera gets here."""
    out = []
    for s in segs:
        cam = (s.get("data") or {}).get("camera") if s.get("type") == "panel" else None
        if isinstance(cam, dict):
            z = max(float((cam.get(k) or {}).get("z", 1)) for k in ("from", "to"))
            if z > PUSH_MAX:
                out.append((s["id"], z))
    return out


def stat_ticks(segs, fps=FPS):
    """Video frames where a stat scene's count-up shows a new number, for verify's allow_frames. The count eases out, so
    near its end the number holds for several frames and then steps a digit, which the pop check reads as a pop (three
    stat scenes in Survive Year 1 flagged, 2026-11-25). Exact frames, worked out the way the page draws the count
    (longform.html B.stat), so a real pop on any other frame is still caught. Both rounding rules are tried at an exact
    half, since the page's toLocaleString and Python's round can differ there."""
    from decimal import Decimal, ROUND_HALF_EVEN, ROUND_HALF_UP
    out = set()
    for sg in segs:
        if sg["type"] != "stat":
            continue
        d = sg.get("data") or {}
        dec, val = int(d.get("decimals") or 0), float(d.get("value") or 0)
        c0 = d["t"] - 0.05 if d.get("t") is not None else sg["t0"] + 0.2
        c1 = c0 + min(2.0, (sg["t1"] - c0) * 0.5)
        q = Decimal(1).scaleb(-dec)
        shown = lambda t, mode: Decimal(repr(val * (1 - (1 - min(1.0, max(0.0, (t - c0) / (c1 - c0)))) ** 3))).quantize(q, mode)
        prev = None
        for f in range(int(c0 * fps), int(c1 * fps) + 2):
            cur = {shown(f / fps, m) for m in (ROUND_HALF_UP, ROUND_HALF_EVEN)}
            if prev is not None and cur != prev:
                out.add(f)
            prev = cur
    return out


def map_boil_steps(segs, fps=30, end=None):
    """The map scene's boil: every 8th frame each paper piece, ink line and dust speck jumps to its next small jitter
    (mapscene.js), so the map is never still. Each jump is a step on otherwise held frames, which the pop check reads as a
    pop once a map carries enough pieces (a US-states map flagged every 24th frame, the cycle's biggest jump, 2026-11-25).
    Returns the frame numbers where the boil steps inside map segments, for verify's allow_frames: exact frames, so a
    real pop on any other frame of a map is still caught."""
    out = set()
    for k, sg in enumerate(segs):
        if sg["type"] == "map":     # a film that ends on a map keeps the map (and its boil) to the last frame
            t1 = end if k == len(segs) - 1 and end else sg["t1"]
            out.update(f for f in range(int(sg["t0"] * fps + 1e-3), int(t1 * fps + 1e-3) + 1) if f % 8 == 0)
    return out


def cmd_final(a):
    with JobStatus(Path(a.film).resolve(), "final"):
        _final(a)


def _final(a):
    import verify
    film = Path(a.film).resolve()
    if not (film / "out" / "mix.wav").exists():     # render makes only the picture; the sound is its own step
        raise SystemExit(f"final needs out/mix.wav, which `render` doesn't make: run `longform.py mix {a.film}` first")
    require_fresh_mix(film, "final")
    import render
    out = render.mux(film / "out" / "silent.mp4", film / "out" / "mix.wav", film / "out" / f"{film.name}.mp4")
    # scene wipes, stamps and drama panels' hard cuts are meant to hit: don't call them pops
    tl = load_timeline(film)
    back = clip_backwards(tl["segments"])
    if back:
        raise SystemExit(f"final stopped: {', '.join(back)} play a clip backwards or restart it (ping-pong or loop), which the "
                         "owner's rule forbids: set the clip to play once and hold its last frame, run timeline, and render again")
    allow = [(sg["t0"], sg["t0"] + 0.5) for sg in tl["segments"] if sg["type"] in ("stage", "board", "crowd")]
    allow += [(sg["t1"], sg["t1"] + 0.5) for sg in tl["segments"] if sg["type"] in ("stage", "board", "crowd")]
    allow += [(t, t + 0.1) for sg in tl["segments"] if sg["type"] in ("panel", "map") for t in (sg["t0"], sg["t1"])]
    # a whip between flipbook frames is a fast blurred pan either side of the flip (longform.html WHIP = 0.13 s)
    allow += [(ft - 0.15, ft + 0.15) for sg in tl["segments"] if sg["type"] == "panel"
              for ft, k in ((sg.get("data") or {}).get("flips") or [])[1:]
              if str(((sg["data"].get("frames") or [{}])[k] if isinstance((sg["data"].get("frames") or [None])[k], dict) else {}).get("enter", "")).startswith("whip")]
    # map pieces slap in (scale 1.1, 0.98, 1.0 on twos) and ransom letters land one by one: hits on purpose
    allow += [(x["t"] - 0.1, x["t"] + 0.6) for sg in tl["segments"] if sg["type"] == "map"
              for x in (sg.get("data") or {}).get("pieces", []) if x.get("t") is not None]
    # paint sweeps (16 frames) and ink strokes (12) are revealed on twos too, each step a held pair of frames: allow the
    # reveal (a US-states film flagged four steps of a paint sweep, 2026-11-25). Uncued ones start 0.2 s / 0.3 s in.
    for sg in tl["segments"]:
        if sg["type"] == "map":
            for key, lead, n in (("paint", 0.2, 16), ("ink", 0.3, 12)):
                for x in (sg.get("data") or {}).get(key) or []:
                    t = x.get("t", sg["t0"] + lead)
                    allow.append((t - 0.1, t + x.get("frames", n) / 30 + 0.1))
    # the map camera moves on twos, so each step of a move is a held pair of frames: allow the move (at most 26 frames)
    for sg in tl["segments"]:
        if sg["type"] == "map":
            cams = (sg.get("data") or {}).get("camera") or []
            for c in cams if isinstance(cams, list) else [cams]:
                t = c.get("t", sg["t0"])
                allow.append((t - 0.1, t + c.get("frames", 26) / 30 + 0.15))
    allow += [(sg["data"]["stamp"]["t"], sg["data"]["stamp"]["t"] + 0.25) for sg in tl["segments"]
              if sg["type"] == "board" and sg.get("data", {}).get("stamp", {}).get("t") is not None]
    # "loop": true in script.json: a Short cut to loop, whose end jumps back to the opening on purpose (verify checks it does)
    finished = bool(tl.get("finish"))
    if finished and not (film / "out" / "silent-raw.mp4").exists():
        raise SystemExit("final stopped: the film has a finish but no out/silent-raw.mp4 (the unfinished copy the motion "
                         "check reads): run render again")
    ok = verify.verify(out, bpm=None, sheet_dest=None, motion=not finished, allow=allow, allow_frames=map_boil_steps(tl["segments"], end=tl.get("duration")) | flip_frames(tl["segments"]) | stat_ticks(tl["segments"]) | clip_steps(tl["segments"]), ground=film_frame0(film), loop=bool(load_script(film).get("loop")))
    if finished:                     # motion is judged on the unfinished picture: grain would pass a frozen shot
        import motioncheck
        floor, mf = motioncheck.check(motioncheck.windows(film / "out" / "silent-raw.mp4"))
        print(("PASS " if not mf else "FAIL ") + f"motion from frame 0, no 2 s hold, on the unfinished picture (floor {floor:.3f})"
              + (": " + "; ".join(mf[:4]) if mf else ""))
        ok = ok and not mf
    # the channel logo in its own colours wherever it appears: a style pack or grade must never recolour it
    import brand
    rep, probs = brand.brand_problems(film, load_script(film))
    for line in rep:
        print("INFO", line)
    for p in probs:
        print("FAIL brand:", p)
    ok = ok and not probs
    # beats measured on the finished video: a cue that drifted in the render, the joins or the mux, and the sound's offset
    import cuecheck
    lines, cprobs = cuecheck.run(film)
    for line in lines:
        print("INFO", line.strip())
    for p in cprobs:
        print("FAIL beat:", p)
    ok = ok and not cprobs
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


MEASURE_TEXT = """() => {
  const st = document.getElementById('stage').getBoundingClientRect(), out = [];
  document.querySelectorAll('#svg text').forEach(el => {
    const s = el.textContent.trim();
    if (!/[A-Za-z0-9]/.test(s)) return;
    let o = 1;
    for (let n = el; n && n.nodeType === 1 && n.id !== 'svg'; n = n.parentNode) {
      const cs = getComputedStyle(n);
      if (cs.display === 'none' || cs.visibility === 'hidden') return;
      o *= +cs.opacity;
    }
    if (o < 0.5) return;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.right < 0 || r.bottom < 0 || r.left > st.width || r.top > st.height) return;
    // text that rises out of a mask line is unseen until it has risen: count it only when most of it is inside its clip
    const cg = el.parentNode.getAttribute && el.parentNode.getAttribute('clip-path');
    if (cg) {
      const c = document.getElementById(cg.slice(5, -1)), cr = c && c.querySelector('rect'), b = el.getBBox();
      if (cr) { const y0 = +cr.getAttribute('y'), y1 = y0 + +cr.getAttribute('height');
        if (Math.min(y1, b.y + b.height) - Math.max(y0, b.y) < 0.6 * b.height) return; }
    }
    const m = el.getScreenCTM(), k = Math.hypot(m.a, m.b);
    out.push([s.slice(0, 50), Math.round(parseFloat(getComputedStyle(el).fontSize) * k * 10) / 10, el.closest('[data-aux]') ? 1 : 0]);
  });
  return out;
}"""


# Text a viewer must read must not be painted over. Every visible text element (the same filter as MEASURE_TEXT) is
# probed at a 15x5 grid inside its box: document.elementsFromPoint lists what is painted there, top first, so anything
# above the text at a probe covers it, unless it is see-through (under 0.15 opacity after its parents), larger than
# 60% of the frame (vignette, grade, a wipe), or marked data-over (a strike-through meant to cross the words). Text
# under or over other visible text at a probe is reported either way round. Probes, not boxes: hit-testing follows the
# real glyph cells, so a tilted stamp's loose bounding box near a word doesn't count. A collision needs 2 of the 75 probes, so a thin stroke across a word counts.
# Text drawn inside the box of a visible data-clear element (a hand-built stage layer marked keep-clear) fails too, even
# on top of it, unless the text is part of that element.
# Text drawn straight onto a stage foreground piece (data-fore: the shapes in front of the action) fails too, even on
# top of it, because the piece's edges run through the words: the first thing painted under the text at a probe is
# the piece. Text on its own box (a bubble, a card) is drawn on that box, so it passes (Longtok, 2026-10-25).
OVERLAP = """() => {
  const st = document.getElementById('stage').getBoundingClientRect(), frame = st.width * st.height;
  const opac = n => { let o = 1;
    for (; n && n.nodeType === 1 && n.id !== 'svg'; n = n.parentNode) {
      const cs = getComputedStyle(n);
      if (cs.display === 'none' || cs.visibility === 'hidden') return 0;
      o *= +cs.opacity;
    }
    return o; };
  const name = e => e.tagName === 'text' || e.closest('text') ? 'text "' + (e.closest('text') || e).textContent.trim().slice(0, 30) + '"'
    : (u => u ? 'the shape holding "' + u.textContent.trim().slice(0, 30) + '"' : 'a ' + e.tagName)((e.parentNode.closest && e.parentNode.closest('g') || e.parentNode).querySelector('text'));
  const texts = [];
  document.querySelectorAll('#svg text').forEach(el => {
    const s = el.textContent.trim();
    if (!/[A-Za-z0-9]/.test(s) || opac(el) < 0.5) return;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.right < 0 || r.bottom < 0 || r.left > st.width || r.top > st.height) return;
    const cg = el.parentNode.getAttribute && el.parentNode.getAttribute('clip-path');
    if (cg) {
      const c = document.getElementById(cg.slice(5, -1)), cr = c && c.querySelector('rect'), b = el.getBBox();
      if (cr) { const y0 = +cr.getAttribute('y'), y1 = y0 + +cr.getAttribute('height');
        if (Math.min(y1, b.y + b.height) - Math.max(y0, b.y) < 0.6 * b.height) return; }
    }
    texts.push([el, s.slice(0, 50), r]);
  });
  const clears = [...document.querySelectorAll('#svg [data-clear]')].filter(c => opac(c) >= 0.15)
    .map(c => [c, c.getBoundingClientRect(), c.getAttribute('data-clear') || 'a keep-clear layer']).filter(([, b]) => b.width * b.height > 0);
  const out = [];
  texts.forEach(([el, s, r]) => {
    const hits = {};
    for (let i = 0; i < 15; i++) for (let j = 0; j < 5; j++) {
      const x = r.left + r.width * (0.05 + 0.9 * i / 14), y = r.top + r.height * (0.2 + 0.6 * j / 4);
      const stack = document.elementsFromPoint(x, y), at = stack.findIndex(e => e === el || el.contains(e));
      if (at < 0) continue;   // between lines, or outside the text's clip: nothing of it is drawn here
      clears.forEach(([c, b, nm]) => { if (!c.contains(el) && x >= b.left && x <= b.right && y >= b.top && y <= b.bottom) {
        const k = 'inside keep-clear ' + nm; hits[k] = (hits[k] || 0) + 1; } });
      const under = stack.slice(at + 1).find(e => !el.contains(e));
      if (under && under.closest('[data-fore]') && opac(under) >= 0.15) hits['on the stage foreground'] = (hits['on the stage foreground'] || 0) + 1;
      stack.slice(at + 1).forEach(e => { const u = e.closest('text');
        if (u && u !== el && opac(u) >= 0.5) { const k = 'overlaps ' + name(u); hits[k] = (hits[k] || 0) + 1; } });
      for (const e of stack.slice(0, at)) {
        if (el.contains(e) || e.contains(el) || e.closest('[data-over]') || opac(e) < 0.15) continue;
        const b = e.getBoundingClientRect();
        if (b.width * b.height > 0.6 * frame) continue;
        const k = name(e); hits[k] = (hits[k] || 0) + 1; break;
      }
    }
    Object.entries(hits).forEach(([k, n]) => { if (n >= 2) out.push([s, /^(overlaps|inside|on the)/.test(k) ? k : 'covered by ' + k, n]); });
  });
  return out;
}"""


# How much of the frame a scene draws as picture rather than words. A 64x36 grid of probes over the frame: a probe
# counts when the topmost visible thing there is a shape that is not text, not see-through (under 0.15 opacity after its
# parents) and not frame-sized (a background, vignette or wipe, over 60% of the frame). The engine's own text scenes
# measure 0-3%; a kit pattern that turns a statement into motion graphics (piles, shopfronts, acted steps) measures well above.
PICTURE = """() => {
  const st = document.getElementById('stage').getBoundingClientRect(), frame = st.width * st.height;
  const opac = n => { let o = 1;
    for (; n && n.nodeType === 1 && n.id !== 'svg'; n = n.parentNode) {
      const cs = getComputedStyle(n);
      if (cs.display === 'none' || cs.visibility === 'hidden') return 0;
      o *= +cs.opacity;
    }
    return o; };
  let hit = 0;
  for (let i = 0; i < 64; i++) for (let j = 0; j < 36; j++) {
    const x = st.left + st.width * (i + 0.5) / 64, y = st.top + st.height * (j + 0.5) / 36;
    for (const e of document.elementsFromPoint(x, y)) {
      if (!e.closest('#svg') || e.id === 'svg') break;
      if (e.closest('text') || e.tagName === 'g' || opac(e) < 0.15) continue;
      const b = e.getBoundingClientRect();
      if (b.width * b.height > 0.6 * frame) continue;
      hit++; break;
    }
  }
  return hit / (64 * 36);
}"""


def _still_gray(path, w, h):
    """A still as the panel draws it (covering w x h, centred: SVG's xMidYMid slice), in gray, as floats."""
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf",
                          f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},format=gray",
                          "-frames:v", "1", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w).astype(np.float32)


def _sample(img, cam, iw, ih, fw, fh, ow, oh):
    """What the frame shows of the still under camera {z, cx, cy}, at ow x oh, bilinear (the browser's own scaling)."""
    import numpy as np
    k = img.shape[1] / iw                                   # still pixels per scene pixel
    u = (np.arange(ow) + 0.5) * fw / ow - fw / 2
    v = (np.arange(oh) + 0.5) * fh / oh - fh / 2
    x = np.clip((cam["cx"] + u / cam["z"]) * k - 0.5, 0, img.shape[1] - 1.001)
    y = np.clip((cam["cy"] + v / cam["z"]) * k - 0.5, 0, img.shape[0] - 1.001)
    x0, y0 = x.astype(int), y.astype(int)
    ax, ay = (x - x0)[None, :], (y - y0)[:, None]
    a, b = img[y0][:, x0], img[y0][:, x0 + 1]
    c, d = img[y0 + 1][:, x0], img[y0 + 1][:, x0 + 1]
    return (a * (1 - ax) + b * ax) * (1 - ay) + (c * (1 - ax) + d * ax) * ay


def panel_motion(film, segs):
    """{panel id: per-window motion} predicted before any render: the panel's camera path from the page
    (STUDIO.panelCam) applied to its still, measured the way final's motion check measures the video (motioncheck: frames
    at a quarter size, 10 a second, mean change per half-second window). It leaves out what the still doesn't show
    (fades, caption cards, grain), so it reads a little low, which errs toward flagging."""
    import motioncheck
    import numpy as np
    import render
    pan = [s for s in segs if s["type"] == "panel" and (s.get("data") or {}).get("image")]
    if not pan:
        return {}
    out, step = {}, 1 / motioncheck.FPS
    with render.Film(film) as f:
        for s in pan:
            ts = [s["t0"] + i * step for i in range(int((s["t1"] - s["t0"]) / step) + 1)]
            pc = f.page.evaluate("([id, ts]) => { const c = (window.STUDIO.panelCam || {})[id]; "
                                 "return c && { iw: c.iw, ih: c.ih, w: c.w, h: c.h, cams: ts.map(c.at) }; }", [s["id"], ts])
            img = film / s["data"]["image"]
            if not pc or not img.exists():
                continue
            ow, oh = max(2, int(pc["w"]) // 4 // 2 * 2), max(2, int(pc["h"]) // 4 // 2 * 2)
            gw, gh = int(pc["iw"]) // 4, int(pc["ih"]) // 4
            cf, cs = s["data"].get("clip_frames"), s["data"].get("clip_sched")
            if cf and cs:               # a clip panel: the clip's own frames, mixed as the page mixes them, under the camera
                cache = {}
                g = lambda i: cache[i] if i in cache else cache.setdefault(i, _still_gray(film / cf["dir"] / f"{i:04d}.jpg", gw, gh))
                k0 = int(math.floor(s["t0"] * FPS + 0.5))

                def pic(t):
                    a_, b_, w_ = cs[min(len(cs) - 1, max(0, int(math.floor(t * FPS + 0.5)) - k0))]
                    return g(a_) * (1 - w_) + g(b_) * w_ if w_ else g(a_)
                frames = [np.round(_sample(pic(t), c, pc["iw"], pc["ih"], pc["w"], pc["h"], ow, oh)) for t, c in zip(ts, pc["cams"])]
            else:
                still = _still_gray(img, gw, gh)
                frames = [np.round(_sample(still, c, pc["iw"], pc["ih"], pc["w"], pc["h"], ow, oh)) for c in pc["cams"]]
            d = [float(np.abs(frames[i + 1] - frames[i]).mean()) for i in range(len(frames) - 1)]
            per = max(1, int(round(motioncheck.FPS * motioncheck.WINDOW)))
            out[s["id"]] = [float(np.mean(d[i:i + per])) for i in range(0, len(d) - per + 1, per)]
    return out


def map_motion(film, segs):
    """{"<segment> hold a-b s": per-window motion} for every stretch of a map run where the camera isn't in a move,
    measured on the page's own frames the way final measures the video (motioncheck: a frame every 0.1 s at a quarter
    size, gray, mean change per half-second window on the film's 0.5 s grid). A map's look is its vector art, so unlike a
    panel it can't be predicted from a still: the holds are rendered (JPEG, about 0.1 s a frame), the moves are left out
    since they pass by a wide margin. A map that waited for its first cue word sat still for 3.5 s and failed final three
    renders in a row before this (2026-11-25)."""
    import io
    import motioncheck
    import numpy as np
    import render
    from PIL import Image
    if not any(s["type"] == "map" for s in segs):
        return {}
    step, per = 1 / motioncheck.FPS, max(1, int(round(motioncheck.FPS * motioncheck.WINDOW)))
    out = {}
    with render.Film(film) as f:
        ev = f.page.evaluate("typeof MAP !== 'undefined' && MAP ? MAP.events() : null")
        if not ev:
            return {}
        ow, oh = max(2, f.w // 4 // 2 * 2), max(2, f.h // 4 // 2 * 2)
        cache = {}

        def gray(k):                    # the k-th tenth of a second, as final's motion check sees it
            if k not in cache:
                f.page.evaluate(f"window.seek({k * step:.6f})")
                im = Image.open(io.BytesIO(f.page.screenshot(type="jpeg", quality=95))).convert("L")
                cache[k] = np.asarray(im.resize((ow, oh), Image.BICUBIC), dtype=np.int16)
            return cache[k]
        for r0, r1 in ev["runs"]:
            busy = sorted((t - 0.05, t + d + 0.05) for t, d in ev["moves"] if r0 - 1e-6 <= t <= r1)
            holds, a = [], r0
            for m0, m1 in busy:
                if m0 > a:
                    holds.append((a, min(m0, r1)))
                a = max(a, m1)
            if a < r1:
                holds.append((a, r1))
            for h0, h1 in holds:
                w0 = math.ceil(h0 / motioncheck.WINDOW - 1e-9) * motioncheck.WINDOW
                wins = []
                while w0 + motioncheck.WINDOW <= h1 + 1e-9:
                    k0 = int(round(w0 * motioncheck.FPS))
                    fr = [gray(k0 + j) for j in range(per + 1)]
                    wins.append(float(np.mean([np.abs(fr[j + 1] - fr[j]).mean() for j in range(per)])))
                    w0 += motioncheck.WINDOW
                    for k in [k for k in cache if k < k0]:
                        del cache[k]
                if wins:
                    sid = next((s["id"] for s in segs if s["t0"] - 1e-6 <= h0 < s["t1"]), "map")
                    out[f"{sid} map hold {h0:.1f}-{h1:.1f} s"] = wins
    return out


STAT_STILL = {}      # "<id> stat": True when the page never moved the scene (filled by stat_motion)
STAT_TRAVEL = {}     # "<id> stat": (px at 1080 lines, seconds) the page moved the scene over its hold


def _translate(tf):
    import re
    m = re.match(r"\s*translate\(\s*([-\d.e]+)[ ,]+([-\d.e]+)\s*\)\s*$", tf or "")
    return (float(m.group(1)), float(m.group(2))) if m else None


def stat_motion(film, segs):
    """{"<segment> stat": per-window motion} for every stat scene of 2 s or more, measured on the page's own frames the
    way final measures the video (as map_motion does). A stat scene is a count-up and then words on a plain ground: a
    7.5 s stat held still for 5 s after its count and failed final (2026-11-25). Turn on "stat_drift" to fix it."""
    import io
    import motioncheck
    import numpy as np
    import render
    from PIL import Image
    stats = [s for s in segs if s["type"] == "stat" and s["t1"] - s["t0"] >= 2.0]
    if not stats:
        return {}
    step, per, out = 1 / motioncheck.FPS, max(1, int(round(motioncheck.FPS * motioncheck.WINDOW))), {}
    with render.Film(film) as f:
        ow, oh = max(2, f.w // 4 // 2 * 2), max(2, f.h // 4 // 2 * 2)

        def gray(k):
            f.page.evaluate(f"window.seek({k * step:.6f})")
            im = Image.open(io.BytesIO(f.page.screenshot(type="jpeg", quality=95))).convert("L")
            return np.asarray(im.resize((ow, oh), Image.BICUBIC), dtype=np.int16)
        # whether this film's page drifts its stat scenes at all: a film keeps the film.html `init` copied in, and one
        # copied before stat drift existed (#141) measures as a still whatever the timeline says (Survive Year 1 read
        # 0.034-0.036 on three stats with drift 14 set, 2026-11-27)
        cam = "(id) => { const g = document.querySelector(`#scenes > [data-seg=${id}]`); return g ? g.getAttribute('transform') : null; }"
        for sg in stats:
            f.page.evaluate(f"window.seek({sg['t0'] + 1.5:.3f})"); a = f.page.evaluate(cam, sg["id"])
            f.page.evaluate(f"window.seek({sg['t1'] - 0.1:.3f})"); b = f.page.evaluate(cam, sg["id"])
            STAT_STILL[f"{sg['id']} stat"] = a == b
            # how far it went, so a flag can tell a page that drifts too little from a drift the frames don't show
            pa, pb = _translate(a), _translate(b)
            if pa and pb:
                STAT_TRAVEL[f"{sg['id']} stat"] = (math.hypot(pb[0] - pa[0], pb[1] - pa[1]) * 1080 / f.h, sg["t1"] - 0.1 - sg["t0"] - 1.5)
        for sg in stats:
            w0, wins = math.ceil(sg["t0"] / motioncheck.WINDOW - 1e-9) * motioncheck.WINDOW, []
            while w0 + motioncheck.WINDOW <= sg["t1"] + 1e-9:
                fr = [gray(int(round(w0 * motioncheck.FPS)) + j) for j in range(per + 1)]
                wins.append(float(np.mean([np.abs(fr[j + 1] - fr[j]).mean() for j in range(per)])))
                w0 += motioncheck.WINDOW
            out[f"{sg['id']} stat"] = wins
    return out


def motion_flags(est, floor=None, stretch=2.0, margin=1.2):
    """(floor, [(panel id, quietest 2 s mean)]) for panels predicted to sit under final's motion floor. Without a floor
    given, final's own rule is applied to the panels' estimates (a quarter of the median window, from 0.02 to 0.2),
    and a panel within 20% of it is flagged too, since the estimate leaves out fades, cards and grain."""
    import motioncheck
    import numpy as np
    allw = [v for w in est.values() for v in w]
    if not allw:
        return 0.0, []
    floor = floor if floor is not None else motioncheck.floor_of(allw)
    n, low = max(1, int(round(stretch / motioncheck.WINDOW))), []
    for sid, w in est.items():
        if len(w) < n:
            continue                    # under 2 s: the cuts either side carry the motion check
        worst = min(sum(w[i:i + n]) / n for i in range(len(w) - n + 1))
        if worst < floor * margin:
            low.append((sid, worst))
    return floor, low


def picture_share(film, segs):
    """{segment id: the largest share of the frame drawn as picture} for the text-only scene types, sampled at 30% and
    60% through each and once it has settled, so a kit pattern that builds its picture up over the scene is seen."""
    import render
    out = {}
    with render.Film(film) as f:
        for s in segs:
            if s["type"] not in TEXT_ONLY:
                continue
            ts = {s["t0"] + 0.3 * (s["t1"] - s["t0"]), s["t0"] + 0.6 * (s["t1"] - s["t0"]), max(s["t0"] + 0.5, s["t1"] - 0.4)}
            best = 0.0
            for t in sorted(ts):
                f.page.evaluate(f"window.seek({t:.3f})")
                best = max(best, f.page.evaluate(PICTURE))
            out[s["id"]] = best
    return out


# Reading text must sit wholly inside the frame, and inside the safe area on an upright Short (where YouTube's buttons
# and caption bar cover the edges): the same visible-text filter as MEASURE_TEXT, then the text's box is compared with
# SAFE (the template's safe area, frame units) scaled to the page. Labels (data-aux: source lines, kickers) are held
# only to the frame, since the template sets some in a Short's margin on purpose. 2 px of slack; text marked data-edge
# (a ticker meant to run off the side) is skipped. Text wholly off the frame is unseen and not reported.
EDGE = """() => {
  const st = document.getElementById('stage').getBoundingClientRect(), out = [];
  const FW = typeof W !== 'undefined' ? W : st.width, FH = typeof H !== 'undefined' ? H : st.height;
  const k = st.width / FW, S = typeof SAFE !== 'undefined' ? SAFE : { x0: 0, x1: FW, y0: 0, y1: FH }, upright = FH > FW;
  const safe = { l: st.left + S.x0 * k, r: st.left + S.x1 * k, t: st.top + S.y0 * k, b: st.top + S.y1 * k };
  const whole = { l: st.left, r: st.right, t: st.top, b: st.bottom };
  document.querySelectorAll('#svg text').forEach(el => {
    const s = el.textContent.trim();
    if (!/[A-Za-z0-9]/.test(s) || el.closest('[data-edge]')) return;
    for (let n = el, o = 1; n && n.nodeType === 1 && n.id !== 'svg'; n = n.parentNode) {
      const cs = getComputedStyle(n);
      if (cs.display === 'none' || cs.visibility === 'hidden') return;
      o *= +cs.opacity; if (o < 0.5) return;
    }
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.right < st.left || r.bottom < st.top || r.left > st.right || r.top > st.bottom) return;
    const cg = el.parentNode.getAttribute && el.parentNode.getAttribute('clip-path');
    if (cg) {
      const c = document.getElementById(cg.slice(5, -1)), cr = c && c.querySelector('rect'), b = el.getBBox();
      if (cr) { const y0 = +cr.getAttribute('y'), y1 = y0 + +cr.getAttribute('height');
        if (Math.min(y1, b.y + b.height) - Math.max(y0, b.y) < 0.6 * b.height) return; }
    }
    const aux = !!el.closest('[data-aux]'), box = aux ? whole : safe;   // a label (source line, kicker) may sit in a Short's margin, never off the frame
    const sides = [['left', box.l - r.left], ['right', r.right - box.r], ['top', box.t - r.top], ['bottom', r.bottom - box.b]].filter(([, d]) => d > 2);
    sides.forEach(([side, d]) => out.push([s.slice(0, 50), (upright && !aux ? 'outside the safe area at the ' : 'cut off at the ') + side + ' edge', Math.round(d / k)]));
  });
  return out;
}"""


# A stage notification or text message placed over someone's head, or over a quarter of someone's torso (the stage
# builder records each in its group's __covers): the placement avoids both, so one that lands there means the scene has
# no room for the card. Longtok found a message card covering its holder from the head down, then one over its holder's
# torso and legs in an upright two-shot (2026-10-24).
COVERS = """() => [...document.querySelectorAll('#svg g')].filter(g => g.__covers && g.__covers.length && getComputedStyle(g).display !== 'none')
  .flatMap(g => g.__covers.map(([s, who]) => [String(s).slice(0, 50), 'card over ' + who, 0]))"""


# Every visible piece of reading text at this moment (the MEASURE_TEXT filter, labels and captions left out), with
# whether it is fully shown (opacity >= 0.95 after its parents, at least 98% inside its rise mask and its scene's
# wipe) and its box in its scene's own coordinates, so a camera move does not count as the text moving. Each element
# keeps an id across samples (el.__tid).
TEXT_STATE = """() => {
  const st = document.getElementById('stage').getBoundingClientRect(), out = [];
  window.__tidN = window.__tidN || 0;
  const inside = (b, r) => r ? Math.max(0, Math.min(r.x + r.width, b.x + b.width) - Math.max(r.x, b.x)) * Math.max(0, Math.min(r.y + r.height, b.y + b.height) - Math.max(r.y, b.y)) / Math.max(1e-6, b.width * b.height) : 1;
  const clipRect = n => { const a = n.getAttribute && n.getAttribute('clip-path'); if (!a) return null;
    const c = document.getElementById(a.slice(5, -1)), r = c && c.querySelector('rect');
    return r ? { x: +r.getAttribute('x'), y: +r.getAttribute('y'), width: +r.getAttribute('width'), height: +r.getAttribute('height') } : null; };
  const corners = (b, m) => { const p = [[b.x, b.y], [b.x + b.width, b.y], [b.x, b.y + b.height], [b.x + b.width, b.y + b.height]]
    .map(([x, y]) => [m.a * x + m.c * y + m.e, m.b * x + m.d * y + m.f]), xs = p.map(q => q[0]), ys = p.map(q => q[1]);
    return { x: Math.min(...xs), y: Math.min(...ys), width: Math.max(...xs) - Math.min(...xs), height: Math.max(...ys) - Math.min(...ys) }; };
  document.querySelectorAll('#svg text').forEach(el => {
    const s = el.textContent.trim();
    if (!/[A-Za-z0-9]/.test(s) || el.closest('[data-aux]')) return;
    let o = 1;
    for (let n = el; n && n.nodeType === 1 && n.id !== 'svg'; n = n.parentNode) {
      const cs = getComputedStyle(n);
      if (cs.display === 'none' || cs.visibility === 'hidden') return;
      o *= +cs.opacity;
    }
    if (o < 0.5) return;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.right < 0 || r.bottom < 0 || r.left > st.width || r.top > st.height) return;
    const b = el.getBBox(), seg = el.closest('[data-seg]');
    let shown = inside(b, clipRect(el.parentNode));
    // the scene's own space: the segment group under the camera; its wipe clip is in that space too
    const sg = seg && seg.parentNode && seg.parentNode.id === 'scenes' ? seg : null;
    // a paper piece's boil (data-boil: a jitter of a pixel or two every few frames, mapscene.js) is left out, so it doesn't
    // read as the text moving: the boil group's own transform is taken back out of the element's
    let em = el.getCTM();
    const bg = el.closest('[data-boil]');
    if (bg) { const P = bg.parentNode.getCTM(), B = P.inverse().multiply(bg.getCTM()); em = P.multiply(B.inverse()).multiply(P.inverse()).multiply(em); }
    const m = sg ? sg.getCTM().inverse().multiply(em) : em, box = corners(b, m);
    if (sg) shown = Math.min(shown, inside(box, clipRect(sg)));
    if (shown < 0.6) return;      // still under its mask line or the wipe, as MEASURE_TEXT
    el.__tid = el.__tid || ++window.__tidN;
    out.push([el.__tid, seg ? seg.getAttribute('data-seg') : '', s, o >= 0.95 && shown >= 0.98 ? 1 : 0, box.x, box.y, box.width, box.height,
              el.closest('[data-payoff]') ? 1 : 0, Math.round(parseFloat(getComputedStyle(el).fontSize))]);
  });
  return out;
}"""


# The pacing probe, installed once on the page check samples. It records, between one sample and the next: every figure
# placed (F.place and F.closeup's place), with its pose as one vector (joints in degrees, face settings and closeup
# values weighted to degree-like units, the placement in px), so a figure moving beyond its breathing and sway can be
# told from one that only idles; every pose key sequence passed to F.at or F.closeup's at; and every element of the
# drawing whose attributes or text changed (through a MutationObserver), leaving out figures' own parts, <defs>, and
# things under 32 x 32 px (the background dots). An element counts as moving when its box moves faster than 20 px/s or
# its opacity changes faster than 0.2 a second; as entering when it first changes, when it shows or hides, or when its
# colour or text changes. The probe draws nothing and changes nothing the page draws.
PACE_INSTALL = """() => {
  if (window.__pace) return;
  const P = window.__pace = { n: 0, ids: new WeakMap(), placed: [], keys: new Map(), prev: new WeakMap(), fprev: new Map(), roots: new Set(), inFig: new WeakMap() };
  P.id = e => P.ids.get(e) || (P.ids.set(e, ++P.n), P.n);
  const FW = { lean: 1, head: 1, ls: 1, le: 1, rs: 1, re: 1, lh: 1, lk: 1, rh: 1, rk: 1, crouch: 40, brow: 20, mouth: 20, open: 20, look: 10, lookY: 10 };
  const CW = { rise: 90, tilt: 1, slide: 60, squash: 60, hlRise: 45, hlX: 30, hrRise: 45, hrX: 30, hlGrip: 20, hrGrip: 20,
               brow: 20, mouth: 20, open: 20, look: 10, lookY: 10, pupil: 20, lens: 30 };
  const wrapPlace = (obj, blend, w, side) => { if (!obj || !obj.place) return; const orig = obj.place;
    obj.place = function (fig, x, y, h, pose, o) {
      const r = orig.apply(this, arguments);
      try { const q = blend(pose, pose, 0), v = [x, y, h, side(fig, o || {}) === 'left' ? -30 : 30];
        for (const k in w) v.push((q[k] || 0) * w[k]);
        P.placed.push([fig.root, [fig.root, fig.front].filter(Boolean), v]); } catch (e) {}
      return r; }; };
  const wrapAt = obj => { if (!obj || !obj.at) return; const orig = obj.at;
    obj.at = function (t, keys) {
      try { if (P.keys.size < 5000) { const k = keys.map(x => [x[0], typeof x[1] === 'string' ? x[1] : JSON.stringify(x[1])]), s = JSON.stringify(k);
        if (!P.keys.has(s)) P.keys.set(s, k); } } catch (e) {}
      return orig.apply(this, arguments); }; };
  if (window.F) { wrapPlace(F, F.blend, FW, (f, o) => o.face || f.o.face); wrapAt(F);
    if (F.closeup) { wrapPlace(F.closeup, F.closeup.blend, CW, (f, o) => o.facing || f.o.facing); wrapAt(F.closeup); } }
  const svg = document.getElementById('svg');
  P.buf = []; P.mo = new MutationObserver(r => { for (const m of r) P.buf.push(m); });   // delivered after each seek returns
  P.mo.observe(svg, { subtree: true, attributes: true, attributeOldValue: true, characterData: true, childList: true });
  P.placed = [];
}"""

PACE_SAMPLE = """dt => {
  const P = window.__pace, st = document.getElementById('stage').getBoundingClientRect();
  const recs = P.buf.concat(P.mo.takeRecords()), placed = P.placed; P.buf = []; P.placed = [];
  placed.forEach(([r, parts]) => parts.forEach(e => P.roots.add(e)));
  const inFig = e => { if (P.inFig.has(e)) return P.inFig.get(e);
    let v = false; for (let n = e; n && n.id !== 'svg'; n = n.parentNode) if (P.roots.has(n)) { v = true; break; }
    P.inFig.set(e, v); return v; };
  // shown: no display none, hidden or (nearly) see-through ancestor up to the drawing; checkVisibility doesn't see these
  // on SVG elements. Cached per node for this sample
  const sc = new Map(), shown = n => { if (!n || n.nodeType !== 1 || n.id === 'svg') return 1;
    if (sc.has(n)) return sc.get(n); const cs = getComputedStyle(n);
    const v = cs.display === 'none' || cs.visibility === 'hidden' ? 0 : +cs.opacity * shown(n.parentNode); sc.set(n, v); return v; };
  const COLOUR = new Set(['fill', 'stroke', 'stop-color', 'href', 'xlink:href', 'class']);
  const changed = new Map();
  recs.forEach(m => {
    const e = m.type === 'characterData' ? m.target.parentElement : m.target;
    if (!e || e.nodeType !== 1) return;
    let c = changed.get(e); if (!c) changed.set(e, c = { attrs: new Map(), text: false });
    if (m.type === 'attributes') { if (!c.attrs.has(m.attributeName)) c.attrs.set(m.attributeName, m.oldValue); }
    else c.text = true;
  });
  const moving = [], A = 32 * 32;
  const opac = e => ['opacity', 'fill-opacity', 'stroke-opacity'].reduce((a, k) => a * (e.hasAttribute(k) ? +e.getAttribute(k) : 1), 1);
  changed.forEach((c, e) => {
    const diff = [...c.attrs].filter(([k, old]) => old !== e.getAttribute(k)).map(([k]) => k);
    if (!diff.length && !c.text) return;
    if (e.closest('defs') || inFig(e)) return;
    const vis = shown(e) > 0.02;
    const was0 = P.prev.get(e), r = e.getBoundingClientRect(), x0 = Math.max(r.left, st.left), x1 = Math.min(r.right, st.right), y0 = Math.max(r.top, st.top), y1 = Math.min(r.bottom, st.bottom);
    const area = vis && x1 > x0 && y1 > y0 ? (x1 - x0) * (y1 - y0) : 0, now = { l: r.left, t: r.top, r: r.right, b: r.bottom, area, op: opac(e), vis: area > 0, s: c.text ? e.textContent : was0 && was0.s };
    const was = P.prev.get(e); P.prev.set(e, now);   // (text set again to the same words is no change)
    if (now.area < A && !(was && was.area >= A)) return;
    let go = false;
    if (!was) go = now.vis;
    else if (was.vis !== now.vis || (c.text && now.s !== was.s) || diff.some(k => COLOUR.has(k))) go = true;
    else if (now.vis) go = Math.max(Math.abs(now.l - was.l), Math.abs(now.t - was.t), Math.abs(now.r - was.r), Math.abs(now.b - was.b)) / dt > 20
                      || Math.abs(now.op - was.op) / dt > 0.2;
    if (go) moving.push(P.id(e));
  });
  const figs = placed.map(([root, parts, v]) => {
    const id = P.id(root), was = P.fprev.get(id); P.fprev.set(id, v);
    const r = root.getBoundingClientRect(), vis = shown(root) > 0.02 && r.width > 0
      && r.right > st.left && r.left < st.right && r.bottom > st.top && r.top < st.bottom;
    const rate = was ? v.reduce((a, x, i) => a + Math.abs(x - was[i]), 0) / dt : -1;
    return [id, root.getAttribute('data-who') || '', root.hasAttribute('data-extra') ? 1 : 0, vis ? 1 : 0, Math.round(rate * 10) / 10, Math.round(v[0])];
  });
  const hold = [...document.querySelectorAll('#svg [data-hold]')].some(e => shown(e) > 0.02);
  return [moving, figs, hold ? 1 : 0];
}"""

PACE_KEYS = "() => window.__pace ? [...window.__pace.keys.values()] : []"


def sample_film(film, total, step, pace=True):
    """One pass over the film every `step` seconds for the text sheet and the pacing tests: at each sample the reading
    text on screen (TEXT_STATE) and, with pace, what moved or entered since the last sample (PACE_SAMPLE). Returns the
    samples [(t, text rows, pace or None)] and every pose key sequence the page passed to F.at or F.closeup's at."""
    import render
    out = []
    with render.Film(film) as f:
        if pace:
            f.page.evaluate(PACE_INSTALL)
        for k in range(int(total / step) + 1):
            t = round(k * step, 3)
            f.page.evaluate(f"window.seek({min(t, total - 1e-3):.3f})")
            out.append((t, f.page.evaluate(TEXT_STATE), f.page.evaluate(PACE_SAMPLE, step) if pace else None))
        keys = f.page.evaluate(PACE_KEYS) if pace else []
    return out, keys


def text_holds(samples, total, chapter_ends, step):
    """The on-screen text sheet: every block of reading text, when it is fully shown in its final state, and how long it
    holds there. Sampled every `step` seconds (sample_film). A text element's final state is its last shown string and size; it is
    settled from the first sample after which it stays fully shown, with that string and size, moving under 60 px/s in
    its scene (a bubble bobbing with its speaker still reads). Lines of one wrapped block (same segment and font size,
    stacked within 1.7 line heights and overlapping sideways, settling within 1 s and leaving within 0.3 s of each other) are read as one block,
    held from its last line's settle. A block needs max(2 s, 1 s + words / 3) on screen, at least 3 s when marked
    data-payoff (or its segment has "payoff": true) or still up on the last frame before a chapter cut."""
    runs, live = [], {}
    for t, rows, _ in samples:
        now = {r[0]: r for r in rows}
        for tid in list(live):
            if tid not in now:
                runs.append(live.pop(tid) + [t])
        for tid, r in now.items():
            live.setdefault(tid, [r[1], []])[1].append((t, r))
    runs += [v + [total] for v in live.values()]
    lines = []
    for seg, samples, t_end in runs:
        # readable stretches: fully shown, moving under 60 px/s, one string and one size throughout; the last one is the
        # text's final state, held from its first sample until the sample after its last
        stretch, last = None, None
        for i, (t, r) in enumerate(samples):
            nb = samples[i - 1] if i else (samples[1] if len(samples) > 1 else None)
            speed = math.hypot(nb[1][4] - r[4], nb[1][5] - r[5]) / abs(nb[0] - t) if nb else 0
            ok = r[3] and speed < 60
            same = stretch and r[2] == stretch["r"][2] and abs(r[6] - stretch["r"][6]) <= max(2, 0.02 * r[6]) \
                and abs(r[7] - stretch["r"][7]) <= max(2, 0.02 * r[7])
            if ok and same:
                stretch["i1"] = i
            elif ok:
                stretch = {"r": r, "i0": i, "i1": i}
                last = stretch
            else:
                stretch = None
        if last:
            r, k = last["r"], last["i1"]
            settle, end = samples[last["i0"]][0], samples[k + 1][0] if k + 1 < len(samples) else t_end
        else:
            r, settle, end = samples[-1][1], None, t_end
        lines.append({"seg": seg, "text": r[2], "t": settle, "end": end, "x": r[4], "w": r[6], "y": r[5], "h": r[7], "size": r[9],
                      "payoff": bool(r[8]), "first": samples[0][0], "lines": [r[2]]})
    # wrapped lines of one block read together
    lines.sort(key=lambda x: (x["seg"], x["first"], x["y"]))
    blocks = []
    fits = lambda b, ln: (b["seg"] == ln["seg"] and b["size"] == ln["size"] and abs(b["end"] - ln["end"]) <= 0.3
                          and 0 < ln["y"] - b["y"] <= 1.7 * ln["size"] and ln["t"] is not None and b["t"] is not None
                          and abs(ln["t"] - b["t0"]) <= 1.0
                          and min(b["x"] + b["w"], ln["x"] + ln["w"]) - max(b["x"], ln["x"]) >= 0.3 * min(b["w"], ln["w"]))
    for ln in lines:
        b = next((b for b in reversed(blocks) if fits(b, ln)), None)
        if b:
            b["text"] += " " + ln["text"]; b["lines"].append(ln["text"]); b["y"] = ln["y"]; b["x"], b["w"] = ln["x"], ln["w"]
            b["t"] = max(b["t"], ln["t"]); b["end"] = min(b["end"], ln["end"]); b["payoff"] |= ln["payoff"]
        else:
            blocks.append(dict(ln, t0=ln["t"]))
    sheet = []
    for b in sorted(blocks, key=lambda x: (x["t"] if x["t"] is not None else x["first"], x["seg"])):
        words = len(re.findall(r"[A-Za-z0-9$%][\w'$%.,-]*", b["text"]))
        hold = 0.0 if b["t"] is None else b["end"] - b["t"]
        need, why = max(2.0, 1.0 + words / 3), ""
        if b["payoff"]:
            need, why = max(need, 3.0), "payoff"
        elif b["t"] is not None and any(b["t"] < c <= b["end"] + step + 1e-6 for c in chapter_ends):
            need, why = max(need, 3.0), "before a chapter cut"
        sheet.append({"seg": b["seg"], "text": b["text"], "t": b["t"], "hold": hold, "need": need, "why": why, "words": words,
                      "lines": b["lines"], "end": b["end"], "readable": hold})
    return sheet


# Contrast probe: the client boxes of a block's lines on screen now, found by segment and string, and a switch to hide
# or show them again. Used twice per moment: a picture with the text, then one with it hidden.
TEXT_BOXES = """([seg, lines, hide]) => {
  const st = document.getElementById('stage').getBoundingClientRect(), out = [];
  window.__hid = window.__hid || [];
  if (hide === 0) { window.__hid.forEach(e => e.style.visibility = ''); window.__hid = []; return out; }
  // how opaque the text is drawn: its own fill-opacity times every ancestor's opacity up to the drawing (a fade-out is a
  // group's opacity, which the element's own style doesn't show)
  const op = n => { let v = 1; for (let e = n; e && e.nodeType === 1 && e.id !== 'svg'; e = e.parentNode) {
    const cs = getComputedStyle(e); if (cs.display === 'none' || cs.visibility === 'hidden') return 0; v *= +cs.opacity; }
    return v * +getComputedStyle(n).fillOpacity; };
  document.querySelectorAll('#svg text').forEach(el => {
    const s = el.textContent.trim(), g = el.closest('[data-seg]');
    if (!lines.includes(s) || (g ? g.getAttribute('data-seg') : '') !== seg) return;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return;
    if (hide === 1) { el.style.visibility = 'hidden'; window.__hid.push(el); }
    out.push([Math.max(st.left, Math.floor(r.left)), Math.max(st.top, Math.floor(r.top)),
              Math.min(st.right, Math.ceil(r.right)), Math.min(st.bottom, Math.ceil(r.bottom)), hide === 2 ? op(el) : 1]);
  });
  return out;
}"""
FADED = 0.95         # text drawn below this opacity is fading in or out: the contrast sampler skips that moment


def _hex_rgb(c):
    import re
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", str(c or "").strip())
    return tuple(int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4)) if m else None


def _wcag(a, b):
    def lum(rgb):
        lin = [(v / 255) / 12.92 if v / 255 <= 0.04045 else ((v / 255 + 0.055) / 1.055) ** 2.4 for v in rgb]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


PALETTE_TEXT = {"ACCENT": "the big stat numbers and accent words", "INK": "labels and body text", "GREY": "stat notes and asides"}


def palette_warnings(palette, floor=3.0):
    """Warnings for palette text colours under `floor`:1 (WCAG) against BG, each with the nearest colour of the same hue
    that reaches floor + 0.5, found by moving it toward black on a light ground or toward white on a dark one. A stat in
    #C8922E on #E6DAC3 measured 2.0:1 and only check's frame sampler caught it, after the timeline (Survive Year 1,
    2026-11-27). Colours that aren't plain #rrggbb (a gradient, a name) are skipped; the house colours fill any key left
    out, read from kit.js so there is one source."""
    import re
    house = dict(re.findall(r'(\w+): "(#[0-9a-fA-F]{6})"', re.search(r"const HOUSE = \{([^}]*)\}", (HERE / "kit.js").read_text()).group(1)))
    pal = {**house, **{k: v for k, v in (palette or {}).items() if v}}
    bg, out = _hex_rgb(pal.get("BG")), []
    if not bg:
        return out
    toward = (0, 0, 0) if _wcag(bg, (0, 0, 0)) > _wcag(bg, (255, 255, 255)) else (255, 255, 255)
    for k, use in PALETTE_TEXT.items():
        c = _hex_rgb(pal.get(k))
        if not c or _wcag(c, bg) >= floor:
            continue
        fix = None
        for i in range(1, 101):
            m = tuple(round(c[j] + (toward[j] - c[j]) * i / 100) for j in range(3))
            if _wcag(m, bg) >= floor + 0.5:
                fix = "#%02X%02X%02X" % m
                break
        out.append(f"palette {k} {pal[k]} is {_wcag(c, bg):.1f}:1 against BG {pal['BG']} (needs {floor:g}:1): {use} will "
                   f"read faint" + (f"; {fix} keeps its hue at {_wcag(_hex_rgb(fix), bg):.1f}:1" if fix else ""))
    return out


def _pngs(pngs, w, h):
    """Decode same-sized PNGs (bytes) in one ffmpeg run: a list of H x W x 3 uint8 arrays."""
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-f", "image2pipe", "-c:v", "png", "-i", "-", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], input=b"".join(pngs), capture_output=True, check=True).stdout
    return list(np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3))


def text_contrast(film, sheet, floor, samples=3):
    """Measure each block's contrast (readcheck.worst_contrast) at `samples` moments through its hold, and set its
    "contrast" (worst ratio seen), "contrast_t" (when) and "readable" (the hold times the share of measured moments at
    or above `floor`): text lost against its ground for part of its hold is readable only for the rest."""
    import readcheck
    import render
    with render.Film(film) as f:
        for b in sheet:
            if b["t"] is None or b["hold"] <= 0:
                b["contrast"] = None
                continue
            span = max(0.0, b["hold"] - 0.05)
            ts = [b["t"] + span * k / max(1, samples - 1) for k in range(samples)] if samples > 1 else [b["t"]]
            worst, when, ok, seen = None, None, 0, 0
            # a moment mid-fade measures the fade, not the colours (chart labels at 1.0-2.6:1 and map tags on their exit,
            # Survive Year 1, 2026-11-27), so it is left out like a moment the text wasn't found. Text that is never drawn
            # fully opaque (a label set translucent on purpose) is still measured, at its most opaque moment
            ops = []
            for t in ts:
                f.seek(t)
                bx_ = [x for x in f.page.evaluate(TEXT_BOXES, [b["seg"], b["lines"], 2]) if x[2] - x[0] >= 2 and x[3] - x[1] >= 2]
                ops.append(min((x[4] for x in bx_), default=None))
            seenop = [o for o in ops if o is not None]
            keep = [t for t, o in zip(ts, ops) if o is not None and o >= FADED]
            if not keep and seenop:
                keep = [ts[ops.index(max(seenop))]]
            b["faded_skipped"] = sum(1 for o in seenop if o < FADED) - (1 if seenop and max(seenop) < FADED else 0)
            for t in keep:
                f.seek(t)
                boxes = f.page.evaluate(TEXT_BOXES, [b["seg"], b["lines"], 2])
                boxes = [x for x in boxes if x[2] - x[0] >= 2 and x[3] - x[1] >= 2]
                if not boxes:
                    continue
                x0, y0 = min(x[0] for x in boxes), min(x[1] for x in boxes)
                x1, y1 = max(x[2] for x in boxes), max(x[3] for x in boxes)
                clip = {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}
                drawn = f.page.screenshot(type="png", clip=clip, animations="disabled")
                f.page.evaluate(TEXT_BOXES, [b["seg"], b["lines"], 1])
                behind = f.page.screenshot(type="png", clip=clip, animations="disabled")
                f.page.evaluate(TEXT_BOXES, [b["seg"], b["lines"], 0])
                A, B = _pngs([drawn, behind], x1 - x0, y1 - y0)
                r = min(readcheck.worst_contrast(A[bx[1] - y0:bx[3] - y0, bx[0] - x0:bx[2] - x0],
                                                 B[bx[1] - y0:bx[3] - y0, bx[0] - x0:bx[2] - x0])[0] for bx in boxes)
                ok += r >= floor; seen += 1
                if worst is None or r < worst:
                    worst, when = r, t
            b["contrast"], b["contrast_t"] = worst, when
            if seen:                        # a moment where the text wasn't found isn't counted either way
                b["readable"] = b["hold"] * ok / seen
    return sheet


def speech_spans(segs, sc, gap):
    """When the voice is speaking: each aligned word's span (a narrated segment with no word times counts as speaking
    from its start to its end less the pad), merged across pauses shorter than `gap` seconds."""
    narrated = {s["id"] for s in sc.get("segments", []) if s.get("narration")}
    iv = sorted([(w[1], w[2]) for s in segs for w in s.get("words") or []] +
                [(s["t0"], max(s["t0"], s["t1"] - 0.45)) for s in segs if not s.get("words") and s["id"] in narrated])
    out = []
    for a, b in iv:
        if out and a - out[-1][1] < gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def spoken(spans, a, b):
    """Seconds of `spans` inside a..b."""
    return sum(max(0.0, min(b, y) - max(a, x)) for x, y in spans)


# A fast-cut benchmark (an X article on 2.5D stickman channels, 2026-11-24, the author's own count of about 33,000 frames
# from 20 videos of one channel; unverified): something moves in 96.6% of frames, the picture changes about every 2.1 s,
# something new starts about every 1 s, narration runs about 3.67 words a second, and about 8 sound effects land in
# every 10 s. It is reported beside each film's numbers for comparison, not enforced: that format is short and fast, and
# our long-form explainers hold pictures longer on purpose (picture-truth.md: something new every 5-8 s).
PACE_BENCH = {"moving": 0.966, "shot_every": 2.1, "new_every": 1.0, "wps": 3.67, "sfx_10s": 8.0}
PACE_MERGE = 0.5    # new things starting within 0.5 s of the last counted one read as one event (a typed word, a burst)


def shot_starts(segs):
    """Where each shot starts: every segment, plus each camera key inside a set3d segment (a new framing of the set is a
    new shot, as a cut between panels is)."""
    out = []
    for s in segs:
        out.append(s["t0"])
        if s.get("type") == "set3d":
            out += [k["t"] for k in (s.get("data") or {}).get("camera", []) if isinstance(k.get("t"), (int, float)) and s["t0"] < k["t"] < s["t1"]]
    return sorted(out)


def pace_profile(beat_starts, moving, n_samples, words, talk_s, sfx_times, t_in, t_out, shot_starts=()):
    """The film's pace in the benchmark's terms: share of samples with something moving, median seconds between shots
    (segments), median and 90th-percentile seconds between new things (starts within PACE_MERGE of the last counted
    one merge into it), narration words a second over the time the voice runs, sound effects per 10 s (None until
    `mix` has written out/sfx_events.json)."""
    import statistics
    ev = []
    for t in sorted(beat_starts):
        if not ev or t - ev[-1] >= PACE_MERGE:
            ev.append(t)
    gaps = [b - a for a, b in zip(ev, ev[1:] + [t_out])]
    q = sorted(gaps)
    shots = sorted(shot_starts)
    sg = [b - a for a, b in zip(shots, shots[1:] + [t_out])]
    dur = max(1e-6, t_out - t_in)
    return {"moving": moving / n_samples if n_samples else None,
            "shot_every": statistics.median(sg) if sg else None,
            "new_every": statistics.median(gaps) if gaps else None,
            "new_every_p90": q[min(len(q) - 1, int(0.9 * len(q)))] if q else None,
            "wps": words / talk_s if talk_s > 0 else None,
            "sfx_10s": None if sfx_times is None else 10 * sum(1 for t in sfx_times if t_in <= t < t_out) / dur}


def pace_line(p):
    f = lambda v, fmt: "?" if v is None else fmt.format(v)
    b = PACE_BENCH
    return (f"pace profile: something moving {f(p['moving'], '{:.0%}')} of the time (fast-cut benchmark {b['moving']:.0%}); "
            f"a new shot every {f(p['shot_every'], '{:.1f}')} s median (benchmark {b['shot_every']} s); "
            f"something new every {f(p['new_every'], '{:.1f}')} s median, {f(p['new_every_p90'], '{:.1f}')} s at the 90th "
            f"percentile (benchmark ~{b['new_every']:.0f} s); narration {f(p['wps'], '{:.2f}')} words/s (benchmark "
            f"{b['wps']}); sound effects {f(p['sfx_10s'], '{:.1f}')} per 10 s (benchmark {b['sfx_10s']:.0f}"
            + ("" if p["sfx_10s"] is not None else "; run mix first") + "). For comparison only (critic.md, pace profile)")


def pacing(samples, keys, segs, sc, step, a):
    """Three pacing tests from one sample_film pass, each a flag for judgement rather than a failure.
    Dead air: the voice pauses (no word within 0.05 s before to 0.15 s after) while at most 3 words of reading text are
    on screen and nothing moves faster than an idle, for more than a.dead_air seconds. Pose variety: a pose key that
    repeats the key before it (a beat that changes nothing), and a figure (not a background extra) on screen moving no
    more than its idle (20 degree-like units a second; breathing and sway are about 8) while the voice speaks for more
    than a.idle_figure seconds of the stretch. Beat length: a stretch in which nothing new starts moving or enters (a
    figure starting a new move counts as new; a loop that keeps going does not) while the voice speaks for more than
    a.beat seconds of it, unless something marked data-hold is on screen in it."""
    report, flags = [], []
    t_in, t_out = segs[0]["t0"], segs[-1]["t1"]
    seg_at = lambda t: next((s["id"] for s in segs if s["t0"] <= t < s["t1"]), segs[-1]["id"])
    talk = speech_spans(segs, sc, 0.6)                                   # the voice running, over breaths between words
    words = speech_spans(segs, sc, 0.0)
    rows = [(t, txt, p) for t, txt, p in samples if p is not None and t_in <= t < t_out]
    first = samples[0][0] if samples else 0
    figs_moving, starts = {}, {}
    per = []
    for t, txt, (moving, figs, hold) in rows:
        live = set(map(str, moving))
        for fid, who, extra, vis, rate, x in figs:
            go = vis and (rate < 0 or rate > 20)
            if go and not figs_moving.get(fid):
                starts[fid] = starts.get(fid, 0) + 1
            figs_moving[fid] = go
            if go:
                live.add(f"f{fid}:{starts[fid]}")
        nwords = len(re.findall(r"[A-Za-z0-9$%][\w'$%.,-]*", " ".join({r[0]: r[2] for r in txt}.values())))
        per.append((t, live, figs, hold, nwords))
    # 1. dead air
    quiet = lambda t: not any(x - 0.05 <= t + step / 2 <= y + 0.15 for x, y in words)
    dead, run = [], None
    for t, live, figs, hold, nwords in per:
        if t > first and quiet(t) and nwords <= 3 and not live:
            run = run or [t, nwords]; run[1] = max(run[1], nwords); run.append(t)
        else:
            if run and run[-1] + step - run[0] > a.dead_air:
                dead.append((run[0], run[-1] + step, run[1]))
            run = None
    if run and run[-1] + step - run[0] > a.dead_air:
        dead.append((run[0], run[-1] + step, run[1]))
    dead = dead if a.dead_air > 0 else []
    report.append(f"dead air: {len(dead)} pause(s) over {a.dead_air:.1f} s with the voice stopped, 3 words or fewer on screen and nothing moving")
    for t0, t1, n in dead:
        flags.append(f"{seg_at(t0)}: dead air from {t0:.1f} to {t1:.1f} s ({t1 - t0:.1f} s): the voice pauses over "
                     + (f"{n} word{'s' if n != 1 else ''}" if n else "no words") + " on screen and nothing moving")
    # 2. pose variety: repeated keys, then figures holding idle through the voice
    reps = set()
    for k in keys:
        for (ta, pa), (tb, pb) in zip(k, k[1:]):
            if pa == pb and t_in <= tb < t_out:
                reps.add((seg_at(tb), pb if len(pb) < 40 else "the same pose", round(ta, 2), round(tb, 2)))
    idle, track = [], {}
    for t, live, figs, hold, nwords in per + [(t_out, set(), [], 0, 0)]:
        seen = set()
        for fid, who, extra, vis, rate, x in figs:
            if extra:
                continue
            seen.add(fid)
            r = track.get(fid)
            if vis and 0 <= rate <= 20 and not (r and t - r[1] > step * 1.5):
                track[fid] = [r[0] if r else t, t, who, x] if r else [t, t, who, x]
            else:
                if r:
                    idle.append(r)
                track.pop(fid, None)
        for fid in [f for f in track if f not in seen]:
            idle.append(track.pop(fid))
    idle = [(t0, t1 + step, who, x, spoken(talk, t0, t1 + step)) for t0, t1, who, x in idle]
    idle = [r for r in idle if r[4] > a.idle_figure] if a.idle_figure > 0 else []
    report.append(f"pose variety: {len(reps)} pose key(s) repeating the one before; {len(idle)} stretch(es) of a figure "
                  f"only idling while the voice speaks over {a.idle_figure:.0f} s")
    for sid, pose, ta, tb in sorted(reps, key=lambda r: r[3]):
        flags.append(f"{sid}: pose \"{pose}\" keyed at {tb:.1f} s right after the same pose at {ta:.1f} s: the beat changes nothing")
    for t0, t1, who, x, sp in idle:
        flags.append(f"{seg_at(t0)}: {who or f'the figure at x {x}'} only idles from {t0:.1f} to {t1:.1f} s "
                     f"while the voice speaks for {sp:.1f} s: give it a pose, a look or a move")
    # 3. beat length
    beats, known, b0, held = [], set(), None, False
    for t, live, figs, hold, nwords in per:
        if b0 is None or live - known:
            if b0 is not None:
                beats.append((b0, t, held))
            known, b0, held = set(live), t, bool(hold)
        else:
            held |= bool(hold)
    if b0 is not None:
        beats.append((b0, t_out, held))
    long = [(t0, t1, spoken(talk, t0, t1)) for t0, t1, held in beats if not held]
    long = [r for r in long if r[2] > a.beat] if a.beat > 0 else []
    report.append(f"beat length: {len(beats)} beat(s); {len(long)} with the voice running over {a.beat:.0f} s and nothing new moving or entering")
    ev = Path(a.film) / "out" / "sfx_events.json"
    try:
        sfx_t = [e[0] for e in json.loads(ev.read_text())] if ev.exists() else None
    except (ValueError, IndexError, TypeError):
        sfx_t = None
    nw = sum(len(s.get("words") or []) for s in segs)
    report.append(pace_line(pace_profile([b[0] for b in beats], sum(1 for r in per if r[1]), len(per), nw,
                                         sum(y - x for x, y in talk), sfx_t, t_in, t_out, shot_starts(segs))))
    for t0, t1, sp in long:
        flags.append(f"{seg_at(t0)}: one beat from {t0:.1f} to {t1:.1f} s with {sp:.1f} s of voice and nothing new "
                     f"moving or entering: add a beat, or mark it data-hold (segment \"steady\": true) if it is meant to hold")
    return report, flags


def overlaps(film, segs, step):
    """{(segment id, text, what covers it): [times]} for text painted over. Sampled where text_sizes samples (each
    segment's settled end and a beat after each word cue), 0.3 s after each of those, and every `step` seconds through
    the segment, so a card or a stamp that crosses words on its way in is seen as well as one that lands on them. A
    collision counts once it is seen at two samples at most 0.35 s apart: a stamp slamming down or a card flying past
    for a frame or two is not reported, anything that stays is. Text cut off by the frame edge (or outside a Short's
    safe area, see EDGE) is sampled and judged the same way; its `what` starts with "cut off" or "outside"."""
    import render
    hits = {}
    with render.Film(film) as f:
        # the counter's heroes hold big at the centre for about a second: sample each one twice while it holds, so a hero
        # landing on a callout is seen (the episode thread, 2026-11-28, v31a)
        heroes = f.page.evaluate("window.STUDIO.heroes || []")
        for s in segs:
            rest = {max(s["t0"] + 0.5, s["t1"] - 0.4)}
            rest |= {c["t"] + 0.7 for c in walk_cues(s.get("data")) if s["t0"] < c["t"] + 0.7 < s["t1"] - 0.3}
            ts = rest | {r + 0.3 for r in rest if r + 0.3 < s["t1"] - 0.05}
            for h in heroes:
                ts |= {x for x in (h["t0"] + 0.45, h["t0"] + 0.75) if x < h["fly"] and s["t0"] + 0.05 < x < s["t1"] - 0.05}
            if step:
                ts |= {s["t0"] + 0.25 + k * step for k in range(int((s["t1"] - s["t0"] - 0.5) / step) + 1)}
            seg = {}
            for t in sorted(round(x, 3) for x in ts):
                f.page.evaluate(f"window.seek({t:.3f})")
                for txt, what, _ in f.page.evaluate(OVERLAP) + f.page.evaluate(EDGE) + f.page.evaluate(COVERS):
                    seg.setdefault((s["id"], txt, what), []).append(t)
            for key, seen in seg.items():
                if any(0 < b - a <= 0.35 for a, b in zip(seen, seen[1:])):
                    hits[key] = seen
    return hits


def text_sizes(film, segs):
    """The on-screen size of every visible piece of text, per segment: sampled once the segment's reveals are done and
    a beat after each of its word cues (a bubble or a notification can come and go mid-segment)."""
    import render
    seen = {}
    with render.Film(film) as f:
        for s in segs:
            ts = {max(s["t0"] + 0.5, s["t1"] - 0.4)}
            ts |= {c["t"] + 0.7 for c in walk_cues(s.get("data")) if s["t0"] < c["t"] + 0.7 < s["t1"] - 0.3}
            for t in sorted(ts):
                f.page.evaluate(f"window.seek({t:.3f})")
                for txt, px, aux in f.page.evaluate(MEASURE_TEXT):
                    key = (s["id"], txt)
                    if key not in seen or px < seen[key][1]:
                        seen[key] = (s["type"], px, aux)
        w = f.w
    return seen, w


def walk_cues(d):
    """Every object in a segment's data that `timeline` gave a word time."""
    if isinstance(d, dict):
        if isinstance(d.get("t"), (int, float)) and "at" in d:
            yield d
        for v in d.values():
            yield from walk_cues(v)
    elif isinstance(d, list):
        for v in d:
            yield from walk_cues(v)


# Every data-icon fully shown now (0.95 opacity or more after its parents), with its box on the frame, and whether an
# end screen (a group marked data-endscreen) is fully shown
ICONS = """() => {
  const shown = n => { let o = 1; for (; n && n.nodeType === 1 && n.id !== 'svg'; n = n.parentNode) {
      const cs = getComputedStyle(n); if (cs.display === 'none' || cs.visibility === 'hidden') return 0; o *= +cs.opacity; } return o; };
  const st = document.getElementById('stage').getBoundingClientRect();
  const icons = [...document.querySelectorAll('#svg [data-icon]')].map((e, i) => [i, e.getAttribute('data-icon') || 'icon', shown(e), e.getBoundingClientRect()])
    .filter(([, , o, r]) => o >= 0.95 && r.width > 0 && r.right > st.left && r.left < st.right && r.bottom > st.top && r.top < st.bottom)
    .map(([i, n, , r]) => [i, n, r.width, r.height]);
  return [icons, [...document.querySelectorAll('#svg [data-endscreen]')].some(e => shown(e) >= 0.95) ? 1 : 0];
}"""


def end_screen(film, floor, step=0.5):
    """(report, flags) for icons and the end screen. Sampled every `step` s: each icon marked data-icon at its largest
    while fully shown must be at least `floor` px on its longer side (the drawn symbol's, not its 24-unit box) when the frame is shown 360 px wide (a 16:9 video
    on a phone held upright). The end screen (data-endscreen), at the middle of its last fully shown stretch, is saved
    360 px wide as review/endscreen-360.png for a look at phone size."""
    import render
    best, runs, on = {}, [], None
    with render.Film(film) as f:
        k360 = 360 / f.w
        for k in range(int(f.dur / step) + 1):
            t = min(k * step, f.dur - 1e-3)
            f.seek(t)
            icons, end = f.page.evaluate(ICONS)
            for i, name, w, h in icons:
                if i not in best or max(w, h) > best[i][1]:
                    best[i] = (name, max(w, h), t)
            if end and on is None:
                on = t
            elif not end and on is not None:
                runs.append((on, t - step)); on = None
        if on is not None:
            runs.append((on, f.dur))
        report, flags = [], []
        if runs:
            t = round(sum(runs[-1]) / 2, 3)
            png = f.shot(t)
            out = film / "review" / "endscreen-360.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", "-", "-vf", "scale=360:-2:flags=lanczos", str(out)], input=png, check=True)
            report.append(f"end screen: {out.relative_to(film)} at {t:.1f} s, 360 px wide: look at it at phone size")
    small = [(n, px * k360, t) for n, px, t in best.values() if px * k360 < floor]
    if best:
        report.append(f"icons: {len(best)} marked, {len(small)} under {floor:.0f} px at 360 px wide")
    flags += [f"icon \"{n}\" at {t:.1f} s is {px:.0f} px at 360 px wide (needs {floor:.0f}): draw it so its longer side is at least "
              f"{floor / k360:.0f} px on this frame" for n, px, t in small]
    return report, flags


def join_gaps(segs, hold):
    """The voice gap at each join between spoken segments (last word of one to first word of the next), and a flag for
    each transition whose gap is under the median continuation gap plus `hold` (less 0.02 s of rounding). With no
    continuation joins to measure, the usual gap is the smallest transition gap minus `hold`, so only an uneven
    transition is flagged. Segments made before joins existed count as continuations unless a chapter starts there."""
    gaps = []
    for p, s in zip(segs, segs[1:]):
        if p.get("words") and s.get("words"):
            j = s.get("join") or ("transition" if s.get("chapter") and s.get("chapter") != p.get("chapter") else "continue")
            gaps.append((s["id"], j, s["words"][0][1] - p["words"][-1][2]))
    cont = sorted(g for _, j, g in gaps if j == "continue")
    trans = [(sid, g) for sid, j, g in gaps if j == "transition"]
    if not gaps or not hold:
        return [], []
    base = cont[len(cont) // 2] if cont else min(g for _, g in trans) - hold
    short = [(sid, g) for sid, g in trans if g < base + hold - 0.02]
    report = [f"joins: {len(cont)} continuation(s), usual voice gap {base:.2f} s; {len(trans)} transition(s), "
              f"{len(short)} under {base + hold:.2f} s"]
    flags = [f"{sid}: transition voice gap {g:.2f} s, under the usual {base:.2f} s plus {hold:.2f} s: hold the last picture "
             f"before it longer (extra on the segment before, or \"join\": \"continue\" if it isn't a new place or subject)"
             for sid, g in short]
    return report, flags


# ── dead space (owner, 2026-11-27): a still with room in it while the voice says a number, a quote or a named source
# gets that fact animated into the shot (panel callouts). `deadspace` reports it per beat; `check` flags each beat where
# there is room, something worth showing, and nothing in the shot showing it. A beat can opt out with "deadspace":
# "<why>" (e.g. "the quiet sky is the point of the shot").
DEADSPACE_ROOM = (380, 170)      # screen px: about the smallest callout card (a value and a one-line label at reading size)


def page_warnings(film):
    """STUDIO.warnings from the page: data the page could not use as given (a bad screen quad, a panel fx it doesn't
    know, a callout with nowhere quiet to go)."""
    import render
    with render.Film(film) as f:
        return f.page.evaluate("window.STUDIO.warnings || []")


def deadspace_scan(film, segs, sc):
    """One row per panel beat: {"id", "t0", "region" (deadspace.largest_quiet or None), "candidates" (each with
    "shown"), "opt_out"}, with the camera read from the page so the region is what the frame shows the whole beat."""
    import render, deadspace
    narr = {x["id"]: x.get("narration") or "" for x in sc.get("segments", [])}
    rows = []
    with render.Film(film) as f:
        for sg in segs:
            d = sg.get("data") or {}
            if sg.get("type") != "panel" or not d.get("image") or not (film / d["image"]).exists():
                continue
            cam = f.page.evaluate("""([id, t0, t1]) => { const c = (window.STUDIO.panelCam || {})[id]; if (!c) return null;
                return { iw: c.iw, ih: c.ih, w: c.w, h: c.h, v: [...Array(8).keys()].map(j => { const q = c.at(t0 + (t1 - t0) * j / 7); return [q.z, q.cx, q.cy]; }) }; }""",
                                  [sg["id"], sg["t0"], sg["t1"]])
            if not cam:
                continue
            qm = d.get("quiet") or deadspace.quiet_map(film / d["image"])
            region = deadspace.largest_quiet(qm, cam["v"], cam["iw"], cam["ih"], cam["w"], cam["h"], d.get("focus"), d.get("keep_clear") or [])
            cands = deadspace.candidates(narr.get(sg["id"], ""), sg.get("source"))
            for c in cands:
                c["shown"] = bool(c.get("from")) or deadspace.covered(c, sg)   # the source line is on screen already
            rows.append({"id": sg["id"], "t0": sg["t0"], "region": region, "candidates": cands, "opt_out": sg.get("deadspace")})
    return rows


# ── spoken numbers on screen (editing-quality study, 2026-11-27) ──
NUM_BEFORE, NUM_AFTER, NUM_EARLY = 0.3, 0.7, 1.0


def number_sync(film, segs):
    """[{"seg", "text", "value", "t", "state"}] for every amount, count or duration spoken (numbersync.spoken), where
    state is "ok" (on screen from NUM_BEFORE s before its word to NUM_AFTER s after), "missing", "early" (already on
    screen NUM_EARLY s before its word, and not said earlier in the film; never the second end of a range, which lands
    with its first) or "opted out" (the segment's "unshown": true,
    a reason, or a list of the values it leaves off screen)."""
    import numbersync
    import render
    rows, said = [], []
    with render.Film(film) as f:
        def seen(t):
            f.seek(max(0.0, t))
            return numbersync.on_screen(f.page.evaluate(VISIBLE_TEXT))
        for sg in segs:
            opt = sg.get("unshown")
            for a in numbersync.spoken(sg.get("words") or []):
                row = {"seg": sg["id"], "text": a["text"].strip(".,;:"), "value": a["value"], "t": a["t"]}
                if opt is True or isinstance(opt, str) or (isinstance(opt, list) and any(
                        numbersync.matches(a["value"], numbersync.on_screen([str(x)])) for x in opt)):
                    row["state"] = "opted out"
                else:
                    win = [a["t"] - NUM_BEFORE + k * (NUM_BEFORE + NUM_AFTER) / 5 for k in range(6)]
                    hit = any(numbersync.matches(a["value"], seen(t)) for t in win)
                    early = (a["t"] >= NUM_EARLY and a.get("range_of") is None     # a range lands on its first end
                             and not any(numbersync.matches(a["value"], {v}) for v in said)
                             and numbersync.matches(a["value"], seen(a["t"] - NUM_EARLY)))
                    row["state"] = "early" if early else "ok" if hit else "missing"
                said.append(abs(a["value"]))
                rows.append(row)
    return rows


PACE_GAP = 4.0      # s (pace.py): the beat-sync check flags narration this long with nothing new landing


def pace_flags(segs, gap=PACE_GAP):
    """(report lines, flags) for beat sync (pace.py): stretches of narration with nothing new landing, and reveals that
    land at a default time while their own words are said in the segment."""
    import pace
    med, near = pace.rhythm(segs)
    lines, flags = [], []
    if med is not None:
        lines.append(f"beat sync: something new every {med:g} s (median while the voice runs); {near:.0%} of spoken time is "
                     f"within {pace.AIM:g} s of a landing")
    for s in segs:
        for g in pace.gaps(s, gap):
            flags.append(f"{g['seg']}: {g['len']:g} s with nothing new on screen ({g['t0']:.1f}-{g['t1']:.1f} s, \"{g['said'][:80]}\")"
                         + (f"; land something on \"{g['suggest']}\"" if g["suggest"] else "") + ". `longform.py pace` lists them all")
        for u in pace.uncued(s):
            if u["match"]:
                flags.append(f"{u['seg']}: {pace.label(u['path'])} (\"{u['text']}\") appears at a default time, but its words are "
                             f"said: cue it with \"at\": \"{u['match']}\" (`longform.py pace --apply` does it)")
        for e in pace.cued_early(s):
            flags.append(f"{e['seg']}: {e['label']} (\"{e['text']}\") lands on \"{e['cue']}\" at {e['t']:.1f} s, before its number is "
                         f"said (\"{e['number']}\" at {e['said_t']:.1f} s): cue it on \"{e['number']}\" unless the early landing is meant")
    return lines, flags


def cmd_finish(a):
    """review/finish-<t>.png: the film's finish (script.json "finish") before and after, side by side, at a few times.
    The dashboard and contact sheets show the clean page, so this is where the finished look is judged before a render."""
    import io
    import render
    from PIL import Image
    film = Path(a.film).resolve()
    tl = require_fresh_timeline(film, "finish")
    if not tl.get("finish"):
        raise SystemExit('this film has no "finish" in script.json (then run timeline)')
    times = a.at or [round(tl["duration"] * k / 4, 2) for k in (1, 2, 3)]
    (film / "review").mkdir(exist_ok=True)
    with render.Film(film) as f:
        for t in times:
            done, raw = f.frame(t, FPS)
            A, B = Image.open(io.BytesIO(raw)), Image.open(io.BytesIO(done))
            sheet = Image.new("RGB", (A.width * 2 + 16, A.height), (20, 20, 20))
            sheet.paste(A, (0, 0)); sheet.paste(B, (A.width + 16, 0))
            dest = film / "review" / f"finish-{t:g}.png"
            sheet.save(dest)
            print(f"{t:g} s: before | after -> {dest}")


def cmd_pace(a):
    """review/pace.md: every gap in the narration with nothing new on screen (with a suggested cue word) and every reveal
    that isn't on a word, and every number cued to a word said before it. --apply writes the matched words into
    script.json as "at" (a copy goes to script.json.bak), then run timeline; it leaves cues already set alone."""
    import pace
    film = Path(a.film).resolve()
    tl = require_fresh_timeline(film, "pace")
    segs = tl["segments"]
    med, near = pace.rhythm(segs)
    gs = [g for s in segs for g in pace.gaps(s, a.gap)]
    us = [u for s in segs for u in pace.uncued(s)]
    out = [f"# Beat sync\n", f"Something new lands every {med} s (median while the voice runs); {near:.0%} of spoken time is within "
           f"{pace.AIM:g} s of a landing. Aim: every 2-3 s.\n" if med is not None else "No narrated segments.\n",
           f"## Gaps over {a.gap:g} s ({len(gs)})\n", "| segment | from | to | s | said | cue on |", "|---|---|---|---|---|---|"]
    out += [f"| {g['seg']} | {g['t0']:.1f} | {g['t1']:.1f} | {g['len']:g} | {g['said'][:90]} | {g['suggest'] or ''} |" for g in gs]
    out += ["", f"## Reveals not on a word ({len(us)})\n", "| segment | item | text | said as |", "|---|---|---|---|"]
    out += [f"| {u['seg']} | {pace.label(u['path'])} | {u['text']} | {u['match'] or '(not said)'} |" for u in us]
    es = [e for s in segs for e in pace.cued_early(s)]
    out += ["", f"## Numbers cued before they are said ({len(es)})\n", "| segment | item | text | cued on | at | number said | at |",
            "|---|---|---|---|---|---|---|"]
    out += [f"| {e['seg']} | {e['label']} | {e['text']} | {e['cue']} | {e['t']:.1f} | {e['number']} | {e['said_t']:.1f} |" for e in es]
    (film / "review").mkdir(exist_ok=True)
    (film / "review" / "pace.md").write_text("\n".join(out) + "\n")
    print("\n".join(out[:2]).strip())
    print(f"{len(gs)} gap(s), {len(us)} uncued reveal(s), {len(es)} number(s) cued early: {film / 'review' / 'pace.md'}")
    if a.apply:
        sp = film / "script.json"
        sc = json.loads(sp.read_text())
        by = {x["id"]: x for x in sc["segments"]}
        n = 0
        for u in us:
            if u["match"] and pace.set_at((by.get(u["seg"]) or {}).get("data"), u["path"], u["match"]):
                n += 1
        if n:
            shutil.copy(sp, film / "script.json.bak")
            sp.write_text(json.dumps(sc, indent=1, ensure_ascii=False) + "\n")
            print(f"cued {n} reveal(s) in script.json (previous copy: script.json.bak); run timeline")
        else:
            print("nothing to cue: script.json unchanged")
        if es:
            print(f"{len(es)} number(s) land on a word said before them: not changed (pace.md lists them)")


def number_flags(film, segs):
    """(report line, flags) for spoken numbers and the counter's word cues."""
    flags = []
    for sg in segs:
        c = sg.get("counter")
        if isinstance(c, dict) and c.get("t") is None and sg.get("words"):
            flags.append(f"{sg['id']}: the counter card changes at the segment's start, not on a word; give it \"at\" "
                         f"(the word its value is said on)")
    rows = number_sync(film, segs)
    if not rows:
        return None, flags
    n = {k: sum(r["state"] == k for r in rows) for k in ("ok", "missing", "early", "opted out")}
    line = (f"spoken numbers on screen at their word ({NUM_BEFORE:g} s before to {NUM_AFTER:g} s after): {n['ok']} of "
            f"{len(rows) - n['opted out']}" + (f", {n['opted out']} opted out" if n["opted out"] else ""))
    for r in rows:
        if r["state"] == "missing":
            flags.append(f"{r['seg']}: \"{r['text']}\" is said at {r['t']:.1f} s but isn't on screen then. Put it in the "
                         f"shot on its word (a callout, the counter with \"at\", a screen line), or opt out with "
                         f"\"unshown\": [\"{r['text']}\"] or a reason")
        elif r["state"] == "early":
            flags.append(f"{r['seg']}: \"{r['text']}\" is on screen {NUM_EARLY:g} s before it's said at {r['t']:.1f} s; "
                         f"land it on its word")
    return line, flags


# ── the intro (owner's intro rules, 2026-11-27, from a study of a YouTube strategist's intro system) ──
INTRO_MAX = 45.0     # seconds: the intro states the promise, opens its gaps and gets out
MUTE_S = 5.0         # the first seconds must name the subject with the sound off
INTRO_WORDS = re.compile(r"\b(intro|cold open|opening|hook)\b", re.I)
VISIBLE_TEXT = """() => { const out = [];
  document.querySelectorAll('#svg text').forEach(el => { let o = 1;
    for (let n = el; n && n.nodeType === 1 && n.id !== 'svg'; n = n.parentNode) { const cs = getComputedStyle(n);
      if (cs.display === 'none' || cs.visibility === 'hidden') { o = 0; break; } o *= +cs.opacity; }
    const r = el.getBoundingClientRect();
    if (o >= 0.5 && r.width > 2 && r.height > 2 && el.textContent.trim()) out.push(el.textContent.trim()); });
  return out; }"""


def intro_span(segs, chapters):
    """(t0, t1, why) of the film's intro: the segments marked "intro": true; else the first chapter when it is called
    intro, cold open, opening or hook; else the first chapter when the film has more than one; else None."""
    marked = [s for s in segs if s.get("intro")]
    if marked:
        return marked[0]["t0"], marked[-1]["t1"], "segments marked \"intro\""
    if chapters and (INTRO_WORDS.search(chapters[0].get("title", "")) or len(chapters) > 1):
        return chapters[0]["t0"], chapters[0]["t1"], f"its first chapter, \"{chapters[0]['title']}\""
    return None


def _subject_terms(sc):
    v = sc.get("subject")
    terms = [v] if isinstance(v, str) else list(v or [])
    norm = lambda x: re.sub(r"[\s\-]+", " ", str(x).lower()).strip()
    return [norm(t) for t in terms if str(t).strip()], norm


def mute_test(film, segs, sc, secs=MUTE_S):
    """What the first `secs` seconds show with the sound off: {"text": on-screen words, "shows": segments' declared
    "shows", "picture": scene types that draw a picture, "hit": the subject term found, or None}, and a contact strip
    of five frames at review/mute-0-5s.jpg for a blind read. The engine can't see what a still depicts, so a picture
    counts as showing the subject only through the segment's own "shows" line (what the still or clip depicts, written
    by whoever drew it) or the words on screen."""
    import io
    import render
    from PIL import Image
    terms, norm = _subject_terms(sc)
    early = [s for s in segs if s["t0"] < secs]
    shows = [str(s.get("shows") or (s.get("data") or {}).get("shows") or "") for s in early]
    pics = sorted({s["type"] for s in early if s["type"] not in TEXT_ONLY})
    words, shots = set(), []
    with render.Film(film) as f:
        for k in range(10):
            t = (k + 0.5) * secs / 10
            f.seek(t)
            words.update(f.page.evaluate(VISIBLE_TEXT))
            if k % 2 == 0:
                shots.append(Image.open(io.BytesIO(f.page.screenshot(type="jpeg", quality=85))).convert("RGB"))
    if shots:
        w = 384; h = round(w * shots[0].height / shots[0].width)
        strip = Image.new("RGB", (w * len(shots) + 8 * (len(shots) - 1), h), (255, 255, 255))
        for i, im in enumerate(shots):
            strip.paste(im.resize((w, h), Image.LANCZOS), (i * (w + 8), 0))
        (film / "review").mkdir(exist_ok=True)
        strip.save(film / "review" / "mute-0-5s.jpg", quality=88)
    hay = norm(" ".join(sorted(words)) + " " + " ".join(shows))
    # a term matches as a whole word, singular or plural ("porta potty" finds "porta potties")
    plural = lambda t: re.escape(t[:-1]) + r"(?:y|ies)" if t.endswith("y") else re.escape(t) + r"(?:e?s)?"
    hit = next((t for t in terms if t and re.search(r"\b" + plural(t) + r"\b", hay)), None)
    return {"text": sorted(words), "shows": [x for x in shows if x], "picture": pics, "hit": hit, "terms": terms}


def intro_flags(film, tl, sc, max_s=INTRO_MAX, secs=MUTE_S):
    """(report lines, flags) for the intro rules: the intro runs `max_s` seconds at most, and the first `secs` seconds
    name the subject with the sound off (a picture scene, and the subject in its "shows" line or on screen)."""
    segs, report, flags = tl["segments"], [], []
    if tl.get("format") == "9x16":
        return report, flags                    # a Short is all intro; its own rules are in shorts.md
    span = intro_span(segs, tl.get("chapters") or [])
    if span is None:
        flags.append("intro: can't tell where the intro ends; mark its segments \"intro\": true or give it its own first chapter")
    else:
        t0, t1, why = span
        report.append(f"intro: {t1 - t0:.1f} s ({why}), limit {max_s:g} s")
        if t1 - t0 > max_s + 1e-6:
            flags.append(f"intro runs {t1 - t0:.1f} s ({why}); the limit is {max_s:g} s. Cut context to the minimum the "
                         f"promise needs and move the rest into the first chapter")
    if secs <= 0:
        return report, flags
    m = mute_test(film, segs, sc, secs)
    if not m["terms"]:
        flags.append(f"mute test: script.json has no \"subject\" (the words a viewer would use for what the film is about, "
                     f"e.g. [\"porta-potty\", \"portable toilet\"]), so the first {secs:g} s can't be checked; "
                     f"see review/mute-0-5s.jpg")
        return report, flags
    report.append(f"mute test (first {secs:g} s): picture scenes {', '.join(m['picture']) or 'none'}; "
                  f"subject {'shown as ' + repr(m['hit']) if m['hit'] else 'not found'}; frames in review/mute-0-5s.jpg")
    if not m["picture"]:
        flags.append(f"mute test: the first {secs:g} s are words on a background; open on a picture of the subject")
    if not m["hit"]:
        flags.append(f"mute test: nothing in the first {secs:g} s names the subject ({', '.join(m['terms'])}) with the "
                     f"sound off. Open on a picture of it and say so in that segment's \"shows\" (what the still "
                     f"depicts), or put the word on screen. Look at review/mute-0-5s.jpg as a stranger would")
    return report, flags


def cmd_tsheet(a):
    """A T-sheet, one row per segment: its time, what is heard (narration) and what is seen (scene type, still or clip,
    "shows", on-screen text, the counter). Every spoken number is bolded in the audio column and every number on
    screen in the visual one; a spoken number with no match on screen is marked "not on screen". The intro alone
    (review/intro-tsheet.md) by default; --all for the whole film (review/tsheet.md)."""
    import deadspace
    import numbersync
    film = Path(a.film).resolve()
    sc = load_script(film)
    tlp = film / "timeline.js"
    tl = load_timeline(film) if tlp.exists() else None
    segs = tl["segments"] if tl else [dict(s, t0=None, t1=None) for s in sc["segments"]]
    if getattr(a, "all", False):
        rows = segs
    else:
        span = intro_span(segs, tl.get("chapters") if tl else []) if tl else None
        if tl and span:
            rows = [s for s in segs if s["t0"] < span[1] - 1e-6]
        else:
            first = sc["segments"][0].get("chapter") if sc["segments"] else None
            rows = [s for s in segs if s.get("intro")] or [s for s in segs if s.get("chapter", first) == first]
    by_id = {s["id"]: s for s in sc["segments"]}
    cell = lambda x: str(x).replace("|", "/").replace("\n", " ").strip()
    bold = lambda txt: numbersync.SHOWN.sub(lambda m: f"**{m.group(0).rstrip()}**" + (" " if m.group(0).endswith(" ") else ""), txt)
    title = "T-sheet" if getattr(a, "all", False) else "Intro T-sheet"
    out = [f"# {title}: {sc.get('title', film.name)}", "",
           f"Subject: {', '.join(_subject_terms(sc)[0]) or '(set \"subject\" in script.json)'}. "
           f"Intro limit {INTRO_MAX:g} s; the first {MUTE_S:g} s must name the subject with the sound off. Spoken numbers "
           f"are bold, and marked when nothing on screen in that segment shows them.", "",
           "| time | id | audio | visual |", "|---|---|---|---|"]
    missing = 0
    for s in rows:
        src = by_id.get(s["id"], s)
        d = src.get("data") or {}
        vis = [s["type"]] + [str(d[k]) for k in ("still", "clip", "image") if d.get(k)]
        if src.get("shows") or d.get("shows"):
            vis.append("shows: " + str(src.get("shows") or d.get("shows")))
        for k in ("text", "label", "caption_card"):
            if isinstance(d.get(k), str):
                vis.append(f"on screen: {d[k]}")
        if d.get("value") is not None:
            vis.append(f"on screen: {d.get('prefix', '')}{d['value']}{d.get('suffix', '')}")
        c = src.get("counter")
        if isinstance(c, dict):
            vis.append(f"counter: {c.get('value')}" + (f" at \"{c['at']}\"" if c.get("at") else " (no word cue)"))
        for co in d.get("callouts") or []:
            vis.append(f"callout: {co.get('value', '')} {co.get('text', '')}".strip())
        narr = str(src.get("narration", ""))
        shown_txt = deadspace.shown(src) + " " + " ".join(str(d.get(k, "")) for k in ("text", "label", "value"))
        nums = numbersync.spoken([[w, 0, 0] for w in narr.split()])
        have = numbersync.on_screen([shown_txt])
        gone = [n["text"].strip(".,;:") for n in nums if not numbersync.matches(n["value"], have)]
        missing += len(gone)
        audio = narr
        for n in nums:
            w = n["text"].strip(".,;:")
            if not re.search(r"\d", w):
                audio = audio.replace(w, f"**{w}**", 1)
        audio = bold(audio)
        if gone:
            audio += " (not on screen: " + ", ".join(gone) + ")"
        when = f"{s['t0']:.1f}-{s['t1']:.1f}" if s.get("t0") is not None else ""
        out.append(f"| {when} | {s['id']} | {cell(audio)} | {cell(bold('; '.join(vis)))} |")
    out += ["", f"{missing} spoken number(s) with nothing on screen showing them."]
    name = "tsheet.md" if getattr(a, "all", False) else "intro-tsheet.md"
    (film / "review").mkdir(exist_ok=True)
    (film / "review" / name).write_text("\n".join(out) + "\n")
    print(film / "review" / name)


def deadspace_flags(rows):
    out = []
    for r in rows:
        rg, todo = r["region"], [c for c in r["candidates"] if not c["shown"]]
        if r["opt_out"] or not rg or not todo or rg["px"][0] < DEADSPACE_ROOM[0] or rg["px"][1] < DEADSPACE_ROOM[1]:
            continue
        what = ", ".join(f"\"{c.get('value') or c.get('text')}\"" for c in todo[:3])
        out.append(f"{r['id']}: dead space ({rg['px'][0]}x{rg['px'][1]} px of quiet picture around {rg['pos']}) while the voice says "
                   f"{what}: put it in the shot with a panel callout (longform.md Panel callouts; `deadspace` lists ready ones), "
                   f"or set \"deadspace\": \"<why>\" on the beat")
    return out


def cmd_deadspace(a):
    """Per panel beat: the largest quiet region the camera shows throughout, and the numbers, quotes and named sources
    in the narration, as callouts ready to paste -> review/deadspace.json."""
    film = Path(a.film).resolve()
    rows = deadspace_scan(film, load_timeline(film)["segments"], load_script(film))
    out = []
    for r in rows:
        rg = r["region"]
        room = rg and rg["px"][0] >= DEADSPACE_ROOM[0] and rg["px"][1] >= DEADSPACE_ROOM[1]
        sug = [{k: v for k, v in dict(c, pos="auto").items() if k not in ("shown", "from") and v is not None}
               for c in r["candidates"] if not c["shown"]]
        out.append(dict(r, room=bool(room), suggest=sug if room else []))
        if a.json:
            continue
        head = f"{r['id']} ({r['t0']:.1f} s): " + (f"quiet {rg['px'][0]}x{rg['px'][1]} px at {rg['pos']}" if rg else "no quiet region")
        head += "" if room else " (too small for a card)"
        if r["opt_out"]:
            head += f"; opted out: {r['opt_out']}"
        print(head)
        for c in r["candidates"]:
            print(f"    {'shown ' if c['shown'] else 'ADD   ' if room else '      '}{c['kind']:6} {c.get('value') or c.get('text')}"
                  + (f"  (cue \"{c['at']}\")" if c.get("at") else "") + (f"  [{c['from']}]" if c.get("from") else ""))
    (film / "review").mkdir(exist_ok=True)
    (film / "review" / "deadspace.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    if a.json:
        print(json.dumps(out, indent=1, ensure_ascii=False))
    else:
        n = sum(1 for r in out if r["suggest"])
        print(f"\n{n} beat(s) with room and something to show -> review/deadspace.json (each \"suggest\" list pastes into the panel's \"callouts\")")


def cmd_check(a):
    film = Path(a.film).resolve()
    with JobStatus(film, "check"):
        _check(a, film)


def write_gate(film, page_clean, flags):
    """review/check-gate.json: the version of the film this check ran on and whether its page ran clean, which
    render requires (film_stamp)."""
    (film / "review").mkdir(parents=True, exist_ok=True)
    (film / "review" / "check-gate.json").write_text(json.dumps(
        {"stamp": film_stamp(film), "page_clean": page_clean, "flags": flags, "at": JobStatus._now()}, indent=1) + "\n")


def _check(a, film):
    """Dead air, text-only stretches, cue landings and one-frame flashes, measured from the timeline and the render."""
    import numpy as np
    tl = require_fresh_timeline(film, "check")
    require_fresh_mix(film, "check", tl)
    segs, total = tl["segments"], tl["duration"]
    seg_at = lambda t: next((s["id"] for s in segs if s["t0"] <= t < s["t1"]), "end")
    report, flags = [], []
    # 0. the page itself: any JavaScript error, console error or unhandled rejection at any segment's start, middle or
    # end stops the check here (the other tests would trip over the same error) and blocks render until it is fixed
    problems = page_gate(film, None)
    if problems:
        write_gate(film, False, len(problems))
        (film / "review" / "check.txt").write_text(f"page: {len(problems)} problem(s)\n" + "\n".join(problems) + "\n")
        raise SystemExit(f"check stopped: the page is not clean ({len(problems)} problem(s)):\n" + "\n".join(problems))
    flags += draft_stills(film, segs)
    aline, aflags = alignment_flags(film, load_script(film))
    if aline:
        report.append(aline); print(aline)
    flags += aflags
    pk, tj = film / "picks.json", film / "timeline.js"
    if pk.exists() and pk.stat().st_mtime > tj.stat().st_mtime:
        write_gate(film, False, 1)
        raise SystemExit("check stopped: the owner picked takes after the last timeline; run `timeline` so his picks are in the film")
    import review_state
    _notes = json.loads((film / "notes.json").read_text()).get("notes", []) if (film / "notes.json").exists() else []
    for sec in review_state.sections(film, _notes):
        if sec.get("stale"):
            flags.append(f"section \"{sec['title']}\" was locked by the owner and has changed since: tell him what changed and "
                         "ask him to re-lock it in the dashboard (agents never lock or pick)")
    waiting = [g["key"] for g in review_state.take_groups(film, load_script(film), load_timeline(film)) if g["pick"] is None]
    if waiting:
        report.append(f"takes waiting for the owner's pick in the dashboard: {', '.join(waiting)}")
    flags += [f"{sid}: its clip plays backwards or restarts (ping-pong or loop), which the owner's rule forbids; run timeline "
              "again (it now plays clips once and holds the last frame) and fix the panel" for sid in clip_backwards(segs)]
    drift, drift_warn = engine_drift(film, segs)
    if drift:
        write_gate(film, False, len(drift))
        raise SystemExit("check stopped: the film's page can't draw every segment:\n" + "\n".join(drift))
    flags += drift_warn
    report.append("page: clean (no errors at any segment's start, middle or end)")
    if not getattr(a, "no_listen", False):
        line, lf = listen_mix(film, tl)       # the served sound, heard: does it say the timeline's words?
        report.append(line); print(line); flags += lf
    flags += [f"page: {w}" for w in page_warnings(film)]
    # dead space: room in a still while the voice says something worth showing, and nothing showing it
    if a.deadspace:
        ds = deadspace_scan(film, segs, load_script(film))
        dflags = deadspace_flags(ds)
        report.append(f"dead space: {len(dflags)} of {len(ds)} panel beat(s) have quiet room and a number, quote or source not in the shot")
        flags += dflags
    # spoken numbers: each amount on screen at its word, and counter changes cued to words
    if a.numbers:
        nline, nflags = number_flags(film, segs)
        if nline:
            report.append(nline)
        flags += nflags
    # beat sync: something new lands on a word at least every --pace-gap s while the voice runs, and reveals land on words
    if a.pace_gap > 0:
        r, fl = pace_flags(segs, a.pace_gap)
        report += r; flags += fl
    # the intro: its length, and whether its first seconds name the subject with the sound off
    if a.intro_max > 0:
        r, fl = intro_flags(film, tl, load_script(film), a.intro_max, a.mute)
        report += r; flags += fl
    # 1. picture variety: how much of the film is words on a background. A text scene type counts unless what it draws
    # covers --picture-min of the frame (a kit pattern that turns it into motion graphics); measured from the page
    pic = picture_share(film, segs) if a.picture_min > 0 else {}
    drawn = {k for k, v in pic.items() if v >= a.picture_min}
    if drawn:
        report.append("text scene types drawn as pictures (not counted as text-only): "
                      + ", ".join(f"{k} {pic[k]:.0%}" for k in sorted(drawn)))
    text_only = lambda s: s["type"] in TEXT_ONLY and s["id"] not in drawn
    text_time = sum(s["t1"] - s["t0"] for s in segs if text_only(s))
    run_t, run0, worst = 0.0, None, (0.0, None, None)
    for s in segs:
        if text_only(s):
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
    # a run of map segments is one scene by design (one map, one camera flying across it): it is not a repeat
    repeats = [(types[i], segs[i]["id"]) for i in range(2, len(types)) if types[i] == types[i - 1] == types[i - 2] and types[i] != "map"]
    for ty, sid in repeats:
        flags.append(f"three {ty} scenes in a row ending at {sid}")
    # 1b. motion, predicted: a panel whose camera barely moves over a plain still fails final's motion check, and finding
    # out there costs a whole render (a padlock close-up and a dark room under the default push, Shoestring, 2026-11-25)
    if a.motion_estimate:
        floor0 = a.motion_floor
        vid = film / "out" / ("silent-raw.mp4" if tl.get("finish") else "silent.mp4")   # a finish's grain isn't motion
        import motioncheck      # here, not in the branch below: floor_of further down needs it too (a map film, 2026-11-28)
        if floor0 is None and vid.exists():                                # final's floor rides on the whole film's median,
            floor0 = motioncheck.check(motioncheck.windows(vid))[0]       # which the last render gives far better than the panels alone
        est = panel_motion(film, segs)
        mest = map_motion(film, segs)
        if floor0 is None and est and mest:     # no render yet: the floor comes from panels and maps together
            import numpy as np
            floor0 = motioncheck.floor_of([v for w in list(est.values()) + list(mest.values()) for v in w])
        mfloor, low = motion_flags(est, floor=floor0)
        mfloor2, mlow = motion_flags(mest, floor=floor0 if floor0 is not None else (mfloor if est else None))
        sest = stat_motion(film, segs)
        sfloor, slow = motion_flags(sest, floor=floor0 if floor0 is not None else (mfloor if est else None))
        if sest:
            report.append(f"stat scenes (rendered and measured like final): {len(sest)}, {len(slow)} at or near the floor {sfloor:.3f}")
        for sid, m in slow:
            how = "too little" if m < sfloor else "barely above it"
            if STAT_STILL.get(sid):
                flags.append(f"{sid.split()[0]}: stat scene moves {how} (quietest 2 s {m:.3f}, floor {sfloor:.3f}) because this "
                             f"film's page never drifts it: its film.html is older than the engine's stat drift, so drift settings "
                             f"do nothing. Run `longform.py init {film}` to refresh the engine files (script.json is kept), then timeline and check")
                continue
            if sid in STAT_TRAVEL:
                px, secs = STAT_TRAVEL[sid]
                went = (f"the page drifted it {px:.0f} px in {secs:.1f} s ({px / max(secs, 0.1):.1f} px/s at 1080 lines), so ")
                went += ("the drift is on but its frames change little: sparse or low-contrast ink against the ground moves "
                         "few pixels, so raise \"drift\", add \"notes\", or darken the text colours. "
                         if px / max(secs, 0.1) >= 8 else "it barely drifts. ")
                flags.append(f"{sid.split()[0]}: stat scene moves {how} (quietest 2 s {m:.3f}, floor {sfloor:.3f}); {went}"
                             f"Otherwise: \"stat_drift\": true in engine.json, \"drift\" in px/s on the segment or in its "
                             f"data (14 is the default, 20 for a sparse stat), or cut it shorter")
                continue
            flags.append(f"{sid.split()[0]}: stat scene moves {how} (quietest 2 s {m:.3f}, floor {sfloor:.3f}); turn on "
                         f"\"stat_drift\": true in engine.json (the scene drifts slowly from its count), raise \"drift\" (px/s, "
                         f"on the segment or in its data; 14 is the default, 20 for a sparse stat), add \"notes\" that land on "
                         f"later words, or cut it shorter")
        report.append(f"panel motion (predicted from each still and its camera; it reads low, so near the floor usually "
                      f"passes): floor {mfloor:.3f}, {len(low)} panel(s) at or near it")
        if mest:
            report.append(f"map holds (rendered and measured like final): {len(mest)} hold(s), {len(mlow)} at or near "
                          f"the floor {mfloor2:.3f}")
        for sid, m in mlow:
            how = "too little" if m < mfloor2 else "barely above it"
            flags.append(f"{sid}: moves {how} (quietest 2 s {m:.3f}, floor {mfloor2:.3f}); turn on \"map_drift\": true in "
                         f"engine.json (a slow push across every hold), or add a camera key or a piece inside the hold")
        for sid, m in low:
            how = "to move too little" if m < mfloor else "to move barely above it"
            flags.append(f"{sid}: panel predicted {how} (quietest 2 s {m:.3f}, floor {mfloor:.3f}); give it "
                         f"\"camera\": \"slow-push\" (a centred push to 1.14, the owner's preference), a pan with z at least "
                         f"1.12, or cut it shorter. A strong or off-centre push (z over {PUSH_MAX}) reads as zooming at "
                         f"nothing, so a quiet flag on a slow push is acceptable"
                         + ("; its clip ends before the segment does and holds its last frame: give it a camera move, "
                            "slow the clip with \"clip_speed\" under 1, or shorten the segment (never loop or reverse a clip)" if (next((x for x in segs if x["id"] == sid), {}).get("data") or {}).get("clip") else ""))
    for sid, z in strong_pushes(segs):
        flags.append(f"{sid}: its camera zooms to {z:.2f}, past {PUSH_MAX}: a strong push reads as zooming at nothing "
                     f"(owner's note); use \"camera\": \"slow-push\" (centred, to 1.14) and accept a quiet-panel flag")
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
        # 3b. the same cues timed on the video at full rate: how far each landing is from its word (cuecheck.py)
        if video.stat().st_mtime >= (film / "timeline.js").stat().st_mtime:
            import cuecheck
            lines, cprobs = cuecheck.run(film)
            report.extend(lines)
            flags.extend(f"beat: {p}" for p in cprobs)
        else:
            report.append("beat landings: out/silent.mp4 is older than timeline.js, so they weren't timed; render again")
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
        # 4b. flash rate (WCAG 2.3.1): more than 3 flashes in any second over a quarter of a 10-degree field
        if not a.no_flash_rate:
            import readcheck
            spans = readcheck.flash_spans(video)
            report.append(f"flash rate: {len(spans)} stretch(es) over 3 flashes a second (WCAG 2.3.1)")
            for t0, t1, nf, sh in spans:
                flags.append(f"flash rate: {nf} flashes in a second from {t0:.2f} to {t1:.2f} s ({seg_at(t0)}) over {sh:.0%}"
                             " of a 10-degree field; the limit is 3")
    # 5. text size: what a viewer must read has to survive a phone. A 16:9 video in a phone held upright is about 6.5 cm
    # wide, so 56 px of a 1920 frame is about 11 pt, the smallest comfortable reading size (scaled to the frame's width)
    if a.min_text:
        seen, w = text_sizes(film, segs)
        floor = a.min_text * w / 1920
        small = {}
        for (sid, txt), (ty, px, aux) in seen.items():
            if not aux and px < floor - 0.5:
                small.setdefault(sid, (ty, []))[1].append((px, txt))
        info = [px for (ty, px, aux) in seen.values() if not aux]
        report.append(f"text size: smallest reading text {min(info, default=0):.0f} px (floor {floor:.0f} px); "
                      f"{len(small)} segment(s) under it")
        for sid, (ty, items) in small.items():
            items.sort()
            flags.append(f"{sid} ({ty}): text under {floor:.0f} px: " + "; ".join(f"\"{t}\" {px:.0f} px" for px, t in items[:3])
                         + (f" and {len(items) - 3} more" if len(items) > 3 else ""))
    # voice pace: a clip spoken noticeably faster or slower than the rest was likely voiced at another speed
    flags += ["voice " + p for p in speed_outliers(film, segs)]
    # 6. overlap: reading text that something else is painted over, or two pieces of text on top of each other
    if a.overlap_step >= 0:
        found = overlaps(film, segs, a.overlap_step)
        edge = {k: v for k, v in found.items() if k[2].startswith(("cut off", "outside"))}
        hits = {k: v for k, v in found.items() if k not in edge}
        report.append(f"overlap: {len({k[:2] for k in hits})} piece(s) of text collided with something, in {len({k[0] for k in hits})} segment(s)")
        # 7. edge: reading text cut off by the frame edge, or outside a Short's safe area (sampled with the overlap test)
        report.append(f"edge: {len({k[:2] for k in edge})} piece(s) of text cut off at the frame or safe-area edge, in {len({k[0] for k in edge})} segment(s)")
        for found_in in (hits, edge):
            per = {}
            for (sid, txt, what), ts in found_in.items():
                per.setdefault((sid, txt), ([], []))[0].append(what)
                per[(sid, txt)][1].extend(ts)
            for (sid, txt), (whats, ts) in per.items():
                flags.append(f"{sid}: \"{txt}\" {'; '.join(whats)}, from {min(ts):.1f} s to {max(ts):.1f} s")
    # 8. determinism: the same moment rendered twice must give the same pixels, or re-renders and resumed chunks
    # won't match what was reviewed (Chrome's GPU raster did this on gradients and filters; see render.RASTER)
    if a.repeat:
        import render
        ts = [round(total * k / (a.repeat + 1), 3) for k in range(1, a.repeat + 1)]
        rd = render.repeat_diffs(film, ts)
        bad = [(t, d) for t, d in rd if d > 0]
        report.append(f"determinism: {len(bad)} of {len(rd)} moment(s) rendered differently twice")
        for t, d in bad:
            flags.append(f"render at {t:.1f} s ({seg_at(t)}) differs between two renders in {d:.2%} of pixels"
                         + (" (MS_GPU_RASTER=1 is set)" if not render.RASTER else ""))
    # 9. on-screen text: every block of reading text, when it is fully shown and for how long (review/text-sheet.tsv);
    # a reveal that moves on before a viewer can read it fails
    pace = a.text_step > 0 and (a.dead_air > 0 or a.idle_figure > 0 or a.beat > 0)
    if a.text_step > 0:
        ends = [ch["t1"] for ch in tl.get("chapters", [])[:-1] if ch.get("t1")]
        samples, keys = sample_film(film, total, a.text_step, pace)
        sheet = text_holds(samples, total, ends, a.text_step)
        if a.contrast > 0:
            flags += palette_warnings(tl.get("palette"), a.contrast)
            text_contrast(film, sheet, a.contrast)
            low = [x for x in sheet if x.get("contrast") is not None and x["contrast"] < a.contrast]
            report.append(f"text contrast: {len(low)} block(s) under {a.contrast:g}:1 against the pixels behind them")
            for x in low:
                flags.append(f"{x['seg']}: \"{x['text'][:60]}\" contrast {x['contrast']:.1f}:1 at {x['contrast_t']:.1f} s"
                             f" (worst glyph against what is behind it); needs {a.contrast:g}:1; readable for {x['readable']:.1f}"
                             f" of its {x['hold']:.1f} s" + (f", under the {x['need']:.1f} s it needs" if x["readable"] < x["need"] - 1e-6 else ""))
        # the hold test flags a short hold; text lost against its ground is the contrast flag's, so it isn't flagged twice
        short = [x for x in sheet if x["hold"] < x["need"] - 1e-6]
        unread = [x for x in sheet if x["readable"] < x["need"] - 1e-6]
        rows = ["seg\tfully shown (s)\thold (s)\treadable (s)\tneeds (s)\twords\tcontrast\tok\ttext"]
        for x in sheet:
            shown = "never" if x["t"] is None else f"{x['t']:.1f}"
            c = "" if x.get("contrast") is None else f"{x['contrast']:.1f}"
            rows.append(f"{x['seg']}\t{shown}\t{x['hold']:.1f}\t{x['readable']:.1f}\t{x['need']:.1f}"
                        f"{' (' + x['why'] + ')' if x['why'] else ''}\t{x['words']}\t{c}\t{'no' if x in unread else 'yes'}\t{x['text']}")
        (film / "review" / "text-sheet.tsv").write_text("\n".join(rows) + "\n")
        report.append(f"on-screen text: {len(sheet)} block(s), {len(short)} held too briefly to read (review/text-sheet.tsv)")
        for x in short:
            when = "never fully shown" if x["t"] is None else f"fully shown at {x['t']:.1f} s for {x['hold']:.1f} s"
            flags.append(f"{x['seg']}: \"{x['text'][:60]}\" {when}; needs {x['need']:.1f} s"
                         f" ({x['words']} words{', ' + x['why'] if x['why'] else ''})")
    # 10. pacing: dead air, pose variety and beat length, from the same samples (flags for judgement, see pacing)
    if pace:
        rep, fl = pacing(samples, keys, segs, load_script(film), a.text_step, a)
        report += rep; flags += fl
    # 11. icons and the end screen at phone size: every marked icon big enough to read 360 px wide, and the end screen
    # saved at that width for a look
    if a.icon_min > 0:
        rep, fl = end_screen(film, a.icon_min)
        report += rep; flags += fl
    # 12. brand: every logo a kit marks data-logo is drawn in the channel's palette (brand.py); final fails on it too
    if not a.no_brand:
        import brand
        rep, probs = brand.brand_problems(film, load_script(film))
        report += rep; flags += ["brand: " + p for p in probs]
    # 13. transition holds: the voice gap into a transition segment must be at least the film's usual gap at a
    # continuation plus the transition hold, so a new place or chapter gets its breath
    rep, fl = join_gaps(segs, a.transition_hold)
    report += rep; flags += fl
    # pronunciation: what the voice will likely misread in the text it was given
    sp = say_problems(load_script(film))
    report.append(f"pronunciation: {len(sp)} likely misread(s) in the voiced text")
    listen = homograph_listen(load_script(film))
    if listen:
        report.append(f"listen for: {', '.join(listen)} (two readings, usually right)")
    flags += ["say " + p for p in sp]
    print("\n".join(report))
    print(f"\n{len(flags)} flag(s)" + (":" if flags else ""))
    for f in flags:
        print("FLAG:", f)
    (film / "review" / "check.txt").write_text("\n".join(report + [""] + ["FLAG: " + f for f in flags]) + "\n")
    write_gate(film, True, len(flags))


def cmd_share(a):
    """A page that streams the finished cut, for sending a draft to someone without a download. The artifact host
    serves neither .m3u8 nor .ts and caps each file at 15 MB and each publish at 64 MB, so the cut is split into
    fMP4 HLS segments named .mp4 with the playlist as index.txt, and the page plays them through hls.js."""
    import base64
    import html
    film = Path(a.film).resolve()
    src = film / "out" / f"{film.name}.mp4"
    if not src.exists():
        raise SystemExit(f"{src} is missing: run final first")
    sc, tl = load_script(film), load_timeline(film)
    meta = sc.get("share", {})
    dest = film / "out" / "share"
    shutil.rmtree(dest, ignore_errors=True)
    (dest / "v").mkdir(parents=True)
    # segment length from the cut's bitrate: 10 MB at the average rate leaves room for busier stretches and for a cut
    # landing on the next keyframe; a segment still over 14 MB halves the length and splits again
    rate = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=bit_rate", "-of", "csv=p=0", str(src)],
                                capture_output=True, text=True, check=True).stdout.strip() or 0)
    seg = a.segment or (min(60.0, max(4.0, 10e6 * 8 / rate)) if rate else 45.0)
    for _ in range(4):
        shutil.rmtree(dest / "v", ignore_errors=True)
        (dest / "v").mkdir(parents=True)
        run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-c", "copy", "-f", "hls", "-hls_time", f"{seg:.1f}",
             "-hls_playlist_type", "vod", "-hls_segment_type", "fmp4", "-hls_fmp4_init_filename", "init.mp4",
             "-hls_segment_filename", str(dest / "v" / "seg%02d.mp4"), str(dest / "v" / "index.m3u8")])
        (dest / "v" / "index.m3u8").rename(dest / "v" / "index.txt")
        sizes = {f.name: f.stat().st_size for f in sorted((dest / "v").iterdir())}
        big = [n for n, b in sizes.items() if b > 14e6]
        if not big:
            break
        print(f"{seg:.0f} s segments: {', '.join(big)} over 14 MB; splitting at {seg / 2:.0f} s")
        seg /= 2
    else:
        raise SystemExit(f"segments over 14 MB ({', '.join(big)}) even at {seg * 2:.0f} s: the cut's keyframes are too far apart")
    poster = dest / "poster.jpg"
    at = meta.get("poster_at", min(tl["duration"] * 0.25, tl["chapters"][1]["t0"] + 3 if len(tl["chapters"]) > 1 else 5))
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{at:.2f}", "-i", str(src), "-frames:v", "1", "-vf", "scale=960:-2", "-q:v", "5", str(poster)])
    mins = round(tl["duration"] / 60)
    fill = {
        "TITLE": sc["title"], "HEADLINE": meta.get("headline", sc["title"]), "LEDE": meta.get("lede", "Draft cut, not yet published."),
        "EYEBROW": meta.get("eyebrow", f"Explainer · {mins} min"), "FOOT": meta.get("foot", "Sound on."),
        "ACCENT": (sc.get("palette") or {}).get("ACCENT", "#e8772e"),
    }
    page = (HERE.parent / "assets" / "templates" / "share.html").read_text()
    for k, v in fill.items():
        page = page.replace("{{" + k + "}}", html.escape(v))
    chapters = [[f"{int(c['t0'] if i else 0) // 60}:{int(c['t0'] if i else 0) % 60:02d}", c["title"]] for i, c in enumerate(tl["chapters"])]
    page = page.replace("{{CHAPTERS}}", json.dumps(chapters).replace("</", "<\\/"))
    page = page.replace("{{POSTER}}", "data:image/jpeg;base64," + base64.b64encode(poster.read_bytes()).decode())
    (dest / "index.html").write_text(page)
    total = sum(sizes.values())
    print(f"share -> {dest}/index.html + v/ ({len(sizes)} files, {total / 1e6:.1f} MB)")
    print("publish index.html as a private artifact with every file in v/ under its own name (v/index.txt, v/init.mp4, v/seg00.mp4 ...)")
    if total > 60e6:
        print("over 60 MB: one publish carries at most 64 MB, so publish the page with the first segments, then the rest to the same url")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("init"); p.add_argument("film"); p.set_defaults(fn=cmd_init)
    p = sp.add_parser("lines"); p.add_argument("film"); p.add_argument("--json", action="store_true")
    p.add_argument("--allow-homographs", action="store_true", help="hand over lines with unpinned two-reading words anyway")
    p.set_defaults(fn=cmd_lines)
    p = sp.add_parser("voiced"); p.add_argument("film"); p.add_argument("ids", nargs="*", help="segment ids (default: every clip)")
    p.add_argument("--engine"); p.add_argument("--pack"); p.add_argument("--name"); p.add_argument("--speed", type=float)
    p.add_argument("--allow-homographs", action="store_true", help="record clips a listen-through has confirmed read right")
    p.set_defaults(fn=cmd_voiced)
    p = sp.add_parser("panels"); p.add_argument("film"); p.set_defaults(fn=cmd_panels)
    p = sp.add_parser("scale"); p.add_argument("film"); p.add_argument("--marks", help="marks.json from the scale page")
    p.add_argument("--tol", type=float, default=scalecheck.TOL, help="allowed size error against the people in frame (0.2)")
    p.set_defaults(fn=cmd_scale)
    p = sp.add_parser("lint"); p.add_argument("source", help="a film folder, or a JSON list of panel prompts")
    p.add_argument("--boiler", help="regex: sentences to leave out of the word count (reference lines and a shared last sentence already are)")
    p.set_defaults(fn=cmd_lint)
    p = sp.add_parser("review"); p.add_argument("film"); p.add_argument("--checks", help="a checker's verdicts per image (JSON)")
    p.set_defaults(fn=cmd_review)
    p = sp.add_parser("repair"); p.add_argument("film"); g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--beat"); g.add_argument("--image")
    p.add_argument("--box", required=True, help="x,y,w,h in panel pixels, or all four in 0-1 (as the review sheet gives them)")
    p.add_argument("--prompt", required=True, help='what to redraw, e.g. "Redraw the right hand in image 1 with five fingers ..."')
    p.add_argument("--ref", help="a second image, e.g. the sleeve and hand cropped from the character sheet")
    p.add_argument("--model", choices=panelqa.ROUTES,
                   help="chatgpt (default; ChatGPT images through codex). qwen and klein are local and need --draft-local")
    p.add_argument("--draft-local", action="store_true",
                   help="DRAFTS ONLY: repair with a local mflux model (qwen unless --model klein); the render refuses the panel")
    p.add_argument("--allow-codex", action="store_true", help="the ChatGPT route spends the Codex allowance: one image per take")
    p.add_argument("--faceless", action="store_true", help="tell ChatGPT no face may show (a faceless hero)")
    p.add_argument("--method", choices=["pair"] + list(panelqa.METHODS),
                   help="pair (default on ChatGPT): markup and whole, one take each (2 Codex images), keep the one that passes the "
                        "gate. whole: the whole panel with 'change only ...' (wider feather); markup: the whole panel plus a copy "
                        "with the box outlined in red; crop: the square around the box (last resort; the local route's only one). "
                        "Every method pastes back only the box plus --margin")
    p.add_argument("--no-sharpen", action="store_true", help="paste the redraw's detail as it came back (it is sharpened to "
                   "match the panel when under 0.85 of its detail)")
    p.add_argument("--seeds", type=int, nargs="+", help="takes (ChatGPT) or seeds (local): 1 per method for pair, else 3")
    p.add_argument("--context", type=float, default=2.25, help="crop side as a multiple of the box's longer side")
    p.add_argument("--margin", type=int, help="pixels the paste reaches past the box, so a redraw that spills out is kept "
                   "(32; 64 for --method whole)")
    p.add_argument("--feather", type=float, help="pixels of blend inside the paste edge, at most --margin (16; 48 for --method whole)")
    p.add_argument("--gen-size", type=int, help="generate at this size instead of the crop's own (enlarged with Real-ESRGAN when installed)")
    p.add_argument("--no-color-match", action="store_true", help="paste the redraw's colours as they came back")
    p.add_argument("--min-change", type=float, default=0.10,
                   help="drop a seed that changed less than this share of the box by more than 60 levels (0 keeps all)")
    p.add_argument("--gen-args", help='extra mflux arguments, e.g. "-q 8" or "--lora-paths x.safetensors --steps 8"')
    p.set_defaults(fn=cmd_repair)
    p = sp.add_parser("gemini-draw"); p.add_argument("film")
    p.add_argument("--beats", nargs="+", help="only these beats (or panel image paths)")
    p.add_argument("--jobs", help="a JSON list of {image, prompt, refs: [{path, role}], plate, model} instead of script.json")
    p.add_argument("--model", help="flash (Nano Banana 2.1, default) or pro (Nano Banana Pro), or a model id")
    p.add_argument("--size", choices=["1K", "2K", "4K"], help='default 1K (then `upscale`); a panel\'s own "size": "2K" wins')
    p.add_argument("--now", action="store_true", help="interactive, at full price")
    p.add_argument("--redo", action="store_true", help="also panels that exist (moved to history/ first)")
    p.add_argument("--dry-run", action="store_true", help="print each prompt and the price; send nothing"); p.set_defaults(fn=cmd_gemini_draw)
    p = sp.add_parser("gemini-collect"); p.add_argument("film"); p.add_argument("--wait", action="store_true")
    p.add_argument("--every", type=int, default=300, help="seconds between checks with --wait"); p.set_defaults(fn=cmd_gemini_collect)
    p = sp.add_parser("gemini-edit"); p.add_argument("film"); g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--beat"); g.add_argument("--image")
    p.add_argument("--instruction", required=True, help='one change, verb first: "Move the man up the bed against the headboard"')
    p.add_argument("--keep", action="append", help='what must stay exactly the same, e.g. --keep "the room" --keep "every face"')
    p.add_argument("--ref", action="append", help="an extra image, path or path:role (e.g. plates/sickroom.png:the room plate)")
    p.add_argument("--model"); p.add_argument("--size", choices=["1K", "2K", "4K"], help="default 1K"); p.set_defaults(fn=cmd_gemini_edit)
    p = sp.add_parser("gemini-spend"); p.add_argument("--last", type=int, default=10); p.set_defaults(fn=cmd_gemini_spend)
    p = sp.add_parser("accept"); p.add_argument("film"); p.add_argument("repair", help="the repair id, e.g. panels/p07/r02")
    p.add_argument("--seed", type=int); p.set_defaults(fn=cmd_accept)
    p = sp.add_parser("revert"); p.add_argument("film"); p.add_argument("repair"); p.set_defaults(fn=cmd_revert)
    p = sp.add_parser("upscale"); p.add_argument("film"); p.add_argument("--size", default="2304x1296")
    p.add_argument("--model", default="realesrgan-x4plus-anime"); p.add_argument("--bin", default="realesrgan-ncnn-vulkan")
    p.add_argument("--redo", action="store_true"); p.set_defaults(fn=cmd_upscale)
    p = sp.add_parser("align"); p.add_argument("film"); p.add_argument("--redo", action="store_true"); p.set_defaults(fn=cmd_align)
    p = sp.add_parser("timeline"); p.add_argument("film"); p.add_argument("--pad", type=float, default=0.45)
    p.add_argument("--lead", type=float, default=0.3); p.add_argument("--tail", type=float, default=1.5)
    p.add_argument("--transition-hold", type=float, default=TRANSITION_HOLD, help="extra seconds before a transition segment starts (0: none)")
    p.set_defaults(fn=cmd_timeline)
    p = sp.add_parser("finish", help="before/after stills of the film's finish (script.json \"finish\")"); p.add_argument("film")
    p.add_argument("--at", type=float, nargs="+", help="times in seconds (default: a quarter, half and three quarters in)")
    p.set_defaults(fn=cmd_finish)
    p = sp.add_parser("captions"); p.add_argument("film"); p.add_argument("--lang", required=True)
    p.add_argument("--text", required=True, help='JSON {"<segment id>": "translated line"}'); p.set_defaults(fn=cmd_captions)
    p = sp.add_parser("cast-sheet"); p.add_argument("film"); p.set_defaults(fn=cmd_cast_sheet)
    p = sp.add_parser("stills"); p.add_argument("film"); p.add_argument("--every", type=float); p.add_argument("--at", type=float, default=0.75)
    p.set_defaults(fn=cmd_stills)
    p = sp.add_parser("render"); p.add_argument("film"); p.add_argument("--workers", type=int, help="pages rendering at once (default 4; 1 for set3d films on a Mac, references/set3d.md)"); p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--retries", type=int, default=2, help="re-runs of a failed chunk before render stops (finished chunks are kept)")
    p.add_argument("--skip-gate", action="store_true", help="render without a passing check on this version of the film (noted in out/render.status.json)")
    p.add_argument("--allow-spread", action="store_true", help="render although some narrated segments have estimated (spread) word times")
    p.add_argument("--fresh", action="store_true", help="discard finished chunks and render every frame again")
    p.add_argument("--only", help="re-render only these segments (comma-separated ids) and splice them into the last render")
    p.add_argument("--from", dest="seg_from", help="with --to: re-render this run of segments only (inclusive)")
    p.add_argument("--to", dest="seg_to", help="last segment of the --from run")
    p.set_defaults(fn=cmd_render)
    p = sp.add_parser("music"); p.add_argument("film"); p.add_argument("--default", default="serious"); p.add_argument("--fit", type=float, default=0.6)
    p.set_defaults(fn=cmd_music)
    p = sp.add_parser("mix"); p.add_argument("film"); p.add_argument("--plan", action="store_true"); p.add_argument("--music")
    p.add_argument("--whoosh", help="a file or catalog id for the chapter-start whoosh (default: the library clip with role whoosh)")
    p.add_argument("--no-whoosh", dest="whoosh", action="store_const", const="", help="no whoosh at chapter starts")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--bed-db", type=float, help="the bed's gain in dB, fixed (default: set from the voice gap)")
    g.add_argument("--bed-gap", type=float, help="LU the ducked bed sits under the voice (default: the profile's, 16 short / 18 long)")
    p.add_argument("--bed-profile", choices=["auto", "short", "long"], default="auto", help="auto: short for a 9x16 film")
    p.add_argument("--bed-dip", type=float, default=0, help="cut the bed by this many dB at 2.5 kHz, where consonants carry")
    p.add_argument("--bed-exempt", metavar="REASON", help="a build already published: measure and record the bed, but don't stop on it")
    p.add_argument("--sfx", action=argparse.BooleanOptionalAction, default=True,
                   help="UI sounds from the timeline: phone buzz, message ping, card pin, stamp (on by default; --no-sfx)")
    p.add_argument("--sfx-drawn", action="store_true", help="fill kinds with no recorded clip with code-drawn placeholders")
    p.add_argument("--sfx-db", type=float, default=-12)
    p.add_argument("--remix", action="store_true", help="on a Short: build its own mix instead of the one cut from its long-form film")
    p.set_defaults(fn=cmd_mix)
    p = sp.add_parser("short"); p.add_argument("film", help="the long-form film"); p.add_argument("dest", help="the new Short's folder")
    p.add_argument("--from", dest="first", required=True, help="first segment id"); p.add_argument("--to", dest="last", required=True, help="last segment id")
    p.add_argument("--title"); p.set_defaults(fn=cmd_short)
    p = sp.add_parser("credits"); p.add_argument("film"); p.set_defaults(fn=cmd_credits)
    p = sp.add_parser("final"); p.add_argument("film"); p.set_defaults(fn=cmd_final)
    p = sp.add_parser("check"); p.add_argument("film"); p.add_argument("--hold", type=float, default=4.0)
    p.add_argument("--icon-min", type=float, default=18, help="smallest longer side of a data-icon, px, with the frame 360 px wide (0 skips)")
    p.add_argument("--no-brand", action="store_true", help="skip the logo colour check (final still runs it)")
    p.add_argument("--no-listen", action="store_true", help="skip spot-transcribing the served mix (Whisper) against the timeline")
    p.add_argument("--transition-hold", type=float, default=TRANSITION_HOLD, help="a transition's voice gap must beat the usual gap by this (0 skips)")
    p.add_argument("--event", type=float, default=0.0008, help="mean luma change that counts as something happening")
    p.add_argument("--local", type=float, default=0.02, help="change in one small block that counts as a cue landing (small reveals)")
    p.add_argument("--max-text", type=float, default=0.4); p.add_argument("--max-text-run", type=float, default=25.0)
    p.add_argument("--picture-min", type=float, default=0.06,
                   help="a text scene type drawing at least this share of the frame as picture is not text-only (0 = by type alone)")
    p.add_argument("--min-text", type=float, default=56, help="smallest reading text, px at 1920 wide (0 skips the check)")
    p.add_argument("--no-numbers", dest="numbers", action="store_false", help="skip the spoken-numbers-on-screen test")
    p.add_argument("--pace-gap", type=float, default=PACE_GAP, help="longest stretch of narration with nothing new landing on a word, s (0 skips)")
    p.add_argument("--intro-max", type=float, default=INTRO_MAX, help="longest intro, seconds (0 skips the intro tests)")
    p.add_argument("--mute", type=float, default=MUTE_S, help="opening seconds that must name the subject with the sound off (0 skips)")
    p.add_argument("--no-deadspace", dest="deadspace", action="store_false",
                   help="skip the dead-space test (quiet room in a still while the voice says a number, quote or source)")
    p.add_argument("--overlap-step", type=float, default=0.25,
                   help="seconds between in-flight overlap and edge samples (0: only settled moments; -1 skips both tests)")
    p.add_argument("--repeat", type=int, default=8, help="moments rendered twice to check frames are repeatable (0 skips)")
    p.add_argument("--no-motion-estimate", dest="motion_estimate", action="store_false",
                   help="skip predicting each panel's motion from its still and camera")
    p.add_argument("--motion-floor", type=float, help="motion floor for the panel prediction (default: final's rule on the last render, else on the panels)")
    p.add_argument("--text-step", type=float, default=0.1, help="seconds between on-screen samples for the text sheet, hold test and pacing tests (0 skips them all)")
    p.add_argument("--contrast", type=float, default=3.0, help="lowest glyph contrast against the pixels behind it, WCAG ratio (0 skips)")
    p.add_argument("--no-flash-rate", action="store_true", help="skip the WCAG 2.3.1 flash-rate test on the render")
    p.add_argument("--dead-air", type=float, default=1.5, help="flag a voice pause longer than this over 3 words or fewer and no motion (0 skips)")
    p.add_argument("--idle-figure", type=float, default=3.0, help="flag a figure that only idles while the voice speaks longer than this (0 skips)")
    p.add_argument("--beat", type=float, default=5.0, help="flag a beat with nothing new moving or entering while the voice speaks longer than this (0 skips)")
    p.set_defaults(fn=cmd_check)
    p = sp.add_parser("deadspace"); p.add_argument("film"); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_deadspace)
    p = sp.add_parser("pace"); p.add_argument("film"); p.add_argument("--gap", type=float, default=PACE_GAP)
    p.add_argument("--apply", action="store_true", help="write matched cue words into script.json as \"at\"")
    p.set_defaults(fn=cmd_pace)
    p = sp.add_parser("tsheet"); p.add_argument("film"); p.add_argument("--all", action="store_true", help="the whole film, not just the intro")
    p.set_defaults(fn=cmd_tsheet)
    p = sp.add_parser("share"); p.add_argument("film"); p.add_argument("--segment", type=float, help="seconds per streamed file (default: from the bitrate, so each stays under 14 MB)")
    p.set_defaults(fn=cmd_share)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
