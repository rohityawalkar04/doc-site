"""Regression tests for Shorts pacing and local-only visual integrity."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from reelforge import llm, visuals


class ShortsQualityTests(unittest.TestCase):
    def test_beat_selector_keeps_hook_tension_and_ending(self):
        topic = {
            "title": "A short test",
            "beats": [{"n": f"Line {i}", "v": f"visual {i}"} for i in range(13)],
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
            with self.assertRaisesRegex(RuntimeError, "required local AI image unavailable"):
                visuals.for_scene(cfg, None, 0, 3.0, "a clear subject", Path(tmp) / "scene.mp4")

    def test_non_ai_image_providers_are_rejected(self):
        cfg = {"visuals.provider": "openverse"}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "image search and stock pickers are removed"):
                visuals.for_scene(cfg, None, 0, 3.0, "a clear subject", Path(tmp) / "scene.mp4")

    def test_mflux_backend_is_the_only_generation_backend(self):
        cfg = {"visuals.ai_backend": "cloudflare"}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "only; remote image services are disabled"):
                visuals.generate_image(cfg, "a clear subject", 123, Path(tmp) / "image.jpg")

    def test_local_image_cache_is_verified_and_reused(self):
        cfg = {"visuals.provider": "ai_cartoon", "visuals.animate": False}
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            visuals, "_ai_cache", return_value=Path(tmp)
        ):
            image_path = Path(tmp) / "cached.jpg"
            Image.new("RGB", (256, 256), (30, 40, 50)).save(image_path, quality=98)
            with patch.object(visuals, "generate_image") as generate:
                # Use the actual image verification path; rendering is mocked.
                with patch.object(visuals, "from_image", return_value=Path(tmp) / "scene.mp4"):
                    path, source = visuals.for_scene(
                        cfg, type("Store", (), {"bump_visual": lambda *_: None})(),
                        0, 3.0, "cached scene", Path(tmp) / "scene.mp4"
                    )
            generate.assert_not_called()
            self.assertEqual(source.split(":")[0], "ai")


if __name__ == "__main__":
    unittest.main()
