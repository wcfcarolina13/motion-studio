"""The review dashboard's state for a long-form film: take groups and the owner's picks, and each section's progress.

Takes. A panel's still or clip named <shot>_s<seed>.<ext> with sibling files <shot>_s<other seed>.<ext> is a take group.
The owner picks one in the studio dashboard (preview.py) and the pick is saved to <film>/picks.json as
{"<shot>": <seed>} (a clip group whose shot also has still takes is keyed "<shot>:clip"). `timeline` swaps each picked
take into the panel. Agents never pick among takes: a group with no pick keeps the script's path and its section shows
"takes waiting" (owner's rule, 2026-11-26).

Sections are the film's chapters. Each one's state, first match wins:
  locked       the owner locked it (his click in the dashboard, saved to review/state.json); "changed since lock" when its
               segments or picks differ from what he locked
  not started  it has panels and none of their pictures exists yet
  takes        a take group in it has no pick
  review       a note inside it is open
  in progress  some of its panel pictures are still missing
  ready        everything is there and picked, and no note is open: waiting for his lock
"""
import hashlib
import json
import re
import time
from pathlib import Path

TAKE = re.compile(r"^(?P<shot>.+)_s(?P<seed>\d+)$")
STATES = ("locked", "not_started", "takes", "review", "in_progress", "ready")


def _json(p, default):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return default


def load_picks(film):
    return _json(film / "picks.json", {})


def save_picks(film, picks):
    tmp = film / "picks.json.part"
    tmp.write_text(json.dumps(picks, indent=1, sort_keys=True) + "\n"); tmp.replace(film / "picks.json")


def load_state(film):
    return _json(film / "review" / "state.json", {"locked": {}})


def save_state(film, st):
    (film / "review").mkdir(parents=True, exist_ok=True)
    tmp = film / "review" / "state.json.part"
    tmp.write_text(json.dumps(st, indent=1) + "\n"); tmp.replace(film / "review" / "state.json")


def takes_of(film, path):
    """(shot, [(seed, relative path), ...]) for a panel file: its take siblings, or None when it has under two."""
    p = Path(path)
    m = TAKE.match(p.stem)
    if not m:
        return None
    shot, d = m.group("shot"), film / p.parent
    out = []
    for f in sorted(d.glob(f"{glob_escape(shot)}_s*{p.suffix}")):
        k = TAKE.match(f.stem)
        if k and k.group("shot") == shot:
            out.append((int(k.group("seed")), str(f.relative_to(film))))
    out.sort()
    return (shot, out) if len(out) > 1 else None


def glob_escape(s):
    return re.sub(r"([\[\]*?])", r"[\1]", s)


def pick_key(shot, kind, both):
    return f"{shot}:clip" if kind == "clip" and both else shot


def apply_picks(film, data, picks, problems, sid):
    """Swap the owner's picked takes into a panel's data (image and clip), in place."""
    found = {k: takes_of(film, data[k]) for k in ("image", "clip") if isinstance(data.get(k), str)}
    shots = [g[0] for g in found.values() if g]
    for kind, g in found.items():
        if not g:
            continue
        key = pick_key(g[0], kind, shots.count(g[0]) > 1)
        if key in picks:
            hit = [p for s, p in g[1] if s == int(picks[key])]
            if hit:
                data[kind] = hit[0]
            else:
                problems.append(f"{sid}: picked take {key} seed {picks[key]} has no file among {', '.join(p for _, p in g[1])}")


