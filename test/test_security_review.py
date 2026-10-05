# -*- coding: utf-8 -*-
"""Regression tests for the security review of 2025-10-05.

An external review of the tool found four issues, three of them release-blocking. Each was
verified against the code and reproduced before it was fixed, and each has a test here that
fails without the fix:

1. ``pyz`` applied no path confinement to module names, so ``C:/abs/x`` wrote to ``C:\\abs\\x``.
   The CArchive path had been fixed in 1.1.0; the same boundary was left open one layer down.
2. The CArchive parser accepted claimed offsets, lengths and compression flags without checking
   them against the archive, and truncated the table of contents silently.
3. Decompression was unbounded and used the declared size only as an after-the-fact comparison,
   so an entry declaring 20 GB got 20 GB attempted.
4. ``--pyc`` recomputed the output path in a second pass, which disagreed with the confined path
   the bytes had actually been written to, while the manifest still claimed a header was added.

Run:
    python -m unittest discover -s test -v
"""
from __future__ import annotations

import importlib.util
import json
import marshal
import shutil
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE.parent / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


nano = _load("nano_sec", "nanodesu.py")


def make_pyz(modules) -> bytes:
    """A minimal PYZ: header, then zlib streams, then a marshalled table of contents."""
    blob = b""
    records = []
    for name, typ, code in modules:
        if code:
            stored = zlib.compress(code, 6)
            records.append((name, (typ, 17 + len(blob), len(stored))))
            blob += stored
        else:
            records.append((name, (typ, 0, 0)))
    toc = marshal.dumps(records)
    head = (nano.PYZ_MAGIC + bytes.fromhex("cb0d0d0a")
            + struct.pack("!i", 17 + len(blob)) + bytes(5))
    return head + blob + toc


class _Args:
    def __init__(self, out, **kw):
        self.output = str(out)
        self.bare = kw.get("bare", False)
        self.list = 0


