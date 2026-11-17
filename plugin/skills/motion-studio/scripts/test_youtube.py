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


if __name__ == "__main__":
    unittest.main()


class Stop(Exception):
    """Raised by the stubbed check_channel: the upload got past the pre-upload check."""


def upload(tmp, check=None, package=None, env=None):
    """cmd_upload up to its first call to YouTube (check_channel, stubbed to raise Stop)."""
    f = tmp / "film.mp4"
    f.write_bytes(b"x")
    a = types.SimpleNamespace(file=str(f), privacy="private", yes_public=False, pre_upload_check=check, package=package)
    def stop():
        raise Stop
    with mock.patch.object(youtube, "check_channel", stop), mock.patch.object(youtube, "api", mock.Mock(side_effect=AssertionError)), \
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

    def test_package_without_a_check_is_refused(self):
        with self.assertRaises(SystemExit):
            upload(self.tmp, package="pkg")
