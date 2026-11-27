"""Beat sync: does something new land on screen on the words, often enough? (owner, 2026-11-27: "improve how well we pace
what is on screen/the beats with the voiced lines to make sure it is in sync").

From the timeline alone (no rendering): every "visual event" of a segment, meaning its cut and each thing that lands
on a word (any data item with a word time, the counter, the tag, tally lines), against its spoken words. It reports
  - gaps: stretches of narration longer than GAP s with nothing new landing, each with the words said in it and a
    suggested cue word (a number first, then the longest content word near the middle);
  - uncued reveals: items that appear at a default time instead of on a word, with the word in the narration that
    matches their own text, which `pace --apply` writes into script.json as "at";
  - the film's rhythm: the median time between events while the voice runs, and the share of spoken time within AIM s
    of the last event.
A clip panel counts as continuously new (moving footage), and so is skipped. Research behind the numbers: strong
business explainers land something every 2 to 4 s (editing study, 2026-11-27); a still held longer than ~4 s with
nothing new reads as stalled.
"""
import re
import statistics

GAP = 4.0      # s of narration with nothing new on screen: flagged
AIM = 3.0      # s: what "often enough" means for the rhythm line
# data lists whose items are revealed one by one, so each should land on a word
REVEAL_KEYS = ("callouts", "ink", "items", "bullets", "nodes", "spokes", "marks", "series", "pieces", "lines", "rows", "steps", "points_of")
STOP = set("""a an and are as at be been but by can could did do does for from had has have he her his how i if in into is it
its just like more most much my no not now of on once one only or our out over own she so some such than that the their them
then there these they this those through to too up us very was we were what when where which while who why will with would
you your yours about after again all also any because before being both down each few here off other same should until""".split())


def _clean(w):
    return re.sub(r"[^\w$%.,'-]", "", w).strip(".,'").lower()


def _num(w):
    return bool(re.search(r"\d", w)) or _clean(w) in NUMWORDS


NUMWORDS = set("""two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen
eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand million billion percent""".split())


def walk_timed(d):
    """Every word time inside a segment's data."""
    if isinstance(d, dict):
        if isinstance(d.get("t"), (int, float)) and "at" in d:
            yield d["t"]
        for v in d.values():
            yield from walk_timed(v)
    elif isinstance(d, list):
        for v in d:
            yield from walk_timed(v)


def events(seg):
    """Times something new lands in this segment, sorted: its cut, then everything cued to a word."""
    ts = {seg["t0"]}
    ts |= set(walk_timed(seg.get("data")))
    for k in ("counter", "tag"):
        v = seg.get(k)
        if isinstance(v, dict) and isinstance(v.get("t"), (int, float)):
            ts.add(v["t"])
    ta = seg.get("tally")
    if isinstance(ta, dict):
        ts |= {it["t"] for it in ta.get("items") or [] if isinstance(it, dict) and isinstance(it.get("t"), (int, float))}
        if isinstance(ta.get("total"), dict) and isinstance(ta["total"].get("t"), (int, float)):
            ts.add(ta["total"]["t"])
    return sorted(t for t in ts if seg["t0"] - 0.01 <= t <= seg["t1"])


def moving(seg):
    return seg.get("type") == "panel" and bool((seg.get("data") or {}).get("clip"))


def suggest(words):
    """A cue word among these: a number first, else the longest content word nearest the middle."""
    if not words:
        return None
    nums = [w for w in words if _num(w[0])]
    if nums:
        return nums[0]
    content = [w for w in words if len(_clean(w[0])) >= 4 and _clean(w[0]) not in STOP]
    if not content:
        return None
    mid = (words[0][1] + words[-1][2]) / 2
    return max(content, key=lambda w: (len(_clean(w[0])) - abs(w[1] - mid) * 2))


def gaps(seg, gap=GAP):
    """[{seg, t0, t1, words, suggest}] for each stretch of this segment's narration with nothing new for over `gap` s."""
    ws = seg.get("words") or []
    if not ws or moving(seg):
        return []
    ev = events(seg)
    end = ws[-1][2]
    out = []
    for a, b in zip(ev, ev[1:] + [end]):
        b = min(b, end)
        if b - a > gap:
            inside = [w for w in ws if w[1] >= a + 1.0 and w[2] <= b]        # words said after the last landing settles
            s = suggest(inside)
            out.append({"seg": seg["id"], "t0": round(a, 2), "t1": round(b, 2), "len": round(b - a, 1),
                        "said": " ".join(w[0] for w in ws if a <= w[1] < b), "suggest": s[0] if s else None})
    return out


def _item_words(it):
    txt = " ".join(str(it.get(k, "")) for k in ("value", "text", "label", "title") if it.get(k) is not None)
    return [_clean(x) for x in re.split(r"\s+", txt) if _clean(x)]


def uncued(seg, script_seg=None):
    """[{seg, key, index, text, match}] for revealed items with no word time; match is the narration word (or phrase)
    that says them, from their own text: a number in the item matched to the same number said, else a content word."""
    import numbersync
    d = seg.get("data") or {}
    ws = seg.get("words") or []
    if not ws:
        return []
    said = [(_clean(w[0]), w) for w in ws]
    spoken_nums = numbersync.spoken(ws)
    out = []
    for key in REVEAL_KEYS:
        items = d.get(key)
        if not isinstance(items, list) or len(items) == 0 or (key == "series" and d.get("drawn")):
            continue                                    # a "drawn" chart starts with its lines up: nothing to reveal
        for i, it in enumerate(items):
            if not isinstance(it, dict) or "t" in it or "at" in it:
                continue
            words = _item_words(it)
            if not words:
                continue
            match = None
            shown = numbersync.on_screen([" ".join(words)])
            for sp in spoken_nums:
                if numbersync.matches(sp["value"], shown):
                    match = sp["text"].strip(".,;:")
                    break
            if not match:
                for w in words:
                    if len(w) >= 4 and w not in STOP:
                        hit = next((orig for c, orig in said if c in (w, w + "s", w + "es") or w in (c + "s", c + "es")), None)
                        if hit:
                            match = hit[0].strip(".,;:!?\"'")
                            break
            out.append({"seg": seg["id"], "key": key, "index": i, "text": " ".join(words)[:60], "match": match})
    return out


def rhythm(segs, aim=AIM):
    """(median s between events while the voice runs, share of spoken time within `aim` s of the last event)."""
    diffs, near, total = [], 0.0, 0.0
    for s in segs:
        ws = s.get("words") or []
        if not ws or moving(s):
            continue
        ev = events(s)
        end = ws[-1][2]
        pts = [t for t in ev if t <= end] + [end]
        diffs += [b - a for a, b in zip(pts, pts[1:]) if b - a > 0.05]
        for a, b in zip(pts, pts[1:]):
            total += b - a
            near += min(b - a, aim)
    return (round(statistics.median(diffs), 2) if diffs else None), (round(near / total, 3) if total else None)
