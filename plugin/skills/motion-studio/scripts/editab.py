"""A/B of the ways to fix one fault in a panel with ChatGPT images (through codex), so the engine's default is chosen
by evidence, not taste (picture-truth.md rule 16).

    python3 editab.py <film> --image panels/v20b.png --box x,y,w,h --change "make the gate as tall as the fence" --allow-codex
        [--methods crop markup whole] [--takes 1] [--ref plates/gate.jpg] [--faceless] [--no-judge]

Methods (each take is one Codex image):
  crop    the square around the box is the edit base; only the box plus margin is pasted back (repair's crop route)
  markup  the whole panel goes with the box outlined in red, plus the clean panel; only the same region is pasted back
  whole   the whole panel goes with "change only ..."; scored as it came back and with only the region pasted back

Scores, per take: likeness outside the box of the model's own answer (how much it redrew what it was told to keep; 1.0
for the pasted candidates by construction), the share of the box that changed, and for markup the marker red left in.
Then a blind sheet: every candidate's area at the same zoom, lettered in a shuffled order (the key is in key.json), is
shown to a judge that is told the fault but not the methods ($EDITAB_JUDGE_CMD, default `claude -p`; --no-judge skips
it). Everything lands in repairs/ab/<image>/NN/: report.md, results.json, blind.png, key.json and each method's files.
"""
import argparse, json, os, random, re, shlex, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402
import panelqa  # noqa: E402
import codex_stills  # noqa: E402

METHODS = ("crop", "markup", "whole")
JUDGE = ('claude -p "Read the image file at {path}. It shows {n} attempts, lettered, to fix one fault in an illustration. '
         'The fault and the fix asked for: {change}. For each letter judge only that picture: FIXED if the fault is fixed '
         'as asked, else NOT; and CLEAN if the edit blends in (no seam, no style change, no leftover red marker), else SEAM. '
         'End with one line per letter, like: A: FIXED CLEAN" --allowedTools Read')


def whole_ask(change, W, H, faceless):
    return codex_stills.ask_edit({"change": change}, faceless, W, H)


def sheet(items, dest, crop, label=True):
    """Each candidate's area (crop = x, y, side in panel pixels) at 640 px, side by side, lettered."""
    from PIL import Image, ImageDraw
    x, y, side = crop
    tiles = []
    for letter, path in items:
        im = Image.open(path).convert("RGB").crop((x, y, x + side, y + side)).resize((640, 640), Image.LANCZOS)
        if label:
            d = ImageDraw.Draw(im)
            d.rectangle((0, 0, 64, 64), fill=(255, 255, 255)); d.text((22, 18), letter, fill=(0, 0, 0))
        tiles.append(im)
    out = Image.new("RGB", (650 * len(tiles) - 10, 640), (255, 255, 255))
    for i, t in enumerate(tiles):
        out.paste(t, (650 * i, 0))
    out.save(dest)


