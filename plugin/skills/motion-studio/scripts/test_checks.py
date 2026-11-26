"""Tests for motioncheck.py, overlapcheck.py and readcheck.py: python3 -m unittest test_checks (from scripts/). Needs ffmpeg."""
import json
import os
import shutil
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
            self.assertEqual(r.returncode, 1)
            self.assertFalse(Path(d, "out", "a.png").exists()); self.assertTrue(Path(d, "out", "b.png").exists())
            self.assertIn("a\tno usable image in 3 tries", Path(d, "out", "review.tsv").read_text())
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
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)          # z has no base
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
        with tempfile.TemporaryDirectory() as d:
            film = self.film(d)
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
