#!/usr/bin/env python3
"""YouTube uploads for motion-studio films. Standard library only.

One-time setup (Google Cloud Console): enable "YouTube Data API v3", set the OAuth consent
screen to External + Testing with your Google account as a test user, create an OAuth client of
type "Desktop app", and save its JSON as ~/.motion-studio/youtube/client_secret.json (or set YT_SECRETS).

  youtube.py auth                       sign in on this machine (opens the browser, catches the redirect)
  youtube.py auth-url                   print a sign-in link to open anywhere; then:
  youtube.py exchange "<redirected URL>" paste the http://127.0.0.1... address the browser landed on
  youtube.py whoami                     the channel the token uploads to
  youtube.py upload FILE --title T [--description-file F] [--tags a,b] [--privacy private|unlisted|public]
                    [--category 27] [--thumbnail PNG] [--made-for-kids] [--synthetic]
  youtube.py set-privacy VIDEO_ID private|unlisted|public
  youtube.py captions VIDEO_ID FILE.vtt [--language en] [--name English]   add a caption track (longform.py writes one)

Privacy defaults to private. Nothing here publishes publicly unless --privacy public is passed.
Tokens live next to the client secret, in token.json. While the consent screen is in Testing, Google expires the
refresh token after 7 days; run `auth` again, or move the app to In production for a lasting token.
"""
import argparse
import http.server
import json
import mimetypes
import os
import secrets
import sys
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
SECRETS = Path(os.environ.get("YT_SECRETS", Path.home() / ".motion-studio" / "youtube")).expanduser()
CLIENT = SECRETS / "client_secret.json"
TOKEN = SECRETS / "token.json"
# upload + readonly for videos; force-ssl is the scope captions.insert requires
SCOPES = ("https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly "
          "https://www.googleapis.com/auth/youtube.force-ssl")
PORT = 8765
REDIRECT = f"http://127.0.0.1:{PORT}/"


def client():
    if not CLIENT.exists():
        raise SystemExit(f"missing {CLIENT}: download the Desktop OAuth client JSON there (see the docstring)")
    data = json.loads(CLIENT.read_text())
    return data.get("installed") or data.get("web")


def post_form(url, fields):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode(), method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{url} -> {e.code}: {e.read().decode()[:500]}")


def auth_url():
    c = client()
    state = secrets.token_urlsafe(16)
    SECRETS.mkdir(parents=True, exist_ok=True)
    (SECRETS / "state").write_text(state)
    q = {"client_id": c["client_id"], "redirect_uri": REDIRECT, "response_type": "code", "scope": SCOPES,
         "access_type": "offline", "prompt": "consent", "state": state}
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(q)


def exchange(redirected):
    q = urllib.parse.parse_qs(urllib.parse.urlparse(redirected).query)
    if "error" in q:
        raise SystemExit(f"sign-in refused: {q['error'][0]}")
    state_file = SECRETS / "state"
    if state_file.exists() and q.get("state", [""])[0] != state_file.read_text():
        raise SystemExit("state mismatch: use the link from the latest auth-url")
    c = client()
    tok = post_form(c.get("token_uri", "https://oauth2.googleapis.com/token"),
                    {"code": q["code"][0], "client_id": c["client_id"], "client_secret": c["client_secret"],
                     "redirect_uri": REDIRECT, "grant_type": "authorization_code"})
    tok["expires_at"] = time.time() + tok.get("expires_in", 3600) - 60
    TOKEN.write_text(json.dumps(tok, indent=2))
    os.chmod(TOKEN, 0o600)
    print(f"token saved to {TOKEN}")


def access_token():
    if not TOKEN.exists():
        raise SystemExit("no token: run `youtube.py auth` (or auth-url + exchange) first")
    tok = json.loads(TOKEN.read_text())
    if time.time() < tok.get("expires_at", 0):
        return tok["access_token"]
    c = client()
    new = post_form(c.get("token_uri", "https://oauth2.googleapis.com/token"),
                    {"refresh_token": tok["refresh_token"], "client_id": c["client_id"],
                     "client_secret": c["client_secret"], "grant_type": "refresh_token"})
    tok.update(new)
    tok["expires_at"] = time.time() + new.get("expires_in", 3600) - 60
    TOKEN.write_text(json.dumps(tok, indent=2))
    return tok["access_token"]


def api(method, url, body=None, headers=None, raw=None):
    h = {"Authorization": f"Bearer {access_token()}"}
    h.update(headers or {})
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    if body is not None and raw is None:
        h["Content-Type"] = "application/json; charset=UTF-8"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def cmd_auth(a):
    url = auth_url()
    got = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            got["url"] = REDIRECT.rstrip("/") + self.path
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Signed in. You can close this tab.")

        def log_message(self, *args):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", PORT), H)
    print("Opening the browser to sign in. If it doesn't open, visit:\n" + url)
    webbrowser.open(url)
    while "url" not in got:
        srv.handle_request()
    exchange(got["url"])


def cmd_whoami(a):
    s, _, b = api("GET", "https://www.googleapis.com/youtube/v3/channels?part=snippet,status&mine=true")
    data = json.loads(b)
    if s != 200:
        raise SystemExit(f"{s}: {b.decode()[:400]}")
    for ch in data.get("items", []):
        print(ch["id"], ch["snippet"]["title"], ch["snippet"].get("customUrl", ""))
    if not data.get("items"):
        print("this Google account has no YouTube channel yet: create one at youtube.com first")


