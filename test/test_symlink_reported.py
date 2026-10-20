# -*- coding: utf-8 -*-
"""A symlink entry is reported, never materialised.

The bytes of a `n` entry are a path. Writing a real link from an untrusted archive is a traversal primitive:
the link points somewhere the operator can write, and the next thing written follows it. So the bytes are
written as a file and the target is recorded -- but **nothing said any of that**, which is the defect: the
entry looked like a small ordinary file, and the operator had no way to know they were holding a path.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
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


nano = load("nanodesu_link", "nanodesu.py")
tn = load("tn_link", HERE / "test_nanodesu.py")

TARGET = "..\\..\\Windows\\System32"
ENTRIES = [("escape", "n", TARGET.encode(), 0), ("data.txt", "b", b"hello", 0)]


class TestASymlinkIsReported(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.archive = tn.make_archive(self.dir / "a.exe", ENTRIES)

    def test_the_manifest_records_the_target(self):
        out = self.dir / "out"
        nano.extract(self.archive, out)
        meta = json.loads((out / "_archive_manifest.json").read_text(encoding="utf-8"))
        rows = {r["name"]: r for r in meta["entries"]}
        self.assertEqual(rows["escape"]["symlink_target"], TARGET)
        self.assertIsNone(rows["data.txt"]["symlink_target"],
                          "a non-link entry must not claim to be one")

    def test_the_link_is_not_created(self):
        """The bytes are written as a file. **Asserted on the filesystem, not on the manifest**: a manifest
        that says "not a link" while the filesystem holds one would be the worst of both."""
        out = self.dir / "out"
        nano.extract(self.archive, out)
        meta = json.loads((out / "_archive_manifest.json").read_text(encoding="utf-8"))
        row = next(r for r in meta["entries"] if r["name"] == "escape")
        written = out / row["safe_rel"]          # the name is normalised, so ask the manifest
        self.assertTrue(written.is_file())
        self.assertFalse(os.path.islink(str(written)),
                         "the extraction created a real symlink from untrusted bytes")
        self.assertEqual(written.read_bytes().decode().rstrip("\x00"), TARGET)

    def test_the_command_says_it_out_loud(self):
        out = self.dir / "out2"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = nano.main(["extract", str(self.archive), "-o", str(out)])
        self.assertEqual(rc, 0)
        text = buf.getvalue()
        self.assertIn("written as their target text, not as links", text)
        self.assertIn(TARGET, text)


if __name__ == "__main__":
    unittest.main()
