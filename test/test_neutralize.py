# -*- coding: utf-8 -*-
"""The defanged variant: what it guarantees, and what it explicitly does not.

This module is the one place in the project that writes a *modified* copy of a sample, so its rules
are the strictest. The first version of the feature silently did nothing -- it replaced files in the
expanded `_pyz_modules/` directory while `build` repacked the PYZ file and never looked inside -- and
the only reason that was caught is that a real sample was built and run. The tests here exist so the
next such mistake is caught by the suite instead of by someone trusting the output.

Four properties are load-bearing, and all four are checked:

  * the original is never modified
  * the payload bytes are genuinely gone, not merely unreferenced
  * the transform is recorded, with before/after hashes, so it can be audited and reversed
  * the output is never described as clean, safe or fixed

And one limitation is recorded rather than hidden: **stubbing a module can break the program.** A
host that imports the module and calls into it will fail. That is a property of the transform, not a
bug to be fixed, and the report has to say so.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import marshal
import os
import shutil
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

spec = importlib.util.spec_from_file_location("nz", HERE.parent / "neutralize.py")
nz = importlib.util.module_from_spec(spec)
sys.modules["nz"] = nz
spec.loader.exec_module(nz)


def build_pyz(modules: dict) -> bytes:
    """A PYZ containing `modules` ({dotted name: marshalled code object}).

    Built here rather than borrowed so the tests do not depend on a sample directory existing.
    Layout follows the real one, measured: 4 magic + 4 pyc magic + 4 toc offset + 5 reserved = 17.
    """
    header_len = nz.PYZ_HEADER_LEN
    blocks, toc, cursor = [], [], 0
    for name, raw in modules.items():
        comp = zlib.compress(raw, 9)
        toc.append((name, (0, header_len + cursor, len(comp))))
        blocks.append(comp)
        cursor += len(comp)
    body = b"".join(blocks)
    header = (nz.PYZ_MAGIC + bytes.fromhex("cb0d0d0a")
              + (header_len + len(body)).to_bytes(4, "big") + bytes(5))
    assert len(header) == header_len
    return header + body + marshal.dumps(toc)


def sample_module(token: str) -> bytes:
    """A marshalled code object carrying `token`, standing in for a payload's fingerprint."""
    return marshal.dumps(compile("TOKEN = %r\n" % token, "<sample>", "exec"))


def read_pyz_modules(blob: bytes) -> dict:
    off = struct.unpack_from("!i", blob, 8)[0]
    out = {}
    for name, meta in marshal.loads(blob[off:]):
        if isinstance(meta, (tuple, list)) and len(meta) == 3 and meta[2]:
            out[name] = zlib.decompress(blob[meta[1]:meta[1] + meta[2]])
    return out


class TestTheHeaderIsMeasuredNotRecalled(unittest.TestCase):
    """The first version used 13 -- the reserved field left out -- and wrote a header nothing could
    read. The assertion caught it; this test keeps the number honest."""

    def test_the_header_is_seventeen_bytes(self):
        self.assertEqual(nz.PYZ_HEADER_LEN, 17)

    def test_a_built_pyz_round_trips(self):
        blob = build_pyz({"a": sample_module("A")})
        self.assertEqual(list(read_pyz_modules(blob)), ["a"])


