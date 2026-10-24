"""Tests for youtube.py's status updates, with the API stubbed out: python3 -m unittest test_youtube (from scripts/)."""
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
