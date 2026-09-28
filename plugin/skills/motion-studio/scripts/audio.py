"""Audio for motion-studio films: the shared library, music measurement, and the Mix every sound.py uses.

Library (path from ~/.motion-studio.json "library"):
  catalog.json                 every asset: id, title, file, licence, url; tracks also bpm + corrected downbeat
  mixkit/music/<id>.mp3        Mixkit Free License (music): social, YouTube, ads; not broadcast, games, music-only remixes
  mixkit/sfx/<id>.wav          Mixkit Free License (SFX): any video use, no attribution
  kenney/<pack>/...            Kenney CC0 packs
Recorded or licensed sound only: no synthesized beeps.
"""
import html
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

import numpy as np
from scipy.signal import butter, sosfilt

SR = 48000
HERE = Path(__file__).resolve().parent
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
LICENCES = {
    "mixkit-music": "Mixkit Free License (music): https://mixkit.co/license/#musicFree",
    "mixkit-sfx": "Mixkit Free License (SFX): https://mixkit.co/license/#sfxFree",
    "kenney-cc0": "Creative Commons Zero (CC0): https://creativecommons.org/publicdomain/zero/1.0/",
}


# ── library + catalog ──────────────────────────────────────
def library_dir():
    cfg = Path.home() / ".motion-studio.json"
    lib = json.loads(cfg.read_text()).get("library") if cfg.exists() else None
    path = Path(lib or "~/motion-studio/library").expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def catalog():
    p = library_dir() / "catalog.json"
    return json.loads(p.read_text()) if p.exists() else {"tracks": {}, "sfx": {}, "packs": {}}


def save_catalog(cat):
    (library_dir() / "catalog.json").write_text(json.dumps(cat, indent=2, ensure_ascii=False) + "\n")


def _get(url, dest=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    if dest:
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(data)
    return data


def mixkit_search(kind, query):
    """List (id, title) from a Mixkit category page. kind: 'music' (e.g. 'house', 'downtempo') or 'sfx' (e.g. 'footsteps')."""
    base = "https://mixkit.co/free-stock-music/" if kind == "music" else "https://mixkit.co/free-sound-effects/"
    page = _get(base + query.strip("/") + "/").decode("utf-8", "replace")
    pat = r"/music/(\d+)/" if kind == "music" else r"/active_storage/sfx/(\d+)/"
    ids = list(dict.fromkeys(re.findall(pat, page)))
    titles = [html.unescape(re.sub("<[^>]+>", "", t)).strip() for t in re.findall(r'<h2 class="item-grid-card__title">(.*?)</h2>', page, re.S)]
    found = list(zip(ids, titles))
    cache = library_dir() / ".search-titles.json"   # so `fetch` can record the title the search showed
    seen = json.loads(cache.read_text()) if cache.exists() else {}
    seen.update({f"{kind}-{i}": t for i, t in found})
    cache.write_text(json.dumps(seen, indent=1))
    return found


def fetch_mixkit(kind, mid, title=""):
    lib, cat = library_dir(), catalog()
    key = f"mixkit-{mid}"
    if kind == "music":
        dest = lib / "mixkit" / "music" / f"{mid}.mp3"
        url = f"https://assets.mixkit.co/music/{mid}/{mid}.mp3"
        table, lic = "tracks", "mixkit-music"
    else:
        dest = lib / "mixkit" / "sfx" / f"{mid}.wav"
        url = f"https://assets.mixkit.co/active_storage/sfx/{mid}/{mid}.wav"
        table, lic = "sfx", "mixkit-sfx"
    if not dest.exists():
        _get(url, dest)
    entry = cat[table].get(key, {})
    entry.update({"id": key, "file": str(dest.relative_to(lib)), "url": url, "licence": LICENCES[lic]})
    cache = lib / ".search-titles.json"
    title = title or (json.loads(cache.read_text()).get(f"{kind}-{mid}", "") if cache.exists() else "")
    if title:
        entry["title"] = title
    entry.setdefault("title", "")
    cat[table][key] = entry
    save_catalog(cat)
    return key, dest


def fetch_kenney(pack="impact-sounds"):
    """Download a Kenney CC0 pack (default: impact-sounds) into the library."""
    lib, cat = library_dir(), catalog()
    dest = lib / "kenney" / pack
    if not (dest / "License.txt").exists():
        page = _get(f"https://kenney.nl/assets/{pack}").decode("utf-8", "replace")
        m = re.search(rf"https://kenney\.nl/media/pages/assets/{pack}/[^\"]+\.zip", page)
        if not m:
            raise SystemExit(f"no download link found for Kenney pack {pack}")
        zp = dest.with_suffix(".zip")
        _get(m.group(0), zp)
        dest.mkdir(parents=True, exist_ok=True)
        subprocess.run(["unzip", "-oq", str(zp), "-d", str(dest)], check=True)
        zp.unlink()
    cat["packs"][f"kenney-{pack}"] = {"id": f"kenney-{pack}", "dir": str(dest.relative_to(lib)), "licence": LICENCES["kenney-cc0"],
                                      "url": f"https://kenney.nl/assets/{pack}"}
    save_catalog(cat)
    return dest


# ── decoding + small DSP ───────────────────────────────────
def load(path, stereo=False, sr=SR):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "2" if stereo else "1", "-ar", str(sr), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.float32).astype(np.float64)
    return x.reshape(-1, 2) if stereo else x


