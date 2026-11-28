"""Tests for motioncheck.py, overlapcheck.py and readcheck.py: python3 -m unittest test_checks (from scripts/). Needs ffmpeg."""
import contextlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
import unittest.mock
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


class MapBoil(unittest.TestCase):
    """The map's boil steps every 8th frame on purpose; final passes exactly those frames to the pop check."""

    def test_frames_inside_map_segments_only(self):
        import longform
        segs = [{"type": "map", "t0": 1.0, "t1": 2.0}, {"type": "stat", "t0": 2.0, "t1": 3.0}]
        self.assertEqual(sorted(longform.map_boil_steps(segs)), [32, 40, 48, 56])
        self.assertEqual(sorted(longform.map_boil_steps(segs[:1], end=2.5)), [32, 40, 48, 56, 64, 72])

    def test_final_asks_for_the_mix(self):
        import argparse
        import longform
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "out").mkdir()
            with self.assertRaises(SystemExit) as e:
                longform._final(argparse.Namespace(film=d))
            self.assertIn("run `longform.py mix", str(e.exception.code))


class PartialRender(unittest.TestCase):
    TL = {"duration": 40.0, "segments": [{"id": "a", "type": "panel", "t0": 0.3, "t1": 10.0},
                                         {"id": "b", "type": "map", "t0": 10.0, "t1": 12.0},
                                         {"id": "c", "type": "map", "t0": 12.0, "t1": 14.0},
                                         {"id": "d", "type": "stat", "t0": 14.0, "t1": 40.0}]}

    def test_parts_cut_at_segments_and_heads(self):
        import longform
        r = longform.part_ranges(self.TL, 1200)
        self.assertEqual(r[0], (0, 9))                      # before the first segment
        self.assertIn((300, 318), r)                        # b's head (0.6 s), then the rest of b
        self.assertTrue(all(b - a <= longform.CHUNK for a, b in r))
        self.assertEqual([x for p in r for x in p][1:-1:2], [x for p in r for x in p][2::2])   # contiguous

    def test_map_brings_its_run_and_ranges_check(self):
        import longform
        self.assertEqual(longform.segment_pick(self.TL, "c", None, None), ["b", "c"])
        self.assertEqual(longform.segment_pick(self.TL, None, "a", "a"), ["a"])
        with self.assertRaises(SystemExit):
            longform.segment_pick(self.TL, "zz", None, None)


    def test_map_change_splices_every_map_segment(self):
        import copy, json, os, time
        import longform
        with tempfile.TemporaryDirectory() as d:
            film, parts = Path(d), Path(d, "out", "parts")
            (film / "maps").mkdir(); (film / "maps" / "tile.png").write_bytes(b"x")
            parts.mkdir(parents=True)
            tl = copy.deepcopy(self.TL); tl["map"] = {"proj": 1}
            n = int(tl["duration"] * 30); ranges = longform.part_ranges(tl, n)
            for i0, i1 in ranges:
                (parts / f"p{i0:07d}-{i1:07d}.mp4").write_bytes(b"v")
            longform.write_manifest(film, tl, parts, 1.0)
            new = copy.deepcopy(tl); new["map"] = {"proj": 2}; new["segments"][2]["data"] = {"camera": {"zoom": 2}}
            dirty, only = longform.partial_problems(film, new, parts, ["c"], 1.0, ranges)
            self.assertEqual(only, ["b", "c"])
            self.assertTrue(all(a < 14 * 30 + 18 and b > 10 * 30 for a, b in dirty))   # only b, c and d's head
            # a tile repainted with no timeline change: the map segments again, nothing else
            time.sleep(0.01); (film / "maps" / "tile.png").write_bytes(b"yy")
            dirty, only = longform.partial_problems(film, tl, parts, ["b", "c"], 1.0, ranges)
            self.assertEqual(only, ["b", "c"])
            # a change outside the map still refuses
            other = copy.deepcopy(tl); other["grade"] = {"vignette": 0.5}
            with self.assertRaises(SystemExit):
                longform.partial_problems(film, other, parts, ["b", "c"], 1.0, ranges)


class PanelMotion(unittest.TestCase):
    def test_flags_only_quiet_panels(self):
        import longform
        est = {"still": [0.003] * 8, "busy": [0.5] * 8, "short": [0.0] * 3}
        floor, low = longform.motion_flags(est, floor=0.07)
        self.assertEqual([x[0] for x in low], ["still"])

    def test_floor_has_a_ceiling(self):
        import motioncheck
        busy = [1.7] * 40 + [0.25] * 4             # a busy film: a quarter of its median would be 0.425
        floor, faults = motioncheck.check(busy, first=0)
        self.assertEqual(floor, 0.2)
        self.assertEqual(faults, [])
        self.assertEqual(motioncheck.floor_of([0.3] * 9), 0.075)
        self.assertEqual(motioncheck.check([1.7] * 40 + [0.1] * 4, first=0)[1][0].split(":")[0], "20.0-22.0 s")


