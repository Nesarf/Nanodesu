# -*- coding: utf-8 -*-
"""Tests for nanodesu.py.

Run with either:
    python -m unittest discover -s test -v
    python test/test_nanodesu.py

Most tests build small synthetic archives, so nothing large is needed and the
whole suite finishes in a second. Regression tests for the three format details
that silently produce a broken repack are at the bottom.
"""
from __future__ import annotations

import importlib.util
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODULE_PATH = HERE.parent / "nanodesu.py"
spec = importlib.util.spec_from_file_location("nanodesu", MODULE_PATH)
nano = importlib.util.module_from_spec(spec)
sys.modules["nanodesu"] = nano
spec.loader.exec_module(nano)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def toc_entry(name: str, offset: int, payload: bytes, cmprs: int, tcode: str,
              stored: bytes | None = None) -> bytes:
    body = stored if stored is not None else payload
    entry_len = nano.TOC_ENTRY_LEN + len(name.encode()) + 1
    if entry_len % 16:
        entry_len += 16 - (entry_len % 16)
    name_field = entry_len - nano.TOC_ENTRY_LEN
    return (struct.pack(nano.TOC_ENTRY_FORMAT, entry_len, offset, len(body),
                        len(payload), cmprs, tcode.encode())
            + name.encode().ljust(name_field, b"\x00"))


def make_archive(path: Path, entries, *, pyver: int = 312,
                 pylib: str = "python312.dll", pkg_header: int = 88,
                 stub_len: int = 512) -> Path:
    """Write a synthetic archive. `entries` is a list of
    (name, tcode, payload, compress)."""
    payload = b""
    toc = b""
    for name, tcode, data, compress in entries:
        comp = zlib.compress(data, 9)
        use_comp = 1 if (compress and len(comp) < len(data)) else 0
        stored = comp if use_comp else data
        toc += toc_entry(name, pkg_header + len(payload), data, use_comp,
                         tcode, stored)
        payload += stored
    toc_off = pkg_header + len(payload)
    pkg_body = bytes(pkg_header) + payload + toc
    pkg_len = len(pkg_body) + nano.COOKIE_LEN
    cookie = struct.pack(nano.COOKIE_FORMAT, nano.MAGIC, pkg_len, toc_off,
                         len(toc), pyver, pylib.encode().ljust(64, b"\x00"))
    path.write_bytes(b"\x00" * stub_len + pkg_body + cookie)
    return path


SAMPLE = [
    ("module_one", "m", b"\xe3\x00\x00\x00 fake bytecode", 1),
    ("data/blob.bin", "b", b"B" * 4000, 1),
    ("PYZ.pyz", "z", b"PYZ\x00" + b"\xcb\r\r\n" + b"X" * 700, 0),
]


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #

class TestParsing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_all_entries(self):
        p = make_archive(self.dir / "a.exe", SAMPLE)
        ar = nano.find_archive(p)
        self.assertEqual([e.name for e in ar.toc], [n for n, *_ in SAMPLE])
        self.assertEqual(ar.pyver_str(), "3.12")
        self.assertEqual(ar.pylib, "python312.dll")

    def test_pkg_len_includes_cookie_and_trailing_bytes(self):
        """The archive start must be derived from pkg_len including the cookie."""
        p = make_archive(self.dir / "a.exe", SAMPLE)
        ar = nano.find_archive(p)
        self.assertEqual(ar.base_pos, 512)
        self.assertEqual(ar.cookie_pos, p.stat().st_size - nano.COOKIE_LEN)

    def test_entry_payloads_round_trip(self):
        p = make_archive(self.dir / "a.exe", SAMPLE)
        ar = nano.find_archive(p)
        by_name = {e.name: e for e in ar.toc}
        self.assertEqual(nano.read_entry(ar, by_name["data/blob.bin"]), b"B" * 4000)
        self.assertEqual(nano.read_entry(ar, by_name["PYZ.pyz"])[:4], b"PYZ\x00")

    def test_lowercase_z_is_a_pyz_entry(self):
        """Regression: lowercase z (PYZ archive) must parse; uppercase Z is a zipfile."""
        p = make_archive(self.dir / "a.exe", SAMPLE)
        ar = nano.find_archive(p)
        kinds = {e.name: e.tcode for e in ar.toc}
        self.assertEqual(kinds["PYZ.pyz"], "z")
        self.assertIn("z", nano.VALID_TYPES)
        self.assertIn("Z", nano.VALID_TYPES)

    def test_rejects_non_archive(self):
        p = self.dir / "notanexe.bin"
        p.write_bytes(b"MZ" + b"\x00" * 5000)
        with self.assertRaises(SystemExit):
            nano.find_archive(p)

    def test_missing_and_directory_paths_fail_cleanly(self):
        with self.assertRaises(SystemExit) as ctx:
            nano.find_archive(self.dir / "nope.exe")
        self.assertIn("no such file", str(ctx.exception))
        with self.assertRaises(SystemExit) as ctx:
            nano.find_archive(self.dir)
        self.assertIn("directory", str(ctx.exception))

    def test_rejects_implausible_pkg_len(self):
        p = make_archive(self.dir / "a.exe", SAMPLE)
        raw = bytearray(p.read_bytes())
        # corrupt pkg_len to something enormous
        off = len(raw) - nano.COOKIE_LEN + 8
        raw[off:off + 4] = struct.pack("!I", 0xFFFFFFF0)
        bad = self.dir / "bad.exe"
        bad.write_bytes(bytes(raw))
        with self.assertRaises(SystemExit):
            nano.find_archive(bad)


