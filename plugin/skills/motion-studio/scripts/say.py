"""What the voice says, when it differs from what the screen and captions show, and the words a TTS voice gets wrong.

script.json carries a "say" map, film-wide and per segment ({"John Does": "John Doughs"}). The narration stays as written
for captions and word cues; `lines` hands the TTS the spoken form, and `align` times the spoken words back onto the
written ones. `lint` lists what the voice is likely to misread in the text it will actually be given.
"""
import re

# Misreads heard on our own films, with where. Each is (pattern on the spoken text, why, a spelling that fixed it).
# Add one whenever a listen-through catches a new misread, so the next film is warned before it is voiced.
KNOWN = [
    (re.compile(r"\b[A-Z][a-z]+ Does\b(?!\s+(?:not|n't|it|this|that|the|a|an|so|too|he|she|they|we|you|I)\b)"),
     'Kokoro reads the plural surname "Does" ("the John Does") as the verb "does" (toll-text, 2026-10-23)', "Doughs"),
]

URL = re.compile(r"\b(?:https?://\S+|www\.\S+|[\w-]+(?:\.[\w-]+)*\.(?:com|org|net|gov|edu|io|co|us|uk|info|biz|app|site|online|"
                 r"xyz|top|example)\b\S*|[\w.+-]+@[\w-]+\.[\w.]+)", re.I)
YEAR = re.compile(r"(19|20)\d\d")
DIGIT = dict(zip("0123456789", "oh one two three four five six seven eight nine".split()))


def _pattern(phrase):
    words = [re.escape(w) for w in phrase.split()]
    return re.compile(r"(?<![\w'])" + r"\s+".join(words) + r"(?![\w'])", re.I)


def merged(film_map, seg_map):
    out = dict(film_map or {})
    out.update(seg_map or {})
    return out


def apply(text, mapping):
    """The narration as the voice should get it: each written phrase replaced by its spoken form (longest first)."""
    for written in sorted(mapping or {}, key=len, reverse=True):
        text = _pattern(written).sub(lambda m, s=mapping[written]: s, text)
    return text


def spans(text, mapping):
    """[(start, end, spoken)] character spans of the written phrases in `text`, longest first, not overlapping."""
    taken, out = [], []
    for written in sorted(mapping or {}, key=len, reverse=True):
        for m in _pattern(written).finditer(text):
            if not any(a < m.end() and m.start() < b for a, b in taken):
                taken.append((m.start(), m.end()))
                out.append((m.start(), m.end(), mapping[written]))
    return sorted(out)


def lint(text):
    """What a TTS voice is likely to misread in `text` (the spoken form): [(the words, why, suggestion)]."""
    out = []
    for pat, why, fix in KNOWN:
        for m in pat.finditer(text):
            out.append((m.group(0), why, m.group(0).replace("Does", fix)))
    for m in URL.finditer(text):
        u = m.group(0).rstrip(".,;:!?)")
        spoken = re.sub(r"^https?://", "", u).replace("www.", "w w w dot ").replace(".", " dot ").replace("-", " ").replace("/", " slash ").replace("@", " at ")
        out.append((u, "web address: voices read these unevenly (letters, dots, dashes)", " ".join(spoken.split())))
    for m in re.finditer(r"[^\s]*\d[^\s]*", text):
        tok = m.group(0).strip(".,;:!?\"'()“”")
        if not tok or URL.fullmatch(tok):
            continue
        bare = tok.lstrip("$£€").rstrip("%").replace(",", "")
        if re.fullmatch(r"\d+(\.\d+)?[kmbKMB]?", bare) and (len(bare.split(".")[0]) < 5 or "," in tok) or YEAR.fullmatch(tok) \
                or re.fullmatch(r"\d+(st|nd|rd|th)", tok):
            continue                                        # amounts, short numbers, years, ordinals read fine
        if re.fullmatch(r"\+?[\d\s().-]{7,}", tok) or re.fullmatch(r"\d{3}-\d{3,4}(-\d{4})?", tok):
            why, fix = "phone-style number: write the digits as they should be heard", " ".join(DIGIT[ch] for ch in tok if ch.isdigit())
        elif re.fullmatch(r"\d{5,}", bare):
            why, fix = "long digit string with no commas: read as one huge number or digit by digit, unpredictably", \
                       f"{int(bare):,}" if len(bare) <= 9 else " ".join(DIGIT[c] for c in bare)
        elif re.search(r"[A-Za-z]", tok):
            why, fix = "letters and digits mixed (a code or model name): spell out how it is said", ""
        elif re.fullmatch(r"\d{1,2}[:/.]\d{1,2}([:/.]\d{2,4})?", tok):
            why, fix = "time or date in figures: write it as said (\"ten thirty\", \"October twenty-third\")", ""
        else:
            why, fix = "figures the voice may read oddly: write them as said", ""
        out.append((tok, why, fix))
    return out