class TestPyzConfinement(unittest.TestCase):
    """Issue 1: the module name is untrusted input exactly as an entry name is."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nano-pyz-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, module_name: str):
        out = self.tmp / "mods"
        out.mkdir(parents=True, exist_ok=True)
        pyz = self.tmp / ("p%d.pyz" % (abs(hash(module_name)) % 10 ** 6))
        pyz.write_bytes(make_pyz([(module_name, 0, b"\xe3fake")]))
        nano.cmd_pyz(str(pyz), _Args(out))
        # Anything written outside `mods` but inside the temp root is an escape.
        return [p.relative_to(self.tmp).as_posix() for p in self.tmp.rglob("*")
                if p.is_file() and p.suffix != ".pyz"
                and "mods" not in p.relative_to(self.tmp).parts]

    def test_a_drive_absolute_module_name_cannot_escape(self):
        """This one wrote to C:\\abs\\x.pyc before the fix."""
        self.assertEqual(self._run("C:/abs/PWNED"), [])

    def test_an_absolute_module_name_cannot_escape(self):
        """This one landed outside the output directory entirely."""
        self.assertEqual(self._run("/abs/PWNED"), [])

    def test_parent_segments_cannot_escape(self):
        self.assertEqual(self._run("../../PWNED"), [])

    def test_backslash_parent_segments_cannot_escape(self):
        self.assertEqual(self._run("..\\..\\PWNED"), [])

    def test_an_ordinary_module_name_is_written_where_expected(self):
        """The clamp must not move a name that never tried to leave."""
        out = self.tmp / "mods"
        out.mkdir(parents=True)
        pyz = self.tmp / "ok.pyz"
        pyz.write_bytes(make_pyz([("pkg.sub", 0, b"\xe3code")]))
        nano.cmd_pyz(str(pyz), _Args(out))
        self.assertTrue((out / "pkg" / "sub.pyc").is_file())

    def test_namespace_packages_are_still_skipped(self):
        out = self.tmp / "mods"
        out.mkdir(parents=True)
        pyz = self.tmp / "ns.pyz"
        pyz.write_bytes(make_pyz([("ns", 3, b""), ("real", 0, b"\xe3x")]))
        nano.cmd_pyz(str(pyz), _Args(out))
        self.assertFalse((out / "ns.pyc").exists())
        self.assertTrue((out / "real.pyc").is_file())


class TestBoundedDecompression(unittest.TestCase):
    """Issue 3: a declared size is a claim, so it cannot authorise the allocation."""

    def test_a_bomb_is_refused_rather_than_allocated(self):
        bomb = zlib.compress(b"\x00" * (64 << 20), 9)
        self.assertLess(len(bomb), 1 << 20, "the fixture should be small compressed")
        with self.assertRaises(zlib.error) as ctx:
            nano._inflate(bomb, 1 << 20)
        self.assertIn("more than", str(ctx.exception))

    def test_a_bomb_is_refused_quickly(self):
        """The point is to stop before allocating, not after."""
        import time
        bomb = zlib.compress(b"\x00" * (200 << 20), 9)
        started = time.time()
        with self.assertRaises(zlib.error):
            nano._inflate(bomb, 1 << 20)
        self.assertLess(time.time() - started, 5.0,
                        "a refusal that takes seconds is allocating anyway")

    def test_within_the_limit_still_decompresses(self):
        payload = b"hello" * 1000
        out = nano._inflate(zlib.compress(payload, 9), len(payload) + 1024)
        self.assertEqual(out, payload)

    def test_there_is_an_absolute_ceiling_independent_of_the_claim(self):
        """An archive that honestly declares an absurd size must still be refused."""
        self.assertGreater(nano.MAX_ENTRY_BYTES, 0)
        self.assertLessEqual(nano.MAX_ENTRY_BYTES, 8 << 30)


class TestArchiveIntegrity(unittest.TestCase):
    """Issue 2: claimed values are checked against the archive, and problems are reported."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nano-int-"))
        self.src = self.tmp / "good.exe"
        self._build_good()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _build_good(self):
        sys.path.insert(0, str(HERE))
        spec = importlib.util.spec_from_file_location("tn_sec", HERE / "test_nanodesu.py")
        tn = importlib.util.module_from_spec(spec)
        sys.modules["tn_sec"] = tn
        spec.loader.exec_module(tn)
        tn.make_archive(self.src, [("a.txt", "b", b"hello", 0), ("b.txt", "b", b"world", 0)])

    def _corrupt(self, label: str, mutate) -> Path:
        raw = bytearray(self.src.read_bytes())
        cookie_at = len(raw) - 88
        _magic, pkg_len, toc_off, _toc_len, *_ = struct.unpack(
            nano.COOKIE_FORMAT, raw[cookie_at:cookie_at + 88])
        base = cookie_at + 88 - pkg_len
        mutate(raw, base, toc_off)
        out = self.tmp / (label + ".exe")
        out.write_bytes(bytes(raw))
        return out

    def test_a_good_archive_reports_no_integrity_problems(self):
        """The most important case: no false positives on a valid file."""
        ar = nano.find_archive(self.src)
        self.assertEqual(ar.integrity, [])

    def test_an_undefined_compression_flag_is_reported(self):
        def mutate(raw, base, toc_off):
            struct.pack_into("!B", raw, base + toc_off + 16, 7)
        ar = nano.find_archive(self._corrupt("flag", mutate))
        self.assertTrue(any("compression flag" in n for n in ar.integrity), ar.integrity)

    def test_an_out_of_range_offset_is_reported(self):
        def mutate(raw, base, toc_off):
            struct.pack_into("!I", raw, base + toc_off + 8, 0xFFFFFF00)
        ar = nano.find_archive(self._corrupt("offset", mutate))
        self.assertTrue(any("past the end" in n for n in ar.integrity), ar.integrity)

    def test_a_misaligned_entry_length_is_refused_with_a_reason(self):
        def mutate(raw, base, toc_off):
            struct.pack_into("!I", raw, base + toc_off, 19)
        with self.assertRaises(SystemExit) as ctx:
            nano.find_archive(self._corrupt("align", mutate))
        self.assertIn("16-byte aligned", str(ctx.exception))

    def test_an_unsupported_type_code_is_refused_with_a_reason(self):
        def mutate(raw, base, toc_off):
            struct.pack_into("!c", raw, base + toc_off + 17, b"Q")
        with self.assertRaises(SystemExit) as ctx:
            nano.find_archive(self._corrupt("type", mutate))
        msg = str(ctx.exception)
        self.assertIn("unsupported type code", msg)
        self.assertIn("'Q'", msg)

    def test_a_rejection_says_why_rather_than_just_that_it_failed(self):
        """'could not parse' alone is the least useful thing to tell somebody."""
        def mutate(raw, base, toc_off):
            struct.pack_into("!I", raw, base + toc_off, 19)
        with self.assertRaises(SystemExit) as ctx:
            nano.find_archive(self._corrupt("why", mutate))
        msg = str(ctx.exception)
        self.assertIn("could not parse", msg)
        self.assertIn("aligned", msg)


