"""Policy checks before upload: refuse a package whose answer file is missing, incomplete or answers a check wrongly.

Each episode package holds policy.json, written by a person (or a reviewer the person reads) after watching the cut.
It answers YouTube policy questions that no script can judge from pixels; this file only makes sure each was asked and
answered. Wire it as the channel's pre-upload check in channels.json ("pre_upload_check": ["python3 .../policycheck.py"]),
so youtube.py runs it with --package as the last argument before anything is sent.

  python3 policycheck.py <package>             check <package>/policy.json
  python3 policycheck.py --template            print an empty answer file

Sources: YouTube Help pages by answer id (support.google.com/youtube/answer/<id>), as cited by the project's policy
audit of 2026-11-23: 1311392 (channel monetization policies), 2801973 (Spam Policy) and 14328491 (disclosing
altered or synthetic content), with the section each check rests on. Re-read the pages when YouTube changes them.
"""
import json
import re
import sys
from pathlib import Path

# id: (question, kind, the answer that passes, source)
CHECKS = {
    "M1_new": ("In one sentence, what does this episode add that the channel's last three didn't (its own storyline, "
               "focus or concept)?", "sentence", None, "1311392: content must be materially varied; each video needs a "
               "distinct storyline, focus or concept"),
    "M2_same_outcome": ("Is this the same situation with the same outcome as an earlier episode, with only names or "
                        "numbers swapped? A recurring format is fine.", "bool", False, "1311392: a recurring frame is fine, the same situation and outcome is not"),
    "M3_unnarrated_slideshow": ("Is any stretch a slideshow or scrolling text with no narration or commentary?",
                                "bool", False, "1311392, Generic or repetitive content; longform.py check measures text-only stretches"),
    "M4_ai_expert_advice": ("Does an AI voice or character pose as a human expert giving finance, health, legal or "
                            "political advice, rather than attributing it to named sources?", "bool", False,
                            "1311392, AI personas related to sensitive topics"),
    "M5_realistic_fake_events": ("Does it show realistic shocking imagery or events that didn't happen?", "bool", False,
                                 "1311392, Unsatisfying or off-putting content"),
    "S1_title_thumbnail_deliver": ("Do the title and thumbnail promise only what the video shows?", "bool", True,
                                   "2801973 (Spam Policy), Malicious clickbait"),
    "S1_payoff_at": ("Where in the video is the title's promise paid off (m:ss)?", "time", None, "with S1"),
    "S2_schemes_or_payouts": ("Does it promote a scheme, a job offer, a payout or a get-rich offer?", "bool", False,
                              "2801973: spam and scams"),
    # YouTube asks for the altered-or-synthetic flag only for content that looks real: "Creators don't need to disclose
    # non-realistic content that's made with AI", e.g. "an AI-generated or altered animation of a missile in a fully
    # animated video"; "Cloning one's own voice to create voice overs or dubs" is listed as not needing it (14328491,
    # read 2026-11-25). The owner asked that our animated content stop being marked as AI (2026-11-25), so the flag
    # follows this answer: off unless something looks real.
    "D1_realistic": ("Does anything in it look real that isn't: a real person appearing to say or do something they "
                     "didn't (including a voice made to sound like a real person other than the creator), altered "
                     "footage of a real event or place, a realistic generated scene that didn't happen, or AI-generated "
                     "music as the video's main focus?", "bool", None,
                     "14328491, the four kinds creators must disclose; animation and the creator's own cloned voice don't count"),
    "D1_ai_disclosure": ("Is the altered or synthetic content flag set for this upload? It must match D1_realistic: "
                         "true when something looks real, false otherwise (youtube.py sets it from this file).",
                         "match", "D1_realistic", "14328491: disclose realistic AI content; don't mark the rest"),
    "D1_provenance_kept": ("Are all C2PA data and model watermarks left in place (no step strips or re-renders to hide them)?",
                           "bool", True, "2801973: evading detection counts as spam"),
}


def template():
    return {k: (None if kind != "sentence" else "") for k, (_, kind, _, _) in CHECKS.items()}


def problems(answers, siblings=()):
    """Every unanswered or failing check, as a line naming its id, the question and its source."""
    out = []
    for k, (q, kind, ok, src) in CHECKS.items():
        v = answers.get(k)
        if kind == "sentence":
            words = len(re.findall(r"\w+", v or ""))
            if words < 8:
                out.append(f"{k}: answer in a full sentence (8 words or more): {q} [{src}]")
            elif any((s.get(k) or "").strip().lower() == v.strip().lower() for s in siblings):
                out.append(f"{k}: the same sentence as another episode's answer; say what this one adds [{src}]")
        elif kind == "match":
            want = answers.get(ok)
            v = False if isinstance(v, str) and v.lower().startswith("not needed") else v     # the old answer form
            if not isinstance(v, bool):
                out.append(f"{k}: answer true or false: {q} [{src}]")
            elif isinstance(want, bool) and v is not want:
                out.append(f"{k}: answered {str(v).lower()} but {ok} is {str(want).lower()}: "
                           f"{'set the flag, since something looks real' if want else 'leave the flag off, since nothing looks real'} [{src}]")
        elif kind == "time":
            if not (isinstance(v, str) and re.fullmatch(r"\d{1,3}:[0-5]\d", v.strip())):
                out.append(f"{k}: give m:ss: {q} [{src}]")
        elif not isinstance(v, bool):
            out.append(f"{k}: answer true or false: {q} [{src}]")
        elif ok is not None and v is not ok:
            out.append(f"{k}: answered {str(v).lower()}, which fails: {q} [{src}]")
    unknown = sorted(set(answers) - set(CHECKS) - {"notes"})
    if unknown:
        out.append("unknown keys (misspelt?): " + ", ".join(unknown))
    return out


def sibling_answers(package):
    """policy.json of the other packages beside this one, so a copied M1 sentence is caught."""
    out = []
    for p in package.parent.glob("*/policy.json"):
        if p.parent.resolve() != package.resolve():
            try:
                out.append(json.loads(p.read_text()))
            except (OSError, ValueError):
                pass
    return out


def main(argv):
    if argv[:1] == ["--template"]:
        print(json.dumps(template(), indent=1)); return 0
    if len(argv) != 1:
        print(__doc__); return 2
    package = Path(argv[0])
    f = package / "policy.json"
    if not f.exists():
        print(f"policy check: no {f}; write it after watching the cut (policycheck.py --template prints one)")
        return 1
    try:
        answers = json.loads(f.read_text())
    except ValueError as e:
        print(f"policy check: {f} is not valid JSON ({e})"); return 1
    found = problems(answers, sibling_answers(package))
    if found:
        print(f"policy check: {len(found)} problem(s) in {f}")
        for p in found:
            print("  " + p)
        return 1
    print(f"policy check: all {len(CHECKS)} answers pass ({f})")
    return 0


def synthetic_from(package):
    """True or False from a package's policy.json (D1_ai_disclosure, or D1_realistic when that is all it says), or None
    when the package has no answer, for youtube.py to set the altered-or-synthetic flag from."""
    try:
        ans = json.loads((Path(package) / "policy.json").read_text())
    except (OSError, ValueError, TypeError):
        return None
    for k in ("D1_ai_disclosure", "D1_realistic"):
        v = ans.get(k)
        if isinstance(v, bool):
            return v
        if isinstance(v, str) and v.lower().startswith("not needed"):
            return False
    return None


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
