"""Anatomy gate for stills: a vision judge (the Claude CLI on the owner's plan by default) looks at every still with
people in it for anatomy horror, and a failed still is drawn again.
    python3 anatomy.py <stills_dir> --jobs <in_dir>/jobs.json [--fix] [--tries 3] [--faceless]
A folder of Wan clips (.mp4) works the same way: each clip's last frame is judged against its first, for anatomy and for
people or faces the source still didn't have (picture-truth.md rule 13).

The owner, after a head twisted far over a man's shoulder got through (v32b, 2026-11-25): "Be sure anatomy horror is
nipped in the process from now on." codex_stills.py calls judge() after each still and re-rolls a FAIL within --tries.
For the Colab stills route, run this file on the pulled stills: it judges each <id>.png it hasn't judged yet, and with
--fix deletes a failed one and moves its seed in jobs.json on by 1000, so the next run_colab.sh (which redoes only
missing stills) draws it again. A still that has failed --tries times is kept and listed in review.tsv for a person.

Every verdict, with the judge's reason, goes to judge.tsv beside the stills (id, try, verdict, reason, file), so misses
can be audited. The judge runs on a shot when it has char_in_frame, or its prompt names a person, hands or arms.

ANATOMY_CHECK_CMD overrides the judge ({path} is the image, {faceless} is "yes" or "no"); "off" skips it. A vision
judge agreed with people only about 60% of the time in our tests, so its FAILs cost re-rolls that weren't always needed
and its PASSes are not proof: the pick pass still looks at every still."""
import argparse, json, os, re, shlex, subprocess, sys, time

PEOPLE = re.compile(r"\b(?:man|men|woman|women|person|people|worker|workers|guest|guests|crowd|boy|girl|child|kids?|"
                    r"he|she|they|hero|driver|figure|hands?|arms?|fingers?|legs?|someone)\b", re.I)

CRITERIA = ("1) a head or neck turned past what a real neck allows against the shoulders and chest (a head facing back "
            "over a shoulder while the chest faces forward is a fail); 2) an elbow, knee or wrist bent the wrong way or "
            "where no joint is; 3) a person with more or fewer than two arms, two legs or two hands; 4) hands: not five "
            "fingers, fused or melted fingers, or a hand with no arm; 5) a head far too big or small for its body; "
            "6) a body part floating or detached, or a person floating above the ground they stand on; 7) left and right "
            "swapped, such as a right hand on a left arm or feet pointing opposite ways.")

DEFAULT = ('claude -p "Read the image file at {path}. Judge only the anatomy of the people in it, never the style. Fail it '
           'for any of: ' + CRITERIA.replace('"', "'") + ' {faceless_rule} Answer on the last line with PASS, or FAIL: '
           'followed by the reason in a few words." --allowedTools Read')
FACELESS_RULE = "8) any visible human face (this hero must be faceless)."
# a character model sheet lays out separate head and face close-ups beside the full figure: they are views, not detached
# parts (cel test, 2026-11-28: a sheet failed for "three detached floating heads with no neck or body")
SHEET_RULE = ("This is a character model sheet: separate head or face close-ups and turnaround views beside the full figure are "
              "intended views of the same person, not floating or detached parts and not extra people.")
# a Wan image-to-video clip is judged on its last frame against its first (the source still): Wan invents people with
# faces at desks, doors, gates and in close-ups, and grows extra boots (picture-truth.md rule 13, 2026-11-25)
CLIP = ('claude -p "Read two image files. The first, {first}, is the still a video clip started from; the second, {path}, is the '
        'clip\'s last frame. Judge only the people and bodies in the last frame, never the style. Fail it for any of: 0) a person, '
        'face, ghostly face or body part in the last frame that is not in the first (count the people in each), or a doubled '
        'object such as a third boot; ' + CRITERIA.replace('"', "'") + ' {faceless_rule} Answer on the last line with PASS, or '
        'FAIL: followed by the reason in a few words." --allowedTools Read')


def clip_frames(mp4, work):
    """(first frame png, last frame png) of a clip, written under work/."""
    os.makedirs(work, exist_ok=True)
    stem = os.path.splitext(os.path.basename(mp4))[0]
    first, last = os.path.join(work, stem + "_first.png"), os.path.join(work, stem + "_last.png")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", mp4, "-frames:v", "1", first], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-sseof", "-0.25", "-i", mp4, "-update", "1", last], check=True)
    return first, last


def judge_clip(mp4, faceless=False, cmd=None):
    """judge() for a video clip: its last frame against its first."""
    cmd = cmd or os.environ.get("ANATOMY_CLIP_CMD", os.environ.get("ANATOMY_CHECK_CMD", CLIP))
    if cmd == "off":
        return "PASS", "judge off"
    first, last = clip_frames(mp4, os.path.join(os.path.dirname(mp4), ".judge"))
    return judge(last, faceless, cmd.replace("{first}", shlex.quote(first)))