class StillsRoute(unittest.TestCase):
    """Owner's rule (2026-11-25): turbo stills are drafts; full-quality models ship."""
    CLI = Path(__file__).resolve().parent.parent / "assets" / "colab" / "cli"

    def run_jobs(self, d, *extra):
        Path(d, "shots.json").write_text('{"style": "ink", "shots": [{"id": "a", "prompt": "a yard"}]}')
        return subprocess.run(["python3", str(self.CLI / "shots_to_jobs.py"), str(Path(d, "shots.json")), str(Path(d, "in")), *extra],
                              capture_output=True, text=True)

    def test_default_is_full_quality_and_turbo_needs_draft(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            r = self.run_jobs(d)
            self.assertEqual(r.returncode, 0, r.stderr)
            m = json.loads(Path(d, "in", "model.json").read_text())
            self.assertFalse(m.get("draft"))
            self.assertGreater(m["call"]["num_inference_steps"], 20)
            self.assertNotEqual(self.run_jobs(d, "--model", "z-image-turbo").returncode, 0)
            self.assertEqual(self.run_jobs(d, "--model", "z-image-turbo", "--draft").returncode, 0)
            self.assertEqual(self.run_jobs(d, "--model", "qwen-image-2512").returncode, 0)
            self.assertEqual(Path(d, "in", "min_vram_gb").read_text(), "40")

    def test_render_refuses_draft_stills(self):
        import longform
        try:
            from PIL import Image
            from PIL.PngImagePlugin import PngInfo
        except ImportError:
            self.skipTest("PIL not installed")
        with tempfile.TemporaryDirectory() as d:
            i = PngInfo(); i.add_text("motion-studio-model", "z-image-turbo"); i.add_text("motion-studio-draft", "1")
            Image.new("RGB", (8, 8)).save(Path(d, "a.png"), pnginfo=i); Image.new("RGB", (8, 8)).save(Path(d, "b.png"))
            segs = [{"id": "s1", "type": "panel", "data": {"image": "a.png"}}, {"id": "s2", "type": "panel", "data": {"image": "b.png"}}]
            self.assertEqual([x.split(":")[0] for x in longform.draft_stills(Path(d), segs)], ["s1"])


class PaceProfile(unittest.TestCase):
    def test_numbers(self):
        import longform
        # new things at 0, 0.2 (merges into 0), 1, 3; shots at 0 and 4; 20 words over 5 s of voice; 4 sounds in 10 s
        p = longform.pace_profile([0, 0.2, 1, 3], 9, 10, 20, 5.0, [1, 2, 3, 4, 11], 0, 10, [0, 4])
        self.assertAlmostEqual(p["moving"], 0.9)
        self.assertEqual(p["new_every"], 2)            # gaps 1, 2, 7
        self.assertEqual(p["new_every_p90"], 7)
        self.assertEqual(p["shot_every"], 5.0)          # gaps 4, 6
        self.assertEqual(p["wps"], 4.0)
        self.assertEqual(p["sfx_10s"], 4.0)
        q = longform.pace_profile([], 0, 0, 0, 0, None, 0, 10)
        self.assertIsNone(q["sfx_10s"]); self.assertIsNone(q["wps"]); self.assertIn("run mix first", longform.pace_line(q))


class CodexStills(unittest.TestCase):
    """The ChatGPT-image route through the Codex CLI, against a fake codex: aspect and face re-rolls, crop, provenance."""
    FAKE = r"""
import os, sys, time
from PIL import Image
st = os.environ["FAKE_STATE"]; n = int(open(st).read()) if os.path.exists(st) else 0
open(st, "w").write(str(n + 1))
open(st + ".prompts", "a").write(sys.argv[-1] + "\n")
open(st + ".argv", "a").write(" ".join(sys.argv[1:-1]) + "\n")
w, h = [tuple(map(int, x.split("x"))) for x in os.environ["FAKE_SIZES"].split()][n]
d = os.path.join(os.environ["CODEX_IMAGES"], "sess%d" % n); os.makedirs(d, exist_ok=True)
Image.new("RGB", (w, h), (200, 150, 90)).save(os.path.join(d, "ig_%d.png" % n))
"""
    JUDGE = r"""
import os
st = os.environ["JUDGE_STATE"]; n = int(open(st).read()) if os.path.exists(st) else 0
open(st, "w").write(str(n + 1)); print("Looking.\n" + os.environ["JUDGE_SAYS"].split()[n])
"""

    def run_it(self, d, sizes, says, *extra, ok=True, shots=None, env_extra=None):
        import json, os
        try:
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest("PIL not installed")
        Path(d, "fake.py").write_text(self.FAKE); Path(d, "judge.py").write_text(self.JUDGE)
        Path(d, "shots.json").write_text(json.dumps(shots or {"style": "ink", "faceless": True, "shots": [
            {"id": "a", "prompt": "a yard"}, {"id": "b", "prompt": "a truck", "faceless": False}]}))
        env = dict(os.environ, CODEX_CMD=f"python3 {d}/fake.py", CODEX_ARGS="", CODEX_IMAGES=f"{d}/gen",
                   FAKE_STATE=f"{d}/n", FAKE_SIZES=sizes, JUDGE_STATE=f"{d}/j", JUDGE_SAYS=says,
                   FACE_CHECK_CMD=f"python3 {d}/judge.py {{path}}", ANATOMY_CHECK_CMD="off")
        env.update(env_extra or {})
        env.pop("CODEX_STILLS_OK", None)
        return subprocess.run(["python3", str(Path(__file__).with_name("codex_stills.py")), f"{d}/shots.json", f"{d}/out",
                               *(["--allow-codex"] if ok else []), *extra], capture_output=True, text=True, env=env)

    def test_portrait(self):
        # cel style test, 2026-11-28: a vertical Short needs a portrait still, not a 576x1024 crop of a landscape one
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            shots = {"style": "ink", "aspect": "portrait", "shots": [{"id": "v", "prompt": "a yard"}]}
            r = self.run_it(d, "1536x1024 1024x1536", "", shots=shots)           # a landscape result is re-rolled
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("is not portrait", r.stdout)
            self.assertEqual(Image.open(f"{d}/out/v.png").size, (864, 1536))      # 9:16
            self.assertEqual(Image.open(f"{d}/out/originals/v.png").size, (1024, 1536))
            self.assertIn("portrait, 1024x1536", Path(d, "n.prompts").read_text())
            bad = {"style": "ink", "shots": [{"id": "w", "prompt": "a yard", "aspect": "square"}]}
            r = self.run_it(d, "1536x1024", "", "--check", ok=False, shots=bad)
            self.assertIn('"aspect" is "square"', r.stdout)

    def test_usage_limit_stops_the_run(self):
        # cel test, 2026-11-28: a limit answer still spent a second try on every shot (4 calls, 0 images)
        with tempfile.TemporaryDirectory() as d:
            Path(d, "limit.py").write_text("import os, sys\nst = os.environ['FAKE_STATE']\n"
                                           "n = int(open(st).read()) if os.path.exists(st) else 0\nopen(st, 'w').write(str(n + 1))\n"
                                           "sys.stderr.write(\"ERROR: You've hit your usage limit. Try again at 3:14 PM.\\n\"); sys.exit(1)\n")
            r = self.run_it(d, "1536x1024", "", env_extra={"CODEX_CMD": f"python3 {d}/limit.py"})
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertEqual(Path(d, "n").read_text(), "1")                      # one call, no retry, no second shot
            self.assertIn("Try again at 3:14 PM", r.stderr)
            rv = Path(d, "out", "review.tsv").read_text()
            self.assertIn("a\tnot run: Codex usage limit", rv); self.assertIn("b\tnot run: Codex usage limit", rv)

    def test_limit_on_exit_0_and_the_reason_for_no_image(self):
        # cel test, 2026-11-28: the limit text as Codex prints it (curly apostrophe), and a shot that drew nothing twice
        # with no reason in the log
        with tempfile.TemporaryDirectory() as d:
            Path(d, "limit.py").write_text("import os, sys\nst = os.environ['FAKE_STATE']\n"
                                           "n = int(open(st).read()) if os.path.exists(st) else 0\nopen(st, 'w').write(str(n + 1))\n"
                                           "print(\"ERROR: You\u2019ve hit your usage limit. Upgrade to Pro, or try again at 8:13 PM.)\")\n")
            r = self.run_it(d, "1536x1024", "", env_extra={"CODEX_CMD": f"python3 {d}/limit.py"})
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertEqual(Path(d, "n").read_text(), "1"); self.assertIn("try again at 8:13 PM", r.stderr)
        with tempfile.TemporaryDirectory() as d:
            Path(d, "talk.py").write_text("print('I made a plan for the picture but stopped before drawing it.')\n")
            r = self.run_it(d, "1536x1024", "", "--tries", "2", "--only", "a", env_extra={"CODEX_CMD": f"python3 {d}/talk.py"})
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)                 # nothing landed: FAILED, not PARTIAL
            self.assertIn("Codex said: I made a plan for the picture", r.stdout)
            self.assertIn("(last: Codex said:", Path(d, "out", "review.tsv").read_text())

    def test_unreadable_image_is_a_failed_try(self):
        # Survive Year 1 cel, 2026-11-28: a corrupt image.png from Codex raised UnidentifiedImageError and ended a 25-shot run
        with tempfile.TemporaryDirectory() as d:
            Path(d, "bad.py").write_text(
                "import os\nfrom PIL import Image\nst = os.environ['FAKE_STATE']\n"
                "n = int(open(st).read()) if os.path.exists(st) else 0\nopen(st, 'w').write(str(n + 1))\n"
                "d = os.path.join(os.environ['CODEX_IMAGES'], 's%d' % n); os.makedirs(d, exist_ok=True)\n"
                "if n == 0: open(os.path.join(d, 'ig.png'), 'wb').write(b'\\x89PNG not really')\n"
                "else: Image.new('RGB', (1536, 1024), (200, 150, 90)).save(os.path.join(d, 'ig.png'))\n")
            r = self.run_it(d, "1536x1024", "", "--only", "a", env_extra={"CODEX_CMD": f"python3 {d}/bad.py", "FACE_CHECK_CMD": "off"})
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("unreadable image", r.stdout); self.assertTrue(Path(d, "out", "a.png").exists())

    def test_dropped_image_is_sent_again(self):
        # cel test, 2026-11-28: Codex dropped the --image on 6 of 24 calls and asked for it again, exiting 0
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            Image.new("RGB", (1536, 1024), (90, 90, 90)).save(f"{d}/photo.png")
            Path(d, "drop.py").write_text(
                "import os, sys\nfrom PIL import Image\nst = os.environ['FAKE_STATE']\n"
                "n = int(open(st).read()) if os.path.exists(st) else 0\nopen(st, 'w').write(str(n + 1))\n"
                "if n < 2: print(\"The character sheet isn't available here. Could you attach it again?\"); sys.exit(0)\n"
                "d = os.path.join(os.environ['CODEX_IMAGES'], 's%d' % n); os.makedirs(d, exist_ok=True)\n"
                "Image.new('RGB', (1536, 1024), (200, 150, 90)).save(os.path.join(d, 'ig.png'))\n")
            shots = {"style": "ink", "shots": [{"id": "p", "prompt": "a yard", "photo": f"{d}/photo.png", "photo_licence": "own"}]}
            r = self.run_it(d, "1536x1024", "", "--tries", "1", shots=shots, env_extra={"CODEX_CMD": f"python3 {d}/drop.py"})
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)                 # landed on its one try
            self.assertEqual(Path(d, "n").read_text(), "3")                        # two re-sends, then the picture
            self.assertIn("didn't get the image", r.stdout); self.assertIn("-> 16:9", r.stdout)

    def test_check_is_a_dry_run(self):
        # 2026-11-27: the one-change rule refused a 39-shot job only after it was queued; --check fails it in seconds
        with tempfile.TemporaryDirectory() as d:
            bad = {"style": "ink", "faceless": True, "shots": [{"id": "a", "prompt": "a yard"},
                   {"id": "b", "change": "Move the truck left. Make the sky grey.", "edit_from": f"{d}/x.png"}]}
            r = self.run_it(d, "1536x1024", "NOFACE", "--check", ok=False, shots=bad)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("STOP b: one change per edit", r.stdout)
            self.assertFalse(Path(d, "n").exists())                  # no Codex call
            self.assertFalse(Path(d, "out").exists())                # no files
            r = self.run_it(d, "1536x1024", "NOFACE", "--check", ok=False)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("2 shot(s) to draw: 0 problem(s)", r.stdout)

    def test_rerolls_crops_and_stamps(self):
        import longform
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            # a: portrait (re-roll), then a face (re-roll), then good; b: not faceless, so no judge call
            r = self.run_it(d, "1024x1536 1536x1024 1536x1024 1536x1024", "FACE NOFACE")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(Path(d, "n").read_text(), "4")
            self.assertEqual(Path(d, "j").read_text(), "2")
            self.assertEqual(Image.open(f"{d}/out/a.png").size, (1536, 864))
            self.assertEqual(Image.open(f"{d}/out/originals/a.png").size, (1536, 1024))
            t = longform.png_text(Path(d, "out", "a.png"))
            self.assertEqual(t["motion-studio-model"], "chatgpt-image via codex")
            self.assertEqual(t["motion-studio-original"], "originals/a.png")
            self.assertNotIn("motion-studio-draft", t)
            self.assertIn("4 codex calls", Path(d, "out", "usage.log").read_text())
            # a rerun spends nothing
            self.assertEqual(self.run_it(d, "", "").returncode, 0)
            self.assertEqual(Path(d, "n").read_text(), "4")

    def test_sized_shots_get_the_scale_sentence_and_a_marking_page(self):
        import json
        sizes = {"man": {"h": 1.78, "source": "CDC"}, "booth": {"h": 2.31, "source": "spec sheet"}}
        shots = {"style": "ink.", "sizes": sizes, "shots": [
            {"id": "a", "prompt": "A man beside a booth.", "char_in_frame": ["man"], "props": ["booth"]},
            {"id": "b", "prompt": "A truck."}]}
        with tempfile.TemporaryDirectory() as d:
            r = self.run_it(d, "1536x1024 1536x1024", "", shots=shots)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            asked = Path(d, "n.prompts").read_text().splitlines()
            self.assertIn("about 1.3 times the man's height", asked[0])
            self.assertLess(asked[0].index("Scale:"), asked[0].index("ink."))     # before the style ending
            self.assertNotIn("Scale:", asked[1])
            lst = json.loads(Path(d, "out", "scale-list.json").read_text())
            self.assertEqual([p["image"] for p in lst["panels"]], ["a.png"])
            self.assertTrue(Path(d, "out", "scale", "index.html").exists())
            self.assertIn("a\tscale: mark it", Path(d, "out", "review.tsv").read_text())
        with tempfile.TemporaryDirectory() as d:
            bad = dict(shots, sizes={"man": {"h": 1.78, "source": "CDC"}})     # booth has no size
            r = self.run_it(d, "1536x1024", "", shots=bad)
            self.assertNotEqual(r.returncode, 0); self.assertIn("booth", r.stderr)
            self.assertFalse(Path(d, "n").exists())                                # no Codex call spent

    def test_needs_permission_and_reports_give_ups(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.run_it(d, "1536x1024", "NOFACE", ok=False)
            self.assertNotEqual(r.returncode, 0); self.assertFalse(Path(d, "n").exists())
        with tempfile.TemporaryDirectory() as d:
            # a: three faces, gives up; b: fine. An unclear judge is kept and listed.
            r = self.run_it(d, "1536x1024 1536x1024 1536x1024 1536x1024", "FACE FACE FACE", "--tries", "3")
            self.assertEqual(r.returncode, 3)                                  # PARTIAL: b landed, a didn't
            self.assertIn("PARTIAL: 1 of 2", r.stdout)
            self.assertFalse(Path(d, "out", "a.png").exists()); self.assertTrue(Path(d, "out", "b.png").exists())
            rv = Path(d, "out", "review.tsv").read_text()
            self.assertIn("a\tno usable image in 3 tries", rv); self.assertTrue(rv.startswith("# run "))
            self.assertIn("originals/a.try<n>.png", rv)                         # the rejected tries are kept
            self.assertTrue(all(Path(d, "out", "originals", f"a.try{k}.png").exists() for k in (1, 2, 3)))
        with tempfile.TemporaryDirectory() as d:
            r = self.run_it(d, "1536x1024 1536x1024", "hmm", "--only", "a")
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertIn("face check unclear", Path(d, "out", "review.tsv").read_text())


if __name__ == "__main__":
    unittest.main()


class Set3D(unittest.TestCase):
    SEG = {"id": "a", "type": "set3d", "t0": 0.3, "t1": 4.0,
           "words": [["Ada", 0.5, 0.8], ["walks", 0.9, 1.2], ["in.", 1.3, 1.6]],
           "data": {"set": "room", "cast": [{"id": "ada", "look": "ada", "at": [-1, 0], "talks": True,
                                             "keys": [{"t": 0.9, "to": [0.5, 0]}, {"t": 2.5, "pose": "cheer"}]},
                                            {"id": "eli", "look": "eli", "back": True, "at": [1.5, 1]}],
                    "camera": [{"shot": "wide"}, {"t": 2.4, "shot": "close", "on": "ada", "cut": True}]}}

    def test_cast_and_stamp(self):
        import copy, longform
        segs = [self.SEG, copy.deepcopy(self.SEG)]
        self.assertEqual([c["id"] for c in longform.set3d_cast(segs)], ["ada", "eli"])
        s0 = longform.cast_stamp(segs, {})
        segs[1]["data"]["cast"][0]["look"] = "bo"          # a changed look is a new design: the sheet is stale
        self.assertNotEqual(longform.cast_stamp(segs, {}), s0)
        self.assertNotEqual(longform.cast_stamp([self.SEG], {"ink": "#000000"}), longform.cast_stamp([self.SEG], {}))

    def test_engine_knows_set3d(self):
        import longform
        with tempfile.TemporaryDirectory() as d:
            film = Path(d)
            shutil.copy(longform.TEMPLATE, film / "film.html")
            probs, _ = longform.engine_drift(film, [self.SEG])
            self.assertTrue(probs)                          # no set3d.js or three.min.js beside the page: unknown
            for f in longform.ENGINE_FILES:
                shutil.copy(longform.HERE / f, film / f)
            self.assertEqual(longform.engine_drift(film, [self.SEG]), ([], []))

    @unittest.skipUnless(os.environ.get("MS_CHROMIUM"), "needs a browser (MS_CHROMIUM)")
    def test_renders_and_moves(self):
        import json, longform, render
        with tempfile.TemporaryDirectory() as d:
            film = Path(d)
            shutil.copy(longform.TEMPLATE, film / "film.html")
            for f in longform.ENGINE_FILES:
                shutil.copy(longform.HERE / f, film / f)
            (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps({"duration": 4.0, "segments": [self.SEG]}) + ";\n")
            with render.Film(film) as f:
                shots = []
                for t in (1.0, 2.0, 3.0):
                    f.seek(t)
                    shots.append(f.page.evaluate("[...document.querySelectorAll('[data-proxy] rect')].map(r => [r.getAttribute('x'), r.getAttribute('class')])"))
                self.assertFalse(f.errors)
            self.assertEqual(len(shots[0]), 3)              # the set's anchor and one box per cutout
            self.assertNotEqual(shots[0], shots[1])         # the walk and the camera move the proxies
            self.assertIn("cheer r", [c for _, c in shots[2]])

    def test_camera_keys_are_shots(self):
        import longform
        self.assertEqual(longform.shot_starts([self.SEG, {"id": "b", "type": "stat", "t0": 4.0, "t1": 6.0}]), [0.3, 2.4, 4.0])

    def test_one_worker_for_set3d_on_a_mac(self):
        import longform
        tl3, tl2 = {"segments": [self.SEG]}, {"segments": [{"id": "b", "type": "stat"}]}
        self.assertEqual(longform.render_workers(tl3, "darwin"), 1)
        self.assertEqual(longform.render_workers(tl3, "linux"), 4)
        self.assertEqual(longform.render_workers(tl2, "darwin"), 4)


class Flipbook(unittest.TestCase):
    def seg(self, **d):
        return {"id": "p", "type": "panel", "t0": 1.0, "t1": 3.0, "data": dict({"frames": ["a.png", "b.png", "c.png"]}, **d)}

    def test_cycles_at_4fps_and_loops(self):
        import longform
        flips, probs = longform.panel_flips(self.seg())
        self.assertFalse(probs)
        self.assertEqual(flips[:5], [[1.0, 0], [1.2667, 1], [1.5, 2], [1.7333, 0], [2.0, 1]])   # 7.5 frames a drawing, rounded to whole frames
        self.assertEqual(len(flips), 8)
        self.assertTrue(all(abs(t * 30 - round(t * 30)) < 0.003 for t, _ in flips))   # on whole video frames (4 decimals)

    def test_hold_last_and_fps(self):
        import longform
        flips, _ = longform.panel_flips(self.seg(hold_last=True, fps=2))
        self.assertEqual(flips, [[1.0, 0], [1.5, 1], [2.0, 2]])
        flips, _ = longform.panel_flips(self.seg(fps=6))
        self.assertEqual(flips[1], [1.1667, 1])                                           # 1 + 1/6 s, on frame 35

    def test_cued_frames(self):
        import longform
        s = self.seg(frames=["a.png", {"image": "b.png", "at": "lifts", "t": 1.7}, {"image": "c.png", "at": "light", "t": 2.4}])
        self.assertEqual(longform.panel_flips(s), ([[1.0, 0], [1.7, 1], [2.4, 2]], []))
        s["data"]["frames"][2] = "c.png"
        self.assertTrue(longform.panel_flips(s)[1])                                       # a cued flipbook needs every cue
        self.assertEqual(longform.flip_frames([dict(self.seg(), data={"flips": [[1.0, 0], [1.7, 1], [2.4, 2]]})]), {51, 72})

    def test_images(self):
        import longform
        self.assertEqual(longform.panel_images({"frames": ["a.png", {"image": "b.png", "at": "x"}]}), ["a.png", "b.png"])
        self.assertEqual(longform.panel_images({"image": "a.png"}), ["a.png"])
        self.assertEqual(longform.panel_images({}), [])

    @unittest.skipUnless(os.environ.get("MS_CHROMIUM"), "needs a browser (MS_CHROMIUM)")
    def test_page_flips_on_schedule(self):
        import io, json, longform, render
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            film = Path(d); (film / "panels").mkdir()
            for name, c in (("a", (200, 30, 30)), ("b", (30, 200, 30)), ("c", (30, 30, 200))):
                Image.new("RGB", (1920, 1080), c).save(film / "panels" / f"{name}.png")
            shutil.copy(longform.TEMPLATE, film / "film.html")
            for f in longform.ENGINE_FILES:
                shutil.copy(longform.HERE / f, film / f)
            seg = {"id": "p", "type": "panel", "t0": 0.0, "t1": 3.0, "data": {"camera": "hold", "image": "panels/a.png",
                   "frames": ["panels/a.png", {"image": "panels/b.png", "t": 1.0}, {"image": "panels/c.png", "t": 2.0}]}}
            seg["data"]["flips"], probs = longform.panel_flips(seg)
            self.assertFalse(probs)
            (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps({"duration": 3.0, "segments": [seg], "grade": {"vignette": 0}}) + ";\n")
            got = []
            with render.Film(film) as f:
                for t in (0.5, 29 / 30, 1.0, 2.5):
                    f.seek(t)
                    px = Image.open(io.BytesIO(f.page.locator("#stage").screenshot())).convert("RGB").getpixel((960, 540))
                    got.append(max(range(3), key=lambda i: px[i]))
                self.assertFalse(f.errors)
            self.assertEqual(got, [0, 0, 1, 2])                    # red, still red one frame before the cue, green, blue


class CodexEdit(unittest.TestCase):
    """codex_stills edit mode: flipbook frames edited from a base still, against the same fake codex."""
    FAKE, JUDGE, run_it = CodexStills.FAKE, CodexStills.JUDGE, CodexStills.run_it

    def test_edit_from_locks_the_base(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            shots = {"style": "ink.", "shots": [
                {"id": "a", "prompt": "A woman holds a flashlight."},
                {"id": "a2", "edit_from": "a", "change": "the flashlight is raised to her shoulder"},
                {"id": "z", "edit_from": "nope", "change": "anything"}]}
            # a2 comes back at the base's shape but half the size: resized to the base
            r = self.run_it(d, "1536x1024 768x512", "", shots=shots)
            self.assertEqual(r.returncode, 3, r.stdout + r.stderr)          # z has no base; a and a2 landed (PARTIAL)
            argv = Path(d, "n.argv").read_text().splitlines()
            self.assertNotIn("--image", argv[0])
            # the image is one --image=<path> argument, never a bare -i, which takes every argument after it as a file
            # and swallowed the prompt on the real CLI
            self.assertEqual(argv[1].split()[-1], f"--image={d}/out/originals/a.png")
            self.assertNotIn("-i", argv[1].split())
            prompts = Path(d, "n.prompts").read_text().splitlines()
            self.assertIn("Change only this: the flashlight is raised to her shoulder.", prompts[1])
            self.assertNotIn("ink.", prompts[1])                             # the style isn't asked for again
            self.assertEqual(Image.open(f"{d}/out/a2.png").size, Image.open(f"{d}/out/a.png").size)
            rv = Path(d, "out", "review.tsv").read_text()
            self.assertIn("a2\tresized 768x512", rv); self.assertIn("z\tedit base nope not found", rv)


    def test_cast_and_props_in_every_prompt(self):
        # Survive Year 1, 2026-11-28: edits without the lead's description came back with strangers and a blue truck
        with tempfile.TemporaryDirectory() as d:
            shots = {"style": "ink.", "cast": {"the owner": "a woman in her 40s, short grey hair, navy work jacket"},
                     "props": {"the truck": "white box truck with a blue stripe", "the unit": "green plastic porta-potty"},
                     "shots": [{"id": "a", "prompt": "The owner by the truck."},
                               {"id": "a2", "edit_from": "a", "change": "the owner opens the truck door"}]}
            r = self.run_it(d, "1536x1024 1536x1024", "", shots=shots)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            prompts = Path(d, "n.prompts").read_text().splitlines()
            for pr in prompts:
                self.assertIn("the owner: a woman in her 40s, short grey hair, navy work jacket", pr)
                self.assertIn("the truck: white box truck with a blue stripe", pr)
                self.assertIn("Do not add any that aren't in it", pr)
            bad = dict(shots, cast=["the owner"])
            r = self.run_it(d, "1536x1024", "", "--check", ok=False, shots=bad)
            self.assertIn('"cast" at the file level', r.stdout + r.stderr)

    def test_frame_snaps_to_its_base_frame(self):
        # episode thread, 2026-11-27: a 1672x940 base came back cropped to 1671x940 and timeline stopped on
        # "flipbook frames differ in size"
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            Image.new("RGB", (1672, 940), (90, 90, 90)).save(f"{d}/base.png")
            shots = {"style": "ink.", "shots": [{"id": "b2", "edit_from": f"{d}/base.png", "change": "the lid is open"}]}
            r = self.run_it(d, "1672x940", "", shots=shots)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            im = Image.open(f"{d}/out/b2.png")
            self.assertEqual(im.size, (1672, 940))
            self.assertTrue(im.text.get("motion-studio-original"))                # its PNG text survives the snap
            self.assertIn("b2\tsnapped 1671x940 to its base frame's 1672x940", Path(d, "out", "review.tsv").read_text())

class PanelFx(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("MS_CHROMIUM"), "needs a browser (MS_CHROMIUM)")
    def test_effects_draw_and_never_flash(self):
        import io, json, longform, render
        import numpy as np
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            film = Path(d); (film / "panels").mkdir()
            Image.new("RGB", (1920, 1080), (60, 60, 70)).save(film / "panels" / "a.png")
            shutil.copy(longform.TEMPLATE, film / "film.html")
            for f in longform.ENGINE_FILES:
                shutil.copy(longform.HERE / f, film / f)
            fx = [{"type": "rain"}, {"type": "snow", "area": [0, 0, 0.5, 1]}, {"type": "beam", "from": [0.3, 0.5], "aim": [0.7, 0.7]},
                  {"type": "flicker", "amount": 1.0}, {"type": "flicker", "pos": [0.5, 0.3], "radius": 0.9, "amount": 1.0},
                  {"type": "steam", "pos": [0.5, 0.8]}, {"type": "highlight", "pos": [0.6, 0.4], "r": 0.9, "zoom": 1.2, "t": 0.5},
                  {"type": "fog"}]
            seg = {"id": "p", "type": "panel", "t0": 0.0, "t1": 2.0, "data": {"image": "panels/a.png", "camera": "hold", "fx": fx}}
            (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps({"duration": 2.0, "segments": [seg], "grade": {"vignette": 0}}) + ";\n")
            frames = []
            with render.Film(film) as f:
                self.assertTrue(any('"fog"' in w for w in f.studio.get("warnings", [])))   # an unknown effect is named
                for i in range(60):
                    f.seek(i / 30)
                    frames.append(np.asarray(Image.open(io.BytesIO(f.page.locator("#stage").screenshot())).convert("RGB").resize((320, 180))))
                self.assertFalse(f.errors)
            self.assertGreater(float(np.abs(frames[30].astype(int) - frames[29]).mean()), 0.5)   # rain and snow move every frame
            self.assertEqual(readcheck.flash_frames(frames, 30, 320, 180), [])                 # the flickers stay under the flash limit


class ReviewNotes1126(unittest.TestCase):
    """Engine fixes from the owner's 51-note review of Survive Year 1 (2026-11-26)."""

    def test_config_keeps_nested_tool_settings(self):
        import studio
        with tempfile.TemporaryDirectory() as d:
            home = Path(d); (home / ".motion-studio.json").write_text(json.dumps({"root": "~/films", "mflux": {"bin": "/x"}}))
            old = os.environ.get("HOME"); os.environ["HOME"] = str(home)
            try:
                cfg = studio.config()
            finally:
                os.environ["HOME"] = old
        self.assertEqual(cfg["mflux"], {"bin": "/x"})
        self.assertEqual(cfg["root"], home / "films")

    def test_refused_words_and_faceless_hero(self):
        import panelqa
        self.assertTrue(panelqa.refused("a row of porta-potties with blank doors"))
        self.assertTrue(panelqa.refused("one featureless plastic door"))
        self.assertFalse(panelqa.refused("doors with a gray pull handle and a red/green occupancy dot"))
        self.assertEqual(panelqa.lint([{"id": 1, "prompt": "The hero from behind, face hidden in black shadow."}], route="codex"), [])
        self.assertTrue(panelqa.lint([{"id": 1, "prompt": "A tank hidden under the floor."}], route="codex"))

    def test_lint_false_positives_from_the_cel_test(self):
        import panelqa, anatomy
        lint = lambda t: " ".join(map(str, panelqa.lint([{"id": 1, "prompt": t}], route="codex")))
        self.assertNotIn("touch", lint("A huge lumpy head with tiny features."))              # "huge" is not "hug"
        self.assertTrue(lint("Two men hug by the truck."))
        self.assertNotIn("tipped", lint("A foreman, hard hat tipped far back, grins."))       # worn, not knocked over
        self.assertIn("tipped", lint("A porta-potty tipped over in the mud."))
        self.assertNotIn("money", lint("A yard at dawn. No money or coins anywhere."))
        self.assertIn("money", lint("A stack of cash on the desk."))
        with tempfile.TemporaryDirectory() as d:                                # "sheet": true tells the judge about detached heads
            cmd = f"echo \"{{faceless_rule}} sheet={{sheet}}\" > {d}/said; echo PASS"
            self.assertEqual(anatomy.judge(f"{d}/x.png", sheet=True, cmd=cmd)[0], "PASS")
            said = Path(d, "said").read_text()
            self.assertIn("model sheet", said); self.assertIn("sheet=yes", said)
            anatomy.judge(f"{d}/x.png", cmd=cmd)
            self.assertNotIn("model sheet", Path(d, "said").read_text())

    def test_strong_push_flagged(self):
        import longform
        strong = {"id": "a", "type": "panel", "data": {"camera": {"from": {"x": 0.3, "y": 0.4, "z": 1.32}, "to": {"x": 0.3, "y": 0.4, "z": 1.75}}}}
        self.assertEqual(longform.strong_pushes([strong, {"id": "b", "type": "panel", "data": {"camera": "slow-push"}}]), [("a", 1.75)])

    @unittest.skipUnless(os.environ.get("MS_CHROMIUM"), "needs a browser (MS_CHROMIUM)")
    def test_caption_card_under_tag_and_source_plate(self):
        import longform, render
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            film = Path(d); (film / "panels").mkdir()
            Image.new("RGB", (1920, 1080), (40, 40, 44)).save(film / "panels" / "a.png")
            shutil.copy(longform.TEMPLATE, film / "film.html")
            for f in longform.ENGINE_FILES:
                shutil.copy(longform.HERE / f, film / f)
            seg = {"id": "p", "type": "panel", "t0": 0.0, "t1": 3.0, "source": "County records, 2025", "tag": {"text": "Day 1"},
                   "data": {"image": "panels/a.png", "camera": "slow-push", "caption_card": "Twelve units, one crew"}}
            (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps({"duration": 3.0, "segments": [seg]}) + ";\n")
            with render.Film(film) as f:
                f.seek(1.5)
                boxes = f.page.evaluate("""() => {
                  const r = el => { const b = el.getBoundingClientRect(); return [b.left, b.top, b.right, b.bottom]; };
                  const tag = [...document.querySelectorAll('rect')].find(x => x.getAttribute('fill') === '#F1EDE4' && x.closest('g') && x.getBBox().x < 200 && x.getBBox().width > 0);
                  const card = [...document.querySelectorAll('rect')].find(x => x.getAttribute('stroke-opacity') === '0.35');
                  const src = [...document.querySelectorAll('text')].find(x => x.textContent.startsWith('Source:'));
                  const plate = src.previousElementSibling;
                  return {tag: r(tag), card: r(card), src: r(src), plate: r(plate), op: +plate.getAttribute('opacity'), fill: src.getAttribute('fill')};
                }""")
                self.assertFalse(f.errors)
        self.assertGreaterEqual(boxes["card"][1], boxes["tag"][3])          # the caption card starts below the tag card
        self.assertGreater(boxes["op"], 0.5)                                  # the plate is up on a panel
        p, t = boxes["plate"], boxes["src"]
        self.assertTrue(p[0] <= t[0] and p[1] <= t[1] and p[2] >= t[2] and p[3] >= t[3])   # and covers the line
        self.assertNotEqual(boxes["fill"].lower(), "#969691")


class RepairRoute(unittest.TestCase):
    """Owner's rule (2026-11-26, picture-truth.md rule 15): local image models are drafts only; repair goes to ChatGPT."""
    FAKE = r"""
import os, sys
from PIL import Image
args = sys.argv[1:-1]
open(os.environ["FAKE_LOG"], "a").write(" ".join(args) + "\n" + sys.argv[-1] + "\n")
base = Image.open([a.split("=", 1)[1] for a in args if a.startswith("--image=")][0]).convert("RGB")
n = len(open(os.environ["FAKE_LOG"]).read().splitlines()) // 2
if n == 1:
    Image.new("RGB", (1536, 1024)).save("image.png")      # first answer not square: asked again
else:
    base.paste((20, 20, 20), (0, 0, base.size[0], base.size[1] // 2)); base.save("image.png")
"""

    def film(self, d):
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo
        film = Path(d) / "film"; (film / "panels").mkdir(parents=True)
        i = PngInfo(); i.add_text("motion-studio-model", "chatgpt-image via codex")
        Image.new("RGB", (1600, 900), (230, 220, 200)).save(film / "panels" / "p1.png", pnginfo=i)
        return film

    def env(self, d):
        Path(d, "fake.py").write_text(self.FAKE)
        return {"CODEX_CMD": f"python3 {d}/fake.py", "CODEX_ARGS": "", "CODEX_IMAGES": f"{d}/none", "FAKE_LOG": f"{d}/log"}

    def test_default_is_chatgpt_with_the_crop_as_base(self):
        import panelqa, longform
        with tempfile.TemporaryDirectory() as d:
            film = self.film(d)
            with unittest.mock.patch.dict(os.environ, self.env(d)):
                os.environ.pop("CODEX_STILLS_OK", None)
                with self.assertRaises(SystemExit) as no:
                    panelqa.repair(film, "panels/p1.png", "600,300,200,200", "Redraw the hand with five fingers")
                self.assertIn("--allow-codex", str(no.exception))
                rdir, made, secs, e = panelqa.repair(film, "panels/p1.png", "600,300,200,200", "Redraw the hand with five fingers",
                                                     seeds=[1], allow_codex=True)
            log = Path(d, "log").read_text().splitlines()
            self.assertEqual(len(log), 4)                                   # one re-ask for the non-square answer
            self.assertIn(f"--image={rdir / 'crop.png'}", log[0])          # the crop is the edit base
            self.assertIn("Change only this: Redraw the hand with five fingers.", log[1])
            self.assertEqual((e["model"], e["draft"], made), ("chatgpt", False, [1]))
            panelqa.accept(film, e["id"])
            t = longform.png_text(film / "panels" / "p1.png")
            self.assertEqual(t.get("motion-studio-model"), "chatgpt-image via codex")   # stamps survive the paste
            self.assertNotIn("motion-studio-draft", t)
            self.assertEqual(panelqa.draft_repairs(film), {})

    def test_codex_gets_no_stdin_from_an_open_pipe(self):
        # codex exec reads stdin when it is a pipe: from an agent's background shell every call hung to the timeout
        with tempfile.TemporaryDirectory() as d:
            Path(d, "fake.py").write_text("import sys\nsys.stdin.read()\nfrom PIL import Image\nImage.new('RGB', (64, 64)).save('image.png')\n")
            Path(d, "base.png").write_bytes(b"")
            child = ("import sys; sys.path.insert(0, %r); import codex_stills; "
                     "print(codex_stills.edit_image('p', [], %r))") % (str(Path(__file__).parent), f"{d}/out.png")
            env = dict(os.environ, CODEX_CMD=f"python3 {d}/fake.py", CODEX_ARGS="", CODEX_IMAGES=f"{d}/none", CODEX_TIMEOUT="5")
            p = subprocess.Popen(["python3", "-c", child], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)
            try:
                out = p.stdout.readline()          # the pipe into the child stays open, as in a background shell
                p.wait(timeout=20)
            finally:
                p.stdin.close(); p.kill()
            self.assertEqual(out.strip(), "None")
            self.assertTrue(Path(d, "out.png").exists())
        for f in ("codex_stills.py", "anatomy.py"):     # every agent CLI call closes stdin
            calls = [x for x in Path(__file__).with_name(f).read_text().split("subprocess.run(")[1:] if not x.startswith('["ffmpeg"')]
            self.assertTrue(calls and all("stdin=subprocess.DEVNULL" in x.split("\n", 2)[0] + x.split("\n", 2)[1] for x in calls), f)

    def test_markup_route_and_edit_ab(self):
        # the markup route sends the whole panel with the region outlined plus the clean panel; the fake answers with the
        # clean panel darkened in the box, at ChatGPT's 3:2 shape, with a stray change far outside it
        fake = r"""
import os, sys
from PIL import Image, ImageDraw
args = sys.argv[1:-1]; prompt = sys.argv[-1]
open(os.environ["FAKE_LOG"], "a").write(" ".join(args) + "\n")
ims = [a.split("=", 1)[1] for a in args if a.startswith("--image=")]
im = Image.open(ims[0]).convert("RGB")
W, H = im.size
d = ImageDraw.Draw(im)
if W == H:                                  # the crop route: darken the middle
    d.rectangle((W // 3, H // 3, 2 * W // 3, 2 * H // 3), fill=(30, 30, 30))
else:
    d.rectangle((600, 300, 800, 500), fill=(30, 30, 30)); d.rectangle((0, 0, 120, 120), fill=(0, 90, 0))
    im = im.resize((1536, 1024)) if "red rectangle" in prompt else im
im.save("image.png")
"""
        import panelqa, longform
        from PIL import Image, ImageDraw
        with tempfile.TemporaryDirectory() as d:
            film = self.film(d)
            im = Image.open(film / "panels" / "p1.png"); dr = ImageDraw.Draw(im)
            for i in range(0, 1600, 80):
                dr.line((i, 0, i + 300, 900), fill=(90, 70, 50), width=5)          # texture, so likeness means something
            im.save(film / "panels" / "p1.png")
            Path(d, "fake.py").write_text(fake)
            env = dict(self.env(d), EDITAB_JUDGE_CMD="echo 'A: FIXED CLEAN'; echo 'B: NOT SEAM'")
            with unittest.mock.patch.dict(os.environ, env):
                rdir, made, secs, e = panelqa.repair(film, "panels/p1.png", "600,300,200,200", "darken it", seeds=[1],
                                                     allow_codex=True, method="markup")
                self.assertEqual(made, [1])
                self.assertEqual(e["full"]["1"]["fit"], "stretched")             # 3:2 answer mapped onto 16:9
                self.assertLess(e["full"]["1"]["outside_likeness"], 1.0)
                self.assertEqual(e["full"]["1"]["red_left"], 0.0)
                mk = np.asarray(Image.open(rdir / "markup.png").convert("RGB"))
                self.assertEqual(tuple(mk[300 - 32 - 3, 700]), (255, 0, 0))         # outline just outside the paste
                cand = np.asarray(Image.open(rdir / "candidate_1.png").convert("RGB")).astype(int)
                orig = np.asarray(Image.open(film / "panels" / "p1.png").convert("RGB")).astype(int)
                self.assertEqual(int(np.abs(cand[:200, :200] - orig[:200, :200]).max()), 0)   # the stray change is not pasted
                self.assertGreater(np.abs(cand[350:450, 650:750] - orig[350:450, 650:750]).mean(), 40)
                with self.assertRaises(SystemExit):
                    panelqa.repair(film, "panels/p1.png", "600,300,200,200", "x", model="qwen", draft=True, method="markup")
                r = subprocess.run(["python3", str(Path(__file__).with_name("editab.py")), str(film), "--image", "panels/p1.png",
                                    "--box", "600,300,200,200", "--change", "darken it", "--allow-codex", "--seed", "3"],
                                   capture_output=True, text=True, stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            ab = film / "repairs" / "ab" / "panels" / "p1" / "01"
            res = json.loads((ab / "results.json").read_text())
            self.assertEqual([x["method"] for x in res["rows"]], ["crop", "markup", "whole"])
            key = json.loads((ab / "key.json").read_text())
            self.assertEqual(len(key), 4)                                           # crop, markup, whole raw, whole pasted
            self.assertEqual(res["blind"], {key["A"]: {"fixed": True, "clean": True}, key["B"]: {"fixed": False, "clean": False}})
            whole = [x for x in res["rows"] if x["method"] == "whole"][0]
            self.assertLess(whole["outside_likeness"], 1.0)
            wl = json.loads((film / "repairs" / "log.json").read_text())[-1]
            self.assertEqual((wl["method"], wl["margin"], wl["feather"]), ("whole", 64, 48))   # the wider blend
            self.assertEqual(json.loads((film / "repairs" / "log.json").read_text())[0]["feather"], 16)
            for f in ("report.md", "blind.png", "compare.png"):
                self.assertTrue((ab / f).exists(), f)

    def test_default_runs_markup_and_whole_and_sharpens_soft_redraws(self):
        import panelqa
        fake = r"""
import os, sys
from PIL import Image, ImageFilter
args = sys.argv[1:-1]
open(os.environ["FAKE_LOG"], "a").write(" ".join(args) + "\n")
im = Image.open([a.split("=", 1)[1] for a in args if a.startswith("--image=")][0]).convert("RGB")
im.filter(ImageFilter.GaussianBlur(0.7)).save("image.png")        # a soft answer, as ChatGPT's were
"""
        from PIL import Image, ImageDraw
        with tempfile.TemporaryDirectory() as d:
            film = self.film(d)
            im = Image.open(film / "panels" / "p1.png"); dr = ImageDraw.Draw(im)
            for i in range(0, 1600, 24):
                dr.line((i, 0, i + 200, 900), fill=(40, 30, 20), width=2)
            im.save(film / "panels" / "p1.png")
            env = dict(os.environ, **self.env(d)); env.pop("CODEX_STILLS_OK", None)
            Path(d, "fake.py").write_text(fake)
            r = subprocess.run(["python3", str(Path(__file__).with_name("longform.py")), "repair", str(film), "--image", "panels/p1.png",
                                "--box", "600,300,200,200", "--prompt", "fix the hand", "--allow-codex", "--min-change", "0"],
                               capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            log = json.loads((film / "repairs" / "log.json").read_text())
            self.assertEqual([(e["method"], e["seeds"]) for e in log], [("markup", [1]), ("whole", [1])])   # 2 Codex images
            self.assertEqual(len(Path(d, "log").read_text().splitlines()), 2, Path(d, "log").read_text())
            for e in log:
                c = e["checks"]["1"]
                self.assertGreater(c["sharpen"], 0)                        # the soft redraw was sharpened
                self.assertGreater(c["sharpness"], 0.7, c)                 # to near the panel's detail
            # accept pastes the same sharpened redraw the candidate showed
            panelqa.accept(film, log[1]["id"])
            a = np.asarray(Image.open(film / "panels" / "p1.png").convert("RGB")).astype(int)
            b = np.asarray(Image.open(film / "repairs" / "panels" / "p1" / "r02" / "candidate_1.png").convert("RGB")).astype(int)
            self.assertLessEqual(int(np.abs(a - b).max()), 1)

    def test_local_models_are_drafts_only_and_render_refuses_them(self):
        import panelqa, longform
        with tempfile.TemporaryDirectory() as d:
            film = self.film(d)
            for m in ("qwen", "klein"):
                with self.assertRaises(SystemExit) as no:
                    panelqa.repair(film, "panels/p1.png", "600,300,200,200", "fix", model=m)
                self.assertIn("drafts only", str(no.exception))
            self.assertFalse((film / "repairs").exists())                  # refused before anything ran
            (film / "repairs").mkdir()
            (film / "repairs" / "log.json").write_text(json.dumps([{"id": "panels/p1/r01", "image": "panels/p1.png",
                                                                     "model": "qwen", "status": "accepted"}]))
            segs = [{"id": "s1", "type": "panel", "data": {"image": "panels-hd/panels/p1.png"}}]
            flags = longform.draft_stills(film, segs)
            self.assertEqual(len(flags), 1); self.assertIn("qwen repair", flags[0])
        src = Path(longform.__file__).read_text()
        self.assertIn('p.add_argument("--model", choices=panelqa.ROUTES,', src)    # no local default on the CLI
        self.assertEqual(panelqa.repair.__kwdefaults__["model"], "chatgpt")


class RedrawRules(unittest.TestCase):
    """picture-truth.md rule 16 (owner, 2026-11-27): photo first, one flaw one spot, blockout then style pass, gate."""

    def test_shot_problems(self):
        import codex_stills
        with tempfile.TemporaryDirectory() as d:
            ph = Path(d, "p.jpg"); ph.write_bytes(b"x")
            bad = codex_stills.shot_problems([
                {"id": "a", "prompt": "yard", "photo": str(ph)},                                   # no licence
                {"id": "b", "edit_from": "a", "change": "raise the gate", "note": 12},             # a note's fix
                {"id": "c", "edit_from": "a", "change": "raise the gate. Move the truck left."},   # two changes
                {"id": "d", "prompt": "yard", "photo": str(ph), "photo_licence": "own", "style_refs": ["1", "2", "3", "4", "5"]},
                {"id": "e", "prompt": "yard", "blockout": f"{d}/missing.png"},
            ], {})
            got = {x.split(":")[0] for x in bad}
            self.assertEqual(got, {"a", "b", "c", "d", "e"}, bad)
            self.assertEqual(codex_stills.shot_problems([{"id": "f", "prompt": "yard", "photo": str(ph), "photo_licence": "own"},
                                                         {"id": "g", "edit_from": "f", "change": "the gate is raised"}], {}), [])

    def test_photo_and_blockout_routes(self):
        from PIL import Image
        fake = r"""
import os, sys
from PIL import Image
open(os.environ["FAKE_LOG"], "a").write(repr(sys.argv[1:]) + "\n")
Image.new("RGB", (1536, 1024), (120, 110, 100)).save("image.png")
"""
        with tempfile.TemporaryDirectory() as d:
            Path(d, "fake.py").write_text(fake)
            for f in ("photo.jpg", "block.png", "style.png"):
                Image.new("RGB", (900, 600)).save(Path(d, f))
            Path(d, "shots.json").write_text(json.dumps({"style": "ink on cream", "style_refs": [f"{d}/style.png"], "shots": [
                {"id": "a", "prompt": "a wedding trailer", "photo": f"{d}/photo.jpg", "photo_licence": "own"},
                {"id": "b", "prompt": "a gate by a fence", "blockout": f"{d}/block.png"},
                {"id": "c", "prompt": "a truck at dawn"}]}))                   # plain: the style ref is its only image
            env = dict(os.environ, CODEX_CMD=f"python3 {d}/fake.py", CODEX_ARGS="", CODEX_IMAGES=f"{d}/none",
                       FAKE_LOG=f"{d}/log", FACE_CHECK_CMD="off", ANATOMY_CHECK_CMD="off")
            r = subprocess.run(["python3", str(Path(__file__).with_name("codex_stills.py")), f"{d}/shots.json", f"{d}/out",
                                "--allow-codex"], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            calls = [eval(x) for x in Path(d, "log").read_text().splitlines()]
            self.assertEqual(len(calls), 4)                                     # the photo, blockout + style pass, plain
            self.assertEqual(calls[0][:2], [f"--image={d}/photo.jpg", f"--image={d}/style.png"])   # the photo is image 1
            self.assertIn("Image 1 is a reference photo", calls[0][-1]); self.assertIn("Image 2 is a style reference", calls[0][-1])
            self.assertEqual(calls[1][0], f"--image={d}/block.png"); self.assertIn("blockout", calls[1][-1])
            self.assertIn("Repaint only the surfaces", calls[2][-1])
            self.assertIn("ink on cream", calls[2][-1])
            self.assertEqual(calls[3][0], f"--image={d}/style.png")                 # cel test: refs were dropped here
            self.assertTrue(calls[3][-1].startswith("Image 1 is a style reference only"))
            self.assertIn("4 codex calls", Path(d, "out", "usage.log").read_text())

    def test_gate_holds_new_takes_and_ruler(self):
        import gate, preview
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            film = Path(d)
            Image.new("RGB", (800, 450), (230, 220, 200)).save(film / "t.png")
            m = round((film / "t.png").stat().st_mtime, 3)
            self.assertEqual(gate.status(film, "t.png", m), "unchecked")
            self.assertEqual(gate.status(film, "t.png", gate.SINCE - 10), "old")      # made before the rule
            with self.assertRaises(SystemExit):
                gate.record(film, "t.png", "", True)                                  # the note's own question is required
            out = gate.ruler(film, "t.png", 400, 400, 120, 7)                         # a 7 ft door from y 400 to 120
            px = np.asarray(Image.open(out).convert("RGB"))
            self.assertEqual(tuple(px[400 - 140, 400 + 5]), (255, 255, 0))           # 40 px a foot: the 3.5 ft tick
            self.assertEqual(tuple(px[400 - 140, 400 + 15]), (230, 220, 200))        # a half-foot tick is short
            self.assertEqual(tuple(px[400 - 160, 400 + 15]), (255, 255, 0))          # a whole-foot one is long
            gate.record(film, "t.png", "Is the handle at 3.5 ft?", True, ruler_args=[400, 400, 120, 7])
            self.assertEqual(gate.status(film, "t.png", m), "pass")
            os.utime(film / "t.png", (m + 5, m + 5))                                  # redrawn under the same name
            self.assertEqual(gate.status(film, "t.png", m + 5), "unchecked")
            self.assertIn("gwait", Path(preview.__file__).with_name("preview.html").read_text())


class HudDelta(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("MS_CHROMIUM"), "needs a browser (MS_CHROMIUM)")
    def test_change_chip_pops_holds_fades_and_can_be_hidden(self):
        import io, longform, render, readcheck
        from PIL import Image
        segs = [{"id": "a", "type": "title", "t0": 0.0, "t1": 3.0, "data": {"text": "Month one"},
                 "counter": {"value": "−$25,375", "label": "Cash"}},
                {"id": "b", "type": "title", "t0": 3.0, "t1": 7.0, "data": {"text": "Month two"},
                 "counter": {"value": "−$24,302", "delta_label": "this month", "hero": False}},   # the corner chip itself (CounterHero covers the hero)
                {"id": "c", "type": "title", "t0": 7.0, "t1": 10.0, "data": {"text": "Reset"},
                 "counter": {"value": "−$28,227", "delta": False}}]
        with tempfile.TemporaryDirectory() as d:
            film = Path(d)
            shutil.copy(longform.TEMPLATE, film / "film.html")
            for f in longform.ENGINE_FILES:
                shutil.copy(longform.HERE / f, film / f)
            (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps({"duration": 10.0, "segments": segs}) + ";\n")
            chip = "() => { const g = document.querySelector('#hud'); const r = [...g.querySelectorAll('rect[rx=\"10\"]')][0]; " \
                   "const p = r.parentNode; return [p.style.display !== 'none' && p.getAttribute('display') !== 'none' ? " \
                   "+(p.getAttribute('opacity') || 1) : 0, r.getAttribute('fill'), p.querySelector('text').textContent, " \
                   "p.querySelectorAll('text')[1].textContent]; }"
            with render.Film(film) as f:
                got = {}
                for t in (1.0, 3.05, 3.6, 5.0, 5.8, 7.6):
                    f.seek(t); got[t] = f.page.evaluate(chip)
                frames = []
                for i in range(90):
                    f.seek(2.9 + i / 30)
                    frames.append(np.asarray(Image.open(io.BytesIO(f.page.locator("#stage").screenshot())).convert("RGB").resize((320, 180))))
                self.assertFalse(f.errors)
        self.assertEqual(got[1.0][0], 0)                                    # no chip on the first value
        self.assertGreater(got[3.6][0], 0.99)                               # popped in as the count starts
        self.assertEqual(got[3.6][2:], ["+$1,073", "THIS MONTH"])
        self.assertEqual(got[3.6][1].lower(), "#2e7d32")                    # POS green for a rise
        self.assertGreater(got[5.0][0], 0.99)                               # held about 2 s
        self.assertEqual(got[5.8][0], 0)                                    # then gone
        self.assertEqual(got[7.6][0], 0)                                    # "delta": false on a reset
        self.assertEqual(readcheck.flash_frames(frames, 30, 320, 180), [])


class PanelScreen(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("MS_CHROMIUM"), "needs a browser (MS_CHROMIUM)")
    def test_text_maps_into_the_screen_types_on_cue_and_reads(self):
        import io, longform, render, readcheck
        from PIL import Image, ImageDraw
        quad = [(700, 260), (1290, 300), (1270, 640), (690, 660)]
        with tempfile.TemporaryDirectory() as d:
            film = Path(d); (film / "panels").mkdir()
            im = Image.new("RGB", (1920, 1080), (226, 214, 190)); ImageDraw.Draw(im).polygon(quad, fill=(30, 40, 50))
            im.save(film / "panels" / "lap.png")
            sc = {"quad": [[x / 1920, y / 1080] for x, y in quad],
                  "lines": [{"text": "Units owned: 40"}, {"text": "Rented: 31 a week"}, {"text": "Cash: +$4,860", "t": 3.0}]}
            segs = [{"id": "v01a", "type": "panel", "t0": 0, "t1": 6, "data": {"image": "panels/lap.png", "shot": "MED", "screen": sc}},
                    {"id": "bad", "type": "panel", "t0": 6, "t1": 7, "data": {"image": "panels/lap.png", "screen": {"quad": [[0, 0]], "lines": ["x"]}}}]
            shutil.copy(longform.TEMPLATE, film / "film.html")
            for f in longform.ENGINE_FILES:
                shutil.copy(longform.HERE / f, film / f)
            (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps({"duration": 7, "segments": segs}) + ";\n")
            words = """() => [...document.querySelectorAll('[data-screen=v01a] text')].filter(e => e.style.display !== 'none')
                         .map(e => { const r = e.getBoundingClientRect(); return [e.textContent, (r.left + r.right) / 2, (r.top + r.bottom) / 2]; })"""
            with render.Film(film) as f:
                f.seek(2.0); early = f.page.evaluate(words)
                f.seek(5.9); late = f.page.evaluate(words); sizes = f.page.evaluate(longform.MEASURE_TEXT)
                quadNow = f.page.evaluate("""() => { const p = document.querySelector('#scrv01a polygon'), m = p.getScreenCTM();
                    return p.getAttribute('points').split(' ').map(s => s.split(',').map(Number)).map(([x, y]) => [m.a * x + m.c * y + m.e, m.b * x + m.d * y + m.f]); }""")
                frames = []
                for i in range(60):
                    f.seek(2.6 + i / 30)
                    frames.append(np.asarray(Image.open(io.BytesIO(f.page.locator("#stage").screenshot())).convert("RGB").resize((320, 180))))
                warns = f.page.evaluate("window.STUDIO.warnings || []"); labels = f.page.evaluate("window.STUDIO.text.filter(x => x.where === 'screen').map(x => x.text)")
                self.assertFalse(f.errors)
        self.assertNotIn("+$4,860", [w[0] for w in early])                  # line 3 waits for its cue
        self.assertEqual([w[0] for w in late][-1], "+$4,860")
        def inside(x, y):                    # convex quad, clockwise on screen: every edge has the point on its right
            q = quadNow
            return all((q[(i + 1) % 4][0] - q[i][0]) * (y - q[i][1]) - (q[(i + 1) % 4][1] - q[i][1]) * (x - q[i][0]) > 0 for i in range(4))
        self.assertTrue(all(inside(x, y) for _, x, y in late), (late, quadNow))   # every word on the screen
        ys = [y for w, _, y in late if w in ("Units", "owned:", "40")]
        self.assertLess(max(ys) - min(ys), 40)                               # a line stays on its line
        self.assertGreater(min(sz for w, sz, aux in sizes if w in ("Units", "owned:", "Rented:", "Cash:", "+$4,860")), 50)
        self.assertEqual(labels, ["Units owned: 40", "Rented: 31 a week", "Cash: +$4,860"])
        self.assertTrue(any("bad: screen needs a quad" in w for w in warns), warns)
        self.assertEqual(readcheck.flash_frames(frames, 30, 320, 180), [])


class PanelCallouts(unittest.TestCase):
    def test_candidates_and_room_and_flags(self):
        import deadspace, longform
        c = deadspace.candidates('According to the U.S. Census Bureau there are 1,200 porta-potty companies. One rented forty units '
                                 'at $125 a week, and 62% of jobs are events. He said "you will hate February".')
        self.assertEqual([(x["kind"], x.get("value") or x.get("text")) for x in c],
                         [("source", "U.S. Census Bureau"), ("stat", "1,200 porta-potty companies"), ("stat", "forty units"),
                          ("stat", "$125 a week"), ("bar", "62%"), ("note", "“you will hate February”")])
        seg = {"counter": {"value": "$125 a week"}, "data": {"screen": {"lines": ["Units: 1200"]}}}
        self.assertTrue(deadspace.covered({"value": "$125 a week"}, seg))
        self.assertTrue(deadspace.covered({"value": "1,200 porta-potty companies"}, seg))   # the number is on the screen
        self.assertFalse(deadspace.covered({"value": "62%"}, seg))
        import numpy as np
        ok = np.zeros((6, 8), bool); ok[1:4, 2:7] = True; ok[5, :] = True
        self.assertEqual(deadspace.max_rect(ok), (15, 1, 2, 3, 5))
        row = {"id": "b1", "t0": 0, "opt_out": None, "region": {"pos": [0.7, 0.3], "box": [0.5, 0.1, 0.4, 0.4], "px": [700, 400]},
               "candidates": [dict(c[4], shown=False)]}
        self.assertEqual(len(longform.deadspace_flags([row])), 1)
        self.assertEqual(longform.deadspace_flags([dict(row, opt_out="the empty sky is the point")]), [])
        self.assertEqual(longform.deadspace_flags([dict(row, region=dict(row["region"], px=[300, 120]))]), [])

    @unittest.skipUnless(os.environ.get("MS_CHROMIUM"), "needs a browser (MS_CHROMIUM)")
    def test_cards_go_in_quiet_room_ride_the_camera_and_read(self):
        import io, longform, render, readcheck, deadspace
        from PIL import Image, ImageDraw
        with tempfile.TemporaryDirectory() as d:
            film = Path(d); (film / "panels").mkdir()
            rng = np.random.default_rng(3)
            a = (np.full((1080, 1920, 3), (226, 214, 190)) + rng.normal(0, 5, (1080, 1920, 1))).clip(0, 255).astype("uint8")
            im = Image.fromarray(a); dr = ImageDraw.Draw(im)
            for i in range(48):                                  # dense ink over the left third (the subject)
                dr.line((i * 12, 0, i * 12 + 50, 1080), fill=(40, 30, 20), width=4)
            dr.rectangle((1560, 820, 1760, 1000), outline=(60, 50, 40), width=8)   # an object in the quiet part
            im.save(film / "panels" / "yard.png")
            busy = Image.new("RGB", (1920, 1080)); bd = ImageDraw.Draw(busy)
            for i in range(0, 1920, 18):
                bd.line((i, 0, i + 40, 1080), fill=(255, 255, 255), width=3)
            busy.save(film / "panels" / "busy.png")
            q = deadspace.quiet_map(film / "panels" / "yard.png")
            calls = [{"kind": "stat", "value": "+$4,860", "text": "cash by month 3", "t": 1.0},
                     {"kind": "source", "text": "U.S. Census Bureau", "value": "2022", "t": 1.6},
                     {"kind": "bar", "value": "62%", "text": "of jobs are events", "t": 2.2},
                     {"kind": "icon", "icon": "truck", "text": "one truck", "t": 2.6, "hold": 2.0}]
            segs = [{"id": "y1", "type": "panel", "t0": 0, "t1": 7, "counter": {"value": "$4,860", "label": "CASH", "t": 0.5},
                     "data": {"image": "panels/yard.png", "focus": [0.3, 0.5], "camera": "pan-right", "callouts": calls, "quiet": q}},
                    {"id": "y2", "type": "panel", "t0": 7, "t1": 9, "data": {"image": "panels/busy.png", "quiet": deadspace.quiet_map(film / "panels" / "busy.png"),
                     "callouts": [{"kind": "note", "text": "a long note that has nowhere quiet to go in this picture", "t": 7.3}]}}]
            shutil.copy(longform.TEMPLATE, film / "film.html")
            for f_ in longform.ENGINE_FILES:
                shutil.copy(longform.HERE / f_, film / f_)
            (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps({"duration": 9, "segments": segs}) + ";\n")
            boxes = """() => [...document.querySelectorAll('[data-callout=y1]')].filter(e => e.style.display !== 'none')
                         .map(e => { const r = e.getBoundingClientRect(); return [e.querySelector('text:not([data-aux])').textContent, r.left, r.top, r.right, r.bottom]; })"""
            hud = """() => { const r = [...document.querySelectorAll('#hud rect')].filter(e => e.getBoundingClientRect().width > 0).map(e => e.getBoundingClientRect());
                         return r.length ? [Math.min(...r.map(b => b.left)), Math.min(...r.map(b => b.top)), Math.max(...r.map(b => b.right)), Math.max(...r.map(b => b.bottom))] : null; }"""
            with render.Film(film) as f:
                f.seek(0.9); early = f.page.evaluate(boxes)
                f.seek(3.0); mid = f.page.evaluate(boxes); hudbox = f.page.evaluate(hud)
                sizes = f.page.evaluate(longform.MEASURE_TEXT); over = f.page.evaluate(longform.OVERLAP)
                f.seek(6.5); late = f.page.evaluate(boxes)
                placed = f.page.evaluate("window.STUDIO.callouts")
                want = f.page.evaluate("""() => window.STUDIO.callouts.filter(c => c.seg === 'y1').map(c => [3.0, 6.5].map(t => {
                    const cam = window.STUDIO.panelCam.y1.at(t); return [(c.pos[0] * 1920 - cam.cx) * cam.z + 960, (c.pos[1] * 1080 - cam.cy) * cam.z + 540]; }))""")
                warns = f.page.evaluate("window.STUDIO.warnings || []")
                labels = f.page.evaluate("window.STUDIO.text.filter(x => x.where === 'callout').map(x => x.text)")
                frames = []
                for i in range(60):
                    f.seek(0.8 + i / 30)
                    frames.append(np.asarray(Image.open(io.BytesIO(f.page.locator("#stage").screenshot())).convert("RGB").resize((320, 180))))
                self.assertFalse(f.errors)
        self.assertEqual(early, [])                                           # nothing before its cue
        self.assertEqual([m[0] for m in mid], ["+$4,860", "U.S. Census Bureau ·", "62%", "one truck"])   # the source wraps
        self.assertEqual([m[0] for m in late], ["+$4,860", "U.S. Census Bureau ·", "62%"])   # the icon's hold ended
        y1 = [c for c in placed if c["seg"] == "y1"]
        self.assertTrue(all(c["busy"] == 0 and c["pos"][0] > 0.35 for c in y1), y1)   # in the quiet part, off the object
        def hit(a, b):
            return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]
        self.assertTrue(hudbox)
        self.assertFalse([m for m in mid if hit(m[1:], hudbox)], (mid, hudbox))            # clear of the counter card
        self.assertFalse([(a, b) for i, a in enumerate(mid) for b in mid[i + 1:] if hit(a[1:], b[1:])])   # clear of each other
        for (name, *box), (at3, at65), box65 in zip(mid, want, late + [None]):
            cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            self.assertLess(abs(cx - at3[0]) + abs(cy - at3[1]), 12, name)                # where the camera puts its anchor
            if box65:
                self.assertGreater(abs((box65[1] + box65[3]) / 2 - cx), 5, name)          # and it rode the pan
        self.assertGreater(min(px for w, px, aux in sizes if w in ("+$4,860", "cash by month 3", "62%", "one truck") and not aux), 50)
        self.assertFalse([o for o in over if o[0] in ("+$4,860", "cash by month 3", "62%", "one truck", "U.S. Census Bureau ·", "2022")], over)
        self.assertIn("cash by month 3", labels); self.assertIn("2022", labels)
        self.assertTrue(any("y2:" in w and "no quiet region big enough" in w for w in warns), warns)
        self.assertEqual(readcheck.flash_frames(frames, 30, 320, 180), [])


class ChartMarksAndHold(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("MS_CHROMIUM"), "needs a browser (MS_CHROMIUM)")
    def test_marks_pop_when_drawn_past_and_a_drawn_chart_holds_without_a_blink(self):
        import io, longform, render, readcheck
        from PIL import Image
        yrs = list(range(0, 61, 6))
        ys = {"Baseline": [-25 + 1.1 * x for x in yrs], "Dense town": [-25 + 2.2 * x for x in yrs], "Heavy competition": [-25 + 0.6 * x for x in yrs]}
        series = [{"label": k, "points": [[x, round(y * 1000)] for x, y in zip(yrs, v)], "t": 0.5 + 0.5 * i} for i, (k, v) in enumerate(ys.items())]
        marks = [{"series": "Baseline", "x": 23, "text": "paid back ~2 yrs"}, {"series": 1, "x": 11, "text": "paid back ~1 yr"},
                 {"series": "Heavy competition", "x": 42, "text": "paid back 3.5 yrs"}]
        base = {"title": "Five years, three ways", "series": series, "x": {"label": "Months"}, "y": {"label": "Cash", "format": "money"}}
        segs = [{"id": "c1", "type": "chart", "t0": 0.0, "t1": 5.0, "counter": None, "data": dict(base, marks=marks)},
                {"id": "c2", "type": "chart", "t0": 5.0, "t1": 9.0, "counter": None, "data": dict(base, marks=marks, drawn=True)}]
        with tempfile.TemporaryDirectory() as d:
            film = Path(d)
            shutil.copy(longform.TEMPLATE, film / "film.html")
            for f_ in longform.ENGINE_FILES:
                shutil.copy(longform.HERE / f_, film / f_)
            (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps({"duration": 9.0, "segments": segs}) + ";\n")
            plates = """(id) => { const st = document.querySelector('#stage').getBoundingClientRect();
              return [...document.querySelectorAll(`[data-seg=${id}] rect[rx="6"]`)].filter(r => +(r.getAttribute('opacity') ?? 1) > 0.5)
                .map(r => { const b = r.getBoundingClientRect(); return [b.left - st.left, b.top - st.top, b.right - st.left, b.bottom - st.top]; }); }"""
            marksOn = """(id) => [...document.querySelectorAll(`[data-seg=${id}] text`)].filter(e => /paid back/.test(e.textContent) && +e.getAttribute('opacity') > 0.5).map(e => e.textContent)"""
            zoom = "(id) => { const m = document.querySelector(`#scenes > [data-seg=${id}]`).getCTM(); return m.a; }"
            with render.Film(film) as f:
                f.seek(1.0); early = f.page.evaluate(marksOn, "c1")
                f.seek(4.9); full = f.page.evaluate(marksOn, "c1"); boxes = f.page.evaluate(plates, "c1"); sizes = f.page.evaluate(longform.MEASURE_TEXT)
                z1 = f.page.evaluate(zoom, "c1")
                f.seek(5.03); held = f.page.evaluate(marksOn, "c2"); z2 = f.page.evaluate(zoom, "c2")
                lines2 = f.page.evaluate("[...document.querySelectorAll('[data-seg=c2] path[stroke-linecap=round]')].map(p => [+p.getAttribute('opacity'), +p.getAttribute('stroke-dashoffset')])")
                frames = []
                for i in range(30):
                    f.seek(4.6 + i / 30)
                    frames.append(np.asarray(Image.open(io.BytesIO(f.page.locator("#stage").screenshot())).convert("RGB").resize((320, 180))))
                warns = f.page.evaluate("window.STUDIO.warnings || []")
                self.assertFalse(f.errors)                                    # two "counter": null in a row no longer throw
        self.assertEqual(early, [])                                           # no line has drawn past its mark yet
        self.assertEqual(sorted(full), sorted(m["text"] for m in marks))
        self.assertEqual(sorted(held), sorted(full))                         # the held chart starts with its marks
        self.assertTrue(all(o == 1 and off < 1 for o, off in lines2), lines2)   # and its lines already drawn
        self.assertLess(abs(z2 - z1), 0.004)                                  # one push across both: no zoom jump at the join
        self.assertEqual(len(boxes), 6)                                       # three end labels, three marks
        for i, a in enumerate(boxes):
            self.assertTrue(a[0] >= 0 and a[1] >= 0 and a[2] <= 1920 and a[3] <= 1080, a)
            for b in boxes[i + 1:]:
                self.assertFalse(a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3], (a, b))
        self.assertGreater(min(px for w, px, aux in sizes if "paid" in w or "back" in w), 50)
        self.assertEqual(warns, [])
        self.assertEqual(readcheck.flash_frames(frames, 30, 320, 180), [])


class ChartScene(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("MS_CHROMIUM"), "needs a browser (MS_CHROMIUM)")
    def test_lines_draw_on_cue_labels_fit_and_no_flash(self):
        import io, longform, render
        from PIL import Image
        yrs = [0, 1, 2, 3, 4, 5]
        ys = {"Baseline": [-25e3, -12e3, 4e3, 22e3, 41e3, 62e3], "Dense town": [-25e3, -6e3, 21e3, 55e3, 96e3, 140e3],
              "Heavy competition": [-25e3, -18e3, -9e3, 1e3, 9e3, 16e3], "More on ads": [-32e3, -15e3, 8e3, 36e3, 70e3, 108e3]}
        series = [{"label": k, "points": [list(p) for p in zip(yrs, v)], "t": 0.8 + 1.8 * i} for i, (k, v) in enumerate(ys.items())]
        seg = {"id": "c", "type": "chart", "t0": 0.0, "t1": 10.0, "data": {"title": "Five years, four ways it can go", "series": series,
               "x": {"label": "Years in business", "ticks": [[0, "Start"]] + [[k, f"Yr {k}"] for k in range(1, 6)]},
               "y": {"label": "Cash position", "format": "money"}, "zero_line": True, "note": "our model, illustrative"}}
        with tempfile.TemporaryDirectory() as d:
            film = Path(d)
            shutil.copy(longform.TEMPLATE, film / "film.html")
            for f in longform.ENGINE_FILES:
                shutil.copy(longform.HERE / f, film / f)
            (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps({"duration": 10.0, "segments": [seg]}) + ";\n")
            with render.Film(film) as f:
                f.seek(1.0)
                drawn = f.page.evaluate("[...document.querySelectorAll('path[stroke-linecap=round]')].map(p => +p.getAttribute('opacity'))")
                self.assertEqual(drawn, [1, 0, 0, 0])                       # only the first line has reached its cue
                frames = []
                for i in range(60):
                    f.seek(i / 30)
                    frames.append(np.asarray(Image.open(io.BytesIO(f.page.locator("#stage").screenshot())).convert("RGB").resize((320, 180))))
                f.seek(9.99)
                boxes = f.page.evaluate("""() => { const st = document.querySelector('#stage').getBoundingClientRect();
                  return [...document.querySelectorAll('rect[rx="6"]')].map(r => { const b = r.getBoundingClientRect();
                    return [b.left - st.left, b.top - st.top, b.right - st.left, b.bottom - st.top, st.width, st.height]; }); }""")
                ticks = f.page.evaluate("[...document.querySelectorAll('text')].filter(x => /\\$/.test(x.textContent)).map(x => [x.textContent, x.getAttribute('fill')])")
                self.assertFalse(f.errors)
        self.assertEqual(readcheck.flash_frames(frames, 30, 320, 180), [])
        self.assertEqual(len(boxes), 4)
        for b in boxes:                                                       # every label plate inside the frame at the deepest zoom
            self.assertTrue(b[0] >= 0 and b[1] >= 0 and b[2] <= b[4] and b[3] <= b[5], b)
        for i, a in enumerate(boxes):                                         # and no two plates touch
            for b2 in boxes[i + 1:]:
                self.assertFalse(a[0] < b2[2] and b2[0] < a[2] and a[1] < b2[3] and b2[1] < a[3])
        cols = dict(ticks)
        self.assertNotEqual(cols["−$50k"], cols["$100k"])                    # red below zero, green above


class StatScene(unittest.TestCase):
    """A stat scene's count-up steps on purpose and its long hold drifts (Survive Year 1, cut 4, 2026-11-25)."""
    SEG = {"id": "s", "type": "stat", "t0": 0.0, "t1": 7.5, "data": {"prefix": "$", "value": 4.3, "decimals": 1, "suffix": "M",
                                                                     "label": "a month to rent one unit", "t": 1.0}}

    def test_ticks(self):
        import longform
        ticks = sorted(longform.stat_ticks([self.SEG]))
        self.assertTrue(28 <= ticks[0] <= 31 and ticks[-1] <= 30 + 2.0 * 30 + 1)
        self.assertGreater(ticks[-1] - ticks[-2], 3)   # the eased tail holds, then steps: the steps final allows

    @unittest.skipUnless(os.environ.get("MS_CHROMIUM"), "needs a browser (MS_CHROMIUM)")
    def test_ticks_match_the_page_and_drift_clears_the_floor(self):
        import json, longform, render
        for drift, want in ((False, False), (True, True)):
            with tempfile.TemporaryDirectory() as d:
                film = Path(d)
                shutil.copy(longform.TEMPLATE, film / "film.html")
                for f in longform.ENGINE_FILES:
                    shutil.copy(longform.HERE / f, film / f)
                (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps(
                    {"duration": 7.5, "segments": [self.SEG], "grade": {"vignette": 0.35}, "stat_drift": drift}) + ";\n")
                est = longform.stat_motion(film, [self.SEG])
                _, low = longform.motion_flags(est, floor=0.2)
                self.assertEqual(not low, want, (drift, est))
                if drift:                   # the frames final allows are exactly the frames where the page's number changes
                    shown, changed = None, set()
                    with render.Film(film) as f:
                        for k in range(20, 100):
                            f.seek(k / 30)
                            v = f.page.evaluate("[...document.querySelectorAll('text')].map(x => x.textContent).find(x => x.startsWith('$'))")
                            if shown is not None and v != shown:
                                changed.add(k)
                            shown = v
                        self.assertFalse(f.errors)
                    self.assertEqual(changed, {k for k in longform.stat_ticks([self.SEG]) if 21 <= k < 100})


    @unittest.skipUnless(os.environ.get("MS_CHROMIUM"), "needs a browser (MS_CHROMIUM)")
    def test_long_hold_keeps_its_speed_and_stays_clear_of_the_hud(self):
        # an 18 s stat with drift on measured 0.03-0.17 (its speed fell with the hold: travel was capped at 12% of the
        # frame); segment-level "drift" was not copied into the timeline (Survive Year 1, 2026-11-27)
        import json, longform, render
        seg = {"id": "v35f", "type": "stat", "t0": 0.0, "t1": 18.0, "drift": 14, "counter": {"value": "$4,860", "label": "CASH", "t": 0.2},
               "data": {"prefix": "$", "value": 4860, "label": "cash after the first season", "t": 0.5}}
        with tempfile.TemporaryDirectory() as d:
            film = Path(d)
            shutil.copy(longform.TEMPLATE, film / "film.html")
            for f in longform.ENGINE_FILES:
                shutil.copy(longform.HERE / f, film / f)
            (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps({"duration": 18.0, "segments": [seg], "grade": {"vignette": 0.35}}) + ";\n")
            est = longform.stat_motion(film, [seg])
            with render.Film(film) as f:
                f.seek(17.9)
                hits = f.page.evaluate(longform.OVERLAP) + f.page.evaluate(longform.EDGE)
                self.assertFalse(f.errors)
        _, low = longform.motion_flags(est, floor=0.2)
        self.assertEqual(low, [], est)
        self.assertEqual(hits, [])
        self.assertFalse(longform.STAT_STILL["v35f stat"])
        px, secs = longform.STAT_TRAVEL["v35f stat"]
        self.assertGreater(px / secs, 10, (px, secs))      # the page holds its speed over a long hold
        # a number cued to a late word: the scene drifts from its own start, not from the count (an 18 s stat cued 15 s in
        # measured 0.037 whatever its drift, Survive Year 1, 2026-11-27)
        late = dict(seg, drift=24, data=dict(seg["data"], t=15.0))
        late.pop("counter")
        with tempfile.TemporaryDirectory() as d:
            film = Path(d)
            shutil.copy(longform.TEMPLATE, film / "film.html")
            for f in longform.ENGINE_FILES:
                shutil.copy(longform.HERE / f, film / f)
            (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps({"duration": 18.0, "segments": [late],
                "palette": {"BG": "#E6DAC3", "INK": "#1D1C20", "GREY": "#4a4740", "ACCENT": "#9A6614"}}) + ";\n")
            est = longform.stat_motion(film, [late])
        self.assertEqual(longform.motion_flags(est, floor=0.2)[1], [], est)
        # a film whose own film.html predates stat drift: check must say so rather than tell it to raise "drift"
        with tempfile.TemporaryDirectory() as d:
            film = Path(d)
            html = longform.TEMPLATE.read_text()
            old = html.replace("g.__cam = dx || dy ? { cx: W / 2 - dx, cy: H / 2 - dy, z: 1, frac: 1 } : null;", "g.__cam = null;")
            self.assertNotEqual(old, html)
            (film / "film.html").write_text(old)
            for f in longform.ENGINE_FILES:
                shutil.copy(longform.HERE / f, film / f)
            (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps({"duration": 18.0, "segments": [seg]}) + ";\n")
            longform.stat_motion(film, [seg])
        self.assertTrue(longform.STAT_STILL["v35f stat"])


class PaletteAndFades(unittest.TestCase):
    """A pale accent on a warm ground is caught when the timeline is written, and the contrast sampler leaves out moments
    when text is mid-fade (Survive Year 1, 2026-11-27: stat numbers at 2.0:1, chart labels flagged on their exit)."""
    def test_palette_lint(self):
        import longform
        w = longform.palette_warnings({"BG": "#E6DAC3", "INK": "#2a231c", "ACCENT": "#C8922E", "GREY": "#6b6257"})
        self.assertEqual(len(w), 1, w)
        self.assertIn("ACCENT", w[0])
        fix = w[0].split("; ")[-1].split()[0]
        self.assertGreaterEqual(longform._wcag(longform._hex_rgb(fix), longform._hex_rgb("#E6DAC3")), 3.5)
        self.assertEqual(longform.palette_warnings({"BG": "#E6DAC3", "INK": "#2a231c", "ACCENT": "#9A6614", "GREY": "#6b6257"}), [])
        # a light ground that leaves GREY to the house value (made for a dark ground) is caught too
        self.assertIn("GREY", " ".join(longform.palette_warnings({"BG": "#E6DAC3", "INK": "#2a231c", "ACCENT": "#9A6614"})))
        self.assertEqual(longform.palette_warnings({}), [])                   # the house colours pass
        self.assertEqual(longform.palette_warnings({"BG": "url(#paper)"}), [])  # not a plain colour: not judged

    @unittest.skipUnless(os.environ.get("MS_CHROMIUM"), "needs a browser (MS_CHROMIUM)")
    def test_sampler_skips_fade_frames(self):
        import json, longform
        seg = {"id": "s", "type": "statement", "t0": 0.0, "t1": 4.0, "data": {"lines": ["Forty units a week"]}}
        for fade, want in ((0.4, None), (1.0, None)):
            with tempfile.TemporaryDirectory() as d:
                film = Path(d)
                html = longform.TEMPLATE.read_text()
                # the scene's group fades to `fade` from 3 s, as a chart label or a map tag does on its way out
                hook = ("<script>{const S = window.seek; window.seek = t => { S(t); "
                        "document.querySelector('#scenes > [data-seg=s]').setAttribute('opacity', t > 3 ? %s : 1); };}</script>" % fade)
                (film / "film.html").write_text(html.replace("</body>", hook + "</body>"))
                for f in longform.ENGINE_FILES:
                    shutil.copy(longform.HERE / f, film / f)
                (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps({"duration": 4.0, "segments": [seg]}) + ";\n")
                sheet = longform.text_contrast(film, [{"seg": "s", "lines": ["Forty units a week"], "t": 1.5, "hold": 2.4}], 3.0)
                b = sheet[0]
                if fade < 1:
                    self.assertEqual(b["faded_skipped"], 1, b)
                    self.assertLess(b["contrast_t"], 3.0, b)
                else:
                    self.assertEqual(b["faded_skipped"], 0, b)
                self.assertGreater(b["contrast"], 3.0, b)
                self.assertAlmostEqual(b["readable"], 2.4, places=3)
        # never fully opaque: still measured, at its most opaque moment
        with tempfile.TemporaryDirectory() as d:
            film = Path(d)
            hook = ("<script>{const S = window.seek; window.seek = t => { S(t); "
                    "document.querySelector('#scenes > [data-seg=s]').setAttribute('opacity', 0.2); };}</script>")
            (film / "film.html").write_text(longform.TEMPLATE.read_text().replace("</body>", hook + "</body>"))
            for f in longform.ENGINE_FILES:
                shutil.copy(longform.HERE / f, film / f)
            (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps({"duration": 4.0, "segments": [seg]}) + ";\n")
            b = longform.text_contrast(film, [{"seg": "s", "lines": ["Forty units a week"], "t": 1.5, "hold": 2.4}], 3.0)[0]
            self.assertIsNotNone(b["contrast"])
            self.assertLess(b["contrast"], 3.0, b)


class IntroRules(unittest.TestCase):
    """The intro runs 45 s at most and its first 5 s name the subject with the sound off (owner's intro rules, 2026-11-27)."""
    def film(self, d, segs, chapters, sc):
        import json, longform
        from PIL import Image
        film = Path(d); (film / "panels").mkdir()
        Image.new("RGB", (1920, 1080), (120, 140, 160)).save(film / "panels" / "yard.png")
        shutil.copy(longform.TEMPLATE, film / "film.html")
        for f in longform.ENGINE_FILES:
            shutil.copy(longform.HERE / f, film / f)
        dur = segs[-1]["t1"]
        tl = {"duration": dur, "segments": segs, "chapters": chapters, "format": "16x9"}
        (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps(tl) + ";\n")
        (film / "script.json").write_text(json.dumps(dict(sc, title="t", segments=[{"id": x["id"]} for x in segs])))
        return film, tl

    def test_span(self):
        import longform
        segs = [{"id": "a", "t0": 0, "t1": 30}, {"id": "b", "t0": 30, "t1": 50, "intro": True}]
        self.assertEqual(longform.intro_span(segs, [])[:2], (30, 50))
        self.assertEqual(longform.intro_span(segs[:1], [{"title": "Cold open", "t0": 0, "t1": 30}])[:2], (0, 30))
        self.assertIsNone(longform.intro_span(segs[:1], [{"title": "All of it", "t0": 0, "t1": 30}]))

    @unittest.skipUnless(os.environ.get("MS_CHROMIUM"), "needs a browser (MS_CHROMIUM)")
    def test_length_and_mute_test(self):
        import longform
        chapters = [{"title": "Intro", "t0": 0, "t1": 50}, {"title": "Year one", "t0": 50, "t1": 60}]
        words = [{"id": "w", "type": "statement", "t0": 0, "t1": 50, "data": {"text": "Nobody tells you this"}},
                 {"id": "z", "type": "statement", "t0": 50, "t1": 60, "data": {"text": "Next"}}]
        with tempfile.TemporaryDirectory() as d:
            film, tl = self.film(d, words, chapters, {"subject": ["porta-potty", "portable toilet"]})
            _, fl = longform.intro_flags(film, tl, longform.load_script(film))
            self.assertTrue((film / "review" / "mute-0-5s.jpg").exists())
        joined = " | ".join(fl)
        self.assertIn("intro runs 50.0 s", joined)
        self.assertIn("words on a background", joined)
        self.assertIn("names the subject", joined)
        # a picture that says what it shows, in a 40 s intro: clean
        pic = [{"id": "p", "type": "panel", "t0": 0, "t1": 40, "shows": "a row of blue porta potties at a fair",
                "data": {"image": "panels/yard.png", "camera": "slow-push"}},
               {"id": "z", "type": "statement", "t0": 40, "t1": 50, "data": {"text": "Next"}}]
        with tempfile.TemporaryDirectory() as d:
            film, tl = self.film(d, pic, [{"title": "Intro", "t0": 0, "t1": 40}, {"title": "Year one", "t0": 40, "t1": 50}],
                                 {"subject": ["porta-potty"]})
            r, fl = longform.intro_flags(film, tl, longform.load_script(film))
            self.assertEqual(fl, [], r)
            # no subject set: say so rather than pass
            (film / "script.json").write_text('{"title": "t", "segments": []}')
            _, fl = longform.intro_flags(film, tl, longform.load_script(film))
            self.assertIn("no \"subject\"", " ".join(fl))


class WordTiming(unittest.TestCase):
    """Whisper word times matched to the script, and estimated times failing check (owner's out-of-sync notes, 2026-11-27)."""
    def test_match_words(self):
        import align
        toks = align.tokens("We made $4,860 in the first season, mostly from forty porta-potties.")
        heard = [("We", 0, .2), (" made", .2, .5), (" $4,860", .5, 1.6), (" in", 1.6, 1.7), (" the", 1.7, 1.8),
                 (" first", 1.8, 2.1), (" season,", 2.1, 2.6), (" mostly", 2.8, 3.2), (" from", 3.2, 3.4),
                 (" 40", 3.4, 3.8), (" porta", 3.8, 4.1), (" potties.", 4.1, 4.6)]
        ws = align.match_words(toks, heard, 5.0)
        self.assertEqual([w["w"] for w in ws], [t["w"] for t in toks])          # the script's words, Whisper's times
        self.assertEqual((ws[2]["t0"], ws[2]["t1"]), (0.5, 1.6))
        self.assertEqual((ws[9]["t0"], ws[9]["t1"]), (3.4, 3.8))
        # a word Whisper misheard shares the gap between its neighbours
        miss = [h if h[0] != " mostly" else (" Most", 2.8, 3.2) for h in heard]
        ws = align.match_words(toks, miss, 5.0)
        self.assertTrue(2.6 <= ws[7]["t0"] < ws[7]["t1"] <= 3.2, ws[7])
        with self.assertRaises(RuntimeError):                                   # the wrong clip
            align.match_words(toks, [("Something", 0, 1), (" else", 1, 2)], 2.0)

    def test_check_flags_spread(self):
        import json, longform
        with tempfile.TemporaryDirectory() as d:
            film = Path(d); (film / "narration").mkdir()
            sc = {"segments": [{"id": "a", "narration": "One."}, {"id": "b", "narration": "Two."}, {"id": "c"}]}
            (film / "narration" / "words.json").write_text(json.dumps({"a": {"method": "whisper"}, "b": {"method": "spread"}}))
            line, fl = longform.alignment_flags(film, sc)
            self.assertIn("spread 1", line); self.assertIn("whisper 1", line)
            self.assertEqual(len(fl), 1); self.assertIn("(b)", fl[0])
            (film / "narration" / "words.json").write_text(json.dumps({"a": {"method": "whisper"}, "b": {"method": "align"}}))
            self.assertEqual(longform.alignment_flags(film, sc)[1], [])


class CounterHero(unittest.TestCase):
    """A counter change lands big at the centre, then flies into the card (owner's note on the cash card, 2026-11-27)."""
    SEGS = [{"id": "a", "type": "statement", "chapter": "One", "t0": 0, "t1": 3, "data": {"text": "The first season"}, "counter": {"value": "$1,000", "label": "CASH", "t": 0.3}},
            {"id": "b", "type": "statement", "chapter": "One", "t0": 3, "t1": 8, "data": {"text": "Then the fair paid out"}, "counter": {"value": "$2,073", "t": 3.5}},
            {"id": "c", "type": "statement", "chapter": "One", "t0": 8, "t1": 11, "data": {"text": "And a repair"}, "counter": {"value": "$1,500", "t": 8.5}}]

    def where(self, segs):
        import io, json, longform, render
        import numpy as np
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            film = Path(d)
            shutil.copy(longform.TEMPLATE, film / "film.html")
            for f in longform.ENGINE_FILES:
                shutil.copy(longform.HERE / f, film / f)
            (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps({"duration": 11, "segments": segs, "chapters": [{"title": "One", "t0": 0, "t1": 11}]}) + ";\n")
            pos = """(v) => [...document.querySelectorAll('#hud text')].filter(e => e.textContent === v && getComputedStyle(e.parentNode).display !== 'none'
                       && +(e.parentNode.getAttribute('opacity') || 1) > 0.5).map(e => { const r = e.getBoundingClientRect(); return [r.left, r.top, r.width]; })"""
            out, bad = {}, []
            with render.Film(film) as f:
                for t, v in ((4.5, "$2,073"), (6.5, "$2,073"), (9.5, "$1,500")):
                    f.seek(t); out[t] = f.page.evaluate(pos, v)
                for k in range(90, 240, 3):
                    f.seek(k / 30)
                    h = f.page.evaluate(longform.OVERLAP) + f.page.evaluate(longform.EDGE)
                    if h: bad.append((k / 30, h[0]))
                g = lambda: np.asarray(Image.open(io.BytesIO(f.page.screenshot())).convert("L"))
                f.seek(5.3); a = g(); f.seek(9.0); f.seek(5.3); b = g()
                self.assertFalse(f.errors)
            return out, bad, bool((a != b).any())

    @unittest.skipUnless(os.environ.get("MS_CHROMIUM"), "needs a browser (MS_CHROMIUM)")
    def test_hero(self):
        out, bad, differs = self.where(self.SEGS)
        self.assertEqual(bad, [])
        self.assertFalse(differs)                       # a pure function of t: the same frame however it is reached
        (x, y, w), = out[4.5]
        self.assertTrue(700 < x + w / 2 < 1220 and 250 < y < 600 and w > 300, out[4.5])   # big, near the centre
        (x, y, w), = out[6.5]
        self.assertTrue(x > 1500 and y < 200, out[6.5])  # landed in the corner card
        (x, y, w), = out[9.5]
        self.assertTrue(x > 1500 and y < 200, out[9.5])  # a second change in the chapter goes straight to the card
        segs = [dict(s) for s in self.SEGS]
        segs[1] = dict(segs[1], counter=dict(segs[1]["counter"], hero=False))
        out, _, _ = self.where(segs)
        (x, y, w), = out[4.5]
        self.assertTrue(x > 1500 and y < 200, out[4.5])  # "hero": false keeps it in the corner

    @unittest.skipUnless(os.environ.get("MS_CHROMIUM"), "needs a browser (MS_CHROMIUM)")
    def test_hero_clears_callouts_and_signs(self):
        """v31a: a hero landed on a callout and check's overlap test never sampled it. v39a: counting from −$25,000 up to
        "+$30,410" drew "−+$12,000"."""
        import json, longform, render
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            film = Path(d); (film / "panels").mkdir()
            Image.new("RGB", (1920, 1080), (200, 190, 170)).save(film / "panels" / "yard.png")
            segs = [{"id": "a", "type": "statement", "chapter": "One", "t0": 0, "t1": 3, "data": {"text": "The first season"}, "counter": {"value": "−$25,000", "label": "CASH", "t": 0.3}},
                    {"id": "b", "type": "panel", "chapter": "Two", "t0": 3, "t1": 9, "counter": {"value": "+$30,410", "t": 4.0},
                     "data": {"image": "panels/yard.png", "callouts": [{"kind": "stat", "value": "$1,000/mo", "text": "rent", "t": 3.2, "pos": [0.5, 0.42]}]}}]
            shutil.copy(longform.TEMPLATE, film / "film.html")
            for f_ in longform.ENGINE_FILES:
                shutil.copy(longform.HERE / f_, film / f_)
            (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps({"duration": 9, "segments": segs, "chapters": [{"title": "One", "t0": 0, "t1": 3}, {"title": "Two", "t0": 3, "t1": 9}]}) + ";\n")
            shown = set()
            with render.Film(film) as f:
                heroes = f.page.evaluate("window.STUDIO.heroes")
                self.assertEqual([h["value"] for h in heroes], ["+$30,410"])
                for t in (4.45, 4.75, 5.2):
                    f.seek(t); self.assertEqual(f.page.evaluate(longform.OVERLAP), [], t)
                for k in range(120, 270):
                    f.seek(k / 30)
                    shown |= set(f.page.evaluate("() => [...document.querySelectorAll('#hud text')].map(e => e.textContent).filter(s => /\\d/.test(s))"))
                self.assertFalse(f.page.evaluate("window.STUDIO.warnings || []"))
                self.assertFalse(f.errors)
            self.assertFalse([v for v in shown if "−+" in v or "++" in v or "+−" in v], shown)
            self.assertTrue(any(v.startswith("−$") for v in shown) and any(v.startswith("+$") and v != "+$30,410" for v in shown))  # counts across zero
            self.assertIn("+$55,410", shown)                   # the change chip
            self.assertEqual(longform.overlaps(film, segs, 0), {})   # check samples the hero's hold, and it is clear


class BlankProps(unittest.TestCase):
    """Blank props (references/styles/cel.md): the still asks for an empty writing area, and the panel letters it with one
    or more screens, cued like any other reveal."""

    def test_write_on_prompt(self):
        import codex_stills
        one = codex_stills.cast_sentence({}, {"write_on": ["the price tag"]})
        self.assertIn("The price tag has a clean, empty writing area", one)
        self.assertNotIn("blank", one.lower())                     # rule 14: say what it has
        two = codex_stills.cast_sentence({"cast": {"Owner": "a man in an ochre cap"}}, {"write_on": ["the tag", "the receipt"]})
        self.assertIn("Owner: a man in an ochre cap", two); self.assertIn("The tag and the receipt each have", two)
        self.assertEqual(codex_stills.cast_sentence({}, {}), "")

    def test_screen_lines_are_reveals(self):
        import pace
        seg = {"id": "p", "type": "panel", "data": {"screen": [{"quad": [[0, 0], [1, 0], [1, 1], [0, 1]], "lines": ["RENT", {"text": "$950/mo", "at": "nine"}]},
                                                             {"quad": [[0, 0], [1, 0], [1, 1], [0, 1]], "lines": [{"text": "$1,200"}]}]}}
        paths = [p for p, _, _ in pace.sites(seg)]
        self.assertEqual(paths, [("screen", 0, "lines", 0), ("screen", 0, "lines", 1), ("screen", 1, "lines", 0)])
        self.assertEqual(pace.label(paths[2]), "screen[1].lines[0]")
        self.assertTrue(pace.set_at(seg["data"], list(paths[0]), "rent"))      # a plain string line becomes {text, at}
        self.assertEqual(seg["data"]["screen"][0]["lines"][0], {"text": "RENT", "at": "rent"})
        self.assertFalse(pace.set_at(seg["data"], paths[1], "x"))              # already cued
        one = {"id": "q", "data": {"screen": {"quad": [], "lines": ["A"]}}}
        self.assertEqual([p for p, _, _ in pace.sites(one)], [("screen", "lines", 0)])

    @unittest.skipUnless(os.environ.get("MS_CHROMIUM"), "needs a browser (MS_CHROMIUM)")
    def test_two_screens_in_a_house_face(self):
        import io, json, longform, mapart, render
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            film = Path(d); (film / "panels").mkdir()
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    mapart.cmd_fonts(film)
            except Exception as e:
                self.skipTest(f"fonts not reachable: {e}")
            Image.new("RGB", (1920, 1080), (226, 214, 190)).save(film / "panels" / "yard.png")
            q1, q2 = [[0.1, 0.1], [0.4, 0.12], [0.4, 0.3], [0.1, 0.28]], [[0.6, 0.5], [0.9, 0.5], [0.9, 0.7], [0.6, 0.7]]
            segs = [{"id": "p", "type": "panel", "t0": 0, "t1": 4,
                     "data": {"image": "panels/yard.png", "screen": [{"quad": q1, "bg": False, "ink": "#2a1710", "font": "marker", "lines": [{"text": "$1,200", "t": 0.5}]},
                                                                     {"quad": q2, "bg": False, "font": "elite", "lines": [{"text": "RENT", "t": 1.0}]}]}}]
            shutil.copy(longform.TEMPLATE, film / "film.html")
            for f_ in longform.ENGINE_FILES:
                shutil.copy(longform.HERE / f_, film / f_)
            (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps({"duration": 4, "segments": segs}) + ";\n")
            with render.Film(film) as f:
                f.seek(0.3)
                self.assertEqual(f.page.evaluate("[...document.querySelectorAll('[data-screen] text')].filter(e => getComputedStyle(e).display !== 'none').length"), 0)
                f.seek(2.0)
                got = f.page.evaluate("""[...document.querySelectorAll('[data-screen] text')].filter(e => getComputedStyle(e).display !== 'none')
                                          .map(e => [e.textContent, e.getAttribute('font-family')])""")
                self.assertEqual(sorted(t for t, _ in got), ["$1,200", "RENT"])
                self.assertIn("MS Marker", dict(got)["$1,200"]); self.assertIn("MS Elite", dict(got)["RENT"])
                self.assertTrue(f.page.evaluate("document.fonts.check('40px \\'MS Marker\\'')"))
                self.assertEqual(f.page.evaluate("document.querySelectorAll('[data-screen] polygon').length"), 0)   # bg false: the still shows
                self.assertFalse(f.page.evaluate("window.STUDIO.warnings || []"))
                self.assertFalse(f.errors)


    def test_screen_on_one_frame_and_until(self):
        # Survive Year 1 cel, 2026-11-28: writing on drawing 1 stayed up over drawings 2 and 3
        import longform
        q = [[0.1, 0.1], [0.4, 0.1], [0.4, 0.3], [0.1, 0.3]]
        seg = {"id": "p", "type": "panel", "t0": 0, "t1": 6,
               "data": {"frames": ["a.png", {"image": "b.png", "t": 2.0}, {"image": "c.png", "t": 4.0}],
                        "flips": [[0, 0], [2.0, 1], [4.0, 2]],
                        "screen": [{"quad": q, "frame": 1, "lines": [{"text": "$1,200", "at": "twelve", "t": 1.2}]},
                                   {"quad": q, "frame": 2, "until": {"at": "gone", "t": 5.0}, "lines": [{"text": "RENT", "at": "rent", "t": 4.5}]},
                                   {"quad": q, "lines": ["A"]}]}}
        cues = [{"seg": "p", "at": "twelve", "t": 1.2}, {"seg": "p", "at": "rent", "t": 4.5}]
        self.assertEqual(longform.screen_spans(seg, cues), [])
        a, b, c = seg["data"]["screen"]
        self.assertEqual(a["spans"], [[2.0, 4.0]]); self.assertEqual(b["spans"], [[4.0, 5.0]]); self.assertEqual(c["spans"], [[0, 6]])
        self.assertEqual((a["lines"][0]["t"], a["lines"][0]["t_word"]), (2.0, 1.2))     # said before its frame: lands on the flip
        self.assertEqual(cues[0]["t"], 2.0); self.assertEqual(b["lines"][0]["t"], 4.5)
        bad = {"id": "q", "type": "panel", "t0": 0, "t1": 2, "data": {"image": "a.png", "screen": {"quad": q, "frame": 1, "lines": ["x"]}}}
        self.assertIn("no flipbook", longform.screen_spans(bad, [])[0])
        bad = {"id": "q", "type": "panel", "t0": 0, "t1": 2, "data": {"frames": ["a.png", "b.png"], "flips": [[0, 0], [1, 1]],
                                                                    "screen": {"quad": q, "frame": 5, "lines": ["x"]}}}
        self.assertIn("from 0 to 1", longform.screen_spans(bad, [])[0])

    @unittest.skipUnless(os.environ.get("MS_CHROMIUM"), "needs a browser (MS_CHROMIUM)")
    def test_frame_screen_camera_and_whip_in_the_page(self):
        import json, longform, render
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            film = Path(d); (film / "panels").mkdir()
            for n, c in (("a", (226, 214, 190)), ("b", (200, 190, 170))):
                Image.new("RGB", (1920, 1080), c).save(film / "panels" / f"{n}.png")
            q = [[0.1, 0.1], [0.4, 0.12], [0.4, 0.3], [0.1, 0.28]]
            segs = [{"id": "p", "type": "panel", "t0": 0, "t1": 4,
                     "data": {"image": "panels/a.png", "camera": "hold",
                              "frames": ["panels/a.png", {"image": "panels/b.png", "t": 2.0, "enter": "whip",
                                                          "camera": {"from": {"x": 0.3, "y": 0.4, "z": 1.3}, "to": {"x": 0.32, "y": 0.4, "z": 1.35}}}],
                              "flips": [[0, 0], [2.0, 1]],
                              "screen": [{"quad": q, "bg": False, "frame": 0, "spans": [[0, 2.0]], "lines": [{"text": "$1,200", "t": 0.5}]},
                                         {"quad": q, "bg": False, "frame": 1, "spans": [[2.0, 4]], "lines": [{"text": "RENT", "t": 1.0}]}]}}]
            shutil.copy(longform.TEMPLATE, film / "film.html")
            for f_ in longform.ENGINE_FILES:
                shutil.copy(longform.HERE / f_, film / f_)
            (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps({"duration": 4, "segments": segs}) + ";\n")
            vis = "[...document.querySelectorAll('[data-screen] text')].filter(e => getComputedStyle(e).display !== 'none').map(e => e.textContent)"
            cam = "(() => { const c = window.STUDIO.panelCam.p.at(%s); return [c.z, c.cx, c.blur || 0]; })()"
            with render.Film(film) as f:
                f.seek(1.5); self.assertEqual(f.page.evaluate(vis), ["$1,200"])
                f.seek(2.6); self.assertEqual(f.page.evaluate(vis), ["RENT"])          # gone with its frame; RENT cued early waits for its own
                z0, z1 = f.page.evaluate(cam % "1.0")[0], f.page.evaluate(cam % "3.0")[0]
                self.assertLess(z0, 1.1); self.assertGreater(z1, 1.29)          # the frame's own camera reframes on the cut
                self.assertGreater(f.page.evaluate(cam % "1.95")[2], 0.5)                # mid-whip: blurred
                self.assertEqual(f.page.evaluate(cam % "2.5")[2], 0)
                f.seek(1.95); self.assertTrue(f.page.evaluate("!!document.querySelector('[filter^=\"url(#pw\"]')"))
                f.seek(2.5); self.assertFalse(f.page.evaluate("!!document.querySelector('[filter^=\"url(#pw\"]')"))
                self.assertFalse(f.page.evaluate("window.STUDIO.warnings || []")); self.assertFalse(f.errors)


    def test_spoken_numbers_as_words_times_and_compounds(self):
        # Survive Year 1 cel, 2026-11-28: four inked numbers counted as missing ("1 of 6")
        import numbersync as ns
        W = lambda t: [[w, i * 0.3, i * 0.3 + 0.2] for i, w in enumerate(t.split())]
        got = lambda t: [(r["text"], r["value"]) for r in ns.spoken(W(t))]
        self.assertEqual(got("the bill was four point three million"), [("four point three million", 4300000.0)])
        self.assertEqual([v for _, v in got("we lost twenty-five thousand three hundred seventy-five.")], [25375.0])
        self.assertEqual(got("open at five-thirty every day"), [("five-thirty", 5.5)])
        self.assertEqual(got("a sixty-day wait"), [("sixty-day", 60.0)]); self.assertEqual(got("a 60-day wait"), [("60-day", 60.0)])
        self.assertEqual(got("two of them"), []); self.assertEqual(got("one-day sale"), []); self.assertEqual(got("twenty-five dollars"), [("twenty-five dollars", 25.0)])
        shown = ns.on_screen(["5:30", "$4,300,000", "\u2212$25,375", "60 DAYS"])
        self.assertEqual(shown, {5.5, 4300000.0, 25375.0, 60.0})               # a clock is one value, not a 5 and a 30

    def test_round_speech_matches_an_exact_figure(self):
        import numbersync as ns
        self.assertTrue(ns.matches(25000, {25375}))           # "twenty-five thousand dollars" against an inked -$25,375
        self.assertTrue(ns.matches(25375, {25000}))           # and a "$25k" card against the exact figure said
        self.assertTrue(ns.matches(4300000, {4286000}))
        self.assertFalse(ns.matches(30000, {25375}))          # two significant figures: thirty thousand is not 25,375
        self.assertFalse(ns.matches(4800, {4860})); self.assertFalse(ns.matches(60, {64}))



class LocalImports(unittest.TestCase):
    """A module imported only inside one branch of a function is local to the whole function, so a run that skips the
    branch dies with UnboundLocalError where it is used outside it. check crashed this way twice (the listen step, then
    the motion floor on a film with a map, 2026-11-28). This walks every engine script for the pattern."""

    @staticmethod
    def unbound(src):
        import ast
        out = []
        for fn in [n for n in ast.walk(ast.parse(src)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            parent = {c: n for n in ast.walk(fn) for c in ast.iter_child_nodes(n)}
            imps = [(st, (a.asname or a.name).split(".")[0]) for st in ast.walk(fn)
                    if isinstance(st, (ast.Import, ast.ImportFrom)) for a in st.names]
            names = {nm for _, nm in imps}

            def covered(use, nm):       # an import of nm comes earlier in a block that encloses the use
                n = use
                while n in parent and n is not fn:
                    p = parent[n]
                    for field in ("body", "orelse", "finalbody"):
                        seq = getattr(p, field, None)
                        if isinstance(seq, list) and n in seq:
                            for st in seq[:seq.index(n)]:
                                if any(inm == nm and (st is i or (isinstance(st, ast.Try) and i in st.body)) for i, inm in imps):
                                    return True
                    n = p
                return False
            out += [(fn.name, u.id, u.lineno) for u in ast.walk(fn)
                    if isinstance(u, ast.Name) and isinstance(u.ctx, ast.Load) and u.id in names and not covered(u, u.id)]
        return out

    def test_no_branch_only_imports(self):
        bad = [(f.name,) + r for f in sorted(Path(__file__).parent.glob("*.py")) for r in self.unbound(f.read_text())]
        self.assertEqual(bad, [])
        self.assertEqual(self.unbound("def f(a):\n    if a:\n        import json\n        json.dumps(1)\n    return json.loads('1')\n"),
                         [("f", "json", 5)])


class PopDiag(unittest.TestCase):
    def test_reads_exact_frames_and_scores_a_pop(self):
        import popdiag
        with tempfile.TemporaryDirectory() as d:
            v = Path(d, "v.mp4")       # grey, with frame 5 alone white: a one-frame pop
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=320x180:r=30:d=0.4",
                            "-vf", "drawbox=x=0:y=0:w=320:h=180:color=white:t=fill:enable='eq(n,5)'", "-pix_fmt", "yuv420p", str(v)], check=True)
            fr = popdiag.video_frames(v, [4, 5, 6, 8], 160, 90)
            self.assertEqual(sorted(fr), [4, 5, 6, 8])
            a, b, c = popdiag.pop_row(fr, 5)
            self.assertGreater(min(a, b), 50); self.assertLess(c, 1)

class VideoFrames(unittest.TestCase):
    """Frames for the encoder come as JPEG q100 (6x faster than Chromium's PNG); stills and checks stay PNG."""

    @unittest.skipUnless(os.environ.get("MS_CHROMIUM"), "needs a browser (MS_CHROMIUM)")
    def test_formats(self):
        import io, json, longform, render
        import numpy as np
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            film = Path(d)
            shutil.copy(longform.TEMPLATE, film / "film.html")
            for f_ in longform.ENGINE_FILES:
                shutil.copy(longform.HERE / f_, film / f_)
            segs = [{"id": "a", "type": "statement", "t0": 0, "t1": 2, "data": {"text": "The first season"}, "counter": {"value": "$1,000", "label": "CASH", "t": 0.3}}]
            (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps({"duration": 2, "segments": segs}) + ";\n")
            with render.Film(film) as f:
                png = f.shot(1.0)
            with render.Film(film, video=True) as f:
                jpg = f.shot(1.0)
                done, raw = f.frame(1.0, 30)
            self.assertTrue(png.startswith(b"\x89PNG")); self.assertTrue(jpg.startswith(b"\xff\xd8")); self.assertTrue(done.startswith(b"\xff\xd8"))
            a = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"), float); b = np.asarray(Image.open(io.BytesIO(jpg)).convert("RGB"), float)
            self.assertGreater(10 * np.log10(255 ** 2 / max(1e-9, ((a - b) ** 2).mean())), 38)   # visually the same frame


class CollageStyle(unittest.TestCase):
    """"style": "collage": tags slap on their word on twos, ink and paint draw on stills, the HUD, source and chart take the
    map's paper and fonts, and every frame is a pure function of t (owner, 2026-11-27: "utilize them over the stills too")."""

    @unittest.skipUnless(os.environ.get("MS_CHROMIUM"), "needs a browser (MS_CHROMIUM)")
    def test_collage(self):
        import io, longform, mapart, render
        from PIL import Image, ImageDraw
        with tempfile.TemporaryDirectory() as d:
            film = Path(d); (film / "panels").mkdir()
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    mapart.cmd_fonts(film)
            except Exception as e:
                self.skipTest(f"fonts not reachable: {e}")
            im = Image.new("RGB", (1920, 1080), (226, 214, 190)); dr = ImageDraw.Draw(im)
            for i in range(40):
                dr.line((i * 12, 0, i * 12 + 50, 1080), fill=(40, 30, 20), width=4)
            im.save(film / "panels" / "yard.png")
            segs = [{"id": "y1", "type": "panel", "t0": 0, "t1": 6, "source": "U.S. Census Bureau", "tag": {"text": "Month 0", "t": 0.1},
                     "counter": {"value": "−$1,000", "label": "Your cash", "t": 0.1},
                     "data": {"image": "panels/yard.png", "focus": [0.15, 0.5],
                              "callouts": [{"kind": "stat", "value": "$16,000", "text": "vacuum tank", "t": 1.0, "point": [0.2, 0.6], "pos": [0.55, 0.5]}],
                              "ink": [{"kind": "paint", "box": [0.1, 0.7, 0.3, 0.8], "t": 0.6}, {"kind": "circle", "place": [0.6, 0.75], "r": 0.05, "t": 0.8},
                                      {"kind": "arrow", "from": [0.4, 0.9], "to": [0.5, 0.75], "t": 0.8}, {"kind": "spiral", "t": 0.8}]}},
                    {"id": "c1", "type": "chart", "t0": 6, "t1": 10, "counter": None, "tag": None,
                     "data": {"y": {"label": "Cash position", "format": "money"}, "series": [{"label": "One", "points": [[0, -10], [10, 50]], "t": 6.2}]}}]
            shutil.copy(longform.TEMPLATE, film / "film.html")
            for f_ in longform.ENGINE_FILES:
                shutil.copy(longform.HERE / f_, film / f_)
            (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps({"duration": 10, "segments": segs, "style": "collage", "paper_grain": True}) + ";\n")
            scale = """() => { const g = document.querySelector('[data-callout=y1]'); if (!g || g.style.display === 'none') return null;
                         return +g.firstChild.getAttribute('transform').match(/scale\\(([\\d.]+)\\)/)[1]; }"""
            fam = """() => [...document.querySelectorAll('text')].filter(e => ['$16,000', 'VACUUM TANK', 'Source: U.S. Census Bureau', 'MONTH 0', 'Cash position'].includes(e.textContent))
                         .map(e => [e.textContent, e.getAttribute('font-family') || ''])"""
            with render.Film(film) as f:
                got = []
                for k in range(27, 34):            # the cue lands 0.05 s before its word: frame 28
                    f.seek(k / 30); got.append(f.page.evaluate(scale))
                fonts = f.page.evaluate("['MS Caslon', 'MS Elite', 'MS Marker'].map(n => document.fonts.check('40px \"' + n + '\"'))")
                f.seek(2.0); fams = f.page.evaluate(fam)
                ink = f.page.evaluate("() => [...document.querySelectorAll('[data-ink=y1] path')].map(p => (p.getAttribute('d') || '').length)")
                over = f.page.evaluate(longform.OVERLAP)
                warns = f.page.evaluate("window.STUDIO.warnings || []")
                f.seek(8.0); fams += f.page.evaluate(fam)
                g = lambda: np.asarray(Image.open(io.BytesIO(f.page.screenshot())).convert("L"))
                f.seek(1.1); a = g(); f.seek(5.0); f.seek(1.1); b = g()
                self.assertFalse(f.errors)
        self.assertEqual(got, [None, 1.1, 1.1, 0.98, 0.98, 1, 1])       # on its word, on twos, never a fade
        self.assertEqual(fonts, [True, True, True])
        fd = dict(fams)
        self.assertIn("MS Caslon", fd["$16,000"]); self.assertIn("MS Elite", fd["VACUUM TANK"])
        self.assertIn("MS Elite", fd["Source: U.S. Census Bureau"]); self.assertIn("MS Elite", fd["MONTH 0"]); self.assertIn("MS Caslon", fd["Cash position"])
        self.assertTrue(all(n > 20 for n in ink), ink)                    # paint, circle (stroke and crayon), arrow all drawn
        self.assertFalse([o for o in over if o[0] in ("$16,000", "VACUUM TANK")], over)
        self.assertTrue(any('ink kind "spiral"' in w for w in warns), warns)
        self.assertFalse((a != b).any())                                  # the same frame however it is reached


class NapkinMath(unittest.TestCase):
    """Napkin math (owner's pick, 2026-11-27): a price tag tied to its object, a tally that adds a line per cost on its
    word, a cash card that ticks by each "cash" item, and a total that sums the lines."""

    @unittest.skipUnless(os.environ.get("MS_CHROMIUM"), "needs a browser (MS_CHROMIUM)")
    def test_napkin(self):
        import io, longform, mapart, render
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            film = Path(d); (film / "panels").mkdir()
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    mapart.cmd_fonts(film)
            except Exception as e:
                self.skipTest(f"fonts not reachable: {e}")
            Image.new("RGB", (1920, 1080), (226, 214, 190)).save(film / "panels" / "yard.png")
            segs = [{"id": "a", "type": "panel", "t0": 0, "t1": 6, "counter": {"value": "−$1,000", "label": "Your cash", "t": 0.1},
                     "tally": {"title": "Startup", "items": [{"label": "Truck", "value": "$1,000", "t": 0.5},
                                                             {"label": "Tank", "value": "$500", "t": 2.0, "cash": True}], "total": {"label": "Total", "t": 3.5}},
                     "data": {"image": "panels/yard.png", "callouts": [{"kind": "price", "value": "$500", "text": "tank", "point": [0.7, 0.6], "t": 2.0}]}},
                    {"id": "b", "type": "panel", "t0": 6, "t1": 8, "tally": None, "data": {"image": "panels/yard.png"}}]
            shutil.copy(longform.TEMPLATE, film / "film.html")
            for f_ in longform.ENGINE_FILES:
                shutil.copy(longform.HERE / f_, film / f_)
            (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps({"duration": 8, "segments": segs, "style": "collage"}) + ";\n")
            with render.Film(film) as f:
                seen = {}
                for t in (0.3, 1.0, 2.5, 4.5, 7.5):
                    f.seek(t); seen[t] = set(f.page.evaluate(longform.VISIBLE_TEXT))
                f.seek(4.5)
                string = f.page.evaluate("() => (document.querySelector('[data-callout=a] > path[stroke=\"#f3ecd9\"]') || {getAttribute: () => ''}).getAttribute('d') || ''")
                over = f.page.evaluate(longform.OVERLAP)
                self.assertFalse(f.errors)
        flat = lambda t: " | ".join(sorted(x if isinstance(x, str) else str(x[0]) for x in seen[t]))
        self.assertNotIn("Truck", flat(0.3)); self.assertIn("Truck", flat(1.0)); self.assertNotIn("Tank", flat(1.0))
        self.assertIn("−$1,000", flat(1.0)); self.assertIn("−$1,500", flat(4.5))      # the card ticked by the tank
        self.assertIn("$1,500", flat(4.5)); self.assertIn("Total", flat(4.5))           # the total sums the lines
        self.assertNotIn("Truck", flat(7.5))                                            # "tally": null clears it
        self.assertGreater(len(string), 40)                                             # the price tag's string
        self.assertFalse([o for o in over if o[0] in ("Truck", "Tank", "Total", "$1,000", "$500", "$1,500", "STARTUP")], over)


class BeatSync(unittest.TestCase):
    """Something new lands on a word every few seconds while the voice runs, and reveals land on their words (owner,
    2026-11-27: "pace what is on screen/the beats with the voiced lines to make sure it is in sync")."""
    WORDS = [["The", 0.2, 0.3], ["truck", 0.3, 0.6], ["costs", 0.6, 0.9], ["nine", 0.9, 1.1], ["thousand", 1.1, 1.4], ["dollars.", 1.4, 1.8],
             ["Then", 2.5, 2.7], ["you", 2.7, 2.8], ["wait", 2.8, 3.1], ["for", 3.1, 3.2], ["customers", 3.2, 3.8], ["while", 4.5, 4.7],
             ["the", 4.7, 4.8], ["phone", 4.8, 5.1], ["stays", 5.1, 5.4], ["quiet", 5.4, 5.8], ["for", 6.4, 6.5], ["twelve", 6.5, 6.9], ["weeks.", 6.9, 7.4]]

    def seg(self, **kw):
        d = {"id": "a", "type": "panel", "t0": 0, "t1": 8, "words": self.WORDS,
             "data": {"image": "x.png", "callouts": [{"kind": "stat", "value": "$9,000", "text": "the truck", "at": "nine", "t": 0.9},
                                                     {"kind": "stat", "value": "12 weeks", "text": "of quiet"}]}}
        d.update(kw)
        return d

    def test_gaps_and_uncued(self):
        import pace
        s = self.seg()
        self.assertEqual(pace.events(s), [0, 0.9])
        g = pace.gaps(s)
        self.assertEqual(len(g), 1)
        self.assertEqual((g[0]["t0"], g[0]["t1"]), (0.9, 7.4))
        self.assertEqual(g[0]["suggest"], "twelve")                     # a number said in the gap first
        u = pace.uncued(s)
        self.assertEqual([(x["key"], x["index"], x["match"]) for x in u], [("callouts", 1, "twelve weeks")])     # the number as said, with its unit
        s2 = self.seg(); s2["data"]["callouts"][1].update(at="twelve", t=6.5)
        self.assertEqual(len(pace.gaps(s2)), 1)                         # 0.9 -> 6.5 is 5.6 s: still a gap

    def test_gap_closes_with_a_landing_and_clips_skip(self):
        import pace
        s = self.seg(); s["data"]["callouts"][1].update(at="twelve", t=6.5)
        s["data"]["ink"] = [{"kind": "circle", "place": [0.5, 0.5], "at": "customers", "t": 3.2}]
        self.assertEqual(pace.gaps(s), [])
        self.assertEqual(pace.gaps(self.seg(data={"clip": "a.mp4"})), [])  # moving footage is new all the time
        med, near = pace.rhythm([s])
        self.assertTrue(med and near > 0.9, (med, near))

    def test_apply_writes_at(self):
        import io, longform
        with tempfile.TemporaryDirectory() as d:
            film = Path(d)
            sc = {"title": "t", "segments": [{"id": "a", "type": "panel", "narration": "x", "data": {"image": "x.png", "callouts": [
                  {"kind": "stat", "value": "$9,000", "text": "the truck", "at": "nine"}, {"kind": "stat", "value": "12 weeks", "text": "of quiet"}]}}]}
            (film / "script.json").write_text(json.dumps(sc))
            (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps({"duration": 8, "segments": [self.seg()]}) + ";\n")
            with contextlib.redirect_stdout(io.StringIO()):
                longform.cmd_pace(type("A", (), {"film": str(film), "gap": 4.0, "apply": True})())
            got = json.loads((film / "script.json").read_text())["segments"][0]["data"]["callouts"][1]
            self.assertEqual(got.get("at"), "twelve weeks")
            self.assertTrue((film / "script.json.bak").exists())
            self.assertIn("twelve", (film / "review" / "pace.md").read_text())


    def test_numbers_read_raw_and_more_sites(self):
        import pace
        w = [[x, 0.5 + 0.3 * i, 0.75 + 0.3 * i] for i, x in enumerate(
            "Rent runs twelve hundred dollars a month and the units are fifteen to twenty-five dollars each".split())]
        s = {"id": "p", "type": "panel", "t0": 0, "t1": 6, "words": w, "data": {"image": "x.png",
             "caption_card": "Rent: $1,200/mo", "callouts": [{"kind": "stat", "value": "$15–25", "text": "per unit"}]}}
        got = {pace.label(u["path"]): u["match"] for u in pace.uncued(s)}
        self.assertEqual(got["caption_card"], "twelve hundred dollars")          # "$1,200/mo" was read as 1 before
        self.assertEqual(got["callouts[0]"], "fifteen")                         # "$15–25": a range cues on its first end
        st = {"id": "s", "type": "stat", "t0": 0, "t1": 6, "words": w, "data": {"prefix": "$", "value": 1200, "label": "a month in rent"}}
        self.assertEqual([(u["path"], u["match"]) for u in pace.uncued(st)], [([], "twelve hundred dollars")])
        st["data"]["label"] = "rent each month"                                  # a stat cues on its number only, never its label
        st["words"] = [["Rent", 0.5, 0.8], ["each", 0.8, 1.0], ["month", 1.0, 1.3]]
        self.assertEqual([u["match"] for u in pace.uncued(st)], [None])
        data = {"caption_card": "Rent: $1,200/mo"}
        self.assertTrue(pace.set_at(data, ["caption_card"], "twelve hundred dollars"))
        self.assertEqual(data["caption_card"], {"text": "Rent: $1,200/mo", "at": "twelve hundred dollars"})
        self.assertFalse(pace.set_at(data, ["caption_card"], "rent"))           # a cue already set is left alone

    def test_cued_early(self):
        import pace
        s = self.seg(); s["data"]["callouts"][0].update(at="truck", t=0.3)        # $9,000 lands on "truck", said at 0.9
        self.assertEqual(pace.cued_early(s), [])                                 # 0.6 s early: within the window
        s["data"]["callouts"][0].update(at="The", t=0.0)
        s["words"] = [["The", 0.0, 0.1]] + self.WORDS[1:]
        e = pace.cued_early(s)
        self.assertEqual([(x["label"], x["cue"], x["number"]) for x in e], [("callouts[0]", "The", "nine thousand dollars")])

    def test_range_cued_on_first_end(self):
        import pace, numbersync as n
        w = [[x, 0.5 + 0.4 * i, 0.8 + 0.4 * i] for i, x in enumerate("Payment takes forty-five to sixty days and costs thirty-five to forty thousand".split())]
        self.assertEqual([(a["value"], a.get("range_of")) for a in n.spoken(w)], [(45, None), (60, 45), (35000, None), (40000, 35000)])
        self.assertEqual(n.on_screen(["$35–40k"]), {35000.0, 40000.0})
        s = {"id": "r", "type": "panel", "t0": 0, "t1": 6, "words": w, "data": {"image": "x.png", "callouts": [
            {"kind": "stat", "value": "45–60 days", "at": "forty-five", "t": 1.7},
            {"kind": "stat", "value": "$35–40k", "at": "thirty-five", "t": 3.7},
            {"kind": "stat", "value": "1 per 40", "text": "toilets", "at": "Payment", "t": 0.5}]}}
        self.assertEqual([e["label"] for e in pace.cued_early(s)], [])          # ranges on their first end are right
        w2 = w + [["one", 5.0, 5.1], ["per", 5.1, 5.2], ["forty", 5.2, 5.4], ["people", 5.4, 5.7]]
        s["words"] = w2
        self.assertEqual([e["label"] for e in pace.cued_early(s)], ["callouts[2]"])   # "1 per 40" on a word before "forty"
        self.assertEqual([a["value"] for a in n.spoken([[x, i, i] for i, x in enumerate("two of them broke".split())])], [])

    def test_apply_with_nothing_to_cue(self):
        import io, longform
        with tempfile.TemporaryDirectory() as d:
            film = Path(d)
            (film / "script.json").write_text(json.dumps({"title": "t", "segments": [{"id": "a", "type": "panel", "narration": "x", "data": {}}]}))
            seg = self.seg(); seg["data"]["callouts"] = seg["data"]["callouts"][:1]
            (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps({"duration": 8, "segments": [seg]}) + ";\n")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                longform.cmd_pace(type("A", (), {"film": str(film), "gap": 4.0, "apply": True})())
            self.assertIn("nothing to cue", buf.getvalue())
            self.assertNotIn("script.json.bak", buf.getvalue())
            self.assertFalse((film / "script.json.bak").exists())

    def test_stale_timeline_refused(self):
        # episode thread, 2026-11-27: timeline stopped, left the last timeline.js, and check counted an old cut
        import io, time, longform
        with tempfile.TemporaryDirectory() as d:
            film = Path(d)
            (film / "script.json").write_text(json.dumps({"title": "t", "segments": []}))
            tl = {"duration": 8, "segments": [self.seg()], "script_sha": longform.script_sha(film)}
            (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps(tl) + ";\n")
            self.assertIsNone(longform.stale_timeline(film))
            args = type("A", (), {"film": str(film), "gap": 4.0, "apply": False})()
            with contextlib.redirect_stdout(io.StringIO()):
                longform.cmd_pace(args)
            (film / "script.json").write_text(json.dumps({"title": "t2", "segments": []}))
            self.assertIn("changed since", longform.stale_timeline(film))
            with self.assertRaises(SystemExit) as e:
                longform.cmd_pace(args)
            self.assertIn("pace refused", str(e.exception))
            tl.pop("script_sha")                                         # an old timeline.js: file times decide
            (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps(tl) + ";\n")
            os.utime(film / "script.json", (time.time() + 60, time.time() + 60))
            self.assertIn("newer than", longform.stale_timeline(film))


class SoundMatchesCut(unittest.TestCase):
    """The sound under the picture is the cut's own (2026-11-28: the dashboard played a Nov 25 mix under a Nov 27 cut
    for weeks, and every sync complaint came from that)."""
    def film(self, d, dur=3.0):
        import longform
        film = Path(d); (film / "out").mkdir()
        (film / "script.json").write_text(json.dumps({"title": "t", "segments": []}))
        tl = {"duration": dur, "segments": [], "script_sha": longform.script_sha(film)}
        (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps(tl) + ";\n")
        for n, sec in (("narration.wav", dur), ("mix.wav", dur)):
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=f=220:d={sec}", "-ar", "48000",
                            str(film / "out" / n)], check=True)
        return film, tl

    def test_stamp_and_refusals(self):
        import longform, preview
        with tempfile.TemporaryDirectory() as d:
            film, tl = self.film(d)
            longform.write_mix_stamp(film)
            self.assertIsNone(longform.stale_mix(film))
            self.assertEqual(preview.sound(film, "/w/f/")["mix"], "/w/f/out/mix.wav")
            longform.require_fresh_mix(film, "check")                       # no refusal
            # a new cut: the script and the timeline's length change, the mix stays
            (film / "script.json").write_text(json.dumps({"title": "t2", "segments": []}))
            tl.update(duration=4.0, script_sha=longform.script_sha(film))
            (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps(tl) + ";\n")
            self.assertIn("another version of script.json", longform.stale_mix(film))
            with self.assertRaises(SystemExit) as e:
                longform.require_fresh_mix(film, "final")
            self.assertIn("final refused", str(e.exception))
            snd = preview.sound(film, "/w/f/")
            self.assertEqual(snd["mix"], "/w/f/out/narration.wav")           # the voice alone, never the old mix
            self.assertIn("playing the voice alone", snd["mix_stale"])

    def test_unstamped_mix_by_length(self):
        import longform
        with tempfile.TemporaryDirectory() as d:
            film, tl = self.film(d)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=220:d=1.5", "-ar", "48000",
                            str(film / "out" / "mix.wav")], check=True)
            os.utime(film / "timeline.js", (time.time() - 5, time.time() - 5))
            self.assertIn("is 1.5 s and the cut is 3.0 s", longform.stale_mix(film))

    @unittest.skipUnless(os.environ.get("MS_CHROMIUM"), "needs a browser (MS_CHROMIUM)")
    def test_check_listens_end_to_end(self):
        # 2026-11-28: check crashed at the listen step (a later local named `listen` shadowed the function); only the
        # function was tested, never check itself
        import io, sys, longform, align
        with tempfile.TemporaryDirectory() as d:
            film, tl = self.film(d, dur=14.0)
            shutil.copy(longform.TEMPLATE, film / "film.html")
            for f_ in longform.ENGINE_FILES:
                shutil.copy(longform.HERE / f_, film / f_)
            (film / "script.json").write_text(json.dumps({"title": "t", "segments": [{"id": "a", "type": "statement", "narration": "x", "data": {"text": "Rent"}}]}))
            words = [[w, 0.3 + 0.3 * i, 0.55 + 0.3 * i] for i, w in enumerate(("rent is due on the first of every month " * 4).split())]
            tl.update(segments=[{"id": "a", "type": "statement", "t0": 0, "t1": 14, "data": {"text": "Rent"}, "words": words}],
                      script_sha=longform.script_sha(film))
            (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps(tl) + ";\n")
            longform.write_mix_stamp(film)
            real = align._whisper_python, align.whisper_words, sys.argv
            align._whisper_python, align.whisper_words = (lambda: "python3"), (lambda wav: [("rent", 0.1, 0.3)])
            sys.argv = ["longform.py", "check", str(film)]
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    try:
                        longform.main()
                    except SystemExit:
                        pass
            finally:
                align._whisper_python, align.whisper_words, sys.argv = real
            out = buf.getvalue()
            self.assertIn("listen: ", out)
            self.assertIn("the sound is from another cut", out)                 # the fake heard one word: flagged
            self.assertNotIn("4-16 s", out)                                     # windows stay inside the film

    def test_listen_without_whisper_says_so(self):
        import longform, align
        with tempfile.TemporaryDirectory() as d:
            film, tl = self.film(d)
            tl["segments"] = [{"id": "a", "t0": 0, "t1": 3, "words": [["hello", 0.2, 0.5]]}]
            real = align._whisper_python
            align._whisper_python = lambda: (_ for _ in ()).throw(RuntimeError("no whisper here"))
            try:
                line, flags = longform.listen_mix(film, tl)
            finally:
                align._whisper_python = real
            self.assertIn("not run (no whisper here)", line); self.assertEqual(flags, [])

    def test_listen_flags_another_cut(self):
        import longform, align
        with tempfile.TemporaryDirectory() as d:
            film, tl = self.film(d, dur=40.0)
            text = "the porta potty business pays well when you plan the routes and keep the trucks full every single week".split()
            tl["segments"] = [{"id": "a", "t0": 0, "t1": 40, "words": [[w, 0.4 * i, 0.4 * i + 0.3] for i, w in enumerate(text * 5)]}]
            real_py, real_ww = align._whisper_python, align.whisper_words
            align._whisper_python = lambda: "python3"
            try:
                align.whisper_words = lambda wav: [("Completely", 0.1, 0.4), ("different", 0.5, 0.9), ("words", 1.0, 1.3)] * 4
                line, flags = longform.listen_mix(film, tl)
                self.assertEqual(len(flags), 3, line)
                self.assertIn("another cut", flags[0])
            finally:
                align._whisper_python, align.whisper_words = real_py, real_ww


