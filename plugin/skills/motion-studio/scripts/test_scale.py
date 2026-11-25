"""Tests for scalecheck.py (proportions against a size sheet) and the lighting and white-ground lint:
python3 -m unittest test_scale (from scripts/)."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import panelqa
import scalecheck as S

SIZES = {"man": {"h": 1.78, "source": "test"},
         "porta-potty": {"h": 2.31, "w": 1.12, "d": 1.22, "source": "satelliteindustries.com tufway"},
         "bucket": {"h": 0.36, "source": "test"}}
PANEL = {"image": "p.png", "beats": ["s01"], "char_in_frame": ["man"], "props": ["porta-potty"]}


def mark(item, base_y, drawn_m, cam=1.6, horizon=0.4, x=0.5, dim="h"):
    """A mark for a thing drawn drawn_m tall with its base at base_y, seen by a camera cam metres up."""
    top = base_y - drawn_m / cam * (base_y - horizon)
    return {"item": item, "dim": dim, "base": [x, base_y], "top": [x, top]}


class SentenceTest(unittest.TestCase):
    def test_states_ratio_and_body_landmark(self):
        p = dict(PANEL, props=["porta-potty", "bucket"])
        s = S.scale_sentence(p, SIZES)
        self.assertIn("1.3 times the man's height", s)
        self.assertIn("above his head", s)
        self.assertIn("level with his knee", s)

    def test_one_sized_thing_gives_nothing(self):
        self.assertEqual(S.scale_sentence(dict(PANEL, props=[]), SIZES), "")

    def test_goes_before_the_style_ending(self):
        out = S.with_scale("A man by a toilet. Ink and gouache.", "Scale: X.")
        self.assertEqual(out, "A man by a toilet. Scale: X. Ink and gouache.")
        self.assertEqual(S.with_scale(out, "Scale: X."), out)


class JudgeTest(unittest.TestCase):
    def test_right_size_passes_at_another_depth(self):
        # the toilet stands nearer the horizon (further away) and still passes: perspective is accounted for
        v = S.judge(PANEL, SIZES, {"horizon": 0.4, "marks": [mark("man", 0.9, 1.78), mark("porta-potty", 0.6, 2.31)]})
        self.assertTrue(v["ok"], v)
        self.assertAlmostEqual(v["camera_m"], 1.6, places=2)

    def test_too_big_fails_with_a_resize(self):
        v = S.judge(PANEL, SIZES, {"horizon": 0.4, "marks": [mark("man", 0.9, 1.78), mark("porta-potty", 0.8, 3.2)]})
        self.assertFalse(v["ok"])
        self.assertIn("too big by 39%", v["problems"][0])
        self.assertIn("0.72x", v["problems"][0])

    def test_lying_on_its_side_uses_width(self):
        v = S.judge(PANEL, SIZES, {"horizon": 0.4, "marks": [mark("man", 0.9, 1.78), mark("porta-potty", 0.85, 1.12, dim="w")]})
        self.assertTrue(v["ok"], v)

    def test_base_above_horizon_is_refused(self):
        v = S.judge(PANEL, SIZES, {"horizon": 0.4, "marks": [mark("man", 0.9, 1.78),
                                                             {"item": "porta-potty", "base": [0.5, 0.3], "top": [0.5, 0.1]}]})
        self.assertFalse(v["ok"])
        self.assertIn("above the horizon", " ".join(v["problems"]))

    def test_no_horizon_compares_same_row_only(self):
        same = {"horizon": None, "marks": [mark("man", 0.9, 1.78), mark("porta-potty", 0.91, 2.31)]}
        self.assertTrue(S.judge(PANEL, SIZES, same)["ok"])
        far = {"horizon": None, "marks": [mark("man", 0.9, 1.78), mark("porta-potty", 0.6, 2.31)]}
        self.assertIn("different depth", " ".join(S.judge(PANEL, SIZES, far)["problems"]))

    def test_unmarked_and_skip(self):
        self.assertFalse(S.judge(PANEL, SIZES, {"horizon": 0.4, "marks": [mark("man", 0.9, 1.78)]})["ok"])
        self.assertTrue(S.judge(PANEL, SIZES, {"skip": "overhead shot"})["ok"])


class GateTest(unittest.TestCase):
    def test_unchecked_then_passed_then_stale(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "p.png").write_bytes(b"x")
            self.assertIn("not checked", S.gate_problems(root, [PANEL], SIZES, {})[0])
            marks = {"p.png": {"horizon": 0.4, "marks": [mark("man", 0.9, 1.78), mark("porta-potty", 0.6, 2.31)]}}
            v = S.check_all(root, [PANEL], SIZES, marks)
            self.assertEqual(S.gate_problems(root, [PANEL], SIZES, v), [])
            t = (root / "p.png").stat().st_mtime + 10
            os.utime(root / "p.png", (t, t))
            self.assertIn("changed after", S.gate_problems(root, [PANEL], SIZES, v)[0])

    def test_sheet_needs_sources(self):
        self.assertTrue(S.sheet_problems({"cone": {"h": 0.7}}))
        self.assertEqual(S.sheet_problems(SIZES), [])

    def test_page_writes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=0xF2EBDD:s=64x36", "-frames:v", "1",
                            str(root / "p.png")], check=True)
            f, n = S.page(root / "out" / "scale", root, [PANEL], SIZES, "t")
            self.assertEqual(n, 1)
            self.assertIn('"src": "../../p.png"', f.read_text())


class LintTest(unittest.TestCase):
    def warn(self, prompt):
        r = panelqa.lint([{"id": 1, "prompt": prompt}])
        return " ".join(r[0][1]) if r else ""

    def test_night_without_sky(self):
        self.assertIn("doesn't say its sky", self.warn("A man shines a flashlight into a toilet at night. Ink."))
        self.assertEqual(self.warn("A man at night under a dark navy sky. Ink."), "")
        self.assertIn("contradicts", self.warn("A festival at night under a blue sky. Ink."))

    def test_white_ground(self):
        self.assertIn("warm cream", self.warn("Toilets on a white background. Ink."))

    def test_white_share(self):
        with tempfile.TemporaryDirectory() as d:
            for name, c, want in (("w.png", "white", 1.0), ("c.png", "0xF2EBDD", 0.0)):
                f = Path(d) / name
                subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"color=c={c}:s=64x36", "-frames:v", "1", str(f)],
                               check=True)
                self.assertAlmostEqual(panelqa.white_share(f), want)


class CutawayTest(unittest.TestCase):
    SEG = {"id": "s04", "type": "cutaway", "data": {"subject": "porta-potty", "person": "man", "parts": [
        {"label": "tank", "rect": [0.6, 0, 0.58, 0.43]}, {"label": "vent", "rect": [0.82, 0.43, 0.08, 1.88]},
        {"label": "urinal", "path": "0.25 0.75 0.43 0.75 0.41 1.0 0.27 1.0"}]}}

    def test_parts_inside_pass(self):
        import longform
        self.assertEqual(longform.cutaway_problems([self.SEG], SIZES), [])

    def test_part_outside_and_unknown_names_fail(self):
        import longform
        seg = json.loads(json.dumps(self.SEG))
        seg["data"]["parts"][1]["rect"] = [0.82, 0.43, 0.08, 2.1]    # a vent 0.2 m above the roof
        self.assertIn('"vent" reaches outside', " ".join(longform.cutaway_problems([seg], SIZES)))
        seg["data"]["view"] = "front"                                  # front view is only 1.12 m wide: the tank still fits
        seg["data"]["person"] = "giant"
        self.assertIn('person "giant"', " ".join(longform.cutaway_problems([seg], SIZES)))
        seg["data"]["subject"] = "shed"
        self.assertIn('subject "shed"', longform.cutaway_problems([seg], SIZES)[0])


if __name__ == "__main__":
    unittest.main()