def needs_check(shot):
    return bool(shot.get("char_in_frame")) or bool(PEOPLE.search(shot.get("prompt") or shot.get("change") or ""))


def judge(path, faceless=False, cmd=None, sheet=False):
    """("PASS" | "FAIL" | "UNCLEAR", reason). sheet: the image is a character model sheet (SHEET_RULE)."""
    cmd = cmd or os.environ.get("ANATOMY_CHECK_CMD", DEFAULT)
    if cmd == "off":
        return "PASS", "judge off"
    rule = " ".join(x for x in (FACELESS_RULE if faceless else "", SHEET_RULE if sheet else "") if x)
    c = (cmd.replace("{faceless_rule}", rule.replace('"', "'"))
            .replace("{faceless}", "yes" if faceless else "no").replace("{sheet}", "yes" if sheet else "no").replace("{path}", shlex.quote(path)))
    try:
        r = subprocess.run(c, shell=True, capture_output=True, stdin=subprocess.DEVNULL, text=True, timeout=int(os.environ.get("ANATOMY_TIMEOUT", 180)))
    except subprocess.TimeoutExpired:
        return "UNCLEAR", "judge timed out"
    lines = [ln.strip().strip("*") for ln in r.stdout.strip().splitlines() if ln.strip()]
    last = lines[-1] if lines else ""
    m = re.match(r"(PASS|FAIL)\b[:\s-]*(.*)", last, re.I)
    if not m:
        return "UNCLEAR", (last or (r.stderr or "no answer").strip())[:160]
    return m.group(1).upper(), m.group(2).strip()[:160]


def log(out_dir, sid, tri, verdict, reason, file):
    with open(os.path.join(out_dir, "judge.tsv"), "a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{sid}\t{tri}\t{verdict}\t{reason.replace(chr(9), ' ')}\t{file}\n")


def history(out_dir):
    """{id: [verdict, ...]} from judge.tsv, oldest first."""
    p, out = os.path.join(out_dir, "judge.tsv"), {}
    if os.path.exists(p):
        for ln in open(p):
            f = ln.rstrip("\n").split("\t")
            if len(f) >= 4:
                out.setdefault(f[1], []).append(f[3])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stills"); ap.add_argument("--jobs", help="the Colab in_dir's jobs.json (prompts, seeds)")
    ap.add_argument("--fix", action="store_true", help="delete a failed still and move its seed, so the next run redraws it")
    ap.add_argument("--tries", type=int, default=3); ap.add_argument("--faceless", action="store_true")
    a = ap.parse_args()
    jobs = json.load(open(a.jobs)) if a.jobs else []
    by_id = {j["id"]: j for j in jobs}
    hist, fails, review = history(a.stills), [], []
    for name in sorted(os.listdir(a.stills)):
        clip = name.endswith(".mp4")
        if not (name.endswith(".png") or clip):
            continue
        sid = name[:-4]
        j = by_id.get(sid, {})
        if jobs and not clip and not needs_check(j):      # a clip is always judged: Wan invents people in empty frames
            continue
        past = hist.get(sid, [])
        if past and past[-1] in ("PASS", "KEPT"):
            continue
        v, why = (judge_clip if clip else judge)(os.path.join(a.stills, name), a.faceless or j.get("faceless", False))
        tri = len(past) + 1
        print(f"{sid}: {v}{': ' + why if why else ''}")
        if v == "FAIL" and a.fix and tri < a.tries and sid in by_id:
            log(a.stills, sid, tri, v, why, name)
            os.remove(os.path.join(a.stills, name))
            by_id[sid]["seed"] = int(by_id[sid].get("seed", 7)) + 1000
            fails.append(sid)
        else:
            log(a.stills, sid, tri, "KEPT" if v == "FAIL" else v, why, name)
            if v != "PASS":
                review.append((sid, f"anatomy {v.lower()} after {tri} tries: {why}" if v == "FAIL" else f"anatomy judge unclear: {why}"))
    if a.fix and fails:
        tmp = a.jobs + ".part"; json.dump(jobs, open(tmp, "w"), indent=1); os.replace(tmp, a.jobs)
        print(f"{len(fails)} stills failed and were deleted with new seeds: run run_colab.sh again to redraw {' '.join(fails)}")
    if review:
        with open(os.path.join(a.stills, "review.tsv"), "a") as f:
            f.writelines(f"{i}\t{w}\n" for i, w in review)
    sys.exit(1 if fails or review else 0)


if __name__ == "__main__":
    main()
