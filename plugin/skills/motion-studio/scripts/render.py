"""Frames out of a film: stills, contact sheets, drafts and finals.

A film is any folder with film.html exposing window.seek(t) and window.DUR. Frame size comes from
window.STUDIO.format when the film was built with kit.js, else from the #stage element (older films).
Chrome is the system Chrome via Playwright (no browser download needed).
"""
import os
import subprocess
from pathlib import Path

from playwright.sync_api import sync_playwright

FPS = 30
# Chrome's GPU rasteriser and partial raster can paint the same moment differently twice on pages with gradients and
# filters, so a re-render (or a resumed chunk) would not match its neighbours. Software raster of whole tiles is
# deterministic; MS_GPU_RASTER=1 opts back into GPU raster where speed matters more than repeatable frames.
RASTER = [] if os.environ.get("MS_GPU_RASTER") == "1" else ["--disable-gpu-rasterization", "--disable-partial-raster"]


class PageError(RuntimeError):
    """A JavaScript error in the film's page. It is deterministic, so a retry would fail the same way: whatever is
    rendering stops at once and names the error, the time and the segment."""


def _where(e):
    """'message (file:line)' for a Playwright pageerror, the first stack frame being where it was thrown."""
    at = next((ln.strip() for ln in (getattr(e, "stack", "") or "").splitlines()[1:] if ln.strip().startswith("at ")), "")
    return f"{getattr(e, 'message', str(e))}" + (f" ({at})" if at else "")


def _crashed(e):
    """A browser or page that died (or timed out), as opposed to an error thrown by the film's own code."""
    from playwright.sync_api import TimeoutError as PwTimeout
    return isinstance(e, PwTimeout) or any(k in str(e) for k in ("Target page", "Target closed", "Target crashed", "has been closed"))


class Film:
    """An open headless page on a film; use as a context manager. Any JavaScript error in the page, while it loads or
    while a frame is drawn, makes shot() raise PageError; console errors are kept in .console for page_problems."""

    def __init__(self, project: Path):
        self.project = Path(project).resolve()

    def __enter__(self):
        self._pw = sync_playwright().start()
        # the installed Chrome by default; MS_CHROMIUM=/path/to/chromium for machines without it (CI, cloud sandboxes)
        kw = {"executable_path": os.environ["MS_CHROMIUM"]} if os.environ.get("MS_CHROMIUM") else {"channel": "chrome"}
        self.browser = self._pw.chromium.launch(headless=True, args=["--allow-file-access-from-files", *RASTER], **kw)
        page = self.browser.new_page(viewport={"width": 2000, "height": 2000}, device_scale_factor=1)
        self.errors, self.console = [], []
        page.on("pageerror", lambda e: (self.errors.append(_where(e)), print("PAGE ERROR:", _where(e), flush=True)))
        page.on("console", lambda m: m.type == "error" and self.console.append(m.text + (f" ({m.location.get('url', '').rsplit('/', 1)[-1]}:{m.location.get('lineNumber', 0) + 1})" if m.location.get("url") else "")))
        page.goto((self.project / "film.html").as_uri())
        page.evaluate("document.fonts.ready")
        page.evaluate("window.STUDIO && window.STUDIO.ready")   # panel images decoded (long-form panel scenes)
        page.wait_for_timeout(250)
        info = page.evaluate("""() => {
            const s = window.STUDIO, st = document.getElementById('stage').getBoundingClientRect();
            return { dur: window.DUR, studio: s || null, w: s ? s.format.w : Math.round(st.width), h: s ? s.format.h : Math.round(st.height) };
        }""")
        if info["dur"] is None:
            if self.errors:
                raise PageError(f"the page failed while loading: {self.errors[0]}")
            raise SystemExit(f"{self.project}/film.html does not set window.DUR (is window.seek defined?)")
        self.dur, self.studio, self.w, self.h = info["dur"], info["studio"], info["w"], info["h"]
        for warning in (self.studio or {}).get("warnings", []):
            print("LINT:", warning, flush=True)
        page.set_viewport_size({"width": self.w, "height": self.h})
        self.page = page
        return self

    def __exit__(self, *exc):
        self.browser.close()
        self._pw.stop()

    def seg_at(self, t):
        """The id of the long-form segment playing at t, or None (other films have no timeline)."""
        try:
            return self.page.evaluate("t => (((window.TIMELINE || {}).segments || []).find(s => s.t0 <= t && t < s.t1) || {}).id || null", t)
        except Exception:
            return None

    def seek(self, t):
        """Seek, turning an error thrown by the film's code into PageError (a dead page is re-raised as it came)."""
        try:
            self.page.evaluate(f"window.seek({min(max(t, 0), self.dur - 1e-4):.6f})")
        except Exception as e:
            if _crashed(e):
                raise
            raise PageError(self._msg(t, str(e).removeprefix("Page.evaluate: ").split("\n    at <anonymous>")[0].split("\n    at eval")[0])) from None

    def _msg(self, t, err):
        seg = self.seg_at(t)
        return f"page error at {t:.2f} s" + (f" (segment {seg})" if seg else "") + f": {err}"

    def shot(self, t):
        self.seek(t)
        png = self.page.locator("#stage").screenshot(type="png", animations="disabled")
        if self.errors:
            raise PageError(self._msg(t, self.errors[0]))
        return png

    def bar_times(self, at=0.72):
        """One time per bar, `at` of the way through it (captions have landed by then)."""
        s = self.studio
        if not s:
            raise SystemExit("this film has no window.STUDIO (bpm/bars); pass explicit times instead")
        bar = 4 * 60 / s["bpm"]
        return [round(k * bar + at * bar, 3) for k in range(s["bars"])]


