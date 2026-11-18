"""Regression tests for Shorts visual integrity and beat pacing."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reelforge import llm, visuals


class ShortsQualityTests(unittest.TestCase):
    def test_beat_selector_keeps_hook_tension_and_ending(self):
        topic = {
            "title": "A short test",
            "beats": [
                {"n": f"Line {i}", "v": f"visual {i}"} for i in range(13)
            ],
        }
        script = llm._from_beats(topic, {"script.beats_max_scenes": 9})
        scenes = script["scenes"]
        self.assertEqual(len(scenes), 9)
        self.assertEqual(scenes[0]["narration"], "Line 0")
        self.assertEqual(scenes[1]["narration"], "Line 1")
        self.assertEqual(scenes[-2]["narration"], "Line 11")
        self.assertEqual(scenes[-1]["narration"], "Line 12")

    def test_missing_ai_image_fails_instead_of_using_gradient(self):
        cfg = {
            "visuals.provider": "ai_cartoon",
            "visuals.require_ai_images": True,
            "visuals.animate": False,
        }
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            visuals, "_ai_cache", return_value=Path(tmp)
        ), patch.object(visuals, "generate_image", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "refusing to render a gradient"):
                visuals.for_scene(cfg, None, 0, 3.0, "a clear subject", Path(tmp) / "scene.mp4")

    def test_cloudflare_quota_exhaustion_is_fatal(self):
        cfg = {
            "visuals.ai_backend": "cloudflare",
            "visuals.cloud_no_local_fallback": True,
        }
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            visuals.fallback, "available", return_value=True
        ), patch.object(visuals.fallback, "disable"), patch.object(
            visuals, "_gen_cloudflare", side_effect=visuals.AiQuotaExhausted("quota exhausted")
        ):
            with self.assertRaises(visuals.AiQuotaExhausted):
                visuals.generate_image(cfg, "a clear subject", 123, Path(tmp) / "image.jpg")


if __name__ == "__main__":
    unittest.main()