def asset(key, stereo=False):
    """Decode a catalogued asset by id ('mixkit-2541'), or a Kenney file by name ('impactSoft_heavy_003')."""
    lib, cat = library_dir(), catalog()
    for table in ("sfx", "tracks"):
        if key in cat[table]:
            return load(lib / cat[table][key]["file"], stereo)
    for pack in cat["packs"].values():
        hits = list((lib / pack["dir"]).rglob(f"{key}.*"))
        if hits:
            return load(hits[0], stereo)
    raise SystemExit(f"{key} is not in the library: fetch it first (studio.py fetch ...)")


def lowpass(x, hz):
    return sosfilt(butter(2, hz, "lowpass", fs=SR, output="sos"), x)


def fade(x, fin=0.005, fout=0.02):
    x = x.copy()
    a, c = int(fin * SR), min(int(fout * SR), len(x))
    if a: x[:a] *= np.linspace(0, 1, a)
    if c: x[-c:] *= np.linspace(1, 0, c)
    return x


def cut(x, start, dur, fin=0.005, fout=0.02):
    start = max(0.0, start)          # an onset in the first 20 ms would otherwise start the slice before 0
    return fade(x[int(start * SR): int((start + dur) * SR)], fin, fout)


def onsets(x, thr=0.12, gap=0.3):
    env = np.convolve(np.abs(x), np.ones(240) / 240, "same")
    out = []
    for j in np.where(env > thr * env.max())[0]:
        if not out or j - out[-1] > gap * SR:
            out.append(j)
    return [o + int(np.argmax(np.abs(x[o:o + int(0.05 * SR)]))) for o in out]


def single_hits(*keys, length=0.32):
    """Cut individual hits (footsteps, taps) out of longer recordings: vary them by cycling the list."""
    hits = []
    for k in keys:
        x = asset(k)
        hits += [cut(x, o / SR - 0.02, length, 0.002, 0.08) for o in onsets(x) if o + (length + 0.03) * SR < len(x)]
    return hits


# ── music: measure the grid, correct the downbeat ──────────
def sections(loud, jump_db=4.0):
    """Summarise a bar-loudness map as sections: [(from_bar, to_bar, mean_db)], split where loudness jumps."""
    out, start = [], 0
    for i in range(1, len(loud) + 1):
        if i == len(loud) or abs(loud[i] - loud[i - 1]) >= jump_db:
            out.append((start, i, round(sum(loud[start:i]) / (i - start), 1)))
            start = i
    return out


