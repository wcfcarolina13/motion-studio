"""Tests for youtube.py's status updates and pre-upload check, with the API stubbed out: python3 -m unittest test_youtube (from scripts/)."""
import json
import types
import unittest
from unittest import mock

import youtube

# what videos.list?part=status returns: no containsSyntheticMedia, plus read-only fields
FETCHED = {"uploadStatus": "processed", "privacyStatus": "private", "license": "youtube", "embeddable": True,
           "publicStatsViewable": True, "madeForKids": False, "selfDeclaredMadeForKids": False}


def run(privacy, cfg, fetched=FETCHED, synthetic=False, not_synthetic=False, answer_synthetic=None):
    """cmd_set_privacy against a stub API; returns the status it PUT."""
    sent = {}

    def api(method, url, body=None, **_):
        if method == "GET":
            return 200, {}, json.dumps({"items": [{"status": dict(fetched)}]}).encode()
        sent.update(body["status"])
        back = dict(body["status"])
        if answer_synthetic is not None:
            back["containsSyntheticMedia"] = answer_synthetic
        return 200, {}, json.dumps({"status": back}).encode()

    a = types.SimpleNamespace(video_id="v1", privacy=privacy, yes_public=True, synthetic=synthetic, not_synthetic=not_synthetic)
    with mock.patch.object(youtube, "api", api), mock.patch.object(youtube, "check_channel", lambda: None), \
            mock.patch.object(youtube, "channel_cfg", lambda: cfg), mock.patch("builtins.print"):
        youtube.cmd_set_privacy(a)
    return sent


class SetPrivacy(unittest.TestCase):
    def test_public_keeps_the_channels_disclosure(self):
        st = run("public", {"synthetic": True})
        self.assertEqual(st["privacyStatus"], "public")
        self.assertIs(st["containsSyntheticMedia"], True)

    def test_flags_override_the_channel(self):
        self.assertIs(run("unlisted", {"synthetic": True}, not_synthetic=True)["containsSyntheticMedia"], False)
        self.assertIs(run("unlisted", {}, synthetic=True)["containsSyntheticMedia"], True)

    def test_unknown_disclosure_is_refused(self):
        with self.assertRaises(SystemExit):
            run("public", {})

    def test_a_returned_disclosure_is_kept(self):
        self.assertIs(run("public", {}, fetched=dict(FETCHED, containsSyntheticMedia=True))["containsSyntheticMedia"], True)

    def test_only_writable_fields_are_sent(self):
        st = run("public", {"synthetic": True}, fetched=dict(FETCHED, publishAt="2026-11-01T00:00:00Z"))
        self.assertFalse({"uploadStatus", "madeForKids", "publishAt"} & st.keys())
        self.assertIs(st["selfDeclaredMadeForKids"], False)

    def test_an_answer_that_dropped_the_disclosure_fails(self):
        with self.assertRaises(SystemExit):
            run("public", {"synthetic": True}, answer_synthetic=False)


class PolicyCheck(unittest.TestCase):
    GOOD = {"M1_new": "It follows one roadside crew through a whole night shift for the first time.",
            "M2_same_outcome": False, "M3_unnarrated_slideshow": False, "M4_ai_expert_advice": False,
            "M5_realistic_fake_events": False, "S1_title_thumbnail_deliver": True, "S1_payoff_at": "0:04",
            "S2_schemes_or_payouts": False, "D1_ai_disclosure": True, "D1_provenance_kept": True}

    def test_all_good_passes(self):
        import policycheck
        self.assertEqual(policycheck.problems(self.GOOD), [])

    def test_each_wrong_or_missing_answer_is_named(self):
        import policycheck
        bad = dict(self.GOOD, S2_schemes_or_payouts=True, D1_provenance_kept=None, S1_payoff_at="soon", M1_new="new stuff")
        found = policycheck.problems(bad)
        for k in ("S2_schemes_or_payouts", "D1_provenance_kept", "S1_payoff_at", "M1_new"):
            self.assertTrue(any(f.startswith(k) for f in found), (k, found))
        self.assertEqual(len(found), 4)

    def test_a_copied_sentence_fails(self):
        import policycheck
        found = policycheck.problems(self.GOOD, siblings=[{"M1_new": self.GOOD["M1_new"].upper()}])
        self.assertTrue(found and found[0].startswith("M1_new"))

    def test_a_misspelt_key_is_reported(self):
        import policycheck
        self.assertTrue(any("unknown" in f for f in policycheck.problems(dict(self.GOOD, S2_scheme=False))))


if __name__ == "__main__":
    unittest.main()


