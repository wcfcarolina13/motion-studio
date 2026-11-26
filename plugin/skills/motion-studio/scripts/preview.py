"""The studio preview: a localhost server for scrubbing, playing and annotating a film.

Serves preview.html at "/" (the Browser pane only accepts an origin), the film's folder tree under /w/,
and a small API:
  GET  /api/info            film name, film URL, mix URL (out/mix.wav if present)
  GET  /api/mtime           newest mtime of the film's sources and mix: the page polls it to hot-reload
  GET  /api/notes           notes.json
  POST /api/notes/<id>/done|open   the owner ticks a note done, or reopens it
  POST /api/notes           {"t": 12.3, "text": "hard cut here", "region"?: {"x","y","w","h"} in film pixels}
                            -> appends an open note, then saves a still of that exact frame (the region circled on it)
                            to review/notes/note-<id>.png in the background and records it as the note's "still"
  GET  /api/review          a long-form film's take groups (review_state.take_groups) and section states
  POST /api/picks           {"key": "m1c", "seed": 7 | null}: the owner's pick among a shot's takes, saved to picks.json
  POST /api/lock            {"title": "<chapter>", "locked": true | false}: the owner locks a section (review/state.json)
Picks and locks are the owner's clicks; agents never post them (review_state.py).
With --lan the server listens on the local network (for the owner's phone on home wifi) and every request needs the
link's key (?k=..., then a cookie), since anyone on that network could otherwise read and write the film.
Files are served with byte ranges, so the audio element can seek (python's http.server cannot).
"""
import json
import mimetypes
import os
import re
import secrets
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
WATCH = ("*.html", "*.js", "*.css", "*.json", "*.svg", "*.png", "*.jpg")


LOCK = threading.Lock()       # notes.json is written by request threads and still threads


def notes_path(film):
    return film / "notes.json"


def load_notes(film):
    p = notes_path(film)
    return json.loads(p.read_text()) if p.exists() else {"notes": []}


def save_notes(film, data):
    notes_path(film).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _update_note(film, note_id, **fields):
    with LOCK:
        data = load_notes(film)
        for n in data["notes"]:
            if n["id"] == note_id:
                n.update(fields)
        save_notes(film, data)


# The circle is drawn into the film's own page before the shot, so the still is the rendered frame, pixel for pixel,
# with the owner's mark on it (no image library needed).
MARK = """r => {
    const st = document.getElementById('stage');
    if (getComputedStyle(st).position === 'static') st.style.position = 'relative';
    const d = document.createElement('div');
    const pad = 6;
    Object.assign(d.style, { position: 'absolute', left: (r.x - pad) + 'px', top: (r.y - pad) + 'px',
        width: (r.w + 2 * pad) + 'px', height: (r.h + 2 * pad) + 'px', borderRadius: '50%', boxSizing: 'border-box',
        border: '6px solid #ff5a1f', boxShadow: '0 0 0 3px rgba(0,0,0,.65), inset 0 0 0 3px rgba(0,0,0,.65)',
        zIndex: 2147483647, pointerEvents: 'none' });
    st.appendChild(d);
}"""


def take_still(film: Path, note_id: int, t: float, region=None):
    """Save note <id>'s frame as review/notes/note-<id>.png, with its region circled. Runs in a background thread."""
    try:
        import render
        out = film / "review" / "notes" / f"note-{note_id}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        with render.Film(film) as f:
            f.seek(t)
            if region:
                f.page.evaluate(MARK, region)
            out.write_bytes(f.page.locator("#stage").screenshot(type="png", animations="disabled"))
        _update_note(film, note_id, still=str(out.relative_to(film)))
    except BaseException as e:      # SystemExit from render included: the note stands without a picture
        _update_note(film, note_id, still_error=str(e)[:300])


def _region(body):
    r = body.get("region")
    if not isinstance(r, dict):
        return None
    try:
        r = {k: round(float(r[k])) for k in ("x", "y", "w", "h")}
    except (KeyError, TypeError, ValueError):
        return None
    return r if r["w"] >= 4 and r["h"] >= 4 else None


def review(film: Path):
    """Take groups and section states for the dashboard ({} parts for a film that isn't long-form)."""
    import review_state
    try:
        script = json.loads((film / "script.json").read_text())
    except (OSError, ValueError):
        return {"groups": [], "sections": []}
    try:
        tl = json.loads((film / "timeline.js").read_text().split("=", 1)[1].rstrip().rstrip(";"))
    except (OSError, ValueError, IndexError):
        tl = None
    notes = load_notes(film)["notes"]
    return {"groups": review_state.take_groups(film, script, tl), "sections": review_state.sections(film, notes)}