def measure(key):
    """Run product-film's beats.py, then correct the downbeat phase from where the song's sections change.

    beats.py can land the downbeat on the wrong beat of the bar (Cat Walk: 2 beats early). Section changes
    (drops, breakdowns) happen on downbeats, so the beat phase where loudness jumps most is the real one.
    """
    lib, cat = library_dir(), catalog()
    entry = cat["tracks"][key]
    src = lib / entry["file"]
    out = lib / "mixkit" / "music" / f"{key}.beats.json"
    subprocess.run([sys.executable, str(HERE / "beats.py"), "--drums", str(src), "--out", str(out)], check=True, capture_output=True)
    grid = json.loads(out.read_text())
    bpm, beat = grid["bpm"], grid["beatSeconds"]
    x = load(src, sr=22050)
    first = grid["firstBeat"]
    n = int((len(x) / 22050 - first) / beat)
    db = np.array([20 * np.log10(np.sqrt(np.mean(x[int((first + k * beat) * 22050): int((first + (k + 1) * beat) * 22050)] ** 2)) + 1e-9) for k in range(n)])
    jump = np.abs(np.diff(db))
    jump = np.maximum(0, jump - np.median(jump))                      # only real changes count
    raw_k = round((grid["firstDownbeat"] - first) / beat)
    scores = [float(sum(jump[k - 1] for k in range(1, n) if (k - raw_k - p) % 4 == 0)) for p in range(4)]
    p = int(np.argmax(scores))
    downbeat = grid["firstDownbeat"] + p * beat
    bar = 4 * beat
    bars = int((len(x) / 22050 - downbeat) / bar)
    loud = [round(float(20 * np.log10(np.sqrt(np.mean(x[int((downbeat + i * bar) * 22050): int((downbeat + (i + 1) * bar) * 22050)] ** 2)) + 1e-9)), 1) for i in range(bars)]
    entry.update({"duration_s": round(len(x) / 22050, 1), "bpm": round(bpm, 3), "beat": beat, "downbeat": round(downbeat, 4), "raw_downbeat": grid["firstDownbeat"],
                  "phase_shift_beats": p, "grid_spread_ms": grid["gridCheckMs"]["spread"], "bar_loudness_db": loud})
    save_catalog(cat)
    return entry


# ── the mix ────────────────────────────────────────────────
def grid_from_film(film_html):
    """bpm and bars from the film's K.setup(...) call, so sound.py always matches the picture."""
    s = Path(film_html).read_text()
    setup = re.search(r"K\.setup\(\{(.*?)\}\)", s, re.S)   # the K.setup call itself, not the first "bpm:" anywhere
    if not setup:
        raise SystemExit(f"{film_html}: no K.setup({{...}}) call found")
    bpm = float(re.search(r"bpm:\s*([\d.]+)", setup.group(1)).group(1))
    bars = int(re.search(r"bars:\s*(\d+)", setup.group(1)).group(1))
    return bpm, bars


