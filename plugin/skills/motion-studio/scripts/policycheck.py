"""Policy checks before upload: refuse a package whose answer file is missing, incomplete or answers a check wrongly.

Each episode package holds policy.json, written by a person (or a reviewer the person reads) after watching the cut.
It answers YouTube policy questions that no script can judge from pixels; this file only makes sure each was asked and
answered. Wire it as the channel's pre-upload check in channels.json ("pre_upload_check": ["python3 .../policycheck.py"]),
so youtube.py runs it with --package as the last argument before anything is sent.

  python3 policycheck.py <package>             check <package>/policy.json
  python3 policycheck.py --template            print an empty answer file

Sources: YouTube Help pages by answer id (support.google.com/youtube/answer/<id>), as cited by the project's policy
audit of 2026-11-23: 1311392 (channel monetization policies) and 2801973 (spam policy). Checks whose source says
"no page yet" were judged in the audit without a page id; cite one before treating them as YouTube's rule. Re-read
the pages when YouTube changes them.
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
                                "bool", False, "the audit's M3; longform.py check measures text-only stretches"),
    "M4_ai_expert_advice": ("Does an AI voice or character pose as a human expert giving finance, health, legal or "
                            "political advice, rather than attributing it to named sources?", "bool", False,
                            "the audit gives no page for this one yet"),
    "M5_realistic_fake_events": ("Does it show realistic shocking imagery or events that didn't happen?", "bool", False,
                                 "the audit gives no page for this one yet"),
    "S1_title_thumbnail_deliver": ("Do the title and thumbnail promise only what the video shows?", "bool", True,
                                   "the audit gives no page for this one yet"),
    "S1_payoff_at": ("Where in the video is the title's promise paid off (m:ss)?", "time", None, "with S1"),
    "S2_schemes_or_payouts": ("Does it promote a scheme, a job offer, a payout or a get-rich offer?", "bool", False,
                              "2801973: spam and scams"),
    "D1_ai_disclosure": ("Is the altered or synthetic content disclosure set for this upload?", "bool", True,
                         "YouTube's altered or synthetic content disclosure; youtube.py --synthetic sets it"),
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
        elif kind == "time":
            if not (isinstance(v, str) and re.fullmatch(r"\d{1,3}:[0-5]\d", v.strip())):
                out.append(f"{k}: give m:ss: {q} [{src}]")
        elif not isinstance(v, bool):
            out.append(f"{k}: answer true or false: {q} [{src}]")
        elif v is not ok:
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


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