def make_handler(film: Path, key=None):
    root = film.parent            # films may reference siblings (e.g. ../sources/), so serve the parent tree
    base = f"/w/{film.name}/"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _allowed(self):
            """With a key (--lan), a request carries it in ?k= (the link) or the cookie set from it."""
            if not key:
                return True
            q = re.search(r"[?&]k=([^&]+)", self.path)
            c = re.search(r"(?:^|;\s*)msk=([^;]+)", self.headers.get("Cookie", ""))
            ok = (q and secrets.compare_digest(q.group(1), key)) or (c and secrets.compare_digest(c.group(1), key))
            self._ok = bool(ok)
            if not ok:
                self.send_error(403, "open the link with its key")
            return bool(ok)

        def end_headers(self):
            if key and getattr(self, "_ok", False):      # the cookie only ever goes to a request that already had the key
                self.send_header("Set-Cookie", f"msk={key}; Path=/; SameSite=Strict; HttpOnly")
            super().end_headers()

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _file(self, path: Path):
            if not path.is_file():
                return self.send_error(404)
            size = path.stat().st_size
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            rng = self.headers.get("Range")
            start, end = 0, size - 1
            m = re.match(r"bytes=(\d*)-(\d*)", rng or "")
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                else:
                    start = size - int(m.group(2))
                end = min(end, size - 1)
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            else:
                self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(end - start + 1))
            self.end_headers()
            with open(path, "rb") as fh:
                fh.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = fh.read(min(65536, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)

        def do_GET(self):
            if not self._allowed():
                return
            path = self.path.split("?")[0]
            if path == "/":
                return self._file(HERE / "preview.html")
            if path == "/api/info":
                mix = film / "out" / "mix.wav"
                return self._json({"film": film.name, "url": base + "film.html", "mix": base + "out/mix.wav" if mix.exists() else None})
            if path == "/api/mtime":
                files = [p for pat in WATCH for p in film.glob(pat) if p.name != "notes.json"] + [film / "out" / "mix.wav"]   # notes must not reload the film
                return self._json({"mtime": max((p.stat().st_mtime for p in files if p.exists()), default=0)})
            if path == "/api/notes":
                return self._json(load_notes(film))
            if path == "/api/review":
                return self._json(review(film))
            if path.startswith("/w/"):
                target = (root / path[3:]).resolve()
                if root.resolve() not in target.parents and target != root.resolve():
                    return self.send_error(403)
                return self._file(target)
            self.send_error(404)

        def do_POST(self):
            if not self._allowed():
                return
            self.path = self.path.split("?")[0]
            if self.path in ("/api/picks", "/api/lock"):
                import review_state
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                with LOCK:
                    if self.path == "/api/picks":
                        k = str(body.get("key", ""))
                        groups = {g["key"]: g for g in review(film)["groups"]}
                        if k not in groups:
                            return self._json({"error": f"no take group {k!r}"}, 404)
                        picks = review_state.load_picks(film)
                        if body.get("seed") is None:
                            picks.pop(k, None)
                        elif int(body["seed"]) in [x["seed"] for x in groups[k]["takes"]]:
                            picks[k] = int(body["seed"])
                        else:
                            return self._json({"error": f"{k} has no take with seed {body['seed']}"}, 400)
                        review_state.save_picks(film, picks)
                        return self._json({"key": k, "pick": picks.get(k)})
                    try:
                        st = review_state.set_lock(film, str(body.get("title", "")), bool(body.get("locked")), load_notes(film)["notes"])
                    except KeyError:
                        return self._json({"error": "no such section"}, 404)
                    return self._json(st)
            m = re.fullmatch(r"/api/notes/(\d+)/(done|open)", self.path)
            if m:                       # the owner ticks a note done (or reopens it) from the page
                nid, status = int(m.group(1)), m.group(2)
                with LOCK:
                    data = load_notes(film)
                    for n in data["notes"]:
                        if n["id"] == nid:
                            n["status"] = status
                            if status == "done":
                                n["resolved"], n["closed_by"] = time.strftime("%Y-%m-%d %H:%M:%S"), "owner"
                            else:
                                for k in ("closed_by", "resolved", "reply"):
                                    n.pop(k, None)
                            save_notes(film, data)
                            return self._json(n)
                return self._json({"error": f"no note #{nid}"}, 404)
            if self.path != "/api/notes":
                return self.send_error(404)
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            text = str(body.get("text", "")).strip()
            if not text:
                return self._json({"error": "empty note"}, 400)
            region = _region(body)
            with LOCK:
                data = load_notes(film)
                note = {"id": 1 + max((n["id"] for n in data["notes"]), default=0), "t": round(float(body.get("t", 0)), 3),
                        "where": body.get("where", ""), "text": text, "status": "open", "created": time.strftime("%Y-%m-%d %H:%M:%S")}
                if region:
                    note["region"] = region
                data["notes"].append(note)
                save_notes(film, data)
            threading.Thread(target=take_still, args=(film, note["id"], note["t"], region), daemon=True).start()
            return self._json(note)

    return Handler


class Server(ThreadingHTTPServer):
    """The stdlib default listen backlog is 5. A film page asks for a dozen scripts and stills at once, and over Wi-Fi
    macOS resets every connection past the backlog, so a phone got a black page (three.min.js, set3d.js and
    timeline.js never loaded). Loopback hides it. Measured on the owner's Mac over the LAN IP, 30 parallel fetches:
    25 reset at 5, 30 of 30 at 128 (2026-11-26)."""
    request_queue_size = 128


def _run(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def lan_urls(port, key):
    """[(label, url)] a phone can open: the Tailscale IP when the Mac is on a tailnet, then the Bonjour name, then the
    Wi-Fi IP as a fallback.
    The Bonjour name is macOS's LocalHostName (`scutil --get LocalHostName`), which is not the computer name that
    gethostname() returns: http://Mac.local did not resolve on the owner's network (2026-11-26)."""
    out, q = [], f":{port}/?k={key}"
    # Tailscale first: a Wi-Fi that isolates clients (the owner's building, 2026-11-26) blocks phone to Mac, and the
    # tailnet gets through it. Its traffic is encrypted end to end, unlike the plain-HTTP Wi-Fi links below.
    ts = (_run("tailscale", "ip", "-4") or _run("/Applications/Tailscale.app/Contents/MacOS/Tailscale", "ip", "-4")).split()
    if ts:
        out.append(("over Tailscale (works on any network)", f"http://{ts[0]}{q}"))
    name = _run("scutil", "--get", "LocalHostName") or socket.gethostname().split(".")[0]
    if name:
        out.append(("by name", f"http://{name}.local{q}"))
    ip = _run("ipconfig", "getifaddr", "en0") or _run("ipconfig", "getifaddr", "en1")
    if not ip:
        try:  # the address of the interface that routes outward; a UDP connect sends no packet
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as u:
                u.connect(("192.0.2.1", 9)); ip = u.getsockname()[0]
        except OSError:
            ip = ""
    if ip and not ip.startswith("127."):
        out.append(("by IP, if the name does not open", f"http://{ip}{q}"))
    return out


def serve(film: Path, port: int = 4173, lan: bool = False):
    """localhost only by default. lan=True listens on every interface so a phone on the same wifi can open it, behind
    a random key in the link (kept in review/.lan-key so the link stays the same across restarts)."""
    key = None
    if lan:
        kp = film / "review" / ".lan-key"
        kp.parent.mkdir(parents=True, exist_ok=True)
        key = kp.read_text().strip() if kp.exists() else secrets.token_urlsafe(18)
        kp.write_text(key + "\n"); os.chmod(kp, 0o600)
    server = Server(("0.0.0.0" if lan else "127.0.0.1", port), make_handler(film, key))
    print(f"motion-studio preview for {film.name}: http://localhost:{port}" + (f"/?k={key}" if key else ""), flush=True)
    if lan:
        for label, url in lan_urls(port, key):
            print(f"on the home network (phone), {label}: {url}", flush=True)
    server.serve_forever()


def list_notes(film: Path, show_all=False):
    notes = load_notes(film)["notes"]
    for n in notes:
        if show_all or n["status"] == "open":
            reply = f"  -> {n['reply']}" if n.get("reply") else ""
            print(f"#{n['id']} [{n['status']}] t={n['t']:.2f}s {n.get('where', '')}: {n['text']}{reply}")
            if n.get("still"):
                r = n.get("region")
                print(f"     still: {film / n['still']}" + (f"  (circled x={r['x']} y={r['y']} w={r['w']} h={r['h']} px)" if r else ""))
            elif n.get("still_error"):
                print(f"     still failed: {n['still_error']}")
    if not any(n["status"] == "open" for n in notes):
        print("no open notes")


def resolve_note(film: Path, note_id: int, reply: str):
    data = load_notes(film)
    for n in data["notes"]:
        if n["id"] == note_id:
            n["status"], n["reply"], n["resolved"] = "done", reply, time.strftime("%Y-%m-%d %H:%M:%S")
            save_notes(film, data)
            return n
    raise SystemExit(f"no note #{note_id}")