class Mix:
    def __init__(self, film_dir):
        self.film = Path(film_dir)
        self.bpm, self.bars = grid_from_film(self.film / "film.html")
        self.beat = 60 / self.bpm
        self.dur = self.bars * 4 * self.beat
        self.buf = np.zeros((int((self.dur + 0.5) * SR), 2))

    def b(self, bar, beat=0):
        return (bar * 4 + beat) * self.beat

    def put(self, sig, at, gain=1.0, pan=0.0, peak=None):
        """Place a mono cue so its transient (peak, seconds into the clip; measured if None) lands on `at`."""
        if peak is None:
            peak = np.argmax(np.abs(sig[: int(0.6 * SR)])) / SR
        i = int(round((at - peak) * SR))
        if i < 0:
            sig, i = sig[-i:], 0
        n = min(len(sig), len(self.buf) - i)
        if n <= 0:
            return
        self.buf[i:i + n, 0] += gain * np.sqrt(0.5 * (1 - pan)) * sig[:n]
        self.buf[i:i + n, 1] += gain * np.sqrt(0.5 * (1 + pan)) * sig[:n]

    def music(self, key, segments, at_bar=0, gain=0.8, fade_out=1.4):
        """Cut a measured track on its bars and lay it on the film's grid.

        segments: [(from_bar, to_bar), ...] in the song's (corrected) bars, to_bar exclusive; they play back to back
        from film bar `at_bar`. The film must run at the song's tempo: set K.setup({bpm}) to the catalog's bpm.
        """
        entry = catalog()["tracks"].get(key)
        if not entry or "downbeat" not in entry:
            raise SystemExit(f"{key}: fetch and measure it first (studio.py fetch music <id>, or studio.py measure {key})")
        if abs(entry["bpm"] - self.bpm) > 0.05:
            print(f"WARNING: film bpm {self.bpm} != track bpm {entry['bpm']}; set K.setup({{bpm: {entry['bpm']}}})", flush=True)
        song = load(library_dir() / entry["file"], stereo=True)
        bar = 4 * 60 / entry["bpm"]
        pieces = []
        for a, c in segments:
            seg = song[int((entry["downbeat"] + a * bar) * SR): int((entry["downbeat"] + c * bar) * SR)].copy()
            f = int(0.005 * SR)
            seg[:f] *= np.linspace(0, 1, f)[:, None]; seg[-f:] *= np.linspace(1, 0, f)[:, None]
            pieces.append(seg)
        edit = np.concatenate(pieces)
        fo = int(fade_out * SR)
        edit[-fo:] *= np.linspace(1, 0, fo)[:, None]
        i = int(self.b(at_bar) * SR)
        n = min(len(edit), len(self.buf) - i)
        self.buf[i:i + n] += gain * edit[:n]

    def bed(self, key, spans, gain=0.5, offset=20.0):
        """An ambience under spans [(from_s, to_s, level), ...], with 0.35 s soft edges."""
        x = asset(key)
        x = x[int(offset * SR): int(offset * SR) + len(self.buf)]
        x = np.pad(x, (0, max(0, len(self.buf) - len(x))))
        env = np.zeros(len(self.buf))
        for a, c, lvl in spans:
            env[int(a * SR): int(c * SR)] = np.maximum(env[int(a * SR): int(c * SR)], lvl)
        k = int(0.35 * SR)
        env = np.convolve(env, np.ones(k) / k, "same")
        self.buf[:, 0] += gain * x * env
        self.buf[:, 1] += gain * np.roll(x, 2400) * env

    def master(self, dest=None, lufs=-14.0, tp=-1.5):
        """Two-pass loudnorm to `lufs` / `tp`, 48 kHz 16-bit; writes out/mix.wav by default."""
        dest = Path(dest or self.film / "out" / "mix.wav")
        dest.parent.mkdir(parents=True, exist_ok=True)
        mix = self.buf[: int(self.dur * SR)].copy()
        tail = int(0.8 * SR)
        mix[-tail:] *= np.linspace(1, 0, tail)[:, None]
        raw = dest.with_suffix(".raw.wav")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f64le", "-ar", str(SR), "-ac", "2", "-i", "-", str(raw)], input=mix.tobytes(), check=True)
        probe = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(raw), "-af", f"loudnorm=I={lufs}:TP={tp}:LRA=11:print_format=json", "-f", "null", "-"],
                               capture_output=True, text=True).stderr
        m = json.loads(probe[probe.rindex("{"): probe.rindex("}") + 1])
        flt = (f"loudnorm=I={lufs}:TP={tp}:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
               f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-af", flt, "-ar", str(SR), "-c:a", "pcm_s16le", str(dest)], check=True)
        raw.unlink()
        print(f"{dest}  (input {m['input_i']} LUFS -> {lufs})")
        return dest
