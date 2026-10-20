# -*- coding: utf-8 -*-
"""What bounded the extraction has to be in the result.

A cap a reader cannot see turns a limited result into an apparently complete one: `extract` reports what it
wrote, so the manifest is the only place "what stopped me" can live. These are safety ceilings rather than
calibrated thresholds -- which is why they are recorded as facts about the run, not as a rating with a
version.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))


def load(name, filename):
    path = Path(filename)
    if not path.is_absolute():
        path = ROOT / path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


nano = load("nanodesu_limits", "nanodesu.py")
tn = load("tn_limits", HERE / "test_nanodesu.py")


class TestTheCapsAreInTheResult(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.archive = tn.make_archive(self.dir / "a.exe", tn.SAMPLE)
        self.addCleanup(nano._set_allow_huge, False)

    def test_the_manifest_records_them(self):
        out = self.dir / "out"
        nano.extract(self.archive, out)
        meta = json.loads((out / "_archive_manifest.json").read_text(encoding="utf-8"))
        self.assertIn("limits", meta)
        caps = meta["limits"]["caps"]
        self.assertEqual(caps["max_entry_bytes"], nano.MAX_ENTRY_BYTES)
        self.assertEqual(caps["max_total_bytes"], nano.MAX_TOTAL_BYTES)
        self.assertEqual(caps["max_output_files"], nano.MAX_OUTPUT_FILES)
        self.assertEqual(caps["max_pyz_records"], nano.MAX_PYZ_RECORDS)

    def test_the_library_result_records_them(self):
        result = nano.extract(self.archive, self.dir / "out2")
        self.assertIn("limits", result)
        self.assertEqual(result["limits"]["caps"]["max_total_bytes"], nano.MAX_TOTAL_BYTES)

    def test_it_says_whether_the_ceiling_was_lifted(self):
        """A report that showed the configured caps while the run had lifted them would describe a run
        that did not happen, so the flag travels with the numbers."""
        nano._set_allow_huge(True)
        result = nano.extract(self.archive, self.dir / "out3")
        self.assertTrue(result["limits"]["allow_huge"])
        self.assertEqual(result["limits"]["caps"]["max_total_bytes"], nano.MAX_TOTAL_BYTES,
                         "the caps are reported as configured, and the flag says they were lifted")


if __name__ == "__main__":
    unittest.main()