def page_problems(project):
    """Everything wrong in the film's page, found in seconds: it loads the page and seeks to the start, middle and end
    of every long-form segment (every second for other films), and lists each JavaScript error, console error and
    unhandled promise rejection as (time or None at load, segment, message), each distinct message once. console.warn
    and console.log are allowed."""
    out, seen = [], set()

    def add(t, seg, msg):
        if msg not in seen:
            seen.add(msg); out.append((t, seg, msg))
    try:
        with Film(project) as f:
            for m in f.errors:
                add(None, None, "error: " + m)
            for m in f.console:
                add(None, None, "console.error: " + m)
            segs = f.page.evaluate("((window.TIMELINE || {}).segments || []).map(s => [s.id, s.t0, s.t1])")
            ts = [(sid, t) for sid, t0, t1 in segs for t in (t0 + 0.01, (t0 + t1) / 2, max(t0, t1 - 0.01))] if segs else \
                 [(None, float(k)) for k in range(int(f.dur) + 1)]
            ne, nc = len(f.errors), len(f.console)
            for sid, t in ts:
                try:
                    f.seek(t)
                except PageError as e:
                    add(t, sid, "error: " + str(e).split(": ", 1)[1])
                for m in f.errors[ne:]:
                    add(t, sid, "error: " + m)
                for m in f.console[nc:]:
                    add(t, sid, "console.error: " + m)
                ne, nc = len(f.errors), len(f.console)
    except PageError as e:
        add(None, None, "error: " + str(e))
    return out


def repeat_diffs(project, times):
    """[(t, share of pixels that differ)] for each moment rendered twice: again in the same page after seeking away,
    and in a fresh browser. Anything above 0 means the film's frames are not repeatable."""
    import numpy as np
    with Film(project) as f:
        first = [f.shot(t) for t in times]
        again = [f.shot(t) for t in reversed(times)][::-1]
    with Film(project) as f:
        fresh = [f.shot(t) for t in times]
        w, h = f.w, f.h

    def px(png):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", "-", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             input=png, capture_output=True, check=True).stdout
        return np.frombuffer(raw, np.uint8).reshape(h, w, 3)
    out = []
    for t, a, b, c in zip(times, first, again, fresh):
        worst = 0.0
        for o in (b, c):
            if o != a:
                worst = max(worst, float((px(a) != px(o)).any(axis=2).mean()))
        out.append((t, worst))
    return out


def stills(project, times, out_dir, bars=False):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with Film(project) as f:
        ts = f.bar_times() if bars else [float(t) for t in times]
        for i, t in enumerate(ts):
            name = f"{i:02d}-t{t:06.2f}.png" if bars else f"t{t:06.2f}.png"
            (out / name).write_bytes(f.shot(t))
        return out, len(ts), f.errors


def sheet(src_dir, dest, cols=6, tile_w=300):
    """Stack a folder of PNGs into one contact sheet (videos: verify.beat_sheet)."""
    src = Path(src_dir)
    files = sorted(src.glob("*.png"))
    if not files:
        raise SystemExit(f"no PNGs in {src}")
    from struct import unpack
    with open(files[0], "rb") as fh:
        head = fh.read(24)
    w, h = unpack(">II", head[16:24])
    th = round(tile_w * h / w)
    inputs = sum([["-i", str(p)] for p in files], [])
    scales = "".join(f"[{i}:v]scale={tile_w}:{th}[v{i}];" for i in range(len(files)))
    layout = "|".join(f"{(i % cols) * tile_w}_{(i // cols) * th}" for i in range(len(files)))
    if len(files) == 1:
        graph = f"[0:v]scale={tile_w}:{th}"
    else:
        graph = scales + "".join(f"[v{i}]" for i in range(len(files))) + f"xstack=inputs={len(files)}:layout={layout}:fill=0x333333"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", graph, str(dest)], check=True)
    return dest


