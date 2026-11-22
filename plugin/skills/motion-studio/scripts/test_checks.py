"""Tests for motioncheck.py, overlapcheck.py and readcheck.py: python3 -m unittest test_checks (from scripts/). Needs ffmpeg."""
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np

import motioncheck
import overlapcheck
import readcheck


def clip(path, still_first):
    """A 6 s 180x320 clip: a moving test pattern, optionally preceded by a 2 s still frame."""
    moving = "testsrc2=size=180x320:rate=30:duration=4" if still_first else "testsrc2=size=180x320:rate=30:duration=6"
    if still_first:
        fc = "[0:v]trim=end_frame=1,loop=loop=59:size=1:start=0,setpts=N/30/TB[s];[s][1:v]concat=n=2:v=1[o]"
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=180x320:rate=30:duration=1",
               "-f", "lavfi", "-i", moving, "-filter_complex", fc, "-map", "[o]", "-pix_fmt", "yuv420p", str(path)]
    else:
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", moving, "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True)


class Motion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.still, cls.moving = Path(cls.tmp.name) / "still.mp4", Path(cls.tmp.name) / "moving.mp4"
        clip(cls.still, True); clip(cls.moving, False)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_still_opening_fails(self):
        floor, f = motioncheck.check(motioncheck.windows(self.still))
        self.assertTrue(any("first 3 s" in x for x in f), f)

    def test_moving_passes(self):
        floor, f = motioncheck.check(motioncheck.windows(self.moving))
        self.assertEqual(f, [])

    def test_marked_still_is_excused(self):
        floor, f = motioncheck.check(motioncheck.windows(self.still), stills=[(0.0, 2.5)])
        self.assertEqual(f, [])


class Overlap(unittest.TestCase):
    CAPTION = {"k": "text", "s": "CERTIFIED DIVER", "b": [100, 100, 300, 60]}
    PAD = {"k": "paint", "b": [150, 90, 200, 80]}

    def test_covered_caption_fails(self):
        self.assertEqual(len(overlapcheck.faults([self.CAPTION, self.PAD])), 1)

    def test_paint_drawn_before_text_passes(self):
        self.assertEqual(overlapcheck.faults([self.PAD, self.CAPTION]), [])

    def test_intended_overlap_passes(self):
        self.assertEqual(overlapcheck.faults([self.CAPTION, dict(self.PAD, over=True)]), [])

    def test_key_prop_is_protected(self):
        card = {"k": "box", "what": "certification card", "key": True, "b": [100, 100, 300, 200]}
        self.assertEqual(len(overlapcheck.faults([card, self.PAD])), 1)

    def test_source_line_is_not_protected(self):
        self.assertEqual(overlapcheck.faults([dict(self.CAPTION, role="source"), self.PAD]), [])


def text_on(ground, ink, w=60, h=20):
    """A picture of three 'glyphs' (bars) in colour ink on a ground, and the same ground without them."""
    behind = np.zeros((h, w, 3), np.uint8) + np.array(ground, np.uint8)
    drawn = behind.copy()
    for x0 in (5, 25, 45):
        drawn[4:16, x0:x0 + 8] = ink
    return drawn, behind


class Contrast(unittest.TestCase):
    def test_wcag_ratio(self):
        white, black = readcheck.luminance(np.array([[[255, 255, 255]]], np.uint8)), readcheck.luminance(np.array([[[0, 0, 0]]], np.uint8))
        self.assertAlmostEqual(float(readcheck.ratio(white, black)[0, 0]), 21.0, places=2)
        grey = readcheck.luminance(np.array([[[118, 118, 118]]], np.uint8))      # #767676 is 4.54:1 on white
        self.assertAlmostEqual(float(readcheck.ratio(white, grey)[0, 0]), 4.54, places=1)

    def test_glyphs_split_and_pass(self):
        g = readcheck.glyph_contrasts(*text_on((255, 255, 255), (0, 0, 0)))
        self.assertEqual(len(g), 3)
        self.assertTrue(all(r > 20 for _, _, r in g), g)

    def test_low_contrast_fails(self):
        r, n = readcheck.worst_contrast(*text_on((200, 200, 200), (150, 150, 150)))
        self.assertLess(r, 3.0)

    def test_one_glyph_on_a_matching_patch_is_the_worst(self):
        drawn, behind = text_on((255, 255, 255), (0, 0, 0))
        behind[:, 40:60] = (20, 20, 20); drawn[:, 40:60] = (20, 20, 20)
        drawn[4:16, 45:53] = (0, 0, 0)               # the third glyph sits on a near-black patch
        r, n = readcheck.worst_contrast(drawn, behind)
        self.assertLess(r, 1.5)

    def test_invisible_text_is_ratio_one(self):
        drawn, behind = text_on((90, 90, 90), (91, 91, 91))
        self.assertEqual(readcheck.worst_contrast(drawn, behind), (1.0, 0))


def frames(levels, w=48, h=36, part=1.0):
    """Frames whose left `part` of the width has the given grey levels, the rest mid grey."""
    for v in levels:
        f = np.full((h, w, 3), 128, np.uint8)
        f[:, :int(w * part)] = v
        yield f


class FlashRate(unittest.TestCase):
    def test_five_flashes_a_second_fail(self):
        lv = ([0] * 3 + [255] * 3) * 10                   # 30 fps: 5 black-white cycles a second for 2 s
        self.assertTrue(readcheck.flash_frames(frames(lv), 30, 48, 36))

    def test_two_flashes_a_second_pass(self):
        lv = ([0] * 8 + [255] * 7) * 4                    # 2 cycles a second
        self.assertEqual(readcheck.flash_frames(frames(lv), 30, 48, 36), [])

    def test_small_dim_change_passes(self):
        lv = ([100] * 3 + [110] * 3) * 10                 # fast, but a change far under 0.1 relative luminance
        self.assertEqual(readcheck.flash_frames(frames(lv), 30, 48, 36), [])

    def test_bright_pair_passes(self):
        lv = ([235] * 3 + [255] * 3) * 10                 # both states over 0.8: no flash under WCAG
        self.assertEqual(readcheck.flash_frames(frames(lv), 30, 48, 36), [])

    def test_small_area_passes(self):
        lv = ([0] * 3 + [255] * 3) * 10
        self.assertEqual(readcheck.flash_frames(frames(lv, part=0.04), 30, 48, 36), [])

    def test_video(self):
        with tempfile.TemporaryDirectory() as d:
            v = Path(d) / "strobe.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:size=160x90:rate=30:duration=2",
                            "-vf", "geq=lum='if(lt(mod(N,6),3),16,235)':cb=128:cr=128", "-pix_fmt", "yuv420p", str(v)], check=True)
            self.assertTrue(readcheck.flash_spans(v))


if __name__ == "__main__":
    unittest.main()
