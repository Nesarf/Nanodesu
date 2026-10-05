"""The voice now reaches the commands, and the one character that disagreed across two files.

## The wiring, and the bug it exposed

`PROSE` and `NSFW_PROSE` existed with three registers, a gate, and tests — and were referenced from
exactly two places: `--help` and the no-command path. **Every real path was silent.** The parts were
all there and nothing was connected, which is the kind of gap tests do not find because each piece
passed on its own.

Wiring it found a second defect, and one that no visual check could: the same sentence existed in
`nanodesu.py` and `PERSONA.md` with **different characters for 実** — U+5B9E (simplified Chinese) in
the code, U+5B9F (Japanese) in the document. The terminal renders both the same, so every reading of
the output agreed with itself.

`unicodedata` cannot help: both are `CJK UNIFIED IDEOGRAPH` and Unicode carries no simplified-to-
traditional mapping. What *can* be checked is that two files saying the same sentence say it with the
same code points, which is exactly the failure that occurred.
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

NANODESU = HERE.parent / "nanodesu.py"

spec = importlib.util.spec_from_file_location("nano_bv", HERE.parent / "boundary_voice.py")
bv = importlib.util.module_from_spec(spec)
sys.modules["nano_bv"] = bv
spec.loader.exec_module(bv)

nspec = importlib.util.spec_from_file_location("nano_v", NANODESU)
nano = importlib.util.module_from_spec(nspec)
sys.modules["nano_v"] = nano
nspec.loader.exec_module(nano)

PERSONA = HERE.parent / "PERSONA.md"

# The two characters that disagreed. Built from code points so this file cannot be mangled by the
# same accident it exists to catch.
SIMPLIFIED = chr(0x5B9E)
JAPANESE = chr(0x5B9F)


class TestTheSameSentenceAgreesAcrossFiles(unittest.TestCase):
    """The defect that no visual check could see."""

    def test_the_code_uses_the_japanese_form(self):
        text = nano.PROSE["cannot_tell"][0]
        self.assertNotIn(SIMPLIFIED, text,
                         "the voice table uses the simplified Chinese form; the terminal shows both "
                         "identically, so this can only be caught by code point")
        self.assertIn(JAPANESE, text)

    def test_the_document_uses_the_same_form_where_it_quotes_the_voice(self):
        """Only the quoted Japanese is checked. The document is Chinese prose and simplified forms
        are correct there -- an earlier version of this test tried to subtract the Chinese words
        with a chain of `replace` calls, which is a denylist wearing a filter's clothes."""
        doc = PERSONA.read_text(encoding="utf-8")
        i = doc.index(chr(0x8AA0))                 # 誠, which only occurs in the quoted line
        self.assertEqual(ord(doc[i + 1]), 0x5B9F,
                         "the document quotes the word with the simplified character")

    def test_a_sentence_present_in_both_is_identical(self):
        """The precise failure: 誠実 appeared in both files with different characters.

        Compared around the word rather than by whole-sentence equality. The document writes
        `分からない、と言うのが` with a comma and the code writes it without one -- a punctuation
        difference that is irrelevant to the defect and would make an exact comparison fail for the
        wrong reason, which is the same "measuring the formatting" mistake in a new costume.
        """
        code = nano.PROSE["cannot_tell"][0]
        doc = re.sub(r"[*`_]", "", PERSONA.read_text(encoding="utf-8"))

        def neighbourhood(text):
            i = text.index(chr(0x8AA0))            # 誠
            window = text[i:i + 4]
            return "".join(c for c in window if c not in "、。,.「」")

        self.assertEqual(neighbourhood(code), neighbourhood(doc),
                         "the same word is surrounded by different characters in the two files, "
                         "which means one of them is the wrong variant")

