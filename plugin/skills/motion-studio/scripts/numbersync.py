"""Spoken numbers and the numbers on screen (editing-quality study of Survive Year 1, 2026-11-27: 6 of 83 spoken
amounts were on screen within 1 s of their word; the owner: "Shouldn't the stats be literally on the screen?").

spoken(words): every amount, count or duration in a segment's timed words, with its value and the time of its first
word. on_screen(texts): every number in a list of visible strings, as values. Pure functions: longform's check samples
the page and compares.

Judgement calls, stated. A bare number word counts when a noun follows it ("forty porta-potties", "three months"), not before a
function word ("two of them", "three or four"); "one" never counts, so "one of them" and "one day" are left alone. Years (1900-2099 written as four digits) and
ordinals are not amounts. A spoken value matches a shown one within 0.5% (a spoken "about forty-eight hundred"
against a shown $4,860 does not). Values are compared without their sign, so "lost $3,925" matches "−$3,925".
"""
import re

ONES = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
                                   "fifteen sixteen seventeen eighteen nineteen".split())}
TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}
SCALE = {"hundred": 100, "thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}
# a number word followed by one of these is not a count of something ("two of them", "three or four", "five to ten")
STOP = {"of", "or", "and", "to", "the", "a", "an", "in", "on", "at", "by", "for", "from", "with", "is", "was", "were",
        "are", "more", "less", "than", "times", "who", "that", "which", "it", "they", "we", "you", "out", "up", "down"}
SUFFIX = {"k": 1e3, "m": 1e6, "mm": 1e6, "bn": 1e9, "b": 1e9}


def _clean(w):
    return w.lower().strip(".,;:!?\"'()[]“”‘’—–").replace("’", "'")


def _digits(tok):
    """Value of a written number token ("$4,860", "62%", "$4.3M", "4,860"), or None. Years and ordinals are None."""
    t = _clean(tok)
    m = re.fullmatch(r"[−-]?\$?(\d[\d,]*(?:\.\d+)?)(k|m|mm|bn|b)?(%)?", t)
    if not m:
        return None
    raw = m.group(1)
    if re.fullmatch(r"(19|20)\d\d", raw) and "$" not in t and not m.group(2) and not m.group(3):
        return None
    v = float(raw.replace(",", ""))
    return v * SUFFIX.get(m.group(2) or "", 1)


def _words_value(ws):
    """(value, n words used) for a run of spoken number words at the start of ws, or (None, 0)."""
    total, cur, used, k = 0, 0, 0, 0
    while k < len(ws):
        w = _clean(ws[k]).replace("-", " ").split()
        if not w:
            break
        ok = True
        for p in w:
            if p in ONES:
                cur += ONES[p]
            elif p in TENS:
                cur += TENS[p]
            elif p == "hundred":
                cur = max(cur, 1) * 100
            elif p in ("thousand", "million", "billion"):
                total += max(cur, 1) * SCALE[p]; cur = 0
            else:
                ok = False; break
        if not ok:
            break
        k += 1; used = k
        if re.search(r"[,.;:!?—–]$", ws[k - 1].strip()):     # a comma ends the number: "four, ten" is two numbers
            break
        if k < len(ws) and _clean(ws[k]) == "and" and k + 1 < len(ws) and (_clean(ws[k + 1]).split("-")[0] in ONES or _clean(ws[k + 1]).split("-")[0] in TENS):
            k += 1
    return (total + cur, used) if used else (None, 0)


def spoken(words):
    """[{"text", "value", "t", "i"}] for the amounts in a segment's timed words [[word, t0, t1], ...]."""
    out, i = [], 0
    toks = [w[0] for w in words]
    while i < len(toks):
        v = _digits(toks[i])
        if v is not None and not re.search(r"\d(st|nd|rd|th)\b", toks[i].lower()):
            n = 1
            nxt = _clean(toks[i + 1]) if i + 1 < len(toks) else ""
            if nxt in ("thousand", "million", "billion"):
                v *= SCALE[nxt]; n = 2
            out.append({"text": " ".join(toks[i:i + n]), "value": v, "t": words[i][1], "i": i})
            i += n; continue
        v, n = _words_value(toks[i:])
        if n:
            unit = _clean(toks[i + n]) if i + n < len(toks) else ""
            ended = re.search(r"[,.;:!?—–]$", toks[i + n - 1].strip())
            if v and v >= 2 and unit and not ended and unit not in STOP and re.match(r"[a-z]", unit):
                out.append({"text": " ".join(toks[i:i + n + 1]), "value": float(v), "t": words[i][1], "i": i})
            i += n; continue
        i += 1
    return out


SHOWN = re.compile(r"[−-]?\$?\d[\d,]*(?:\.\d+)?\s?(?:k|K|M|m|bn|B)?\b")


def on_screen(texts):
    """Every number in visible strings, as absolute values."""
    vals = set()
    for s in texts:
        for m in SHOWN.finditer(s):
            g = m.group(0).replace(" ", "")
            num = re.search(r"\d[\d,]*(?:\.\d+)?", g).group(0).replace(",", "")
            suf = re.search(r"(k|K|M|m|bn|B)$", g)
            vals.add(abs(float(num) * (SUFFIX.get(suf.group(1).lower(), 1) if suf else 1)))
    return vals


def matches(value, shown):
    v = abs(value)
    return any(abs(s - v) <= 0.005 * max(v, 1e-9) for s in shown)