def cmd_upload(a):
    path = Path(a.file)
    size = path.stat().st_size
    if a.privacy == "public" and not a.yes_public:
        raise SystemExit("refusing public upload without --yes-public")
    desc = Path(a.description_file).read_text() if a.description_file else (a.description or "")
    meta = {
        "snippet": {"title": a.title[:100], "description": desc[:5000], "categoryId": a.category,
                    "tags": [t.strip() for t in a.tags.split(",")] if a.tags else []},
        "status": {"privacyStatus": a.privacy, "selfDeclaredMadeForKids": a.made_for_kids,
                   "containsSyntheticMedia": a.synthetic},
    }
    s, h, b = api("POST", "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
                  body=meta, headers={"X-Upload-Content-Length": str(size),
                                      "X-Upload-Content-Type": mimetypes.guess_type(path.name)[0] or "video/mp4"})
    if s != 200:
        raise SystemExit(f"start upload -> {s}: {b.decode()[:600]}")
    session = h.get("Location") or h.get("location")
    chunk, sent = 16 * 1024 * 1024, 0
    with open(path, "rb") as fh:
        while sent < size:
            fh.seek(sent)
            piece = fh.read(chunk)
            end = sent + len(piece) - 1
            for attempt in range(5):
                s, h, b = api("PUT", session, raw=piece, headers={"Content-Range": f"bytes {sent}-{end}/{size}",
                                                                  "Content-Type": "application/octet-stream"})
                if s in (200, 201, 308):
                    break
                if s in (500, 502, 503, 504):
                    time.sleep(2 ** attempt)
                    continue
                raise SystemExit(f"upload chunk -> {s}: {b.decode()[:600]}")
            if s == 308:
                rng = h.get("Range") or h.get("range")
                sent = int(rng.split("-")[1]) + 1 if rng else 0
                print(f"  {sent * 100 // size}%", flush=True)
            else:
                break
    video = json.loads(b)
    vid = video["id"]
    print(f"uploaded {vid} ({video['status']['privacyStatus']}): https://youtu.be/{vid}")
    print(f"studio: https://studio.youtube.com/video/{vid}/edit")
    if a.thumbnail:
        tp = Path(a.thumbnail)
        s, _, b = api("POST", f"https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId={vid}",
                      raw=tp.read_bytes(), headers={"Content-Type": mimetypes.guess_type(tp.name)[0] or "image/png"})
        print("thumbnail:", "set" if s == 200 else f"{s} {b.decode()[:300]} (custom thumbnails need a phone-verified channel)")
    (path.parent / f"{path.stem}.youtube.json").write_text(json.dumps(video, indent=2))


def cmd_set_privacy(a):
    if a.privacy == "public" and not a.yes_public:
        raise SystemExit("refusing to make public without --yes-public")
    s, _, b = api("GET", f"https://www.googleapis.com/youtube/v3/videos?part=status&id={a.video_id}")
    items = json.loads(b).get("items", [])
    if not items:
        raise SystemExit(f"no video {a.video_id} on this channel")
    st = items[0]["status"]
    st["privacyStatus"] = a.privacy
    s, _, b = api("PUT", "https://www.googleapis.com/youtube/v3/videos?part=status", body={"id": a.video_id, "status": st})
    print(s, json.loads(b).get("status", b.decode()[:300]))


def cmd_captions(a):
    meta = json.dumps({"snippet": {"videoId": a.video_id, "language": a.language, "name": a.name, "isDraft": False}})
    boundary = "ms" + secrets.token_hex(8)
    body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n"
            f"--{boundary}\r\nContent-Type: text/vtt\r\n\r\n").encode() + Path(a.file).read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    s, _, b = api("POST", "https://www.googleapis.com/upload/youtube/v3/captions?part=snippet&uploadType=multipart",
                  raw=body, headers={"Content-Type": f"multipart/related; boundary={boundary}"})
    if s != 200:
        raise SystemExit(f"captions -> {s}: {b.decode()[:500]} (a token from before the captions scope was added needs `auth` again)")
    print("caption track added:", json.loads(b)["id"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("auth").set_defaults(fn=cmd_auth)
    sp.add_parser("auth-url").set_defaults(fn=lambda a: print(auth_url()))
    p = sp.add_parser("exchange"); p.add_argument("url"); p.set_defaults(fn=lambda a: exchange(a.url))
    sp.add_parser("whoami").set_defaults(fn=cmd_whoami)
    p = sp.add_parser("upload"); p.add_argument("file"); p.add_argument("--title", required=True)
    p.add_argument("--description"); p.add_argument("--description-file"); p.add_argument("--tags")
    p.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"])
    p.add_argument("--yes-public", action="store_true"); p.add_argument("--category", default="27")
    p.add_argument("--thumbnail"); p.add_argument("--made-for-kids", action="store_true")
    p.add_argument("--synthetic", action="store_true", help="declare altered or synthetic content")
    p.set_defaults(fn=cmd_upload)
    p = sp.add_parser("set-privacy"); p.add_argument("video_id"); p.add_argument("privacy", choices=["private", "unlisted", "public"])
    p.add_argument("--yes-public", action="store_true"); p.set_defaults(fn=cmd_set_privacy)
    p = sp.add_parser("captions"); p.add_argument("video_id"); p.add_argument("file"); p.add_argument("--language", default="en")
    p.add_argument("--name", default="English"); p.set_defaults(fn=cmd_captions)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