class CelFinish(unittest.TestCase):
    """The cel finish (finish.py) on the composed picture, under crisp burned captions (cel style test, 2026-11-28)."""
    def test_pure_and_slow(self):
        import finish
        rgb = np.full((360, 640, 3), 128, np.uint8)
        a, b = finish.cel(rgb, 10), finish.cel(rgb, 10)
        self.assertTrue(np.array_equal(a, b))                                    # a pure function of frame and seed
        step = lambda i, j: np.abs(finish.cel(rgb, i).astype(int) - finish.cel(rgb, j).astype(int)).mean()
        self.assertLess(step(10, 11), 0.5)                     # grain on twos: frames 10 and 11 share it, the mottle drifts
        self.assertGreater(step(11, 12), 1.0)                  # a new grain field on frame 12
        self.assertLess(step(11, 12), 6)                       # fine grain, not a flash
        black = finish.cel(np.zeros((360, 640, 3), np.uint8), 0)[160:200, 300:340].reshape(-1, 3).mean(0)
        self.assertTrue(black[0] > black[2] and black[0] > 10, black)            # blacks lifted toward warm brown
        with self.assertRaises(ValueError):
            finish.apply("sepia", rgb, 0)

    @unittest.skipUnless(os.environ.get("MS_CHROMIUM"), "needs a browser (MS_CHROMIUM)")
    def test_frames_finish_under_captions(self):
        import io, longform, render
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            film = Path(d)
            shutil.copy(longform.TEMPLATE, film / "film.html")
            for f_ in longform.ENGINE_FILES:
                shutil.copy(longform.HERE / f_, film / f_)
            words = [[w, 0.3 + 0.3 * i, 0.55 + 0.3 * i] for i, w in enumerate("Rent is due on the first".split())]
            seg = {"id": "a", "type": "statement", "t0": 0, "t1": 3, "data": {"text": "Rent"}, "words": words}
            tl = {"duration": 3, "segments": [seg], "captions": True, "finish": "cel"}
            (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps(tl) + ";\n")
            with render.Film(film) as f:
                done, raw = f.frame(1.0)
                plain = f.shot(1.0)
                f.page.evaluate("document.getElementById('caps').style.display = 'none'")
                bare = f.page.locator("#stage").screenshot(type="png")
                self.assertFalse(f.errors)
            A, B, P, N = (np.asarray(Image.open(io.BytesIO(x)).convert("RGB")).astype(int) for x in (raw, done, plain, bare))
            self.assertLess(np.abs(A - P).mean(), 0.5)                           # raw is the page as it is
            self.assertGreater(np.abs(B - A).mean(), 2)                          # the finish changed the picture
            # the caption's solid letters come through untouched: they go on after the finish
            m = np.abs(P - N).sum(-1) > 300
            self.assertGreater(m.sum(), 50)
            self.assertLess(np.abs(B[m] - P[m]).mean(), 4)
            # a render chunk writes the finished frames and the unfinished copy final's motion check reads
            dest = film / "parts" / "p0000000-0000006.mp4"; dest.parent.mkdir()
            longform._render_chunk((str(film), 0, 6, str(dest), 0.5))
            self.assertTrue(dest.exists()); self.assertTrue(dest.with_suffix(".raw.mp4").exists())
            # final's frame-0 reference is the finished frame 0, like the video's (first cel render, 2026-11-28)
            with render.Film(film) as f:
                fin0 = np.asarray(Image.open(io.BytesIO(f.frame(0, 30)[0])).convert("RGB")).astype(int)
                raw0 = np.asarray(Image.open(io.BytesIO(f.shot(0))).convert("RGB")).astype(int)
            ref = longform.film_frame0(film).astype(int)
            self.assertLess(np.abs(ref - fin0).mean(), 1.0); self.assertGreater(np.abs(ref - raw0).mean(), 2)
            # every chunk draws one frame and throws it away before its first kept frame
            calls, real = [], render.Film.frame
            def spy(self_, t, fps=30):
                calls.append(round(t * 30)); return real(self_, t, fps)
            render.Film.frame = spy
            try:
                d2 = film / "parts" / "p0000010-0000012.mp4"
                longform._render_chunk((str(film), 10, 12, str(d2), 0.5))
            finally:
                render.Film.frame = real
            self.assertEqual(calls, [9, 10, 11])


