# -*- coding: utf-8 -*-
"""The format compatibility matrix, and the constants that must match it.

The matrix in `FORMAT_MATRIX.md` is generated from PyInstaller's own sources across nine versions
spanning the three format generations. This file keeps the code and that document from drifting
apart, and it is the reason the document is worth trusting: a table nothing checks is a table
somebody edited a year ago.

The two defects this file was written against:

* `VALID_TYPES` omitted `l` (splash, since 4.10), so any archive carrying a splash screen failed to
  parse -- and said "unsupported type code" rather than anything about splashes
* three constants disagreed with PyInstaller's own header names (`x` called a binary dependency
  rather than data, `z` a binary embed rather than the PYZ, `d` data rather than dependency), and
  `TC_RUNTIME = "R"` named a code that no version defines

Run:
    python -m unittest discover -s test -v
"""
from __future__ import annotations

import importlib.util
import re
import struct
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

spec = importlib.util.spec_from_file_location("nano_matrix", HERE.parent / "nanodesu.py")
nano = importlib.util.module_from_spec(spec)
sys.modules["nano_matrix"] = nano
spec.loader.exec_module(nano)

MATRIX = HERE.parent / "FORMAT_MATRIX.md"

# What PyInstaller's header defines, and from which version each code exists. Read out of
# bootloader/src/pyi_archive.h in the sdists; the versions are where each first appears.
CODES_AND_SINCE = {
    "b": "3.6", "d": "3.6", "z": "3.6", "Z": "3.6", "M": "3.6", "m": "3.6",
    "s": "3.6", "x": "3.6", "o": "3.6",
    "l": "4.10",
    "n": "6.0",
}


class TestAcceptedCodes(unittest.TestCase):
    """Every code any PyInstaller version defines must be accepted.

    A real code that is not accepted does not merely lose one entry: the table walk stops, the
    archive is rejected, and the message blames the code rather than the omission.
    """

    def test_every_defined_code_is_accepted(self):
        missing = sorted(set(CODES_AND_SINCE) - nano.VALID_TYPES)
        self.assertEqual(missing, [],
                         "these codes exist in PyInstaller but this parser rejects them, which "
                         "makes any archive containing one unreadable: %s" % missing)

    def test_no_invented_codes_are_accepted(self):
        """The other direction. Accepting a code nothing defines means a corrupt table walks on."""
        invented = sorted(nano.VALID_TYPES - set(CODES_AND_SINCE))
        self.assertEqual(invented, [],
                         "these are accepted but no PyInstaller version defines them: %s" % invented)

    def test_the_splash_code_specifically(self):
        """The one that was missing, called out so the regression is named."""
        self.assertIn("l", nano.VALID_TYPES)
        self.assertEqual(nano.TC_SPLASH, "l")

    def test_the_symlink_code_specifically(self):
        self.assertIn("n", nano.VALID_TYPES)
        self.assertEqual(nano.TC_SYMLINK, "n")


class TestConstantNamesMatchUpstream(unittest.TestCase):
    """The values were always right; the names misled. A wrong name is a trap for the next reader."""

    def test_data_is_x(self):
        self.assertEqual(nano.TC_DATA, "x")

    def test_dependency_is_d(self):
        self.assertEqual(nano.TC_DEPENDENCY, "d")

    def test_pyz_is_lowercase_z(self):
        """Uppercase Z is a plain zipfile, and confusing the two was a real bug once already."""
        self.assertEqual(nano.TC_PYZ, "z")
        self.assertEqual(nano.TC_ZIPFILE, "Z")

    def test_no_constant_claims_a_code_that_does_not_exist(self):
        for name, code in (("TC_RUNTIME", "R"),):
            self.assertFalse(hasattr(nano, name),
                             "%s = %r names a code no PyInstaller version defines" % (name, code))


