"""Tests for mapart.py's projection, smoothing and cut-outs: python3 -m unittest test_mapart (from scripts/). No downloads."""
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

import mapart


class ProjTest(unittest.TestCase):
    def test_corners_and_aspect(self):
        P = mapart.Proj([-14, 40, 16, 60], 7200)
        x, y = P(-14, 60)
        self.assertAlmostEqual(float(x), 0)
        self.assertAlmostEqual(float(y), 0)
        x, y = P(16, 40)
        self.assertAlmostEqual(float(x), 7200, places=3)
        self.assertAlmostEqual(float(y), P.h, delta=1)
        # a degree of latitude is longer on the map than a degree of longitude, by 1 / cos(middle latitude)
        self.assertAlmostEqual(P.h / 20 / (7200 / 30), 1 / math.cos(math.radians(50)), places=2)


class ChaikinTest(unittest.TestCase):
    def test_closed_ring_doubles_and_stays_inside(self):
        sq = np.array([[0, 0], [10, 0], [10, 10], [0, 10]], float)
        out = mapart.chaikin(sq, passes=3)
        self.assertEqual(len(out), 4 * 8)
        self.assertTrue((out >= 0).all() and (out <= 10).all())
        self.assertFalse(any((p == [0, 0]).all() for p in out))     # the corner is cut

    def test_open_line_keeps_its_ends(self):
        ln = np.array([[0, 0], [5, 5], [10, 0]], float)
        out = mapart.chaikin(ln, closed=False)
        self.assertEqual(out[0].tolist(), [0, 0])
        self.assertEqual(out[-1].tolist(), [10, 0])


class CutoutTest(unittest.TestCase):
    def picture(self, d, white_bg):
        a = np.full((120, 160, 4), 255, np.uint8)
        if not white_bg:
            a[..., 3] = 0
        a[30:90, 40:120, :3] = 60
        a[30:90, 40:120, 3] = 255
        src = Path(d) / "in.png"
        Image.fromarray(a, "RGBA").save(src)
        return src

    def test_white_background_keyed_with_edge_and_shadow(self):
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "out.png"
            mapart.cmd_cutout(self.picture(d, True), dest)
            im = np.asarray(Image.open(dest))
            self.assertEqual(im.shape[2], 4)
            self.assertEqual(im[0, 0, 3], 0)                   # the corner is keyed out
            mid = im[im.shape[0] // 2, im.shape[1] // 2]
            self.assertEqual(mid[3], 255)
            self.assertLess(int(mid[:3].mean()), 200)          # the subject stays dark
            row = im[im.shape[0] // 2, :, 3]
            inside = np.nonzero(row == 255)[0]
            self.assertGreater(inside[0], 24 + 40 - 12)        # a scissor edge of a few px, not the whole picture
            self.assertLess(inside[0], 24 + 40)

    def test_doc_keeps_its_paper(self):
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "out.png"
            mapart.cmd_cutout(self.picture(d, True), dest, doc=True)
            im = np.asarray(Image.open(dest))
            self.assertEqual(im[24 + 5, 24 + 5, 3], 255)       # the white paper is part of the piece


if __name__ == "__main__":
    unittest.main()