# --------------------------------------------------------------------------- #
# repacking
# --------------------------------------------------------------------------- #

class TestRepack(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_extract_then_build_is_byte_identical(self):
        src = make_archive(self.dir / "a.exe", SAMPLE, stub_len=512)
        out = self.dir / "tree"
        rc = nano.main(["extract", str(src), "-o", str(out)])
        self.assertEqual(rc, 0)
        self.assertTrue((out / "_archive_manifest.json").is_file())

        rebuilt = self.dir / "b.exe"
        rc = nano.main(["build", str(out), "-o", str(rebuilt),
                        "--pylib", "python312.dll", "--pyver", "312"])
        self.assertEqual(rc, 0)

        a = nano.find_archive(src)
        b = nano.find_archive(rebuilt)
        self.assertEqual([e.name for e in a.toc], [e.name for e in b.toc])
        self.assertEqual([e.tcode for e in a.toc], [e.tcode for e in b.toc])
        self.assertEqual([e.cmprs for e in a.toc], [e.cmprs for e in b.toc])
        self.assertEqual([e.usize for e in a.toc], [e.usize for e in b.toc])
        for ea, eb in zip(a.toc, b.toc):
            self.assertEqual(nano.read_entry(a, ea), nano.read_entry(b, eb),
                             "payload differs for %s" % ea.name)

    def test_build_without_manifest_still_writes_a_readable_archive(self):
        """Falls back to inferring entries from the directory."""
        tree = self.dir / "plain"
        (tree / "sub").mkdir(parents=True)
        (tree / "app.stub").write_bytes(b"\x00" * 256)
        (tree / "module.pyc").write_bytes(b"\xe3\x00\x00\x00 abc")
        (tree / "sub" / "asset.bin").write_bytes(b"D" * 300)
        out = self.dir / "c.exe"
        rc = nano.main(["build", str(tree), "-o", str(out)])
        self.assertEqual(rc, 0)
        ar = nano.find_archive(out)
        self.assertEqual(sorted(e.name for e in ar.toc),
                         ["module.pyc", "sub/asset.bin"])

    def test_pyc_header_is_stripped_again_on_repack(self):
        """Regression: --pyc prepends 16 bytes; build must remove exactly those."""
        src = make_archive(self.dir / "a.exe", SAMPLE)
        out = self.dir / "tree"
        nano.main(["extract", str(src), "-o", str(out), "--pyc"])
        module = out / "module_one.pyc"
        self.assertTrue(module.is_file())
        self.assertEqual(module.read_bytes()[:4], bytes.fromhex("cb0d0d0a"),
                         "a .pyc header should have been prepended")

        rebuilt = self.dir / "b.exe"
        nano.main(["build", str(out), "-o", str(rebuilt), "--pylib", "python312.dll"])
        a, b = nano.find_archive(src), nano.find_archive(rebuilt)
        ea = {e.name: e for e in a.toc}["module_one"]
        eb = {e.name: e for e in b.toc}["module_one"]
        self.assertEqual(nano.read_entry(a, ea), nano.read_entry(b, eb),
                         "module payload was corrupted by the .pyc header")

    def test_contents_directory_prefix_is_stripped(self):
        """The OPTION entry already places files, so stored names must not repeat it."""
        src = make_archive(self.dir / "a.exe",
                           SAMPLE + [("pyi-contents-directory _internal", "o", b"", False)])
        out = self.dir / "tree"
        nano.main(["extract", str(src), "-o", str(out)])
        self.assertTrue((out / "_internal" / "data" / "blob.bin").is_file(),
                        "binary(dep) entries belong under the contents directory")
        rebuilt = self.dir / "b.exe"
        nano.main(["build", str(out), "-o", str(rebuilt), "--pylib", "python312.dll"])
        ar = nano.find_archive(rebuilt)
        names = [e.name for e in ar.toc]
        self.assertIn("data/blob.bin", names)
        # OPTION entries are surfaced separately, not as TOC file entries.
        self.assertIn("pyi-contents-directory _internal", ar.options,
                      "the OPTION entry must survive a repack")


# --------------------------------------------------------------------------- #
# pyz
# --------------------------------------------------------------------------- #

class TestPyz(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    tearDown = lambda self: self.tmp.cleanup()

    PYZ_HEAD = 17      # magic(4) + bytecode magic(4) + toc offset(4) + 5 reserved

    def _pyz(self, modules):
        """Build a minimal PYZ. TOC offsets are absolute file positions."""
        blob = b""
        records = []
        for name, typ, code in modules:
            if code:
                stored = zlib.compress(code, 6)
                records.append((name, (typ, self.PYZ_HEAD + len(blob), len(stored))))
                blob += stored
            else:
                records.append((name, (typ, 0, 0)))
        toc = __import__("marshal").dumps(records)
        head = (nano.PYZ_MAGIC + bytes.fromhex("cb0d0d0a")
                + struct.pack("!i", self.PYZ_HEAD + len(blob)) + bytes(5))
        assert len(head) == self.PYZ_HEAD
        return head + blob + toc

    def test_extracts_modules_and_skips_namespace_packages(self):
        p = self.dir / "PYZ.pyz"
        p.write_bytes(self._pyz([("a.b", 0, b"\xe3aaa"), ("ns", 3, b""),
                                 ("pkg", 1, b"\xe3pkg")]))
        out = self.dir / "out"
        rc = nano.main(["pyz", str(p), "-o", str(out)])
        self.assertEqual(rc, 0)
        self.assertTrue((out / "a" / "b.pyc").is_file())
        self.assertTrue((out / "pkg.pyc").is_file())
        self.assertFalse((out / "ns.pyc").exists())

    def test_path_collision_falls_back_instead_of_overwriting(self):
        """A module and a package can claim the same file path; never overwrite."""
        p = self.dir / "PYZ.pyz"
        p.write_bytes(self._pyz([("utils", 0, b"\xe3module"), ("utils.sub", 0, b"\xe3sub")]))
        out = self.dir / "out"
        nano.main(["pyz", str(p), "-o", str(out)])
        files = sorted(f.relative_to(out).as_posix() for f in out.rglob("*.pyc"))
        self.assertEqual(len(files), 2, "one module was overwritten: %s" % files)
        bodies = {f: (out / f).read_bytes() for f in files}
        self.assertEqual(set(bodies.values()), {b"\xe3module", b"\xe3sub"})


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

class TestHelpers(unittest.TestCase):
    def test_human_readable_sizes(self):
        self.assertEqual(nano.human(512), "512 B")
        self.assertEqual(nano.human(2048), "2.0 KB")
        self.assertEqual(nano.human(5 * 1024 * 1024), "5.0 MB")

    def test_magic_lookup_returns_four_bytes_or_none(self):
        magic = nano._magic_for("3.12")
        self.assertIsNotNone(magic)
        self.assertEqual(len(magic), 4)
        self.assertIsNone(nano._magic_for("9.9"))

    def test_wrap_pyc_layout(self):
        wrapped = nano.wrap_pyc(b"CODE", bytes.fromhex("cb0d0d0a"))
        self.assertEqual(len(wrapped), nano.PYC_HEADER_LEN + 4)
        self.assertEqual(wrapped[:4], bytes.fromhex("cb0d0d0a"))
        self.assertEqual(wrapped[16:], b"CODE")


if __name__ == "__main__":
    unittest.main(verbosity=2)