class ChunkCaptions(unittest.TestCase):
    """Shorts captions in chunks of 1 to 3 words (cel style test, 2026-11-28)."""
    @unittest.skipUnless(os.environ.get("MS_CHROMIUM"), "needs a browser (MS_CHROMIUM)")
    def test_chunks(self):
        import longform, render
        with tempfile.TemporaryDirectory() as d:
            film = Path(d)
            shutil.copy(longform.TEMPLATE, film / "film.html")
            for f_ in longform.ENGINE_FILES:
                shutil.copy(longform.HERE / f_, film / f_)
            text = "Rent is due on the first, every month. Extraordinarily-expensive-portapotties cost more"
            words = [[w, round(0.2 + 0.3 * i, 3), round(0.45 + 0.3 * i, 3)] for i, w in enumerate(text.split())]
            seg = {"id": "a", "type": "statement", "t0": 0, "t1": 4.5, "data": {"text": "Rent"}, "words": words}
            tl = {"duration": 4.5, "segments": [seg], "captions": True, "caption_style": "chunk", "format": "9x16"}
            (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps(tl) + ";\n")
            with render.Film(film) as f:
                pages = [x["text"] for x in f.page.evaluate("window.STUDIO.text") if x["where"] == "caption"]
                f.seek(0.2 + 1 / 30)                             # one frame into the first chunk: dim
                dim = f.page.evaluate("[...document.querySelectorAll('#caps > div')].filter(b => b.style.visibility === 'visible').map(b => +b.style.opacity)")
                f.seek(0.5)
                full = f.page.evaluate("""[...document.querySelectorAll('#caps > div')].filter(b => b.style.visibility === 'visible')
                    .map(b => [+b.style.opacity, getComputedStyle(b).fontWeight, getComputedStyle(b).color, b.scrollWidth <= b.clientWidth + 1])""")
                f.seek(3.9)
                fit = f.page.evaluate("""[...document.querySelectorAll('#caps > div')].filter(b => b.style.visibility === 'visible')
                    .map(b => [[...b.querySelectorAll('span')].reduce((a, s) => a + s.getBoundingClientRect().width, 0), b.clientWidth])""")
                self.assertFalse(f.errors)
        self.assertTrue(all(1 <= len(p.split()) <= 3 for p in pages), pages)
        self.assertIn("Rent is due", pages); self.assertIn("on the first,", pages)       # breaks at the comma
        self.assertEqual(dim, [0.55]); self.assertEqual(full[0][:3], [1, "900", "rgb(255, 226, 26)"])
        self.assertTrue(fit and fit[0][0] <= fit[0][1] + 1, fit)                         # the long word shrinks to fit