def judge(path, n, change):
    cmd = os.environ.get("EDITAB_JUDGE_CMD", JUDGE)
    c = cmd.replace("{path}", shlex.quote(str(path))).replace("{n}", str(n)).replace("{change}", change.replace('"', "'"))
    try:
        r = subprocess.run(c, shell=True, capture_output=True, stdin=subprocess.DEVNULL, text=True,
                           timeout=int(os.environ.get("EDITAB_JUDGE_TIMEOUT", 300)))
    except subprocess.TimeoutExpired:
        return {}, "judge timed out"
    got = {m.group(1): (m.group(2) == "FIXED", m.group(3) == "CLEAN")
           for m in re.finditer(r"^\s*\**([A-Z])\**\s*[:.-]\s*\**(FIXED|NOT)\**\s+\**(CLEAN|SEAM)\**", r.stdout, re.M)}
    return got, r.stdout.strip()[-600:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("film"); ap.add_argument("--image", required=True); ap.add_argument("--box", required=True)
    ap.add_argument("--change", required=True); ap.add_argument("--methods", nargs="+", choices=METHODS, default=list(METHODS))
    ap.add_argument("--takes", type=int, default=1); ap.add_argument("--ref"); ap.add_argument("--faceless", action="store_true")
    ap.add_argument("--allow-codex", action="store_true"); ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--seed", type=int, help="shuffle seed for the blind letters (default: random)")
    a = ap.parse_args()
    if not codex_stills.allowed(a.allow_codex):
        raise SystemExit(f"this A/B spends {len(a.methods) * a.takes} Codex images: pass --allow-codex once he has said yes")
    film = Path(a.film).resolve()
    src = film / a.image
    W, H = panelqa.size(src)
    base = panelqa.read_rgb(src)
    b = panelqa.parse_box(a.box, W, H)
    region = panelqa.grow(b, 32, W, H)
    key = Path(a.image).with_suffix("").as_posix()
    root = film / "repairs" / "ab" / key
    n = 1 + max([int(p.name) for p in root.glob("[0-9]*") if p.name.isdigit()] or [0])
    out = root / f"{n:02d}"; out.mkdir(parents=True)
    seeds = list(range(1, a.takes + 1))
    rows, cands = [], []
    for m in a.methods:
        t0 = time.time()
        if m in ("crop", "markup"):
            rdir, made, secs, e = panelqa.repair(film, a.image, a.box, a.change, ref=a.ref, seeds=seeds, method=m,
                                                 allow_codex=True, faceless=a.faceless, min_change=0)
            for s in seeds:
                c = e["checks"].get(str(s))
                row = {"method": m, "take": s, "repair": e["id"], "seconds": secs, "failed": e["failures"].get(str(s))}
                if c:
                    row.update(change=c["change"], notes=c["notes"], outside_likeness=(e["markup"].get(str(s), {}).get(
                        "outside_likeness") if m == "markup" else None), red_left=e["markup"].get(str(s), {}).get("red_left"))
                    cands.append((f"{m} take {s}", rdir / f"candidate_{s}.png"))
                rows.append(row)
        else:
            sx, sy, side = panelqa.context_square(b, W, H, 2.25, max(max(region[2:]) + 48, panelqa.MIN_SIDE))
            for s in seeds:
                full = out / f"whole_{s}.png"
                why = codex_stills.edit_image(whole_ask(a.change, W, H, a.faceless), [str(src)] + ([a.ref] if a.ref else []), full)
                row = {"method": "whole", "take": s, "seconds": round(time.time() - t0, 1), "failed": why}
                if not why:
                    arr, how, k = panelqa.fit_full(full, W, H, base, region)
                    raw = out / f"whole_{s}_fitted.png"; panelqa.write_rgb(arr, raw)
                    crop = arr[sy:sy + side, sx:sx + side]
                    rc = (region[0] - sx, region[1] - sy, region[2], region[3])
                    gain, off = panelqa.color_match(crop, base[sy:sy + side, sx:sx + side], panelqa.ring(side, side, rc, panelqa.RING))
                    pasted = panelqa.paste(base, np.clip(crop * np.array(gain) + np.array(off), 0, 255), (sx, sy, side),
                                           region, 16, panelqa.open_sides(region, W, H))
                    pp = out / f"whole_{s}_pasted.png"; panelqa.write_rgb(pasted, pp)
                    inbox = (slice(b[1], b[1] + b[3]), slice(b[0], b[0] + b[2]))
                    row.update(fit=how, outside_likeness=k, change=round(float((np.abs(arr[inbox] - base[inbox]).max(-1)
                                                                                 > panelqa.NO_EDIT).mean()), 3))
                    cands += [(f"whole take {s} (as returned)", raw), (f"whole take {s} (region pasted)", pp)]
                rows.append(row)
            with open(film / "repairs" / "usage.log", "a") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\teditab {key}\twhole via codex\t{len(seeds)} takes\n")
    crop = panelqa.context_square(b, W, H, 2.25, max(max(region[2:]) + 48, panelqa.MIN_SIDE))
    sheet([("before", src)] + [(n_, p) for n_, p in cands], out / "compare.png", crop, label=False)
    order = list(range(len(cands)))
    random.Random(a.seed).shuffle(order)
    letters = {chr(65 + i): cands[j][0] for i, j in enumerate(order)}
    sheet([(chr(65 + i), cands[j][1]) for i, j in enumerate(order)], out / "blind.png", crop)
    (out / "key.json").write_text(json.dumps(letters, indent=1) + "\n")
    verdicts, said = ({}, "skipped") if a.no_judge or not cands else judge(out / "blind.png", len(cands), a.change)
    scored = {letters[k]: {"fixed": v[0], "clean": v[1]} for k, v in verdicts.items() if k in letters}
    res = {"image": a.image, "box": list(b), "change": a.change, "rows": rows, "blind": scored, "judge_said": said,
           "at": time.strftime("%Y-%m-%d %H:%M")}
    (out / "results.json").write_text(json.dumps(res, indent=1, ensure_ascii=False) + "\n")
    lines = [f"# Edit A/B: {a.image}", "", f"Fix asked for: {a.change}", "",
             "| candidate | outside likeness (model's answer) | box changed | red left | blind: fixed | blind: clean | notes |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        names = [x for x, _ in cands if x.startswith(f"{r['method']} take {r['take']}")]
        for nm in names or [f"{r['method']} take {r['take']}"]:
            v = scored.get(nm, {})
            lines.append(f"| {nm} | {r.get('outside_likeness', '') if 'pasted' not in nm else '1.0 (pasted)'} | "
                         f"{r.get('change', '')} | {r.get('red_left') if r.get('red_left') is not None else ''} | "
                         f"{v.get('fixed', '')} | {v.get('clean', '')} | {r.get('failed') or '; '.join(r.get('notes') or [])} |")
    lines += ["", "compare.png shows the area before and after each candidate (in the table's order); blind.png is what "
              "the judge saw, lettered as in key.json. A person decides; the judge only orders the look."]
    (out / "report.md").write_text("\n".join(lines) + "\n")
    print((out / "report.md").read_text())
    print(f"-> {out.relative_to(film)}")


if __name__ == "__main__":
    main()