class TestTheVoiceReachesRealPaths(unittest.TestCase):
    """It used to reach only --help and the no-command path."""

    def _run(self, args, encoding="utf-8"):
        env = dict(os.environ, PYTHONIOENCODING=encoding)
        return subprocess.run([sys.executable, str(NANODESU)] + args,
                              capture_output=True, text=True, env=env, encoding="utf-8",
                              errors="replace")

    def test_a_broken_archive_is_spoken_to(self):
        """The path where the tool genuinely does not know what it is looking at, and which used to
        exit in silence because the failure happens before any command runs."""
        import struct
        import tempfile
        import zlib
        magic = b"MEI" + bytes([12, 11, 10, 11, 14])
        payload = zlib.compress(b"hello", 9)
        name = b"x.txt" + bytes(11)
        toc = struct.pack("!IIIIBc", 19, 0, len(payload), 5, 1, b"b") + name
        pkg_len = 88 + len(payload) + len(toc)
        cookie = struct.pack("!8sIIII64s", magic, pkg_len, 88 + len(payload), len(toc), 312,
                             b"python312.dll")
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "broken.exe"
            f.write_bytes(bytes(4096) + bytes(88) + payload + toc + cookie)
            out = self._run(["info", str(f)])
        self.assertNotEqual(out.returncode, 0, "a broken archive should fail")
        self.assertTrue(any(ord(c) > 0x2FFF for c in out.stdout),
                        "the refusal path said nothing, which is the one path that most needs to")

    def test_plain_silences_it_on_that_path_too(self):
        import struct
        import tempfile
        import zlib
        magic = b"MEI" + bytes([12, 11, 10, 11, 14])
        payload = zlib.compress(b"hello", 9)
        name = b"x.txt" + bytes(11)
        toc = struct.pack("!IIIIBc", 19, 0, len(payload), 5, 1, b"b") + name
        cookie = struct.pack("!8sIIII64s", magic, 88 + len(payload) + len(toc),
                             88 + len(payload), len(toc), 312, b"python312.dll")
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "broken.exe"
            f.write_bytes(bytes(4096) + bytes(88) + payload + toc + cookie)
            out = self._run(["--plain", "info", str(f)])
        self.assertFalse(any(ord(c) > 0x2FFF for c in out.stdout),
                         "--plain must silence the refusal path as well")

    def test_the_boundary_notice_survives_plain(self):
        """`--plain` turns off the voice, not the boundary. The boundary is data, not register."""
        out = self._run(["--plain", "--boundary"])
        self.assertEqual(out.returncode, 0)
        self.assertIn("never executes", out.stdout)


class TestNarrationCannotBreakACommand(unittest.TestCase):
    """The analysis has already succeeded by the time anything speaks."""

    def test_speak_returns_empty_rather_than_raising(self):
        class Hostile:
            @property
            def toc(self):
                raise RuntimeError("boom")

            @property
            def integrity(self):
                raise RuntimeError("boom")
        self.assertEqual(bv.speak(Hostile(), mode="info", say=lambda k: "x", plain=False), [])

    def test_emit_refusal_tolerates_a_broken_say(self):
        def bad(_key):
            raise RuntimeError("boom")
        bv.emit_refusal("why", say=bad, plain=False)      # must not raise

    def test_speak_does_nothing_when_plain(self):
        class Ok:
            toc = []
            integrity = []
        self.assertEqual(bv.speak(Ok(), mode="info", say=lambda k: "x", plain=True), [])


class TestTheBeatIsChosenFromEvidence(unittest.TestCase):
    def _ar(self, integrity=None, codes=("b",)):
        class E:
            def __init__(self, t):
                self.tcode = t

        class A:
            def __init__(self):
                self.toc = [E(c) for c in codes]
                self.integrity = integrity or []

            def pyver_str(self):
                return "3.12"
        return A()

    def test_nothing_wrong_is_bored(self):
        self.assertEqual(bv.choose_beat(self._ar()), "bored")

    def test_a_structural_problem_is_probing(self):
        self.assertEqual(bv.choose_beat(self._ar(integrity=["truncated"])), "probing")

    def test_a_wrapper_is_pressed(self):
        self.assertEqual(bv.choose_beat(self._ar(), wrapped=True), "pressed")

    def test_a_structural_problem_outranks_a_wrapper(self):
        """A structural problem is the only category where something is actually wrong."""
        self.assertEqual(bv.choose_beat(self._ar(integrity=["x"]), wrapped=True), "probing")


if __name__ == "__main__":
    unittest.main(verbosity=2)
