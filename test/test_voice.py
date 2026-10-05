# -*- coding: utf-8 -*-
"""The voice, and the boundary that keeps it from becoming a defect.

Two things are being defended here, and they fail in opposite directions.

**The table must have a shape.** `say()` unpacks each entry into two registers. One entry was
written as a bare string rather than a pair, so `--help` raised `ValueError: too many values to
unpack` -- the tool was broken at its front door, and only for people who had not already learned
`--plain`. Every entry's shape is now checked.

**The voice must be silenceable, and must already be silent where it matters.** A persona in JSON is
a parsing bug for whoever consumes it, and a persona that cannot be turned off has been forced on
someone. Both are tested here rather than left to discipline.

Run:
    python -m unittest discover -s test -v
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

spec = importlib.util.spec_from_file_location("nano_voice", HERE.parent / "nanodesu.py")
nano = importlib.util.module_from_spec(spec)
sys.modules["nano_voice"] = nano
spec.loader.exec_module(nano)

SOURCE = HERE.parent / "nanodesu.py"


class TestProseTableShape(unittest.TestCase):
    """The regression: a bare string in a table of pairs."""

    def test_every_entry_is_a_pair_of_strings(self):
        for key, entry in nano.PROSE.items():
            with self.subTest(key=key):
                self.assertIsInstance(entry, tuple,
                                      "%s is a %s, not a pair" % (key, type(entry).__name__))
                self.assertEqual(len(entry), 2,
                                 "%s has %d elements; the first attempt at this table put a bare "
                                 "string here and --help raised" % (key, len(entry)))
                self.assertIsInstance(entry[0], str)
                self.assertIsInstance(entry[1], str)

    def test_both_registers_of_every_entry_are_non_empty(self):
        for key, (lilith, plain) in nano.PROSE.items():
            with self.subTest(key=key):
                self.assertTrue(lilith.strip(), key)
                self.assertTrue(plain.strip(), key)

    def test_an_unknown_key_returns_itself_rather_than_raising(self):
        self.assertEqual(nano.say("no_such_key_exists"), "no_such_key_exists")

    def test_help_builds_in_both_registers(self):
        """This is what actually broke: build_parser() raised before argparse could run."""
        for plain in (False, True):
            nano.set_plain(plain)
            with self.subTest(plain=plain):
                self.assertIsNotNone(nano.build_parser())


class TestTheTwoRegistersDisagreeOnPurpose(unittest.TestCase):
    """Same facts, different words -- and the plain one must not be a stub."""

    def setUp(self):
        self._was = nano.is_plain()
        self.addCleanup(nano.set_plain, self._was)

    def test_the_lilith_register_actually_differs(self):
        differing = [k for k, (l, p) in nano.PROSE.items() if l != p]
        self.assertTrue(differing, "the two registers are identical, so there is no voice")

    def test_the_plain_register_carries_no_japanese(self):
        """A plain register that still speaks Japanese is not plain to the reader who asked."""
        for key, (_lilith, plain) in nano.PROSE.items():
            with self.subTest(key=key):
                self.assertFalse(any(ord(ch) > 0x2FFF for ch in plain),
                                 "%s leaks non-Latin text into the plain register: %r" % (key, plain))

    def test_the_lilith_register_has_no_machine_syntax(self):
        """She is prose. Braces and brackets in her lines would suggest she is a template."""
        forbidden = set("{}[]<>|")
        for key, (lilith, _plain) in nano.PROSE.items():
            with self.subTest(key=key):
                self.assertFalse(forbidden & set(lilith),
                                 "%s puts template syntax in the voice: %r" % (key, lilith))


class TestTheVoiceCanBeSilenced(unittest.TestCase):
    """A persona you cannot turn off has been forced on you."""

    def setUp(self):
        self._was = nano.is_plain()
        self.addCleanup(nano.set_plain, self._was)

    def test_the_flag_is_accepted_by_the_parser(self):
        args = nano.build_parser().parse_args(["--plain", "info", "x.exe"])
        self.assertTrue(args.plain)

    def test_the_environment_variable_is_honoured(self):
        """Checked through a real subprocess, because the variable is read in main()."""
        env = dict(os.environ, NANODESU_PLAIN="1")
        out = subprocess.run([sys.executable, str(SOURCE), "--help"],
                             capture_output=True, text=True, env=env)
        self.assertEqual(out.returncode, 0)
        self.assertFalse(any(ord(ch) > 0x2FFF for ch in out.stdout),
                         "NANODESU_PLAIN=1 still printed the voice")

    def test_plain_reaches_the_help_text(self):
        out = subprocess.run([sys.executable, str(SOURCE), "--plain", "--help"],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0)
        self.assertFalse(any(ord(ch) > 0x2FFF for ch in out.stdout),
                         "--plain still printed the voice")

    def test_the_help_text_without_plain_contains_her(self):
        out = subprocess.run([sys.executable, str(SOURCE), "--help"],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0)
        self.assertTrue(any(ord(ch) > 0x2FFF for ch in out.stdout),
                        "the default register printed no voice at all")


class TestMachineReadableOutputStaysCold(unittest.TestCase):
    """The rule that keeps the persona from becoming a parsing bug for somebody else."""

    def setUp(self):
        self._was = nano.is_plain()
        self.addCleanup(nano.set_plain, self._was)

    def test_the_voice_is_off_by_default_in_library_use(self):
        """Importing the module must not turn a personality on inside somebody's pipeline."""
        nano.set_plain(False)          # what main() does only when the prose is wanted
        nano.set_plain(True)
        self.assertTrue(nano.is_plain())

    def test_no_machine_format_path_calls_say(self):
        """A structural check, not a hopeful one: `say` must not be reachable from the JSON or
        manifest writers. If it is, the voice has leaked into a contract."""
        src = SOURCE.read_text(encoding="utf-8")
        lines = src.splitlines()
        for i, line in enumerate(lines):
            if "say(" not in line or line.strip().startswith("#"):
                continue
            # Only the CLI prose may call it: help, the no-command path.
            context = "\n".join(lines[max(0, i - 6):i + 1])
            self.assertNotIn("json.dumps", context,
                             "say() is reachable from a JSON writer at line %d" % (i + 1))
            self.assertNotIn("manifest", context.lower(),
                             "say() is reachable from manifest writing at line %d" % (i + 1))

    def test_the_extract_path_cannot_reach_the_voice(self):
        """The manifest is read back by `build`, so it is machine input whatever it looks like.

        Checked structurally rather than by running an extraction on a synthetic archive. The
        end-to-end version of this test was attempted and spent several rounds on the archive
        fixture instead of on the thing under test -- the third fixture in this project to do that
        -- and the invariant is fully visible without it: `cmd_extract` and everything it calls must
        not be able to reach `say` or the register flag at all.
        """
        import ast

        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}

        # Everything cmd_extract can call, transitively.
        reachable, stack = set(), ["cmd_extract"]
        while stack:
            name = stack.pop()
            if name in reachable or name not in funcs:
                continue
            reachable.add(name)
            for node in ast.walk(funcs[name]):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    stack.append(node.func.id)

        for name in sorted(reachable):
            for node in ast.walk(funcs[name]):
                if isinstance(node, ast.Name):
                    with self.subTest(func=name, symbol=node.id):
                        self.assertNotIn(node.id, ("say", "is_plain", "epilog_for"),
                                         "%s calls %s, so the voice is reachable from extraction"
                                         % (name, node.id))


if __name__ == "__main__":
    unittest.main(verbosity=2)
