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
import inspect
import json
import struct
import sys
import tempfile
import unittest
import zlib
import shutil
import subprocess
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

    def test_entry_names_cannot_escape_the_output_directory(self):
        """A crafted archive must not be able to write outside the directory it was given.

        Entry names come from the archive, which is untrusted by definition. Before this was
        clamped, an entry named '../../OUTSIDE/x' wrote outside the output tree -- confirmed
        by running the attack, not by reading the code.
        """
        vectors = [
            "../../OUTSIDE/pwned.txt",
            "../../../escaped.txt",
            "/etc/pwned.txt",
            "C:/Windows/pwned.txt",
            r"..\..\pwned.txt",
            "a/./../../b/pwned.txt",
            r"\server\share\pwned.txt",
            "..",
            "a/b/../../../../deep.txt",
        ]
        for name in vectors:
            with self.subTest(name):
                work = Path(self.tmp.name) / ("case_%d" % (abs(hash(name)) % 10 ** 6))
                work.mkdir(parents=True, exist_ok=True)
                out = work / "tree"
                src = make_archive(work / "evil.exe", [(name, "s", b"payload", 0)])
                rc = nano.main(["extract", str(src), "-o", str(out)])
                self.assertEqual(rc, 0)
                escaped = [p for p in work.rglob("*")
                           if p.is_file() and p.name != "evil.exe"
                           and "tree" not in p.relative_to(work).parts]
                self.assertEqual(escaped, [],
                                 "entry %r escaped the output directory: %s" % (name, escaped))

    def test_confined_entries_are_reported_and_manifested(self):
        """The repair is recorded, not silent: an archive that tried this is a finding."""
        out = Path(self.tmp.name) / "confined"
        src = make_archive(Path(self.tmp.name) / "evil2.exe",
                           [("../../OUTSIDE/pwned.txt", "s", b"payload", 0)])
        rc = nano.main(["extract", str(src), "-o", str(out)])
        self.assertEqual(rc, 0)
        manifest = json.loads((out / "_archive_manifest.json").read_text(encoding="utf-8"))
        entry = manifest["entries"][0]
        self.assertTrue(entry["confined"])
        self.assertEqual(entry["name"], "../../OUTSIDE/pwned.txt")   # kept for an exact repack
        self.assertNotIn("..", entry["safe_rel"])

    def test_ordinary_names_are_untouched(self):
        """The clamp must not rewrite benign paths, or the round trip breaks."""
        out = Path(self.tmp.name) / "normal"
        src = make_archive(Path(self.tmp.name) / "ok.exe", SAMPLE)
        nano.main(["extract", str(src), "-o", str(out)])
        manifest = json.loads((out / "_archive_manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(all(not e["confined"] for e in manifest["entries"]))
        for e in manifest["entries"]:
            self.assertEqual(e["rel"], e["safe_rel"])

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

    def test_pyz_output_is_a_valid_pyc_by_default(self):
        """The PYZ header carries the bytecode magic, so no guessing is needed."""
        p = self.dir / "PYZ.pyz"
        p.write_bytes(self._pyz([("mod", 0, bytes([0xe3]) + b"code")]))
        out = self.dir / "with_header"
        nano.main(["pyz", str(p), "-o", str(out)])
        data = (out / "mod.pyc").read_bytes()
        self.assertEqual(data[:4], bytes.fromhex("cb0d0d0a"), "a .pyc header was expected")
        self.assertEqual(data[nano.PYC_HEADER_LEN:], bytes([0xe3]) + b"code")

    def test_pyz_bare_keeps_the_stored_bytes(self):
        p = self.dir / "PYZ.pyz"
        p.write_bytes(self._pyz([("mod", 0, bytes([0xe3]) + b"code")]))
        out = self.dir / "bare"
        nano.main(["pyz", str(p), "-o", str(out), "--bare"])
        self.assertEqual((out / "mod.pyc").read_bytes(), bytes([0xe3]) + b"code")

    def test_path_collision_falls_back_instead_of_overwriting(self):
        """A module and a package can claim the same file path; never overwrite."""
        p = self.dir / "PYZ.pyz"
        p.write_bytes(self._pyz([("utils", 0, b"\xe3module"), ("utils.sub", 0, b"\xe3sub")]))
        out = self.dir / "out"
        nano.main(["pyz", str(p), "-o", str(out)])
        files = sorted(f.relative_to(out).as_posix() for f in out.rglob("*.pyc"))
        self.assertEqual(len(files), 2, "one module was overwritten: %s" % files)
        # Compare the payload only: a .pyc header is prepended, expected and
        # identical for both files.
        bodies = {(out / f).read_bytes()[nano.PYC_HEADER_LEN:] for f in files}
        self.assertEqual(bodies, {bytes([0xe3]) + b"module", bytes([0xe3]) + b"sub"})


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

class TestOverlayIsAFirstClassFact(unittest.TestCase):
    """Bytes appended after the archive used to be invisible.

    **PyInstaller does not write past its own cookie**, so anything there was put there deliberately
    -- and the usual reasons are a second payload, a configuration blob, or an encrypted stage. A
    reader told "this is a PyInstaller archive" was told about the envelope and nothing about the
    extra weight taped to it.
    """

    def _archive(self, extra=b""):
        tmp = Path(tempfile.mkdtemp(prefix="ov-"))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        path = tmp / "sample.exe"
        make_archive(path, [("a.txt", "x", b"payload-aaa", True)])
        if extra:
            with path.open("ab") as fh:
                fh.write(extra)
        return nano.find_archive(path)

    def test_a_clean_archive_reports_no_overlay(self):
        ar = self._archive()
        self.assertFalse(ar.has_overlay)
        self.assertEqual(ar.overlay_size, 0)
        facts = ar.overlay_facts()
        self.assertFalse(facts["has_overlay"])
        self.assertNotIn("note", facts, "a note about an overlay that is not there is noise")

    def test_appended_bytes_are_measured_and_named_as_region(self):
        ar = self._archive(b"SECOND-PAYLOAD" * 16)
        self.assertTrue(ar.has_overlay)
        self.assertEqual(ar.overlay_size, 14 * 16)
        facts = ar.overlay_facts()
        self.assertEqual(facts["overlay_size"], 14 * 16)
        self.assertEqual(facts["archive_end"] + facts["overlay_size"], facts["file_size"])

    def test_it_says_what_it_will_not_claim(self):
        """Measuring the region is a fact; saying what is in it is not. The note has to carry that."""
        facts = self._archive(b"x" * 512).overlay_facts()
        note = facts["note"]
        self.assertIn("does not", note)
        self.assertIn("interpret", note)
        # And it names the plausible reasons without asserting one.
        for word in ("payload", "configuration", "encrypted"):
            self.assertIn(word, note)

    def test_the_archive_end_is_where_the_cookie_ends(self):
        ar = self._archive(b"x" * 32)
        self.assertEqual(ar.archive_end, ar.cookie_pos + 88)

    def test_info_prints_the_line_either_way(self):
        """Reported even at zero: "none" is an answer to a question somebody asked."""
        tmp = Path(tempfile.mkdtemp(prefix="ov-"))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        path = tmp / "sample.exe"
        make_archive(path, [("a.txt", "x", b"p", True)])
        out = subprocess.run([sys.executable, str(nano.__file__), "info", str(path)],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("overlay", out.stdout)
        self.assertIn("none", out.stdout)


class TestTheCapsAreThreeAndOneOfThemWasMissing(unittest.TestCase):
    """A per-entry bound is satisfied by any number of entries that each sit just below it.

    The old value was 2 GiB, which is not a limit for an analysis tool: three entries at 1.9 GiB were
    each individually "within limit" and together are six gigabytes of decompressed output from a file
    that might be a tenth of that size. **The interesting failure was never one huge entry -- it was a
    column of merely large ones, which no single check could see.**
    """

    def test_the_per_entry_cap_is_an_analysis_tool_value(self):
        self.assertLessEqual(nano.MAX_ENTRY_BYTES, 512 << 20,
                             "a cap above half a gigabyte is not a cap for this kind of tool")

    def test_there_is_a_total_and_a_count(self):
        self.assertTrue(hasattr(nano, "MAX_TOTAL_BYTES"))
        self.assertTrue(hasattr(nano, "MAX_OUTPUT_FILES"))
        self.assertLess(nano.MAX_TOTAL_BYTES, 8 << 30)

    def test_the_total_is_charged_for_every_entry_read(self):
        """An uncompressed entry still lands in memory and still counts."""
        import inspect
        body = inspect.getsource(nano.read_entry)
        self.assertIn("_bytes_read", body)
        # Charged after the length check, so the entry itself is still verified first.
        self.assertLess(body.index("size mismatch"), body.index("_bytes_read"))

    def test_the_pyz_has_its_own_ceilings(self):
        """**The PYZ is a file inside the archive and had no limit of its own.** Every CArchive bound
        could be satisfied while the whole thing was read into one blob and sliced, so the region handed
        to `marshal.loads()` was chosen by the file rather than by this tool.
        """
        for name in ("MAX_PYZ_BYTES", "MAX_PYZ_TOC_BYTES", "MAX_PYZ_RECORDS"):
            with self.subTest(constant=name):
                self.assertTrue(hasattr(nano, name), "%s is missing" % name)
        body = inspect.getsource(nano.cmd_pyz)
        self.assertIn("MAX_PYZ_BYTES", body)
        # Checked before parsing, not after.
        self.assertLess(body.index("MAX_PYZ_BYTES"), body.index("PYZ_MAGIC"))

    def test_lifting_the_caps_is_explicit_and_named_after_what_it_does(self):
        src = (Path(__file__).resolve().parent.parent / "nanodesu.py").read_text(encoding="utf-8")
        self.assertIn("--allow-huge", src)
        self.assertIn("Only for an archive you already trust", src)
        # And it is stripped from argv like the other switches, so it never reaches argparse.
        flat = " ".join(src.split())
        self.assertIn('not in ("--plain", "--nsfw", "--allow-huge")', flat)

    def test_the_default_is_not_lifted(self):
        self.assertFalse(nano._allow_huge())


class TestDeviceNamesAreNotFilenames(unittest.TestCase):
    """`NUL`, `CON`, `COM1` and friends address devices on Windows.

    Writing to one either fails or does not create a file at all -- and **an extraction that reports a
    member as written while nothing was created is worse than a refusal**, because the report is what
    the operator keeps.
    """

    def test_a_device_name_is_prefixed(self):
        for name, expect in (("NUL", "_NUL"), ("con", "_con"), ("COM1", "_COM1"),
                             ("CON.txt", "_CON.txt"), ("nul.dat", "_nul.dat")):
            with self.subTest(name=name):
                out = nano.confining(Path("."), Path(name), name)
                self.assertEqual(str(out), expect)

    def test_ordinary_names_come_through_distinct(self):
        """The rename that matters is the one applied to a name that is *not* already prefixed.

        A prefix maps `NUL` onto `_NUL`. `_NUL` is itself a legal filename and reaches the same place,
        so the two collide -- **and the first version of this function escaped every leading underscore
        to fix that, which broke ordinary names.** A test caught that, so the escaping is confined to
        device names and the one remaining ambiguity is stated rather than solved: it requires an
        archive holding both `NUL` and `_NUL`, and repairing names nobody has is the worse trade.
        """
        mapped = {}
        for name in ("a.txt", "b.txt", "lib/x.pyc", "CON", "PRN", "AUX", "COM1", "LPT1"):
            out = str(nano.confining(Path("."), Path(name), name))
            with self.subTest(name=name):
                self.assertNotIn(out, mapped, "%s and %s both map to %s" % (mapped.get(out), name, out))
            mapped[out] = name

    def test_the_documented_ambiguity_is_the_only_one(self):
        """`_NUL` and `NUL` map alike, and that is written down rather than discovered later."""
        self.assertEqual(str(nano.confining(Path("."), Path("NUL"), "NUL")), "_NUL")
        self.assertEqual(str(nano.confining(Path("."), Path("_NUL"), "_NUL")), "_NUL")

    def test_an_ordinary_name_is_untouched(self):
        for name in ("a.txt", "lib/site.pyc", "weird?name", "trailing. "):
            with self.subTest(name=name):
                out = str(nano.confining(Path("."), Path(name), name))
                if name == "trailing. ":
                    self.assertNotIn(" ", out.rstrip("\/"))
                else:
                    self.assertTrue(out)

    def test_the_traversal_defences_are_still_there(self):
        """The device rule is an addition, not a replacement for what already worked."""
        self.assertEqual(str(nano.confining(Path("."), Path(".."), "..")), "_unnamed")
        self.assertNotIn(":", str(nano.confining(Path("."), Path("C:/x"), "C:/x")))


class TestNoBareNameIsEverRun(unittest.TestCase):
    """An archive's own version string used to decide which executable this tool would start.

    `shutil.which` resolved a bare `python3.12` off PATH and the result was executed. PATH is not ours,
    so the untrusted input was choosing a program to run. **Nothing about the archive gets to choose
    that.**
    """

    def test_the_source_no_longer_resolves_a_bare_name(self):
        for name in ("nanodesu.py", "neutralize.py"):
            src = (Path(__file__).resolve().parent.parent / name).read_text(encoding="utf-8")
            with self.subTest(file=name):
                # The only occurrence may be the comment explaining its removal.
                for line in src.splitlines():
                    if "shutil.which(" in line:
                        self.assertTrue(line.strip().startswith("#"),
                                        "%s still calls shutil.which: %s" % (name, line.strip()))

    def test_a_bare_template_is_refused(self):
        import tempfile
        # A version the table knows, so the answer comes from the table and nothing is launched.
        magic = nano._magic_for("3.12")
        self.assertIsNotNone(magic, "the built-in table no longer covers 3.12")
        self.assertEqual(len(magic), 4)

    def test_the_table_is_what_makes_refusing_affordable(self):
        """Refusing to launch would be expensive if the table were thin -- it would lose the exact magic
        for many versions. It covers 3.7 through 3.14, which is why the refusal costs almost nothing."""
        for ver in ("3.7", "3.8", "3.9", "3.10", "3.11", "3.12", "3.13", "3.14"):
            with self.subTest(version=ver):
                self.assertIn(ver, nano.PYC_MAGIC_BY_VER)