class SpokenNumbers(unittest.TestCase):
    """Every spoken amount is on screen at its word (editing-quality study, 2026-11-27: 6 of 83 were)."""
    def test_parse(self):
        import numbersync as n
        w = [[x, i * 0.3, 0] for i, x in enumerate("In 2024 we made $4,860 from forty porta-potties, two of them broke, "
                                                    "one day it hit $4.3M and 62% over three months".split())]
        self.assertEqual([(a["text"].strip(","), a["value"]) for a in n.spoken(w)],
                         [("$4,860", 4860.0), ("forty porta-potties", 40.0), ("$4.3M", 4.3e6), ("62%", 62.0), ("three months", 3.0)])
        self.assertTrue(n.matches(-3925, n.on_screen(["−$3,925"])))
        self.assertTrue(n.matches(4.3e6, n.on_screen(["$4.3M"])))
        self.assertFalse(n.matches(4800, n.on_screen(["$4,860"])))

    @unittest.skipUnless(os.environ.get("MS_CHROMIUM"), "needs a browser (MS_CHROMIUM)")
    def test_check(self):
        import json, longform
        def words(text, t0):
            return [[x, round(t0 + 0.3 * i, 3), round(t0 + 0.3 * i + 0.25, 3)] for i, x in enumerate(text.split())]
        segs = [{"id": "a", "type": "statement", "t0": 0, "t1": 4, "data": {"text": "Cash after season one"},
                 "words": words("We made $4,860 this season", 0.5), "counter": {"value": "$4,860", "label": "Cash", "t": 1.05}},
                {"id": "b", "type": "statement", "t0": 4, "t1": 8, "data": {"text": "Then the fair"},
                 "words": words("We bought forty porta-potties", 4.5)},
                {"id": "c", "type": "statement", "t0": 8, "t1": 12, "data": {"text": "About $900 a month"},
                 "words": words("And then it costs about $900 a month", 8.5)},
                {"id": "d", "type": "statement", "t0": 12, "t1": 15, "data": {"text": "Roughly"},
                 "words": words("Roughly ten trucks", 12.5), "unshown": "a rough count", "counter": {"value": "$5,000"}}]
        with tempfile.TemporaryDirectory() as d:
            film = Path(d)
            shutil.copy(longform.TEMPLATE, film / "film.html")
            for f in longform.ENGINE_FILES:
                shutil.copy(longform.HERE / f, film / f)
            (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps({"duration": 15, "segments": segs}) + ";\n")
            rows = {r["seg"]: r["state"] for r in longform.number_sync(film, segs)}
            line, fl = longform.number_flags(film, segs)
        self.assertEqual(rows, {"a": "ok", "b": "missing", "c": "early", "d": "opted out"})
        self.assertIn("1 of 3", line)
        self.assertTrue(any(x.startswith("d: the counter card changes at the segment's start") for x in fl), fl)