def probe_size(video):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(video)],
                         capture_output=True, text=True, check=True).stdout.strip()
    w, h = out.split(",")
    return int(w), int(h)


def film(project, dest, scale=1.0, sub=1, fps=FPS):
    """Render the whole film to a silent mp4. sub > 1 averages sub-frames for motion blur (sub=4 ≈ 270° shutter)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with Film(project) as f:
        n = int(-(-f.dur * fps // 1))          # ceil: a whole-bar film ends on its last bar, not a frame short
        blur = f"tmix=frames={sub},select='not(mod(n+1\\,{sub}))',setpts=N/({fps}*TB)," if sub > 1 else ""
        # even dimensions for libx264, BT.709 limited range, tagged, fast start
        vf = blur + f"scale=trunc(iw*{scale}/2)*2:trunc(ih*{scale}/2)*2:out_color_matrix=bt709:out_range=tv,format=yuv420p"
        ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "image2pipe", "-framerate", str(fps * sub), "-i", "-",
                               "-vf", vf, "-r", str(fps), "-c:v", "libx264", "-preset", "slow", "-crf", "16",
                               "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
                               "-movflags", "+faststart", str(dest)], stdin=subprocess.PIPE)
        for i in range(n):
            for k in range(sub):
                ff.stdin.write(f.shot((i + k / sub) / fps))
            if i % 150 == 0:
                print(f"frame {i}/{n}", flush=True)
        ff.stdin.close()
        if ff.wait() != 0:
            raise SystemExit("ffmpeg failed")
    return dest


# A lossy encode can overshoot its source: ffmpeg's own AAC turned a mix peaking at -2.5 dBFS into a +4.7 dBFS spike
# (an audible click, toll-text 2026-10-23). The delivered track must decode at or under this, which also leaves room for
# YouTube's own re-encode.
CEILING_DB = -1.0
# encodes tried in order until one decodes under the ceiling: (bitrate, limit in dBFS on the 4x-oversampled mix or None)
MUX_LADDER = [("192k", None), ("256k", None), ("256k", -2.0), ("256k", -3.0)]


def aac_encoder():
    """AudioToolbox's AAC where ffmpeg has it (macOS), which held -2.3 dBTP on the mix that clicked; else ffmpeg's own."""
    out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return "aac_at" if any(line.split()[1:2] == ["aac_at"] for line in out.splitlines()) else "aac"


def decoded_peak(path):
    """(highest sample in dBFS, samples over full scale) of the file's audio as a player decodes it."""
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    x = np.abs(np.frombuffer(raw, dtype=np.float32))
    return 20 * np.log10(max(float(x.max(initial=0)), 1e-9)), int((x > 1).sum())


def mux(silent, mix, dest):
    """Video copied, mix encoded to AAC, then decoded and measured: the first encode on MUX_LADDER that stays under
    CEILING_DB is kept. A limit is applied only when plain encodes overshoot, and only to the peaks."""
    enc = aac_encoder()
    for i, (rate, limit) in enumerate(MUX_LADDER):
        af = [] if limit is None else ["-af", f"aresample=192000,alimiter=limit={10 ** (limit / 20):.4f}:level=false:attack=1:release=50,aresample=48000"]
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(silent), "-i", str(mix), "-c:v", "copy", *af, "-c:a", enc,
                        "-b:a", rate, "-ar", "48000", "-shortest", "-movflags", "+faststart", str(dest)], check=True)
        peak, over = decoded_peak(dest)
        how = f"{enc} {rate}" + (f" with a {limit:.0f} dBFS limit" if limit is not None else "")
        if peak <= CEILING_DB:
            if i:
                print(f"audio: kept {how}, decodes at {peak:+.1f} dBFS", flush=True)
            return dest
        print(f"audio: {how} decodes at {peak:+.1f} dBFS ({over} sample(s) over full scale), over the {CEILING_DB:.0f} dBFS ceiling",
              flush=True)
    raise SystemExit(f"{dest}: every encode overshoots {CEILING_DB:.0f} dBFS; lower the mix's peaks (master to -3 dBTP) and run again")
