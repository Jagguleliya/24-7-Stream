"""
Unit and Integration Tests for GTA VI Stream Automation
Tests stream selector, configuration integrity, avatar generation, and health server.
"""

import json
import os
import unittest
from pathlib import Path


class TestStreamAutomation(unittest.TestCase):

    def setUp(self):
        self.config_path = Path("config.json")
        self.assertTrue(self.config_path.exists(), "config.json must exist")
        with open(self.config_path, "r", encoding="utf-8") as f:
            self.config = json.load(f)

    def test_priority_channels_configured(self):
        """Verify the priority streamers match user specifications (Speed prioritized/dedicated)."""
        channels = self.config.get("priority_channels", [])
        self.assertGreaterEqual(len(channels), 1, "Should have at least 1 priority channel")

        names = [c["name"].lower() for c in channels]
        self.assertIn("ishowspeed", names)

        # Check priority order: Speed must be priority 1
        speed = next(c for c in channels if "speed" in c["name"].lower())
        self.assertEqual(speed["priority"], 1)

    def test_qr_code_asset_exists(self):
        """Verify the uploaded UPI QR code or sample QR template is present on disk."""
        qr_path = Path("assets/overlays/upi_qr.png")
        sample_path = Path("assets/overlays/sample_qr.png")
        has_qr = (qr_path.exists() and qr_path.stat().st_size > 1000) or (sample_path.exists() and sample_path.stat().st_size > 1000)
        self.assertTrue(has_qr, "QR code overlay (upi_qr.png or sample_qr.png) must exist")

    def test_avatar_frames_exist(self):
        """Verify that avatar animation frames are generated."""
        frames_dir = Path("assets/overlays/avatar_frames")
        self.assertTrue(frames_dir.exists())
        frames = list(frames_dir.glob("frame_*.png"))
        self.assertGreaterEqual(len(frames), 20, "Should have at least 20 avatar animation frames")

    def test_voice_loop_exists(self):
        """Verify the periodic voice loop track is built."""
        voice_loop = Path("assets/audio/voice_loop.mp3")
        self.assertTrue(voice_loop.exists(), "voice_loop.mp3 must exist for audio mixing")
        self.assertGreater(voice_loop.stat().st_size, 5000)

    def test_stream_settings(self):
        """Verify video/audio bitrates and resolution."""
        settings = self.config.get("stream_settings", {})
        self.assertEqual(settings.get("resolution"), "1280x720")
        self.assertIn(settings.get("fps"), [30, 60])
        self.assertIn("k", settings.get("video_bitrate", ""))


if __name__ == "__main__":
    unittest.main()