class SameStillRun(unittest.TestCase):
    """Adjacent panels on one still are one camera move, not a cut back to the same picture (owner's note, 2026-11-27)."""
    @unittest.skipUnless(os.environ.get("MS_CHROMIUM"), "needs a browser (MS_CHROMIUM)")
    def test_one_move(self):
        import json, longform, render
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            film = Path(d); (film / "panels").mkdir()
            Image.new("RGB", (1920, 1080), (120, 140, 160)).save(film / "panels" / "yard.png")
            Image.new("RGB", (1920, 1080), (160, 140, 120)).save(film / "panels" / "lot.png")
            segs = [{"id": "a", "type": "panel", "t0": 0, "t1": 3, "data": {"image": "panels/yard.png", "camera": "slow-push"}},
                    {"id": "b", "type": "panel", "t0": 3, "t1": 6, "data": {"image": "panels/yard.png", "camera": "pan-left"}},
                    {"id": "c", "type": "panel", "t0": 6, "t1": 9, "data": {"image": "panels/lot.png", "camera": "slow-push"}}]
            shutil.copy(longform.TEMPLATE, film / "film.html")
            for f in longform.ENGINE_FILES:
                shutil.copy(longform.HERE / f, film / f)
            (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps({"duration": 9, "segments": segs}) + ";\n")
            at = "([id, t]) => { const q = window.STUDIO.panelCam[id].at(t); return [q.z, q.cx, q.cy]; }"
            with render.Film(film) as f:
                f.seek(1.0)
                a_end, b_start = f.page.evaluate(at, ["a", 2.99]), f.page.evaluate(at, ["b", 3.0])
                b_end, c_start = f.page.evaluate(at, ["b", 6.0]), f.page.evaluate(at, ["c", 6.0])
                self.assertFalse(f.errors)
        for x, y in zip(a_end, b_start):
            self.assertAlmostEqual(x, y, delta=0.01 * max(1, abs(x)))          # the cut is invisible: one move
        self.assertAlmostEqual(a_end[0], 1.0 + 0.14 * 0.5, delta=0.02)         # halfway through a push over both panels
        self.assertAlmostEqual(b_end[0], 1.14, places=3)                         # the push ends at the run's end
        self.assertAlmostEqual(c_start[0], 1.0, places=3)                        # a new still starts its own move