class Stop(Exception):
    """Raised by the stubbed check_channel: the upload got past the pre-upload check."""


def upload(tmp, check=None, package=None, env=None, cfg=None):
    """cmd_upload up to its first call to YouTube (check_channel, stubbed to raise Stop)."""
    f = tmp / "film.mp4"
    f.write_bytes(b"x")
    a = types.SimpleNamespace(file=str(f), privacy="private", yes_public=False, pre_upload_check=check, package=package)
    def stop():
        raise Stop
    with mock.patch.object(youtube, "check_channel", stop), mock.patch.object(youtube, "api", mock.Mock(side_effect=AssertionError)), \
            mock.patch.object(youtube, "channel_cfg", lambda: cfg or {}), \
            mock.patch.dict(youtube.os.environ, env or {}, clear=False), mock.patch("builtins.print"):
        if env is None:
            youtube.os.environ.pop("YT_PRE_UPLOAD_CHECK", None)
        youtube.cmd_upload(a)


class PreUploadCheck(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self._d = tempfile.TemporaryDirectory()
        self.tmp = Path(self._d.name)

    def tearDown(self):
        self._d.cleanup()

    def test_a_failing_check_refuses_before_any_api_call(self):
        with self.assertRaises(SystemExit) as e:
            upload(self.tmp, check="false")
        self.assertIn("pre-upload check failed (exit 1)", str(e.exception.code))

    def test_a_passing_check_lets_the_upload_go_on(self):
        with self.assertRaises(Stop):
            upload(self.tmp, check="true")

    def test_no_check_set_changes_nothing(self):
        with self.assertRaises(Stop):
            upload(self.tmp)

    def test_the_env_var_is_used(self):
        with self.assertRaises(SystemExit):
            upload(self.tmp, env={"YT_PRE_UPLOAD_CHECK": "false"})

    def test_the_command_output_is_the_reason_and_the_package_comes_last(self):
        cmd = "sh -c 'echo \"report missing in $1\"; exit 3' check"
        with self.assertRaises(SystemExit) as e:
            upload(self.tmp, check=cmd, package="pkg dir")
        self.assertEqual(e.exception.code, "refusing upload: report missing in pkg dir")

    def test_a_command_that_cannot_start_refuses(self):
        with self.assertRaises(SystemExit) as e:
            upload(self.tmp, check="no-such-command-here")
        self.assertIn("could not run", str(e.exception.code))

    def test_a_channel_list_runs_every_check_in_order(self):
        with self.assertRaises(Stop):
            upload(self.tmp, package="pkg", cfg={"pre_upload_check": ["true", "true"]})
        with self.assertRaises(SystemExit) as e:
            upload(self.tmp, package="pkg", cfg={"pre_upload_check": ["true", "sh -c 'echo second; exit 1' x"]})
        self.assertEqual(e.exception.code, "refusing upload: second")

    def test_the_policy_check_as_a_channel_check(self):
        import json, sys
        from pathlib import Path
        pkg = self.tmp / "ep1"; pkg.mkdir()
        check = f"{sys.executable} {Path(__file__).resolve().parent / 'policycheck.py'}"
        with self.assertRaises(SystemExit) as e:
            upload(self.tmp, package=str(pkg), cfg={"pre_upload_check": [check]})
        self.assertIn("no ", str(e.exception.code))
        good = {"M1_new": "It follows one roadside crew through a whole night shift for the first time.",
                "M2_same_outcome": False, "M3_unnarrated_slideshow": False, "M4_ai_expert_advice": False,
                "M5_realistic_fake_events": False, "S1_title_thumbnail_deliver": True, "S1_payoff_at": "0:04",
                "S2_schemes_or_payouts": False, "D1_ai_disclosure": True, "D1_provenance_kept": True}
        (pkg / "policy.json").write_text(json.dumps(good))
        with self.assertRaises(Stop):
            upload(self.tmp, package=str(pkg), cfg={"pre_upload_check": [check]})

    def test_the_channels_own_check_is_used(self):
        with self.assertRaises(SystemExit):
            upload(self.tmp, package="pkg", cfg={"pre_upload_check": "false"})
        with self.assertRaises(Stop):
            upload(self.tmp, package="pkg", cfg={"pre_upload_check": "true"})

    def test_the_cli_parses(self):
        import subprocess, sys
        r = subprocess.run([sys.executable, youtube.__file__, "upload", "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        self.assertIn("--package", r.stdout)

    def test_package_without_a_check_is_refused(self):
        with self.assertRaises(SystemExit):
            upload(self.tmp, package="pkg")