def take_groups(film, script, timeline=None):
    """[{key, shot, kind, segments, t0, t1, takes: [{seed, path}], pick}] for every take group, in film order."""
    times = {s["id"]: (s["t0"], s["t1"]) for s in (timeline or {}).get("segments", [])}
    picks, groups = load_picks(film), {}
    for s in script.get("segments", []):
        d = s.get("data") or {}
        if s.get("type") != "panel":
            continue
        found = {k: takes_of(film, d[k]) for k in ("image", "clip") if isinstance(d.get(k), str)}
        shots = [g[0] for g in found.values() if g]
        for kind, g in found.items():
            if not g:
                continue
            key = pick_key(g[0], kind, shots.count(g[0]) > 1)
            e = groups.setdefault(key, {"key": key, "shot": g[0], "kind": kind, "segments": [], "t0": None, "t1": None,
                                        "takes": [{"seed": sd, "path": p} for sd, p in g[1]], "pick": picks.get(key)})
            e["segments"].append(s["id"])
            if s["id"] in times:
                a, b = times[s["id"]]
                e["t0"] = a if e["t0"] is None else min(e["t0"], a)
                e["t1"] = b if e["t1"] is None else max(e["t1"], b)
    return list(groups.values())


def _stamp(segs, picks, keys):
    blob = json.dumps({"segments": segs, "picks": {k: picks.get(k) for k in sorted(keys)}}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def sections(film, notes=()):
    """[{title, t0, t1, state, why, stale?}] for the film's chapters, or [] for a film without script and timeline."""
    try:
        script = json.loads((film / "script.json").read_text())
        tl = json.loads((film / "timeline.js").read_text().split("=", 1)[1].rstrip().rstrip(";"))
    except (OSError, ValueError, IndexError):
        return []
    chs, segs = tl.get("chapters") or [], tl.get("segments") or []
    sseg = {s["id"]: s for s in script.get("segments", [])}
    groups, picks, locked = take_groups(film, script, tl), load_picks(film), load_state(film).get("locked", {})
    out = []
    for i, ch in enumerate(chs):
        t0, t1 = ch["t0"], ch.get("t1", tl.get("duration", ch["t0"]))
        mine = [s for s in segs if t0 - 1e-6 <= s["t0"] < t1 - 1e-6]
        ids = {s["id"] for s in mine}
        src = [sseg[i_] for i_ in (s["id"] for s in mine) if i_ in sseg]
        gs = [g for g in groups if ids & set(g["segments"])]
        stamp = _stamp(src, picks, [g["key"] for g in gs])
        panels = [s for s in src if s.get("type") == "panel"]
        files = [x for s in panels for x in ((s.get("data") or {}).get("image"), (s.get("data") or {}).get("clip")) if isinstance(x, str)]
        have = [x for x in files if (film / x).exists() or takes_of(film, x)]
        waiting = [g["key"] for g in gs if g["pick"] is None]
        open_n = [n for n in notes if n.get("status") == "open" and t0 - 1e-6 <= float(n.get("t", -1)) < t1]
        e = {"title": ch["title"], "t0": t0, "t1": t1, "stamp": stamp}
        if ch["title"] in locked:
            e.update(state="locked", why=f"locked {locked[ch['title']].get('at', '')}".strip())
            if locked[ch["title"]].get("stamp") != stamp:
                e.update(stale=True, why="changed since you locked it")
        elif files and not have:
            e.update(state="not_started", why="no pictures yet")
        elif waiting:
            e.update(state="takes", why=f"{len(waiting)} shot(s) waiting for your pick")
        elif open_n:
            e.update(state="review", why=f"{len(open_n)} open note(s)")
        elif len(have) < len(files):
            e.update(state="in_progress", why=f"{len(files) - len(have)} of {len(files)} pictures still to make")
        else:
            e.update(state="ready", why="all picked, no open notes: ready for your lock")
        out.append(e)
    return out


def set_lock(film, title, on, notes=(), who="owner (dashboard)"):
    st = load_state(film)
    if on:
        sec = next((s for s in sections(film, notes) if s["title"] == title), None)
        if not sec:
            raise KeyError(title)
        st.setdefault("locked", {})[title] = {"at": time.strftime("%Y-%m-%d %H:%M"), "by": who, "stamp": sec["stamp"]}
    else:
        st.setdefault("locked", {}).pop(title, None)
    save_state(film, st)
    return st