class TSheetAndHudOverlap(unittest.TestCase):
    def test_tsheet_marks_numbers(self):
        import argparse, json, longform
        with tempfile.TemporaryDirectory() as d:
            film = Path(d)
            (film / "script.json").write_text(json.dumps({"title": "T", "segments": [
                {"id": "a", "chapter": "Intro", "type": "panel", "narration": "Forty porta-potties made $4,860.",
                 "counter": {"value": "$4,860", "at": "$4,860"}, "data": {"image": "x.png"}},
                {"id": "b", "chapter": "Year one", "type": "statement", "narration": "It costs $900 over three months.", "data": {"text": "Costs"}}]}))
            longform.cmd_tsheet(argparse.Namespace(film=d, all=True))
            out = (film / "review" / "tsheet.md").read_text()
        self.assertIn("It costs **$900** over **three months**. (not on screen: $900, three months)", out)
        self.assertIn("counter: **$4,860** at", out)
        self.assertIn("3 spoken number(s) with nothing on screen", out)

    @unittest.skipUnless(os.environ.get("MS_CHROMIUM"), "needs a browser (MS_CHROMIUM)")
    def test_hud_over_chart_text_is_caught(self):
        # the HUD cards sit over every scene; the overlap check must see them covering a chart's own text (note #96)
        import json, longform
        with tempfile.TemporaryDirectory() as d:
            film = Path(d)
            hook = ("<script>{const S = window.seek; window.seek = t => { S(t); const g = document.querySelector('#scenes > [data-seg=a]');"
                    " if (!g.querySelector('#probe')) { const e = document.createElementNS('http://www.w3.org/2000/svg', 'text');"
                    " e.id = 'probe'; e.setAttribute('x', 1600); e.setAttribute('y', 110); e.setAttribute('font-size', 40);"
                    " e.setAttribute('fill', '#000'); e.setAttribute('data-aux', 1); e.textContent = 'Month 6 label'; g.appendChild(e); } };}</script>")
            (film / "film.html").write_text(longform.TEMPLATE.read_text().replace("</body>", hook + "</body>"))
            for f in longform.ENGINE_FILES:
                shutil.copy(longform.HERE / f, film / f)
            segs = [{"id": "a", "type": "chart", "t0": 0, "t1": 5, "data": {"series": [{"name": "x", "points": [[0, 1], [1, 2], [2, 3]]}]},
                     "counter": {"value": "$1,000", "label": "Cash", "t": 0.3}}]
            (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps({"duration": 5, "segments": segs}) + ";\n")
            hits = longform.overlaps(film, segs, 0.25)
        self.assertIn(("a", "Month 6 label", 'covered by the shape holding "CASH"'), hits)


class ClipPanel(unittest.TestCase):
    """A video clip in a panel: frames drawn exactly per render frame (Survive Year 1 intro, Wan clips, 2026-11-25)."""
    def seg(self, **kw):
        return {"id": "p", "type": "panel", "t0": 0.0, "t1": 4.0, "data": dict({"clip": "clips/c1.mp4", "camera": "hold"}, **kw)}

    def test_schedule(self):
        import longform
        sch = lambda **kw: longform.clip_schedule(self.seg(**kw), 49, 16.0)
        h = sch()
        self.assertEqual(len(h), 121)
        self.assertEqual(h[0], [0, 1, 0]); self.assertEqual(h[-1][0], 48)           # plays once and holds the last frame
        self.assertEqual(h[15], [8, 9, 0])                                           # 0.5 s at 16 fps: frame 8 exactly
        self.assertAlmostEqual(h[1][2], 16 / 30, places=3)                           # blended between frames
        hold = sch(clip_retime="hold")
        self.assertTrue(all(w == 0 for _, _, w in hold))
        steps = longform.clip_steps([dict(self.seg(clip_retime="hold"), data=dict(self.seg(clip_retime="hold")["data"], clip_sched=hold))])
        self.assertEqual(len(steps), 48)                                             # every frame step, then the hold
        self.assertTrue(all(b[0] >= a_[0] for a_, b in zip(h, h[1:])))             # never steps back
        self.assertEqual(longform.clip_backwards([dict(self.seg(), data=dict(self.seg()["data"], clip_sched=h))]), [])
        # the owner's rule: no ping-pong, no loop, no reversed speed; and a timeline made before it is still caught
        for kw in ({"clip_mode": "pingpong"}, {"clip_mode": "loop"}, {"clip_speed": -1}):
            probs = []
            longform.clip_prepare(Path("/nonexistent"), self.seg(**kw), "16x9", probs)
            self.assertTrue(probs and "forbids" in probs[0], (kw, probs))
        old = [[0, 1, 0], [5, 6, 0], [48, 48, 0], [40, 41, 0]]
        self.assertEqual(longform.clip_backwards([dict(self.seg(), data=dict(self.seg()["data"], clip_sched=old))]), ["p"])

    def test_wan_lint(self):
        sys_path = str(Path(__file__).resolve().parent.parent / "assets" / "colab" / "cli")
        import sys
        sys.path.insert(0, sys_path)
        import wan_lint
        jobs = [{"id": f"a_s{k}", "image": "a.png", "motion": "Wide shot, the hero walks across the yard"} for k in (1, 2, 3)]
        self.assertEqual(wan_lint.problems(jobs), [])
        bad = [{"id": "b_s1", "image": "b.png", "motion": "Close-up of a laptop on a desk"},
               {"id": "c_s1", "image": "c.png", "motion": "A padlock on the gate", "wan_ok": "wide exterior"}]
        p = wan_lint.problems(bad)
        self.assertTrue(any("b_s1" in x and "close-up" in x for x in p))
        self.assertFalse(any("c_s1" in x for x in p))
        self.assertTrue(any("b.png: 1 take" in x for x in p))

    @unittest.skipUnless(os.environ.get("MS_CHROMIUM"), "needs a browser (MS_CHROMIUM)")
    def test_clip_fit_lands_the_last_frame_on_the_cut(self):
        # Survive Year 1, 2026-11-27: slow a short clip to fill its beat (floor 0.45x), play a long clip's end at 0.75x;
        # no engine rain over a clip
        import longform
        with tempfile.TemporaryDirectory() as d:
            film = Path(d); (film / "clips").mkdir()
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:size=832x480:rate=16", "-frames:v", "49",
                            "-pix_fmt", "yuv420p", str(film / "clips" / "c1.mp4")], check=True)     # 3.0 s to its last frame
            def fit(beat, **kw):
                seg, probs = {"id": "p", "type": "panel", "t0": 0.0, "t1": beat, "data": dict({"clip": "clips/c1.mp4", "clip_fit": True}, **kw)}, []
                longform.clip_prepare(film, seg, "16x9", probs)
                return seg["data"], probs
            dd, probs = fit(4.0)
            self.assertEqual((dd["clip_speed"], dd["clip_start"], probs), (0.75, 0.0, []))
            sch = dd["clip_sched"]
            self.assertEqual(sch[-1][0], 48); self.assertLess(sch[-3][0], 48)                # the last frame arrives at the cut
            dd, probs = fit(2.0)
            self.assertEqual((dd["clip_speed"], dd["clip_start"]), (0.75, 1.5))               # a long clip plays its end
            self.assertEqual(dd["clip_sched"][-1][0], 48); self.assertLess(dd["clip_sched"][-3][0], 48)
            self.assertEqual(dd["clip_sched"][0][0], 24)
            dd, probs = fit(8.0)
            self.assertEqual(dd["clip_speed"], 0.45)
            self.assertTrue(any("ends 1.3 s early" in p for p in probs), probs)              # under the floor: said, and holds
            dd, probs = fit(4.0, clip_speed=0.5)
            self.assertTrue(any("drop one or the other" in p for p in probs))
            dd, probs = fit(4.0, fx=[{"type": "rain"}])
            self.assertTrue(any("rain over a clip" in p for p in probs))
            dd, probs = fit(4.0, fx=[{"type": "rain", "over_clip": True}])
            self.assertEqual(probs, [])

    def test_every_frame_shows_its_clip_frame(self):
        import io, json, longform, render
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            film = Path(d); (film / "clips").mkdir()
            # each clip frame a different flat grey, so a frame drawn stale or blank shows in its mean level
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:size=832x480:rate=16,format=yuv444p,"
                            "geq=lum='20+N*4':cb=128:cr=128", "-frames:v", "49", "-pix_fmt", "yuv420p", "-crf", "1",
                            str(film / "clips" / "c1.mp4")], check=True)
            shutil.copy(longform.TEMPLATE, film / "film.html")
            for f in longform.ENGINE_FILES:
                shutil.copy(longform.HERE / f, film / f)
            for kw in ({}, {"clip_retime": "hold"}):
                seg, probs = self.seg(**kw), []
                longform.clip_prepare(film, seg, "16x9", probs)
                self.assertEqual(probs, [])
                cf, cs = seg["data"]["clip_frames"], seg["data"]["clip_sched"]
                self.assertEqual((cf["n"], cf["fps"]), (49, 16.0))
                lv = [float(np.asarray(Image.open(film / cf["dir"] / f"{i:04d}.jpg").convert("L")).mean()) for i in range(cf["n"])]
                (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps({"duration": 4.0, "segments": [seg], "grade": {"vignette": 0}}) + ";\n")
                bad = []
                with render.Film(film) as f:
                    for k in range(0, 120, 3):
                        f.seek(k / 30)
                        got = float(np.asarray(Image.open(io.BytesIO(f.page.screenshot(type="jpeg", quality=95))).convert("L")).mean())
                        a_, b_, w = cs[k]
                        if abs(got - (lv[a_] * (1 - w) + lv[b_] * w)) > 1.5:
                            bad.append((k, round(got, 1)))
                    self.assertFalse(f.errors)
                self.assertEqual(bad, [], kw)


