"""Tests for motioncheck.py and overlapcheck.py: python3 -m unittest test_checks (from scripts/). Needs ffmpeg."""
import subprocess
import tempfile
import unittest
from pathlib import Path

import motioncheck
import overlapcheck


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


if __name__ == "__main__":
    unittest.main()
