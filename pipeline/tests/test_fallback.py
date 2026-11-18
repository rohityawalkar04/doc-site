"""Tests for persistent provider cooldown state (stdlib unittest only)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reelforge import fallback


class ProviderHealthTests(unittest.TestCase):
    def test_provider_is_available_by_default(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"REELFORGE_ROOT": tmp}):
            self.assertTrue(fallback.available("cloudflare"))

    def test_disable_and_enable_provider(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"REELFORGE_ROOT": tmp}):
            fallback.disable("cloudflare", "quota exhausted", seconds=3600)
            self.assertFalse(fallback.available("cloudflare"))
            saved = json.loads((Path(tmp) / "work" / "provider-health.json").read_text())
            self.assertIn("quota exhausted", saved["cloudflare"]["reason"])
            fallback.enable("cloudflare")
            self.assertTrue(fallback.available("cloudflare"))

    def test_expired_cooldown_is_available(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"REELFORGE_ROOT": tmp}):
            path = Path(tmp) / "work" / "provider-health.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"pollinations": {"disabled_until": 1}}))
            self.assertTrue(fallback.available("pollinations"))

    def test_malformed_state_does_not_block_provider(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"REELFORGE_ROOT": tmp}):
            path = Path(tmp) / "work" / "provider-health.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"local_sd": {"disabled_until": "not-a-time"}}))
            self.assertTrue(fallback.available("local_sd"))


if __name__ == "__main__":
    unittest.main()