class ReviewDashboard(unittest.TestCase):
    def test_lan_server_takes_a_burst_of_connections(self):
        # A page load opens a dozen connections at once; past the listen backlog macOS resets them over Wi-Fi and the
        # phone got a black page (2026-11-26). Connect 30 before the server accepts any: all must queue, then answer.
        import preview, socket as so, threading
        with tempfile.TemporaryDirectory() as d:
            film = self.film(d)
            srv = preview.Server(("0.0.0.0", 0), preview.make_handler(film, "K"))
            port, socks, t = srv.server_address[1], [], None
            try:
                for _ in range(30):
                    c = so.socket(); c.settimeout(2)
                    c.connect(("127.0.0.1", port)); socks.append(c)
                t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
                for c in socks:
                    c.sendall(b"GET /?k=K HTTP/1.0\r\nHost: x\r\n\r\n")
                codes = [c.recv(64).split(b" ")[1] for c in socks]
            finally:
                for c in socks:
                    c.close()
                if t:
                    srv.shutdown()
                srv.server_close()
        self.assertEqual(codes, [b"200"] * 30)

    def test_lan_urls_use_bonjour_name_and_ip(self):
        import preview
        answers = {("scutil", "--get", "LocalHostName"): "Studio-Mac", ("ipconfig", "getifaddr", "en0"): "192.168.1.23"}
        orig = preview._run
        preview._run = lambda *c: answers.get(c, "")
        try:
            urls = [u for _, u in preview.lan_urls(4173, "K")]
        finally:
            preview._run = orig
        self.assertEqual(urls, ["http://Studio-Mac.local:4173/?k=K", "http://192.168.1.23:4173/?k=K"])
        answers[("tailscale", "ip", "-4")] = "100.64.0.7\n"
        preview._run = lambda *c: answers.get(c, "")
        try:
            urls = [u for _, u in preview.lan_urls(4173, "K")]
        finally:
            preview._run = orig
        self.assertEqual(urls[0], "http://100.64.0.7:4173/?k=K")

    def test_dashboard_has_phone_viewport(self):
        # Without it Android Chrome lays the page out 980 px wide, the phone layout never applies and the film is a
        # thumbnail (2026-11-26).
        html = (Path(__file__).parent / "preview.html").read_text()
        self.assertRegex(html, r'<meta name="viewport" content="width=device-width, initial-scale=1">')

    """The owner picks among takes and locks sections in the studio dashboard; agents never do (2026-11-26)."""
    def film(self, d):
        import json, longform
        from PIL import Image
        film = Path(d) / "ep"; (film / "panels").mkdir(parents=True)
        for seed, c in ((7, (200, 60, 60)), (19, (60, 200, 60))):
            Image.new("RGB", (1920, 1080), c).save(film / "panels" / f"m1c_s{seed}.png")
        Image.new("RGB", (1920, 1080), (90, 90, 90)).save(film / "panels" / "solo.png")
        segs = [{"id": "a", "type": "panel", "chapter": "Cold open", "data": {"image": "panels/m1c_s7.png", "camera": "hold"}},
                {"id": "b", "type": "panel", "chapter": "Month one", "data": {"image": "panels/solo.png", "camera": "hold"}}]
        (film / "script.json").write_text(json.dumps({"title": "t", "segments": segs}))
        tl = {"duration": 4.0, "grade": {"vignette": 0}, "chapters": [{"title": "Cold open", "t0": 0.0, "t1": 2.0}, {"title": "Month one", "t0": 2.0, "t1": 4.0}],
              "segments": [dict(segs[0], t0=0.0, t1=2.0), dict(segs[1], t0=2.0, t1=4.0)]}
        (film / "timeline.js").write_text("window.TIMELINE = " + json.dumps(tl) + ";\n")
        shutil.copy(longform.TEMPLATE, film / "film.html")
        for f in longform.ENGINE_FILES:
            shutil.copy(longform.HERE / f, film / f)
        return film

    @unittest.skipUnless(os.environ.get("MS_CHROMIUM"), "needs a browser (MS_CHROMIUM)")
    def test_takes_view_filters_sort_new_posters_and_ack(self):
        import json, threading, time as tm, urllib.request, preview
        from http.server import ThreadingHTTPServer
        from playwright.sync_api import sync_playwright
        with tempfile.TemporaryDirectory() as d:
            film = self.film(d); (film / "clips").mkdir()
            for seed in (1, 2):
                subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"color=c={'red' if seed == 1 else 'blue'}:s=320x180:d=1",
                                "-pix_fmt", "yuv420p", str(film / "clips" / f"m2_s{seed}.mp4")], check=True)
            sc = json.loads((film / "script.json").read_text())
            sc["segments"][0]["data"]["shot"] = "CU"
            sc["segments"][1]["data"]["clip"] = "clips/m2_s1.mp4"
            (film / "script.json").write_text(json.dumps(sc))
            os.utime(film / "panels" / "m1c_s7.png", (1e9, 1e9)); os.utime(film / "panels" / "m1c_s19.png", (1e9, 1e9))   # the stills are old
            import gate
            for seed in (1, 2):                 # new takes reach the view once checked (rule 16.4)
                gate.record(film, f"clips/m2_s{seed}.mp4", "Does the clip hold one shot?", True)
            r = preview.review(film)
            g = {x["key"]: x for x in r["groups"]}
            self.assertEqual((g["m1c"]["framing"], g["m2"]["kind"]), ("CU", "clip"))
            self.assertGreater(g["m2"]["updated"], g["m1c"]["updated"])
            self.assertTrue(preview.poster(film, "clips/m2_s2.mp4").read_bytes()[:2] == b"\xff\xd8")   # a JPEG of the first frame
            self.assertIsNone(preview.poster(film, "../outside.mp4"))
            srv = ThreadingHTTPServer(("127.0.0.1", 0), preview.make_handler(film, None))
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            url = f"http://127.0.0.1:{srv.server_address[1]}"
            with sync_playwright() as p:
                b = p.chromium.launch(executable_path=os.environ["MS_CHROMIUM"])
                pg = b.new_page(viewport={"width": 1280, "height": 800})
                pg.goto(url); pg.wait_for_selector("#takesBtn:not([hidden])")
                self.assertIn("4 new", pg.inner_text("#takesBtn"))
                pg.click("#takesBtn")
                self.assertEqual(pg.locator(".grp h3 b, .grp h3").first.inner_text().split()[0], "m2")      # newest first
                self.assertTrue(pg.locator("video").first.get_attribute("poster").startswith("/api/poster"))
                self.assertIn("?v=", pg.locator(".take img").first.get_attribute("src"))
                pg.select_option("#tkKind", "image")
                self.assertEqual(pg.locator(".grp").count(), 1); self.assertIn("1 of 2", pg.inner_text(".tcount"))
                pg.select_option("#tkKind", "all"); pg.select_option("#tkSort", "film")
                self.assertTrue(pg.locator(".grp h3").first.inner_text().startswith("m1c"))
                pg.click("#takesSeen")
                self.assertEqual(pg.locator(".newb").count(), 0)
                pg.reload(); pg.wait_for_selector("#takesBtn:not([hidden])")
                self.assertNotIn("new", pg.inner_text("#takesBtn"))                          # seen is remembered
                pg.click("#takesBtn"); self.assertEqual(pg.input_value("#tkSort"), "film")    # and so is the sort
                # a comment, then an agent picks it up: the card says so while the note stays open
                pg.locator(".take").first.locator("textarea").fill("hand truck floats")
                pg.locator(".take").first.locator("textarea").press("Enter")
                pg.wait_for_selector(".take .tnote")
                nid = json.loads((film / "notes.json").read_text())["notes"][-1]["id"]
                preview.ack_note(film, nid, "redrawing as seed 5")
                pg.wait_for_selector(".take .tnote.prog .tans", timeout=8000)
                self.assertEqual(pg.inner_text(".take .tnote.prog .tans"), "seen · redrawing as seed 5")
                b.close()
            srv.shutdown()
            self.assertEqual(json.loads((film / "notes.json").read_text())["notes"][-1]["status"], "open")

    def test_upscale_covers_every_take(self):
        # the script names one take; a pick of another needs its upscale too (timeline stopped on each pick, 2026-11-26)
        import json, longform
        with tempfile.TemporaryDirectory() as d:
            film = self.film(d)
            segs = json.loads((film / "script.json").read_text())["segments"]
            self.assertEqual(longform.upscale_list(film, segs), ["panels/m1c_s19.png", "panels/m1c_s7.png", "panels/solo.png"])

    def test_groups_states_picks_and_lock(self):
        import json, review_state
        with tempfile.TemporaryDirectory() as d:
            film = self.film(d)
            sc = json.loads((film / "script.json").read_text())
            g = review_state.take_groups(film, sc)
            self.assertEqual([(x["key"], [t["seed"] for t in x["takes"]], x["pick"]) for x in g], [("m1c", [7, 19], None)])
            st = {x["title"]: x["state"] for x in review_state.sections(film)}
            self.assertEqual(st, {"Cold open": "takes", "Month one": "ready"})
            self.assertEqual(review_state.sections(film, [{"t": 2.5, "status": "open"}])[1]["state"], "review")
            review_state.save_picks(film, {"m1c": 19})
            data, probs = dict(sc["segments"][0]["data"]), []
            review_state.apply_picks(film, data, review_state.load_picks(film), probs, "a")
            self.assertEqual((data["image"], probs), ("panels/m1c_s19.png", []))
            self.assertEqual(review_state.sections(film)[0]["state"], "ready")
            review_state.set_lock(film, "Cold open", True)
            self.assertEqual(review_state.sections(film)[0]["state"], "locked")
            review_state.save_picks(film, {"m1c": 7})                    # a change after his lock shows
            self.assertTrue(review_state.sections(film)[0].get("stale"))

    @unittest.skipUnless(os.environ.get("MS_CHROMIUM"), "needs a browser (MS_CHROMIUM)")
    def test_dashboard_clicks_and_lan_key(self):
        import json, threading, urllib.request, urllib.error, preview
        from http.server import ThreadingHTTPServer
        from playwright.sync_api import sync_playwright
        import gate
        with tempfile.TemporaryDirectory() as d:
            film = self.film(d)
            takes = [t for g in preview.review(film)["groups"] for t in g["takes"]]
            self.assertTrue(takes and all(t["gate"] == "unchecked" for t in takes))      # new takes wait for the check
            for t in takes:
                gate.record(film, t["path"], "Does it answer the note?", True)
            self.assertTrue(all(t["gate"] == "pass" for g in preview.review(film)["groups"] for t in g["takes"]))
            srv = ThreadingHTTPServer(("127.0.0.1", 0), preview.make_handler(film, "sekret"))
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            url = f"http://127.0.0.1:{srv.server_address[1]}"
            with self.assertRaises(urllib.error.HTTPError) as e:          # no key, no film
                urllib.request.urlopen(url + "/api/review")
            self.assertEqual(e.exception.code, 403)
            self.assertNotIn("sekret", str(e.exception.headers))
            with sync_playwright() as p:
                b = p.chromium.launch(executable_path=os.environ["MS_CHROMIUM"])
                pg = b.new_page(viewport={"width": 1280, "height": 800})
                pg.goto(url + "/?k=sekret")
                pg.wait_for_selector("#takesBtn:not([hidden])")
                self.assertIn("1 to pick", pg.inner_text("#takesBtn"))
                pg.click("#takesBtn")
                pg.locator(".take").nth(1).locator("button.use").click()
                pg.wait_for_function("document.querySelector('#takesBtn').textContent.includes('all picked')")
                self.assertEqual(json.loads((film / "picks.json").read_text()), {"m1c": 19})
                # a comment on the second take, typed in its card while the view polls and rebuilds
                box = pg.locator(".take").nth(1).locator("textarea")
                box.click(); box.type("the hand truck is floating")
                pg.evaluate("window.__mark = document.querySelector('.take')")   # the view as built now
                urllib.request.urlopen(urllib.request.Request(url + "/api/notes?k=sekret", data=json.dumps({"t": 0.5, "text": "elsewhere"}).encode(),
                                                              headers={"Content-Type": "application/json"}))
                pg.wait_for_function("document.querySelector('.take') !== window.__mark", timeout=8000)   # rebuilt by the poll
                ta = pg.locator(".take").nth(1).locator("textarea")
                self.assertEqual(ta.input_value(), "the hand truck is floating")
                self.assertTrue(ta.evaluate("el => el === document.activeElement"))
                pg.locator(".take").nth(1).locator("textarea").press("Enter")
                pg.wait_for_selector(".take .tnote")
                note = json.loads((film / "notes.json").read_text())["notes"][-1]
                self.assertEqual((note["take"]["label"], note["take"]["seed"], note["still"]), ("m1c · take 2", 19, "panels/m1c_s19.png"))
                self.assertIn("m1c · take 2", pg.inner_text("#notes"))
                pg.click("#takesClose")
                pg.wait_for_selector("#lockBtn")
                pg.click("#lockBtn")
                pg.wait_for_function("document.querySelector('#section .st').textContent.includes('locked')")
                self.assertIn("Cold open", json.loads((film / "review" / "state.json").read_text())["locked"])
                b.close()
            req = urllib.request.Request(url + "/api/notes?k=sekret", data=json.dumps({"text": "x", "take": {"key": "m1c", "seed": 3}}).encode(),
                                         headers={"Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as e:          # a seed the group doesn't have
                urllib.request.urlopen(req)
            self.assertEqual(e.exception.code, 400)
            srv.shutdown()
            import contextlib, io
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                preview.list_notes(film)
            self.assertIn("on m1c · take 2 (seed 19", out.getvalue())          # agents read which take it is


class BeatLandings(unittest.TestCase):
    """cuecheck: beats timed on the finished video (render-layer test, 2026-11-28)."""

    def film(self, d, box_at, cue_at, sound_delay_ms=0):
        # a grey 4 s film where a white box appears at each time in box_at; timeline cues sit at cue_at
        f = Path(d, "f"); (f / "out").mkdir(parents=True)
        draw = ",".join(f"drawbox=x={40 + 300 * k}:y=40:w=200:h=160:color=white:t=fill:enable='gte(t,{t})'" for k, t in enumerate(box_at))
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=640x360:r=30:d=4",
                        "-vf", draw, "-pix_fmt", "yuv420p", str(f / "out" / "silent.mp4")], check=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anoisesrc=d=4:r=48000:seed=7",
                        str(f / "out" / "mix.wav")], check=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(f / "out" / "silent.mp4"), "-i", str(f / "out" / "mix.wav"),
                        "-af", f"adelay={sound_delay_ms}", "-c:v", "copy", "-shortest", str(f / "out" / "f.mp4")], check=True)
        tl = {"duration": 4, "segments": [{"id": "a", "type": "panel", "t0": 0, "t1": 4}],
              "cues": [{"seg": "a", "at": f"w{k}", "t": t} for k, t in enumerate(cue_at)]}
        (f / "timeline.js").write_text("window.TIMELINE = " + json.dumps(tl) + ";")
        return f

    def test_on_time_late_and_sound(self):
        import cuecheck
        with tempfile.TemporaryDirectory() as d:
            lines, probs = cuecheck.run(self.film(d, [1.0, 2.5], [1.0, 2.5]))
            self.assertEqual(probs, [], lines)
            self.assertIn("2 measured", lines[0])
        with tempfile.TemporaryDirectory() as d:
            lines, probs = cuecheck.run(self.film(d, [1.4, 2.5], [1.0, 2.5]))        # the first box lands 0.4 s late
            self.assertEqual(len(probs), 1, probs); self.assertIn("after its word", probs[0]); self.assertIn("\"w0\"", probs[0])
        with tempfile.TemporaryDirectory() as d:
            lines, probs = cuecheck.run(self.film(d, [1.0, 2.5], [1.0, 2.5], sound_delay_ms=120))
            self.assertEqual(len(probs), 1, probs); self.assertIn("sound is 120 ms late", probs[0])
        with tempfile.TemporaryDirectory() as d:                                       # nothing changes near a cue
            lines, probs = cuecheck.run(self.film(d, [1.0], [1.0, 3.0]))
            self.assertEqual(probs, []); self.assertIn("1 with no clear change", lines[0])


class StillReviewRules(unittest.TestCase):
    """The owner's review of the first 85 ChatGPT stills (picture-truth.md, rule 12), as lint and prompt defaults."""
    def lint(self, p, shot="MED"):
        import panelqa
        return [w for _, ws in panelqa.lint([{"id": 1, "prompt": p, "shot": shot}], route="codex") for w in ws]

    def test_lint(self):
        self.assertIn("money", self.lint("A man counts cash at a folding table.")[0])
        self.assertTrue(self.lint("Close-up of his hands unlatching the door.", "CU"))
        self.assertFalse(self.lint("Waist-up from behind, his hands unlatching the door.", "CU"))
        self.assertTrue(self.lint("A porta-potty tipped on its side in the mud."))
        self.assertFalse(self.lint("A porta-potty tipped on its side, door shut and latched."))
        self.assertTrue(self.lint("The tank hidden under the floor."))
        self.assertFalse(self.lint("The tank hidden under the floor shows as a faint see-through outline."))
        # the open models' limits aren't the codex route's
        self.assertFalse(self.lint("A yard with no trucks. " + "word " * 120))

    ANAT = r"""
import os
st = os.environ["ANAT_STATE"]; n = int(open(st).read()) if os.path.exists(st) else 0
open(st, "w").write(str(n + 1)); print("Looking.\n" + os.environ["ANAT_SAYS"].split("|")[n])
"""

    def test_prompt_default(self):
        import codex_stills
        p = codex_stills.ask({"prompt": "A man at a gate."}, "ink.", True)
        self.assertIn(codex_stills.HANDS, p); self.assertIn("no impossible twist", p); self.assertNotIn("hands only", p)

    def test_anatomy_fail_rerolls_and_logs(self):
        import longform
        with tempfile.TemporaryDirectory() as d:
            Path(d, "anat.py").write_text(self.ANAT)
            shots = {"style": "ink.", "shots": [{"id": "a", "prompt": "A man counts cash."}, {"id": "b", "prompt": "An empty yard."}]}
            env = {"ANATOMY_CHECK_CMD": f"python3 {d}/anat.py {{path}}", "ANAT_STATE": f"{d}/an",
                   "ANAT_SAYS": "FAIL: head turned past the shoulder|PASS"}
            r = CodexEdit.run_it(self, d, "1536x1024 1536x1024 1536x1024", "", shots=shots, env_extra=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(Path(d, "n").read_text(), "3")              # a twice (one anatomy re-roll), b once
            self.assertEqual(Path(d, "an").read_text(), "2")             # b has no people: not judged
            log = [x.split("\t")[1:4] for x in Path(d, "out", "judge.tsv").read_text().splitlines()]
            self.assertEqual(log, [["a", "1", "FAIL"], ["a", "2", "PASS"]])
            self.assertEqual(longform.png_text(Path(d, "out", "a.png"))["motion-studio-anatomy"], "PASS")
            self.assertIn("a\tlint: money in the picture", Path(d, "out", "review.tsv").read_text())

    def test_anatomy_budget_and_last_try_keep(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "anat.py").write_text(self.ANAT)
            shots = {"style": "ink.", "shots": [{"id": "a", "prompt": "A man waves."}]}
            env = {"ANATOMY_CHECK_CMD": f"python3 {d}/anat.py {{path}}", "ANAT_STATE": f"{d}/an", "ANAT_SAYS": "FAIL: three hands|FAIL: bent knee"}
            r = CodexEdit.run_it(self, d, "1536x1024 1536x1024", "", "--anatomy-rerolls", "1", shots=shots, env_extra=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(Path(d, "n").read_text(), "2")              # the budget of 1 re-roll, then kept
            self.assertTrue(Path(d, "out", "a.png").exists())
            self.assertIn("a\tanatomy FAIL on its last try (bent knee)", Path(d, "out", "review.tsv").read_text())
            self.assertIn("\ta\t2\tKEPT\tbent knee", Path(d, "out", "judge.tsv").read_text())

    def test_colab_route_fix_moves_the_seed(self):
        import json
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            Path(d, "anat.py").write_text(self.ANAT); st = Path(d, "stills"); st.mkdir()
            for i in ("s1", "s2", "s3"):
                Image.new("RGB", (8, 8)).save(st / f"{i}.png")
            jobs = Path(d, "jobs.json")
            jobs.write_text(json.dumps([{"id": "s1", "prompt": "A woman lifts a lid.", "seed": 7},
                                        {"id": "s2", "prompt": "A truck at dawn.", "seed": 7},
                                        {"id": "s3", "prompt": "His hands on a hose.", "seed": 9}]))
            env = dict(os.environ, ANATOMY_CHECK_CMD=f"python3 {d}/anat.py {{path}}", ANAT_STATE=f"{d}/an", ANAT_SAYS="FAIL: fused fingers|PASS")
            r = subprocess.run(["python3", str(Path(__file__).with_name("anatomy.py")), str(st), "--jobs", str(jobs), "--fix"],
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertFalse((st / "s1.png").exists()); self.assertTrue((st / "s3.png").exists())
            self.assertEqual([j["seed"] for j in json.loads(jobs.read_text())], [1007, 7, 9])
            self.assertEqual(Path(d, "an").read_text(), "2")              # the truck has no people

    def test_wan_clips_judged_on_their_last_frame(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            Path(d, "anat.py").write_text(self.ANAT); st = Path(d, "clips"); st.mkdir()
            for i in ("w1", "w2"):
                subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=64x48:rate=16", "-frames:v", "8",
                                "-pix_fmt", "yuv420p", str(st / f"{i}.mp4")], check=True)
            jobs = Path(d, "jobs.json")
            jobs.write_text(json.dumps([{"id": "w1", "image": "a.png", "motion": "an empty yard", "seed": 1},
                                        {"id": "w2", "image": "a.png", "motion": "an empty yard", "seed": 2}]))
            env = dict(os.environ, ANATOMY_CLIP_CMD=f"python3 {d}/anat.py {{first}} {{path}}", ANAT_STATE=f"{d}/an",
                       ANAT_SAYS="FAIL: a face behind the fence|PASS")
            r = subprocess.run(["python3", str(Path(__file__).with_name("anatomy.py")), str(st), "--jobs", str(jobs), "--fix"],
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertFalse((st / "w1.mp4").exists()); self.assertTrue((st / "w2.mp4").exists())
            self.assertTrue((st / ".judge" / "w2_last.png").exists())
            self.assertEqual([j["seed"] for j in json.loads(jobs.read_text())], [1001, 2])
            self.assertIn("a face behind the fence", (st / "judge.tsv").read_text())

    FAKE, JUDGE = CodexStills.FAKE, CodexStills.JUDGE
