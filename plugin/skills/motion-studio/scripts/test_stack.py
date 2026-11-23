"""Tests for stack.py's carving: python3 -m unittest test_stack (from scripts/). Needs ffmpeg for the PNG round trip."""
import tempfile
import unittest
from pathlib import Path

import numpy as np

import stack

RED, BLUE, GREEN, GOLD, GREY = (200, 40, 40), (40, 60, 160), (60, 150, 70), (230, 180, 60), (120, 120, 120)


def solid(h, w, rgb):
    a = np.zeros((h, w, 4), np.uint8)
    a[..., :3] = rgb
    a[..., 3] = 255
    return a


def box(W=6, L=8, H=5):
    """A box whose four drawings are each one colour, with two marked columns on FRONT (the viewer's left edge)."""
    top, side, front, back = solid(L, W, GOLD), solid(H, L, BLUE), solid(H, W, RED), solid(H, W, GREEN)
    front[:, :2, :3] = GREY
    return top, side, front, back


class Carve(unittest.TestCase):
    def test_box_fills_its_hull(self):
        vol = stack.carve(*box(), shade=0)
        self.assertEqual(vol.shape, (5, 8, 6, 4))
        self.assertTrue((vol[..., 3] == 255).all())

    def test_walls_take_their_drawing(self):
        vol = stack.carve(*box(), shade=0)
        top_layer, low = vol[-1], vol[1]
        self.assertEqual(tuple(top_layer[4, 3, :3]), GOLD)          # nothing above: the TOP colour
        self.assertEqual(tuple(low[7, 3, :3]), RED)                 # front wall (y = L-1)
        self.assertEqual(tuple(low[0, 3, :3]), GREEN)               # back wall (y = 0)
        self.assertEqual(tuple(low[4, 0, :3]), BLUE)                # side walls
        self.assertEqual(tuple(low[4, 5, :3]), BLUE)

    def test_front_is_not_mirrored(self):
        # the FRONT drawing's left column is the viewer's left standing in front: x = 0, as in the TOP drawing
        vol = stack.carve(*box(), shade=0)
        # (corners tie between two walls, so look one in from the edge)
        self.assertEqual(tuple(vol[1, 7, 1, :3]), GREY)
        self.assertEqual(tuple(vol[1, 7, 4, :3]), RED)

    def test_silhouettes_cut_the_volume(self):
        top, side, front, back = box()
        side[:2, :4, 3] = 0           # SIDE: the top two rows cover only the back half (front points left: columns 4-7)
        vol = stack.carve(top, side, front, back, shade=0)
        self.assertFalse(vol[4, 4:, :, 3].any())                     # top layer: front half (y >= 4) is cut away
        self.assertTrue(vol[4, :4, :, 3].all())

    def test_mismatched_drawings_stop(self):
        top, side, front, back = box()
        with self.assertRaises(SystemExit) as e:
            stack.carve(top, side, front[:, :5], back)
        self.assertIn("widths differ", str(e.exception))

    def test_thin_parts_are_reported(self):
        top, side, front, back = box(W=6, L=6, H=6)
        for v in (side, front, back):
            v[:3, :, 3] = 0
            v[:3, 2, 3] = 255            # a 1 px pole on top of the box
        vol = stack.carve(top, side, front, back, shade=0)
        self.assertEqual(stack.runs_of(stack.thin_layers(vol)), [[3, 5]])

    def test_shade_darkens_walls_only(self):
        vol = stack.carve(*box(), shade=0.2)
        self.assertEqual(tuple(vol[-1, 4, 3, :3]), GOLD)
        self.assertLess(int(vol[1, 7, 3, 0]), RED[0])


class Sheet(unittest.TestCase):
    def test_round_trip_through_a_png(self):
        views = [v.repeat(4, 0).repeat(4, 1) for v in box()]
        h = max(v.shape[0] for v in views)
        sheet = np.zeros((h + 8, sum(v.shape[1] for v in views) + 40, 4), np.uint8)
        x = 8
        for v in views:
            sheet[4:4 + v.shape[0], x:x + v.shape[1]] = v
            x += v.shape[1] + 8
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "views.png"
            stack.write_png(p, sheet)
            back = stack.read_png(p)
        self.assertTrue((back == sheet).all())
        found = stack.split_views(back, stack.foreground(back))
        self.assertEqual(stack.find_scale(found), 4)
        small = [stack.shrink(v, 4) for v in found]
        self.assertEqual([v.shape[:2] for v in small], [(8, 6), (5, 8), (5, 6), (5, 6)])

    def test_draw_is_repeatable(self):
        vol = stack.carve(*box(), shade=0.2)
        self.assertTrue((stack.draw(vol, 37) == stack.draw(vol, 37)).all())


if __name__ == "__main__":
    unittest.main()