class TestPycHeaderPathAgreement(unittest.TestCase):
    """Issue 4: one pass, so the path written and the path reported cannot disagree."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nano-pyc-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _extract(self, entries):
        sys.path.insert(0, str(HERE))
        spec = importlib.util.spec_from_file_location("tn_sec2", HERE / "test_nanodesu.py")
        tn = importlib.util.module_from_spec(spec)
        sys.modules["tn_sec2"] = tn
        spec.loader.exec_module(tn)
        src = self.tmp / "a.exe"
        tn.make_archive(src, entries)
        out = self.tmp / "tree"
        nano.main(["extract", str(src), "-o", str(out), "--pyc"])
        return out, json.loads((out / "_archive_manifest.json").read_text(encoding="utf-8"))

    def test_a_confined_entry_still_gets_its_header(self):
        """Before the fix the header was applied at the unclamped path and never landed."""
        out, manifest = self._extract([("../../evil/mod", "m", b"\xe3code", 0)])
        entry = manifest["entries"][0]
        self.assertTrue(entry["confined"], entry)
        target = out / entry["safe_rel"]
        self.assertTrue(target.is_file(), "the confined entry was not written")
        self.assertEqual(entry["pyc_header"], nano.PYC_HEADER_LEN)
        data = target.read_bytes()
        self.assertEqual(data[:4], bytes.fromhex("cb0d0d0a"),
                         "the manifest claims a header but the file has none")
        self.assertEqual(data[nano.PYC_HEADER_LEN:], b"\xe3code")

    def test_an_unconfined_entry_still_gets_its_header(self):
        out, manifest = self._extract([("goodmod", "m", b"\xe3code", 0)])
        entry = manifest["entries"][0]
        self.assertFalse(entry["confined"])
        data = (out / entry["safe_rel"]).read_bytes()
        self.assertEqual(data[:4], bytes.fromhex("cb0d0d0a"))
        self.assertEqual(data[nano.PYC_HEADER_LEN:], b"\xe3code")

    def test_every_manifest_entry_corresponds_to_a_file_on_disk(self):
        out, manifest = self._extract([
            ("../../evil/mod", "m", b"\xe3code", 0),
            ("goodmod", "m", b"\xe3ok", 0),
            ("data/blob.bin", "b", b"B" * 100, 0),
        ])
        for entry in manifest["entries"]:
            self.assertTrue((out / entry["safe_rel"]).is_file(),
                            "manifest entry with no file: %s" % entry["name"])

    def test_the_header_is_stripped_again_on_repack(self):
        """The corruption this bug produced: manifest says 16, build removes 16."""
        out, manifest = self._extract([("../../evil/mod", "m", b"\xe3code", 0)])
        rebuilt = self.tmp / "back.exe"
        rc = nano.main(["build", str(out), "-o", str(rebuilt), "--pylib", "python312.dll"])
        self.assertEqual(rc, 0)
        original = nano.find_archive(self.tmp / "a.exe")
        back = nano.find_archive(rebuilt)
        eo = next(e for e in original.toc if e.name == "../../evil/mod")
        eb = next(e for e in back.toc if e.name == "../../evil/mod")
        self.assertEqual(nano.read_entry(original, eo), nano.read_entry(back, eb),
                         "the payload lost 16 bytes that were never added")


if __name__ == "__main__":
    unittest.main(verbosity=2)
