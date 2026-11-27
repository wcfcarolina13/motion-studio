"""Spoken numbers and the numbers on screen (editing-quality study of Survive Year 1, 2026-11-27: 6 of 83 spoken
amounts were on screen within 1 s of their word; the owner: "Shouldn't the stats be literally on the screen?").

spoken(words): every amount, count or duration in a segment's timed words, with its value and the time of its first
word. on_screen(texts): every number in a list of visible strings, as values. Pure functions: longform's check samples
the page and compares.

Judgement calls, stated. A bare number word counts when a noun follows it ("forty porta-potties", "three months"), not before a
function word ("two of them", "three or four"); "one" never counts, so "one of them" and "one day" are left alone. Years (1900-2099 written as four digits) and
ordinals are not amounts. A range counts at both ends ("forty-five to sixty days", "$35–40k"), and its second end is
marked range_of, because a range on screen lands on its first number. A spoken value matches a shown one within 0.5% (a spoken "about forty-eight hundred"
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


RANGE = {"to", "or"}
DIGIT_RANGE = re.compile(r"[−-]?\$?(\d[\d,]*(?:\.\d+)?)[–—-]\$?(\d[\d,]*(?:\.\d+)?)(k|m|mm|bn|b)?(%)?")


def _scale_to(v, v2):
    """A range's first end takes the second's scale when said without it: "thirty-five to forty thousand" is 35,000."""
    for sc in (1e9, 1e6, 1e3, 100):
        if v2 >= sc and v < sc and v * sc <= v2:
            return v * sc
    return v


def _value_at(toks, j):
    """(value, n tokens) of a number at toks[j], written or spoken, or (None, 0)."""
    if j >= len(toks):
        return None, 0
    v = _digits(toks[j])
    if v is not None:
        nxt = _clean(toks[j + 1]) if j + 1 < len(toks) else ""
        return (v * SCALE[nxt], 2) if nxt in ("thousand", "million", "billion") else (v, 1)
    return _words_value(toks[j:])


def spoken(words):
    """[{"text", "value", "t", "i"}] for the amounts in a segment's timed words [[word, t0, t1], ...]. Both ends of a
    range count ("forty-five to sixty days", "45–60 days", "thirty-five to forty thousand"); the second end carries
    "range_of": the first end's value, since a range shown on screen lands on its first number."""
    out, i, ends = [], 0, {}
    toks = [w[0] for w in words]
    while i < len(toks):
        m = DIGIT_RANGE.fullmatch(_clean(toks[i]))
        if m and not re.fullmatch(r"(19|20)\d\d", m.group(1)):
            k = SUFFIX.get(m.group(3) or "", 1)
            a, b = float(m.group(1).replace(",", "")) * k, float(m.group(2).replace(",", "")) * k
            out.append({"text": toks[i], "value": _scale_to(a, b), "t": words[i][1], "i": i})
            out.append({"text": toks[i], "value": b, "t": words[i][1], "i": i, "range_of": _scale_to(a, b)})
            i += 1; continue
        v = _digits(toks[i])
        if v is not None and not re.search(r"\d(st|nd|rd|th)\b", toks[i].lower()):
            n = 1
            nxt = _clean(toks[i + 1]) if i + 1 < len(toks) else ""
            if nxt in ("thousand", "million", "billion"):
                v *= SCALE[nxt]; n = 2
            e = {"text": " ".join(toks[i:i + n]), "value": v, "t": words[i][1], "i": i}
            if i in ends:
                e["range_of"] = ends[i]
            if n < len(toks) - i and _clean(toks[i + n]) in RANGE:
                v2, n2 = _value_at(toks, i + n + 1)
                if v2 and v2 > v:
                    e["value"] = _scale_to(v, v2); ends[i + n + 1] = e["value"]
            out.append(e)
            i += n; continue
        v, n = _words_value(toks[i:])
        if n:
            unit = _clean(toks[i + n]) if i + n < len(toks) else ""
            ended = re.search(r"[,.;:!?—–]$", toks[i + n - 1].strip())
            if v and unit in RANGE and not ended:
                v2, n2 = _value_at(toks, i + n + 1)
                if v2 and v2 > v:                       # the first end of a range: "forty-five to sixty days"
                    first = _scale_to(v, v2)
                    out.append({"text": " ".join(toks[i:i + n]), "value": float(first), "t": words[i][1], "i": i})
                    ends[i + n + 1] = float(first)
                    i += n; continue
            if i in ends and v:                         # a range's second end counts with or without a noun after it
                out.append({"text": " ".join(toks[i:i + n]), "value": float(v), "t": words[i][1], "i": i, "range_of": ends[i]})
            elif v and v >= 2 and unit and not ended and unit not in STOP and re.match(r"[a-z]", unit):
                e = {"text": " ".join(toks[i:i + n + 1]), "value": float(v), "t": words[i][1], "i": i}
                if i in ends:
                    e["range_of"] = ends[i]
                out.append(e)
            i += n; continue
        i += 1
    return out


SHOWN = re.compile(r"[−-]?\$?\d[\d,]*(?:\.\d+)?\s?(?:k|K|M|m|bn|B)?\b")


def on_screen(texts):
    """Every number in visible strings, as absolute values."""
    vals = set()
    for s in texts:
        # "$35–40k": the suffix belongs to both ends
        s = re.sub(r"(\d[\d,]*(?:\.\d+)?)\s?[–—-]\s?(\$?)(\d[\d,]*(?:\.\d+)?)\s?(k|K|M|bn|B)\b",
                   lambda m: f"{m.group(1)}{m.group(4)} {m.group(2)}{m.group(3)}{m.group(4)}", str(s))
        for m in SHOWN.finditer(s):
            g = m.group(0).replace(" ", "")
            num = re.search(r"\d[\d,]*(?:\.\d+)?", g).group(0).replace(",", "")
            suf = re.search(r"(k|K|M|m|bn|B)$", g)
            vals.add(abs(float(num) * (SUFFIX.get(suf.group(1).lower(), 1) if suf else 1)))
    return vals


def matches(value, shown):
    v = abs(value)
    return any(abs(s - v) <= 0.005 * max(v, 1e-9) for s in shown)