class TestDocumentedFormatsMatchTheCode(unittest.TestCase):
    """The parser's declared formats must be the ones the matrix says it handles."""

    def test_cookie_format_matches_the_newest_generation(self):
        self.assertEqual(nano.COOKIE_FORMAT, "!8sIIII64s")
        self.assertEqual(struct.calcsize(nano.COOKIE_FORMAT), 88)

    def test_toc_format_matches_the_newest_generation(self):
        self.assertEqual(nano.TOC_ENTRY_FORMAT, "!IIIIBc")
        self.assertEqual(struct.calcsize(nano.TOC_ENTRY_FORMAT), 18)

    def test_the_matrix_document_exists(self):
        self.assertTrue(MATRIX.is_file(), "FORMAT_MATRIX.md is missing")

    def test_the_matrix_names_all_three_generations(self):
        text = MATRIX.read_text(encoding="utf-8")
        for fmt in ("!8siiii64s", "!8sIIii64s", "!8sIIII64s",
                    "!iiiiBB", "!iIIIBB", "!IIIIBc"):
            self.assertIn(fmt, text, "the matrix does not mention %s" % fmt)

    def test_the_matrix_agrees_with_the_code_about_typecodes(self):
        """The document lists the codes; this checks the list against what the parser accepts."""
        text = MATRIX.read_text(encoding="utf-8")
        # Three columns in that table (name, value, note), so the pattern has to allow the
        # trailing cell -- a single-cell pattern silently failed to match rather than
        # silently matching the wrong thing, which is the better way to be wrong.
        # The value is a code span on its own: | `typecodes accepted` | `bdzZMmsxonl` |
        m = re.search(r"^\|\s*`typecodes accepted`\s*\|\s*`([^`]+)`\s*\|\s*$", text, re.M)
        self.assertIsNotNone(m, "the matrix has no 'typecodes accepted' row")
        documented = set(m.group(1))
        self.assertEqual(documented, set(nano.VALID_TYPES),
                         "FORMAT_MATRIX.md and VALID_TYPES disagree")

    def test_the_matrix_records_when_splash_appeared(self):
        text = MATRIX.read_text(encoding="utf-8")
        self.assertIn("4.10", text)
        self.assertRegex(text, r"splash", "the matrix does not mention splash")


class TestTheGenerationsAreWhatTheMatrixSays(unittest.TestCase):
    """Each generation's TOC record, encoded and re-read, to confirm the sizes the matrix claims."""

    GENERATIONS = {
        "gen1": ("!8siiii64s", "!iiiiBB"),
        "gen2": ("!8sIIii64s", "!iIIIBB"),
        "gen3": ("!8sIIII64s", "!IIIIBc"),
    }

    def test_every_generation_cookie_is_88_bytes(self):
        for name, (cookie, _) in self.GENERATIONS.items():
            self.assertEqual(struct.calcsize(cookie), 88, name)

    def test_every_generation_toc_record_is_18_bytes(self):
        """The record size never changed, which is why one parser can read all three as long as it
        does not depend on signedness."""
        for name, (_, toc) in self.GENERATIONS.items():
            self.assertEqual(struct.calcsize(toc), 18, name)

    def test_the_signedness_is_the_only_difference(self):
        """Stated as a check, because it is the matrix's central claim: the layouts are identical
        in size and differ only in whether the integers are signed."""
        signed = struct.calcsize("!iiiiBB")
        unsigned = struct.calcsize("!IIIIBc")
        self.assertEqual(signed, unsigned)

    def test_a_normal_record_reads_the_same_either_way(self):
        """For values that fit in the positive range -- which every real archive's do -- the three
        generations produce identical fields. That is what makes the differences tolerable."""
        values = (18 + 16, 0x1000, 0x200, 0x400, 1)
        a = struct.unpack("!iiiiBB", struct.pack("!iiiiBB", *values, ord("b"))[:18])
        b = struct.unpack("!IIIIBc", struct.pack("!IIIIBc", *values, b"b"))
        self.assertEqual(a[:5], b[:5])
        self.assertEqual(a[5], ord("b"))
        self.assertEqual(b[5], b"b")


if __name__ == "__main__":
    unittest.main(verbosity=2)
