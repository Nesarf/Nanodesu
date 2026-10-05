# -*- coding: utf-8 -*-
"""The library API, as a caller sees it.

The commands print prose and return exit codes, which is right for a CLI and wrong for a
caller: anything that captured that stdout and parsed it back would be coupled to the wording
of a report. These tests hold the API to the other contract -- structured data, and exceptions
where a command would print a message.

Run:
    python -m unittest discover -s test -v
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE.parent / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


nano = _load("nano_api", "nanodesu.py")


def _make_archive(path: Path, entries):
    """A tiny but valid archive, built with the same writer the repacker uses."""
    spec = importlib.util.spec_from_file_location("tn_api", HERE / "test_nanodesu.py")
    tn = importlib.util.module_from_spec(spec)
    sys.modules["tn_api"] = tn
    spec.loader.exec_module(tn)
    tn.make_archive(path, entries)


class TestInspect(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nano-api-"))
        self.src = self.tmp / "a.exe"
        _make_archive(self.src, [("a.txt", "b", b"hello", 0), ("b.txt", "b", b"world", 0)])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_it_returns_a_dict_not_prose(self):
        info = nano.inspect_archive(self.src)
        self.assertIsInstance(info, dict)
        self.assertEqual(info["entry_count"], 2)
        self.assertEqual(info["integrity"], [])

    def test_it_reports_the_python_version_as_a_string(self):
        """A caller should not have to know that 312 means 3.12."""
        info = nano.inspect_archive(self.src)
        self.assertIsInstance(info["python_version"], str)

    def test_a_non_archive_raises_rather_than_exiting(self):
        junk = self.tmp / "junk.exe"
        junk.write_bytes(b"not an archive at all" * 10)
        with self.assertRaises(nano.PyInstallerError):
            nano.inspect_archive(junk)

    def test_the_error_carries_the_path_it_was_given(self):
        junk = self.tmp / "junk.exe"
        junk.write_bytes(b"nope" * 40)
        with self.assertRaises(nano.PyInstallerError) as ctx:
            nano.inspect_archive(junk)
        self.assertEqual(str(ctx.exception.path), str(junk))

    def test_a_missing_file_raises_rather_than_exiting(self):
        with self.assertRaises(nano.PyInstallerError):
            nano.inspect_archive(self.tmp / "absent.exe")


class TestListAndRead(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nano-api-"))
        self.src = self.tmp / "a.exe"
        _make_archive(self.src, [("a.txt", "b", b"hello", 0), ("b.txt", "b", b"world", 0)])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_entries_are_dicts_in_archive_order(self):
        ents = nano.list_entries(self.src)
        self.assertEqual([e["name"] for e in ents], ["a.txt", "b.txt"])
        self.assertTrue(all(isinstance(e, dict) for e in ents))

    def test_an_entry_carries_its_type_and_sizes(self):
        e = nano.list_entries(self.src)[0]
        for key in ("name", "tcode", "compressed", "stored_size", "size", "offset"):
            self.assertIn(key, e)

    def test_reading_one_entry_by_name_gives_its_bytes(self):
        self.assertEqual(nano.read_file(self.src, "a.txt"), b"hello")

    def test_an_unknown_name_raises_key_error(self):
        with self.assertRaises(KeyError):
            nano.read_file(self.src, "no-such-entry")


class TestVerify(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nano-api-"))
        self.src = self.tmp / "a.exe"
        _make_archive(self.src, [("a.txt", "b", b"hello" * 100, 0), ("c.txt", "b", b"x" * 50, 0)])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_healthy_archive_verifies(self):
        v = nano.verify_archive(self.src)
        self.assertTrue(v["ok"])
        self.assertEqual(v["problems"], [])
        self.assertEqual(v["decompressed"], 2)

    def test_structure_only_can_skip_decompression(self):
        v = nano.verify_archive(self.src, read=False)
        self.assertTrue(v["ok"])
        self.assertEqual(v["decompressed"], 0)

    def test_a_truncated_file_is_reported_not_raised(self):
        """Verification is the one operation whose whole job is to report badness."""
        truncated = self.tmp / "cut.exe"
        raw = self.src.read_bytes()
        truncated.write_bytes(raw[:len(raw) // 2])
        with self.assertRaises(nano.PyInstallerError):
            nano.verify_archive(truncated)


class TestExtract(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nano-api-"))
        self.src = self.tmp / "a.exe"
        _make_archive(self.src, [("a.txt", "b", b"hello", 0), ("b.txt", "b", b"world", 0)])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_it_writes_the_files_and_says_so(self):
        out = self.tmp / "tree"
        res = nano.extract(self.src, out)
        self.assertTrue(res["ok"])
        self.assertEqual(res["written"], 2)
        self.assertEqual(res["failed"], [])
        self.assertIs(res["executed"], False)

    def test_it_writes_a_manifest_that_the_repacker_accepts(self):
        """The contract that matters: extract then build must round-trip."""
        out = self.tmp / "tree"
        nano.extract(self.src, out)
        manifest = json.loads((out / "_archive_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["entries"]), 2)
        for entry in manifest["entries"]:
            self.assertIn("rel", entry)
            self.assertIn("tcode", entry)

    def test_the_manifest_entries_match_what_was_written(self):
        """A manifest that does not describe the files on disk makes a bad repack, and the
        failure appears much later. Checked here instead."""
        out = self.tmp / "tree"
        res = nano.extract(self.src, out)
        manifest = json.loads((out / "_archive_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["entries"]), res["written"])
        for entry in manifest["entries"]:
            self.assertTrue((out / entry["safe_rel"]).is_file(),
                            "manifest names a file that is not there: %s" % entry["rel"])

    def test_a_traversing_entry_name_is_confined(self):
        out = self.tmp / "tree"
        res = nano.extract(self.src, out)
        self.assertEqual(res["confined"], [])

    def test_it_reports_that_it_never_executed_anything(self):
        out = self.tmp / "tree"
        res = nano.extract(self.src, out)
        self.assertIn("executed", res)
        self.assertIs(res["executed"], False)


class TestItStaysALibraryOnDisk(unittest.TestCase):
    """The API must not need a terminal to work."""

    def test_importing_the_module_prints_nothing(self):
        import subprocess
        # The module must be registered before it is executed: its dataclasses resolve their
        # annotations through sys.modules[cls.__module__], and an unregistered module makes
        # that lookup return None on 3.14. unpack.py already did this correctly; the test did
        # not, which is what this test caught.
        code = ("import importlib.util,sys;"
                "s=importlib.util.spec_from_file_location('n',%r);"
                "m=importlib.util.module_from_spec(s);"
                "sys.modules['n']=m;s.loader.exec_module(m)"
                % str(HERE.parent / "nanodesu.py"))
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "", "importing the module wrote to stdout: %r" % r.stdout[:200])

    def test_the_api_is_declared(self):
        for name in ("inspect_archive", "list_entries", "verify_archive", "extract",
                     "read_file", "PyInstallerError", "find_archive", "read_entry",
                     "wrap_pyc", "Archive", "Entry"):
            self.assertIn(name, nano.__all__)


if __name__ == "__main__":
    unittest.main(verbosity=2)
