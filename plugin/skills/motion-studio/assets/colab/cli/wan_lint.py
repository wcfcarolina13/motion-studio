"""Lint a Wan 2.2 image-to-video jobs.json before it spends GPU time (picture-truth.md rule 13).
    python3 wan_lint.py <in_dir>/jobs.json [--min-takes 3]
Measured on 33 takes for Survive Year 1's intro (2026-11-25): Wan invents people, with faces, wherever a frame has a desk,
a door, a gate or a close-up. A laptop with nobody at it got a man with a visible face in 3 of 3 takes, folders got a
smiling man in 2 of 3, a padlock a face behind the fence in 2 of 3, icicles a ghost face in 1 of 3, porta-potties had
strangers stepping out, and boots grew a third boot in 2 of 3. Wide shots that already held the hero, or no people at
all, came out clean in 3 of 3 on four shots. So: Wan only on wide shots; close-ups, interiors and props it can fill with
people are refused, and every source still gets at least --min-takes seeds, judged on its last frame (anatomy.py).
A job may pass a refusal with "wan_ok": "<why>" (for example, a wide exterior whose prompt names a door).
With the Lightning LoRAs the guidance is 1.0, so the negative prompt does nothing: "no people" there can't stop it.
Exit 1 when a job is refused or a still has too few takes."""
import argparse, json, re, sys
from collections import Counter

CLOSE = re.compile(r"\b(?:close[- ]?up|closeup|ecu|mcu|cu|insert|macro|detail shot|tight shot)\b", re.I)
INSIDE = re.compile(r"\b(?:interior|inside|indoors?|room|office|kitchen|hallway|corridor|cab|cabin|bathroom|garage)\b", re.I)
PROPS = re.compile(r"\b(?:desks?|laptops?|computers?|door(?:way)?s?|gates?|fences?|padlocks?|locks?|folders?|boots?|shoes?|feet|"
                   r"icicles?|porta-?pott(?:y|ies)|toilets?)\b", re.I)


def problems(jobs, min_takes=3):
    out = []
    for j in jobs:
        if j.get("wan_ok"):
            continue
        text = " ".join(str(j.get(k, "")) for k in ("shot", "motion", "prompt"))
        why = [w for w, rx in (("a close-up", CLOSE), ("an interior", INSIDE), ("props Wan fills with people", PROPS)) if rx.search(text)]
        hits = sorted({m.group(0).lower() for rx in (CLOSE, INSIDE, PROPS) for m in rx.finditer(text)})
        if why:
            out.append(f"{j.get('id')}: {' and '.join(why)} ({', '.join(hits)}): Wan invents people with faces here. Use a "
                       f"wide exterior, keep it a still with panel fx, or set \"wan_ok\": \"<why>\"")
    takes = Counter(j.get("image") for j in jobs)
    out += [f"{img}: {n} take(s); run at least {min_takes} seeds a still and keep the one the judge passes"
            for img, n in takes.items() if n < min_takes]
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("jobs"); ap.add_argument("--min-takes", type=int, default=3)
    a = ap.parse_args()
    p = problems(json.load(open(a.jobs)), a.min_takes)
    for x in p:
        print("WAN", x)
    sys.exit(1 if p else 0)
