from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class B8ExpansionTests(unittest.TestCase):
    def test_manifest_present(self):
        self.assertTrue((ROOT / 'config/journal-expansion-b8.json').exists())
