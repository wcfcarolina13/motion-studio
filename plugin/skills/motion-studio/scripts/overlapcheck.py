"""Overlap gate: nothing may be drawn over reading text or a key prop unless it is marked as intended.

Input: a film page's draw-order log, a list of entries {"k": kind, "b": [x, y, w, h], ...} in the order they were
drawn (screen pixels). Protected entries are kind "text" (except role "source") and any entry with "key": true
(a key prop's face, e.g. a card or a sign). Any entry drawn AFTER a protected one that covers more than `cover`
(default 0.15) of its box is a fault, unless that later entry carries "over": true (an intended overlap: a stamp
landing on a page, a hand holding a card). Kinds that don't paint ("surface", "grounded", "caption") are ignored.

  python3 overlapcheck.py <log.json> [--cover 0.15]      # log.json: one list, or {"frames": {"label": [...]}}
"""
import json
import sys

IGNORE = {"surface", "grounded", "caption"}


def protected(e):
    return (e.get("k") == "text" and e.get("role") != "source") or e.get("key") is True


def faults(entries, cover=0.15, where=""):
    out = []
    for i, p in enumerate(entries):
        if not protected(p) or "b" not in p:
            continue
        X, Y, W, H = p["b"]
        area = max(1.0, W * H)
        for q in entries[i + 1:]:
            if q.get("over") or q.get("k") in IGNORE or "b" not in q:
                continue
            x, y, w, h = q["b"]
            ox = max(0.0, min(X + W, x + w) - max(X, x))
            oy = max(0.0, min(Y + H, y + h) - max(Y, y))
            if ox * oy / area > cover:
                name = p.get("s") or p.get("what") or p.get("k")
                out.append(f"{where}OVERLAP: '{name}' is {100 * ox * oy / area:.0f}% covered by a later {q.get('k')} at x{x:.0f} y{y:.0f} {w:.0f}x{h:.0f}")
                break
    return out


def main(argv):
    data = json.load(open(argv[0]))
    cover = float(argv[argv.index("--cover") + 1]) if "--cover" in argv else 0.15
    frames = data["frames"] if isinstance(data, dict) else {"": data}
    found = [f for label, entries in frames.items() for f in faults(entries, cover, f"{label} " if label else "")]
    print(f"overlapcheck: {len(frames)} frame(s); {len(found)} fault(s)")
    for f in found[:30]:
        print("  " + f)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
