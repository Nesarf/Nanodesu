# -*- coding: utf-8 -*-
"""The boundary notice: one constant, attached to every path a caller can take.

Taken from the same rule a Tor/Firefox posture auditor uses, and for the same reason. **Attached in
one convenient place, a notice like this is missing from exactly the results a careless reader is
most likely to over-read.**

What it guards against is specific to this tool. An extraction produces a directory full of somebody
else's code and a report describing it, and there are four things a reader will otherwise assume:
that extraction means source, that a byte-identical repack means safety, that a clean run means
nothing is hidden, and that unpacking succeeded means unpacking is complete.
"""
from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

spec = importlib.util.spec_from_file_location("nano_boundary", HERE.parent / "boundary.py")
bd = importlib.util.module_from_spec(spec)
sys.modules["nano_boundary"] = bd
spec.loader.exec_module(bd)

SOURCE = HERE.parent / "nanodesu.py"
NANODESU = HERE.parent / "nanodesu.py"


class TestTheNoticeIsAStructureNotProse(unittest.TestCase):
    def test_it_is_a_list_of_lines(self):
        """Separate lines survive rendering as separate points; one paragraph gets skimmed."""
        self.assertIsInstance(bd.BOUNDARY_NOTICE, list)
        self.assertGreaterEqual(len(bd.BOUNDARY_NOTICE), 4)

    def test_every_line_is_a_complete_sentence(self):
        for line in bd.BOUNDARY_NOTICE:
            with self.subTest(line=line[:40]):
                self.assertTrue(line.strip())
                self.assertTrue(line.rstrip().endswith("."))

    def test_no_line_is_merely_cautioning_in_general(self):
        """A vague caveat gets skimmed. Each line has to name a specific thing."""
        text = " ".join(bd.BOUNDARY_NOTICE).lower()
        for specific in ("does not address", "not proof", "not a decompiler",
                         "not a malware detector"):
            with self.subTest(needle=specific):
                self.assertIn(specific, text)


class TestTheSpecificOverReadingsAreNamed(unittest.TestCase):
    """The four assumptions a reader brings to an extraction, each answered explicitly."""

    def test_it_says_bytecode_is_not_source(self):
        text = " ".join(bd.BOUNDARY_NOTICE).lower()
        self.assertIn("not source", text)

    def test_it_says_a_byte_identical_repack_is_not_safety(self):
        text = " ".join(bd.BOUNDARY_NOTICE).lower()
        self.assertIn("byte-identical repack proves fidelity and not safety", text)

    def test_it_says_content_outside_the_toc_is_invisible(self):
        """The payloads that matter most are the ones no archive entry describes."""
        text = " ".join(bd.BOUNDARY_NOTICE).lower()
        self.assertIn("never appears in the table of contents", text)

    def test_it_says_extraction_reports_structure_not_intent(self):
        text = " ".join(bd.BOUNDARY_NOTICE).lower()
        self.assertIn("structure, not intent", text)


class TestItTravelsWithResults(unittest.TestCase):
    def test_the_wrapper_attaches_it(self):
        self.assertIn("boundary_notice", bd.with_boundary({"a": 1}))

    def test_the_wrapper_does_not_mutate_its_input(self):
        original = {"a": 1}
        bd.with_boundary(original)
        self.assertNotIn("boundary_notice", original,
                         "the caller's dict was modified, which would surprise a library user")

    def test_it_tolerates_a_non_dict(self):
        self.assertEqual(bd.with_boundary(None), None)

    def test_the_constant_is_the_same_object_everywhere(self):
        """One source of truth: a copy per call would let them drift apart."""
        self.assertIs(bd.with_boundary({})["boundary_notice"], bd.BOUNDARY_NOTICE)


class TestTheCliExposesIt(unittest.TestCase):
    def test_the_boundary_flag_prints_it_and_exits_zero(self):
        out = subprocess.run([sys.executable, str(NANODESU), "--boundary"],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0)
        self.assertIn("never executes", out.stdout)
        self.assertIn("Python interpreter", out.stdout)

    def test_info_carries_it_too(self):
        """Not only on request: the notice travels with a result a reader did not ask for."""
        src = SOURCE.read_text(encoding="utf-8")
        start = src.index("def cmd_info(")
        body = src[start:src.index("\ndef ", start + 1)]
        self.assertIn("boundary.format_boundary()", body,
                      "cmd_info does not print the boundary")

    def test_the_source_never_claims_the_output_is_safe(self):
        """Overclaim is what the notice exists to counter, so the code must not contradict it."""
        lowered = SOURCE.read_text(encoding="utf-8").lower()
        for phrase in ("is now safe", "the file is safe", "guaranteed safe", "no malware"):
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, lowered)

    def test_the_notice_does_not_claim_no_process_is_ever_created(self):
        """The first version did, and it was false.

        `_magic_for` launches a Python interpreter to ask for its own bytecode magic number. It does
        not touch the archive under audit, but "nothing here creates a process" was simply untrue,
        and a boundary notice that is untrue in its first line is worse than none.
        """
        text = " ".join(bd.BOUNDARY_NOTICE).lower()
        self.assertNotIn("nothing here creates a process", text)
        self.assertIn("python interpreter", text,
                      "the notice must name the one process this tool can create")

    def test_the_one_process_it_creates_is_only_ever_a_magic_number_lookup(self):
        """Checked, so the sentence stays true rather than merely worded carefully.

        Every `subprocess` call in the tool must pass `-c` with a magic-number query. Anything else
        -- an interpreter pointed at a file, a shell, a launch of the target -- would make the notice
        a lie at the one place it matters most.
        """
        src = SOURCE.read_text(encoding="utf-8")
        self.assertIn("subprocess.run", src)
        # Walk the call sites: each must be the interpreter query, recognisable by both markers.
        for chunk in src.split("subprocess.run(")[1:]:
            call = chunk[:400]
            with self.subTest(call=call[:60]):
                self.assertIn("-c", call,
                              "a subprocess call without -c is not the magic-number lookup")
                self.assertIn("MAGIC_NUMBER", call,
                              "a subprocess call that does not ask for a magic number does "
                              "something the boundary notice does not describe")

    def test_the_archive_under_audit_is_never_passed_to_a_process(self):
        src = SOURCE.read_text(encoding="utf-8")
        for chunk in src.split("subprocess.run(")[1:]:
            call = chunk[:400]
            with self.subTest(call=call[:60]):
                for suspicious in ("ar.path", "args.target", "target)"):
                    self.assertNotIn(suspicious, call,
                                     "the archive under audit reached a subprocess")


if __name__ == "__main__":
    unittest.main(verbosity=2)
