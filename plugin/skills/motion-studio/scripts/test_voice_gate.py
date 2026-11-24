"""Tests for the paid-voice gate: python3 -m unittest test_voice_gate (from scripts/)."""
import unittest

import longform


class PaidVoiceGate(unittest.TestCase):
    def test_free_engines_pass(self):
        for e in ("kokoro", "human", "piper"):
            self.assertIsNone(longform.paid_voice_block({}, {"engine": e}))

    def test_paid_engine_waits_for_sign_off(self):
        self.assertIn("paid voice", longform.paid_voice_block({}, {"engine": "elevenlabs"}))
        self.assertIn("paid voice", longform.paid_voice_block({"animation_signed_off": {"by": "owner"}}, {"engine": "ElevenLabs"}))
        self.assertIsNone(longform.paid_voice_block({"animation_signed_off": {"by": "owner", "on": "2026-11-24"}}, {"engine": "elevenlabs"}))


if __name__ == "__main__":
    unittest.main()
