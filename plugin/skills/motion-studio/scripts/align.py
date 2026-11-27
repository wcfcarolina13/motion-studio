"""Word timestamps for narration you already have: forced alignment of known text to its audio.

Most TTS engines (Kokoro included) return audio without word times. Captions that light word by word, and
scene beats keyed to a spoken word, need them. This aligns each segment's script text to its clip with
PocketSphinx (offline, `pip install pocketsphinx`, its English model ships inside the wheel), and falls back to
spreading words by spoken weight between the clip's measured pauses when alignment can't run.

    words = align_clip("narration/s03.mp3", "Behind that one message is a subscription business...")
    # [{"w": "Behind", "t0": 0.0, "t1": 0.64}, ...]   times in seconds from the clip's start

Each result says how it was timed: "whisper", "align" (PocketSphinx) or "spread". Whisper is tried first: its word
timestamps are matched to the script's words, so the text stays the script's and only the times come from the audio.
It runs as mlx-whisper on an Apple-silicon Mac (in this Python, or the interpreter in $MS_WHISPER_PY, or a pipx venv
at ~/.local/pipx/venvs/mlx-whisper), or as faster-whisper elsewhere. Model: $MS_WHISPER_MODEL, default "small"
(MIT-licensed weights, cached after the first run, free). Spread times are estimates: off by up to 0.5 s on a long
clip, which moves every cue keyed to a word, so `check` fails a film with any spread segment (Survive Year 1 had
140 of 140 segments spread because PocketSphinx was never installed on the Mac, 2026-11-27).
"""
import difflib
import json
import os
import sys
import re
import subprocess
import wave
from pathlib import Path

ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def say_int(n):
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + ("" if n % 10 == 0 else " " + ONES[n % 10])
    if n < 1000:
        return ONES[n // 100] + " hundred" + ("" if n % 100 == 0 else " " + say_int(n % 100))
    for size, name in ((10 ** 9, "billion"), (10 ** 6, "million"), (1000, "thousand")):
        if n >= size:
            return say_int(n // size) + " " + name + ("" if n % size == 0 else " " + say_int(n % size))
    return str(n)


def spoken(token):
    """One written token -> the words a narrator says for it ("$4.12" -> four dollars twelve cents)."""
    t = token.lower().strip(".,;:!?\"'()[]“”‘’—–")
    t = t.replace("’", "'")
    if not t:
        return []
    m = re.fullmatch(r"\$([\d,]+)(?:\.(\d\d))?([kmb])?", t)
    if m:
        n = int(m.group(1).replace(",", ""))
        out = say_int(n).split()
        if m.group(3):
            out += [{"k": "thousand", "m": "million", "b": "billion"}[m.group(3)]]
        out += ["dollar" if n == 1 and not m.group(3) else "dollars"]
        if m.group(2) and int(m.group(2)):
            out += say_int(int(m.group(2))).split() + ["cents"]
        return out
    m = re.fullmatch(r"([\d,]+)(?:\.(\d+))?(%|percent)?", t)
    if m and m.group(1).replace(",", "").isdigit():
        raw = m.group(1).replace(",", "")
        n = int(raw)
        if len(raw) == 4 and 1900 <= n <= 2099 and "," not in m.group(1) and not m.group(2):
            out = (say_int(n // 100) + " " + ("hundred" if n % 100 == 0 else ("oh " + ONES[n % 100] if n % 100 < 10 else say_int(n % 100)))).split()
        else:
            out = say_int(n).split()
        if m.group(2):
            out += ["point"] + [ONES[int(d)] for d in m.group(2)]
        if m.group(3):
            out += ["percent"]
        return out
    m = re.fullmatch(r"(\d+)(st|nd|rd|th)", t)
    if m:
        return say_int(int(m.group(1))).split()
    return [w for w in re.split(r"[^a-z0-9']+", t) if w]


def tokens(text, say_map=None):
    """Written tokens as they should appear in a caption, each with its spoken words. A phrase in `say_map` (written ->
    spoken, see say.py) takes its spoken words from the spoken form: word for word when the counts match, else all on
    the phrase's first token."""
    import say
    out, pos = [], []
    for m in re.finditer(r"\S+", text.replace("—", " — ")):
        tok = m.group(0)
        if tok in ("—", "–"):
            continue
        out.append({"w": tok, "say": spoken(tok)}); pos.append(m.start())
    for a, b, sp in say.spans(text.replace("—", " — "), say_map):
        idx = [i for i, p in enumerate(pos) if a <= p < b]
        if not idx:
            continue
        parts = say.plain(sp).split()
        if len(parts) == len(idx):
            for i, w in zip(idx, parts):
                out[i]["say"] = spoken(w)
        else:
            out[idx[0]]["say"] = [w for p in parts for w in spoken(p)]
            for i in idx[1:]:
                out[i]["say"] = []
    return out


def to_wav16k(src, dest):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-ar", "16000", "-ac", "1", "-sample_fmt", "s16", str(dest)], check=True)


def pauses(wav_path, frame=0.02, floor_db=-38.0, min_len=0.12):
    """Silent stretches [(t0, t1)] from a plain energy envelope."""
    import numpy as np
    with wave.open(str(wav_path)) as w:
        sr, x = w.getframerate(), np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    n = int(sr * frame)
    if len(x) < n:
        return [], len(x) / sr
    e = x[: len(x) // n * n].reshape(-1, n)
    db = 20 * np.log10(np.sqrt((e ** 2).mean(axis=1)) + 1e-9)
    ref = np.percentile(db, 95)
    quiet = db < max(floor_db, ref - 30)
    out, start = [], None
    for i, q in enumerate(list(quiet) + [False]):
        if q and start is None:
            start = i
        elif not q and start is not None:
            if (i - start) * frame >= min_len:
                out.append((start * frame, i * frame))
            start = None
    return out, len(x) / sr


def weight(word):
    return 0.12 + 0.045 * len(re.sub(r"[^a-z0-9]", "", word.lower())) + (0.25 if word[-1:] in ",;:" else 0) + (0.35 if word[-1:] in ".!?" else 0)


def spread(toks, wav_path):
    """Fallback: speech runs between measured pauses, words spread inside by spoken weight."""
    gaps, dur = pauses(wav_path)
    t0 = gaps[0][1] if gaps and gaps[0][0] < 0.05 else 0.0
    t1 = gaps[-1][0] if gaps and gaps[-1][1] > dur - 0.05 else dur
    ws = [weight(" ".join(t["say"]) + t["w"][-1:]) for t in toks]
    total, acc, out = sum(ws) or 1, t0, []
    for t, w in zip(toks, ws):
        d = (t1 - t0) * w / total
        out.append({"w": t["w"], "t0": round(acc, 3), "t1": round(acc + d * 0.92, 3)})
        acc += d
    return out


YEAR = re.compile(r"(19|20)\d\d")


def _onset_error(words, gaps):
    """mean distance from each measured speech onset (end of a pause) to the nearest word start"""
    ons = [b for a, b in gaps if a > 0.05]
    if not ons or not words:
        return 0.0
    return sum(min(abs(w["t0"] - t) for w in words) for t in ons) / len(ons)


def _snap(words, gaps, dur):
    """Pull word edges onto measured silence: a word never starts inside a pause or ends deep into one.
    The aligner hands leading silence to the first word and pause time to the word before it."""
    for a, b in gaps:
        for w in words:
            if a - 0.02 <= w["t0"] < b:          # starts in the pause: move to where speech resumes
                w["t0"] = round(min(b, w["t1"] - 0.05), 3)
            if a < w["t1"] <= b + 0.02 and w["t0"] < a:   # runs into the pause: end where it goes quiet
                w["t1"] = round(a + 0.08, 3)
    return words


def align_clip(clip, text, work=None, say_map=None):
    """Align `text` to `clip`; returns (words, method). `say_map`: phrases the voice was given in another form."""
    clip = Path(clip)
    work = Path(work or clip.parent)
    wav = work / f".{clip.stem}.16k.wav"
    to_wav16k(clip, wav)
    toks = tokens(text, say_map)
    gaps, dur = pauses(wav)
    try:
        words, method = _snap(_whisper(toks, wav), gaps, dur), "whisper"
        wav.unlink(missing_ok=True)
        return words, method
    except Exception as e:
        why_w = str(e)
    try:
        words = _sphinx(toks, wav)
        method = "align"
        # a year can be read "twenty twenty-five" or "two thousand twenty-five": try the other reading and keep
        # whichever lands word starts on the measured pauses
        years = [i for i, t in enumerate(toks) if YEAR.fullmatch(t["w"].strip(".,;:!?"))]
        if years:
            alt = [dict(t) for t in toks]
            for i in years:
                alt[i]["say"] = say_int(int(toks[i]["w"].strip(".,;:!?"))).split()
            try:
                w2 = _sphinx(alt, wav)
                if _onset_error(w2, gaps) < _onset_error(words, gaps) - 0.01:
                    words = w2
            except Exception:
                pass
        words = _snap(words, gaps, dur)
    except Exception as e:                                   # no pocketsphinx, or the aligner gave up
        words, method = spread(toks, wav), "spread"
        print(f"  {clip.name}: alignment unavailable (whisper: {why_w}; pocketsphinx: {e}); spread by weight")
    wav.unlink(missing_ok=True)
    return words, method


def _sphinx(toks, wav):
    from pocketsphinx import Decoder
    d = Decoder(samprate=16000, bestpath=False, loglevel="FATAL")
    flat, owner = [], []                                     # spoken words the dictionary knows, and their token
    for i, t in enumerate(toks):
        for w in t["say"]:
            if d.lookup_word(w):
                flat.append(w); owner.append(i)
    if not flat:
        raise RuntimeError("no dictionary words")
    d.set_align_text(" ".join(flat))
    with wave.open(str(wav)) as w:
        data, dur = w.readframes(w.getnframes()), w.getnframes() / 16000
    d.start_utt(); d.process_raw(data, full_utt=True); d.end_utt()
    segs = [s for s in d.seg() if not s.word.startswith("<") and s.word not in ("[NOISE]",)]
    if len(segs) != len(flat):
        raise RuntimeError(f"aligned {len(segs)} of {len(flat)} words")
    span = {}
    for s, i in zip(segs, owner):
        a, b = s.start_frame / 100, (s.end_frame + 1) / 100
        lo, hi = span.get(i, (a, b))
        span[i] = (min(lo, a), max(hi, b))
    # tokens the dictionary didn't know (names, codes): share the gap between their timed neighbours
    out, i = [], 0
    while i < len(toks):
        if i in span:
            out.append({"w": toks[i]["w"], "t0": round(span[i][0], 3), "t1": round(span[i][1], 3)}); i += 1; continue
        j = i
        while j < len(toks) and j not in span:
            j += 1
        a = span[i - 1][1] if i > 0 else 0.0
        b = span[j][0] if j < len(toks) else dur
        step = (b - a) / (j - i) if j > i else 0
        for k in range(i, j):
            out.append({"w": toks[k]["w"], "t0": round(a + (k - i) * step, 3), "t1": round(a + (k - i + 1) * step, 3)})
        i = j
    return out


# ── Whisper word timestamps, matched to the script ──
WHISPER_MODELS = {"tiny": ("mlx-community/whisper-tiny-mlx", "tiny"), "base": ("mlx-community/whisper-base-mlx", "base"),
                  "small": ("mlx-community/whisper-small-mlx", "small"), "medium": ("mlx-community/whisper-medium-mlx", "medium")}
MATCH_MIN = 0.6      # share of the script's spoken words Whisper must hear for its times to be used
_RUN = r"""
import json, sys
wav, repo, size = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    import mlx_whisper
    r = mlx_whisper.transcribe(wav, path_or_hf_repo=repo, word_timestamps=True, language="en")
    ws = [(w["word"], w["start"], w["end"]) for sg in r["segments"] for w in sg.get("words", [])]
except ImportError:
    from faster_whisper import WhisperModel
    segs, _ = WhisperModel(size, device="cpu", compute_type="int8").transcribe(wav, word_timestamps=True, language="en")
    ws = [(w.word, w.start, w.end) for sg in segs for w in (sg.words or [])]
print(json.dumps(ws))
"""


def _whisper_python():
    """The interpreter that has mlx-whisper or faster-whisper: this one, $MS_WHISPER_PY, or the pipx venv."""
    import importlib.util
    if importlib.util.find_spec("mlx_whisper") or importlib.util.find_spec("faster_whisper"):
        return sys.executable
    for c in (os.environ.get("MS_WHISPER_PY"), str(Path.home() / ".local/pipx/venvs/mlx-whisper/bin/python")):
        if c and Path(c).exists():
            return c
    raise RuntimeError("no mlx-whisper or faster-whisper (pipx install mlx-whisper on a Mac)")


def whisper_words(wav):
    """[(word, start, end)] as Whisper heard them, in seconds."""
    repo, size = WHISPER_MODELS.get(os.environ.get("MS_WHISPER_MODEL", "small"), WHISPER_MODELS["small"])
    r = subprocess.run([_whisper_python(), "-c", _RUN, str(wav), repo, size], capture_output=True, text=True, timeout=900)
    if r.returncode:
        raise RuntimeError((r.stderr.strip().splitlines() or ["whisper failed"])[-1][:200])
    return [tuple(x) for x in json.loads(r.stdout.strip().splitlines()[-1])]


def match_words(toks, heard, dur):
    """Script tokens timed by Whisper's words. Both sides are reduced to spoken words ("$4,860" and "4,860" both become
    four thousand eight hundred sixty dollars...), matched in order (difflib), and each token takes the span of its
    matched spoken words. A token Whisper didn't hear shares the gap between its timed neighbours. Raises when under
    MATCH_MIN of the script was heard (wrong clip, or a garbled transcript)."""
    ours, owner = [], []
    for i, t in enumerate(toks):
        for w in t["say"]:
            ours.append(w); owner.append(i)
    theirs, at = [], []
    for w, a, b in heard:
        sp = spoken(w.strip())
        for k, x in enumerate(sp):           # a written number Whisper gave as one word: its spoken words share its span
            step = (b - a) / len(sp)
            theirs.append(x); at.append((a + k * step, a + (k + 1) * step))
    if not ours:
        raise RuntimeError("no spoken words")
    sm = difflib.SequenceMatcher(a=ours, b=theirs, autojunk=False)
    span, hit = {}, 0
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            i, (a, b) = owner[blk.a + k], at[blk.b + k]
            lo, hi = span.get(i, (a, b))
            span[i] = (min(lo, a), max(hi, b)); hit += 1
    if hit < MATCH_MIN * len(ours):
        raise RuntimeError(f"heard {hit} of {len(ours)} script words")
    out, i = [], 0
    while i < len(toks):
        if i in span:
            out.append({"w": toks[i]["w"], "t0": round(span[i][0], 3), "t1": round(span[i][1], 3)}); i += 1; continue
        j = i
        while j < len(toks) and j not in span:
            j += 1
        a = span[i - 1][1] if i > 0 else 0.0
        b = span[j][0] if j < len(toks) else dur
        step = max(0.0, b - a) / (j - i)
        for k in range(i, j):
            out.append({"w": toks[k]["w"], "t0": round(a + (k - i) * step, 3), "t1": round(a + (k - i + 1) * step, 3)})
        i = j
    return out


def _whisper(toks, wav):
    with wave.open(str(wav)) as w:
        dur = w.getnframes() / w.getframerate()
    return match_words(toks, whisper_words(wav), dur)