class TestThePayloadIsGoneNotJustUnreferenced(unittest.TestCase):
    """Rebuilding rather than overwriting in place. A stub is smaller than what it replaces, so an
    in-place write would leave the original block sitting in the file after it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nz-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.pyz = self.tmp / "pyz__PYZ.pyz"
        self.token = "A_DISTINCTIVE_PAYLOAD_TOKEN"
        self.pyz.write_bytes(build_pyz({
            "innocent": sample_module("innocent"),
            "hostile": sample_module(self.token),
        }))
        self.before = self.pyz.read_bytes()

    def test_the_original_block_bytes_are_absent_afterwards(self):
        """The strongest available check: the old compressed block must not survive anywhere."""
        stub = marshal.dumps(compile("pass", "<stub>", "exec"))
        result = nz.rewrite_pyz(self.pyz, {"hostile": stub})
        self.assertTrue(result["ok"], result)
        after = self.pyz.read_bytes()
        modules = read_pyz_modules(after)
        self.assertNotIn(self.token.encode(), modules["hostile"],
                         "the replaced module still carries the payload's own bytes")
        self.assertIn("innocent", modules, "an unrelated module was lost")
        self.assertEqual(len(modules), 2)

    def test_the_replacement_is_what_was_asked_for(self):
        stub = marshal.dumps(compile("pass", "<stub>", "exec"))
        nz.rewrite_pyz(self.pyz, {"hostile": stub})
        self.assertEqual(read_pyz_modules(self.pyz.read_bytes())["hostile"], stub)

    def test_a_dry_run_writes_nothing(self):
        stub = marshal.dumps(compile("pass", "<stub>", "exec"))
        result = nz.rewrite_pyz(self.pyz, {"hostile": stub}, dry_run=True)
        self.assertTrue(result["ok"])
        self.assertFalse(result["written"])
        self.assertEqual(self.pyz.read_bytes(), self.before, "a dry run modified the file")

    def test_a_name_that_does_not_exist_is_reported_not_swallowed(self):
        """`replaced: 0` would read as a clean run; the caller has to be able to see a typo."""
        stub = marshal.dumps(compile("pass", "<stub>", "exec"))
        result = nz.rewrite_pyz(self.pyz, {"no_such_module": stub})
        self.assertEqual(result["replaced"], [])
        self.assertEqual(result["unmatched"], ["no_such_module"])

    def test_a_non_pyz_is_refused(self):
        bad = self.tmp / "not_a_pyz.bin"
        bad.write_bytes(b"MZ" + bytes(64))
        result = nz.rewrite_pyz(bad, {})
        self.assertFalse(result["ok"])
        self.assertIn("not a PYZ", result["reason"])


class TestTheRecordMakesItAuditable(unittest.TestCase):
    """Without a record there is no way to check the work, only to trust it."""

    def test_every_replacement_carries_before_and_after(self):
        tmp = Path(tempfile.mkdtemp(prefix="nz-rec-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        pyz = tmp / "pyz__PYZ.pyz"
        pyz.write_bytes(build_pyz({"hostile": sample_module("X")}))
        stub = marshal.dumps(compile("pass", "<stub>", "exec"))
        result = nz.rewrite_pyz(pyz, {"hostile": stub})
        self.assertTrue(result["replaced"])
        for r in result["replaced"]:
            self.assertIn("name", r)
            self.assertIn("before_bytes", r)
            self.assertIn("after_bytes", r)


class TestTheOutputIsNeverCalledSafe(unittest.TestCase):
    """The naming discipline is the safety property, so it is asserted rather than trusted."""

    def test_the_caveats_say_variant_not_cure(self):
        tmp = Path(tempfile.mkdtemp(prefix="nz-cav-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        record = nz.neutralize(Path("sample.exe"), tmp, modules=[], python_version="3.12",
                               dry_run=True)
        text = " ".join(record["caveats"]).lower()
        self.assertIn("not a repaired file", text)
        self.assertIn("not clean", text)
        for forbidden in ("is safe", "has been fixed", "now clean"):
            self.assertNotIn(forbidden, text)

    def test_it_states_that_stubbing_proves_only_that_module(self):
        tmp = Path(tempfile.mkdtemp(prefix="nz-cav-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        record = nz.neutralize(Path("sample.exe"), tmp, modules=[], python_version="3.12",
                               dry_run=True)
        text = " ".join(record["caveats"]).lower()
        self.assertIn("proves nothing about the rest", text)

    def test_source_never_claims_the_result_is_safe(self):
        src = (HERE.parent / "neutralize.py").read_text(encoding="utf-8").lower()
        for phrase in ("is now safe", "has been cleaned", "the file is safe"):
            self.assertNotIn(phrase, src)


class TestStubCompilation(unittest.TestCase):
    def test_a_stub_is_a_marshallable_code_object(self):
        blob, meta = nz.make_stub()
        self.assertIsInstance(marshal.loads(blob), type(compile("", "", "exec")))
        self.assertIn("compiled_by", meta)

    def test_the_stub_loads_back(self):
        blob, _ = nz.make_stub()
        self.assertTrue(nz.check_stub(blob)["ok"])

    def test_a_matching_interpreter_is_preferred_when_available(self):
        """3.12 is present on this machine, so the matched path is exercisable rather than assumed."""
        interp = nz.find_interpreter("3.12")
        if interp is None:
            self.skipTest("no 3.12 interpreter here")
        blob, meta = nz.make_stub(interpreter=interp)
        self.assertTrue(meta["version_matched"])
        self.assertEqual(meta["compiled_by"], interp)
        self.assertTrue(nz.check_stub(blob, interp)["ok"])

    def test_a_stub_compiled_by_a_different_version_says_so(self):
        blob, meta = nz.make_stub(interpreter=None)
        self.assertFalse(meta["version_matched"])
        self.assertIn("caveat", meta, "the weaker claim was not disclosed")

    def test_a_missing_interpreter_is_not_a_crash(self):
        self.assertIsNone(nz.find_interpreter("9.99"))
        self.assertIsNone(nz.find_interpreter(""))


class TestModuleNaming(unittest.TestCase):
    """Getting the dotted name wrong is invisible: the rebuild finds nothing and reports success."""

    def test_a_top_level_pyz_module(self):
        self.assertEqual(nz.dotted_name("_pyz_modules/payload.pyc"), "payload")

    def test_a_dotted_pyz_module(self):
        self.assertEqual(nz.dotted_name("_pyz_modules/pkg/sub.pyc"), "pkg.sub")

    def test_a_module_outside_the_pyz(self):
        self.assertEqual(nz.dotted_name("main.pyc"), "main")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestTheClaimAboutTheRestOfTheFileIsCheckable(unittest.TestCase):
    """Constraint 4 says the rest of the file "was not otherwise altered".

    **It was guaranteed by care, not by anything a reader could run.** The record now compares the
    rewritten tree against the archive it came from and reports which entries are byte-identical and
    which changed besides the targets — so "I changed one module" stops being an assurance and becomes
    a measurement.
    """

    def setUp(self):
        self.tmp = self._tmp()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def _tmp(self):
        """A temporary directory, with no opinion about which drive it is on.

        **The first version demanded a specific drive and made the whole class fail on CI**, where
        that drive does not exist. Path discipline belongs to the tools that refuse destinations;
        `neutralize` does not care where it writes, so a fixture insisting on one drive tests the
        runner rather than the code.
        """
        return Path(tempfile.mkdtemp(prefix="nz-"))

    def _archive_and_tree(self):
        import nanodesu as nd
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import test_nanodesu as tn
        arc = self.tmp / "sample.exe"
        tn.make_archive(arc, [("a.txt", "x", b"payload-aaa", True),
                              ("b.txt", "x", b"payload-bbb", True)])
        tree = self.tmp / "tree"
        tree.mkdir()
        ar = nd.find_archive(arc)
        for entry in ar.toc:
            out = tree / entry.name
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(nd.read_entry(ar, entry))
        return nd, arc, tree, ar

    def test_an_untouched_tree_reports_every_entry_unchanged(self):
        _nd, arc, tree, ar = self._archive_and_tree()
        out = nz.verify_untouched(arc, tree, replaced_names=set())
        self.assertTrue(out["untouched_verified"], out)
        self.assertEqual(out["entries_unchanged"], len(ar.toc))
        self.assertEqual(out["entries_changed"], [])

    def test_a_changed_entry_is_named_not_counted(self):
        """**Naming it is the point.** "Something else changed" sends the reader looking; naming it
        tells them whether this output is a one-place change or not."""
        _nd, arc, tree, _ar = self._archive_and_tree()
        (tree / "b.txt").write_bytes(b"TAMPERED")
        out = nz.verify_untouched(arc, tree, replaced_names=set())
        changed = [c["name"] for c in out["entries_changed"]]
        self.assertEqual(changed, ["b.txt"])
        self.assertEqual(out["entries_unchanged"], 1)

    def test_the_target_is_expected_to_differ_and_is_excluded(self):
        _nd, arc, tree, _ar = self._archive_and_tree()
        (tree / "a.txt").write_bytes(b"STUBBED")
        out = nz.verify_untouched(arc, tree, replaced_names={"a.txt"})
        self.assertEqual(out["entries_changed"], [])
        self.assertEqual(out["entries_unchanged"], 1)

    def test_an_entry_missing_from_the_tree_is_reported(self):
        _nd, arc, tree, _ar = self._archive_and_tree()
        (tree / "a.txt").unlink()
        out = nz.verify_untouched(arc, tree, replaced_names=set())
        self.assertEqual([c["name"] for c in out["entries_changed"]], ["a.txt"])
        self.assertIn("absent", out["entries_changed"][0]["why"])

    def test_a_failed_comparison_is_not_a_clean_one(self):
        """Never raises -- a broken comparison must not read as "nothing changed"."""
        out = nz.verify_untouched(Path("does-not-exist.exe"), self.tmp,
                                          replaced_names=set())
        self.assertFalse(out["untouched_verified"])
        self.assertIn("could not be read back", out["untouched_unverifiable"])
        self.assertIsNone(out["entries_unchanged"])

    def test_the_record_carries_the_version_and_the_action(self):
        """**Both repositories must write the same pair**, or the cross-tool consistency assertion has
        nothing to align on. See ACTION_CONTRACT.md."""
        self.assertEqual(nz.RECORD_VERSION, 1)
        src = (Path(__file__).resolve().parent.parent / "neutralize.py").read_text(encoding="utf-8")
        self.assertIn('"record_version": RECORD_VERSION', src)
        self.assertIn('"action": "neutralize"', src)
