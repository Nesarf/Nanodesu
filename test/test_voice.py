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

    def test_the_manifest_writer_cannot_reach_the_voice(self):
        """The invariant that actually matters, and it is narrower than it first looked.

        An earlier version of this test asserted that nothing reachable from `cmd_extract` mentions
        the voice, on the reasoning that extraction writes a manifest. **That premise was wrong**:
        `cmd_extract` is a human-facing command that writes the manifest as a side effect, so the
        voice belongs in it. What must never reach the voice is the code that *serialises* the
        machine-readable output -- the manifest writer, and anything else a program reads back.

        Checking the transitively reachable set from `cmd_extract` was also self-defeating: it grows
        every time a human-facing line is added, so it fails for the wrong reason.
        """
        import ast

        src = SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(src)
        funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}

        # The writers, and everything they call. These are the ones a program consumes.
        roots = [name for name in funcs
                 if name in ("cmd_build",) or "manifest" in name.lower()
                 or "write_" in name]
        self.assertTrue(roots, "no writer functions found to check")

        reachable, stack = set(), list(roots)
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
                        self.assertNotIn(node.id, ("say", "is_plain", "emit", "emit_refusal",
                                                   "boundary_voice"),
                                         "%s can reach %s, so the voice is reachable from output a "
                                         "program reads back" % (name, node.id))

    def test_the_report_to_a_person_does_carry_it(self):
        """The other direction, so the narrowed test above cannot pass by the voice being gone."""
        src = SOURCE.read_text(encoding="utf-8")
        for fn in ("cmd_info", "cmd_extract"):
            start = src.index("def %s(" % fn)
            body = src[start:src.index("\ndef ", start + 1)]
            with self.subTest(func=fn):
                self.assertIn("boundary_voice.emit", body,
                              "%s is a human-facing command and should speak" % fn)

if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestTheAdultRegisterIsGatedOff(unittest.TestCase):
    """Off unless asked for, and `--plain` wins if both are asked for.

    The precedence is not arbitrary: `--plain` is a promise that the output is impersonal, and a
    persona somebody cannot silence has been forced on them. So silence outranks heat.
    """

    def setUp(self):
        self._p, self._n = nano.is_plain(), nano.is_nsfw()
        self.addCleanup(nano.set_plain, self._p)
        self.addCleanup(nano.set_nsfw, self._n)
        nano.set_plain(False)
        nano.set_nsfw(False)

    def test_off_by_default(self):
        self.assertFalse(nano.is_nsfw())

    def test_the_flag_enables_it(self):
        nano.set_nsfw(True)
        self.assertTrue(nano.is_nsfw())

    def test_plain_overrides_nsfw(self):
        nano.set_nsfw(True)
        nano.set_plain(True)
        self.assertFalse(nano.is_nsfw(),
                         "--plain must silence the adult register as well, not just the normal one")

    def test_the_environment_variable_is_honoured(self):
        env = dict(os.environ, NANODESU_NSFW="1")
        out = subprocess.run([sys.executable, str(SOURCE), "--help"],
                             capture_output=True, text=True, env=env)
        self.assertEqual(out.returncode, 0)

    def test_both_flags_together_is_not_an_error(self):
        out = subprocess.run([sys.executable, str(SOURCE), "--plain", "--nsfw", "--help"],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0)
        self.assertFalse(any(ord(ch) > 0x2FFF for ch in out.stdout),
                         "--plain --nsfw printed a persona")

    def test_the_adult_table_has_a_different_line_for_every_key_it_defines(self):
        """An adult register that repeats the normal one is not a register, it is a rename."""
        for key, (adult, plain) in nano.NSFW_PROSE.items():
            with self.subTest(key=key):
                self.assertNotEqual(adult, nano.PROSE[key][0],
                                    "%s is identical in both registers" % key)


class TestNoRegisterCanLeakAKeyName(unittest.TestCase):
    """`say` returns the key itself when neither table has it, so a key with no line prints jargon.

    Found while wiring the adult register: asking for an adult line with the register off printed the
    literal string "handed_over". Three keys in the normal table had the same shape -- their plain
    value was the key name -- which is indistinguishable from the failure at the call site.
    """

    def setUp(self):
        self._p, self._n = nano.is_plain(), nano.is_nsfw()
        self.addCleanup(nano.set_plain, self._p)
        self.addCleanup(nano.set_nsfw, self._n)

    def test_every_key_has_a_line_in_every_register(self):
        keys = set(nano.PROSE) | set(nano.NSFW_PROSE)
        self.assertTrue(keys)
        for key in sorted(keys):
            for plain, nsfw in ((False, False), (False, True), (True, False)):
                nano.set_plain(plain)
                nano.set_nsfw(nsfw)
                with self.subTest(key=key, plain=plain, nsfw=nsfw):
                    self.assertNotEqual(nano.say(key), key,
                                        "%s has no line with plain=%s nsfw=%s, so the output would "
                                        "contain the key name" % (key, plain, nsfw))

    def test_no_line_equals_its_own_key(self):
        """The other half: a plain value equal to the key name is a silent version of the same bug."""
        for key, (_lilith, plain) in nano.PROSE.items():
            with self.subTest(key=key):
                self.assertNotEqual(plain, key,
                                    "the plain register for %s is the key name itself" % key)


class TestTheVoiceSurvivesANonUtf8Console(unittest.TestCase):
    """The regression CI caught and no test did, because every test ran where stdout was UTF-8.

    Windows binds `sys.stdout` to the console code page -- cp1252 on a stock runner -- so printing
    Japanese raised UnicodeEncodeError and the built executable failed its own sanity check:

        UnicodeEncodeError: 'charmap' codec can't encode characters in position 3085-3096

    The whole of 1.3.x printed nothing but ASCII, so this arrived with the voice. Checked here by
    forcing the encoding through the environment, which is the condition that broke.
    """

    def _run(self, argv, encoding="cp1252"):
        env = dict(os.environ, PYTHONIOENCODING=encoding)
        return subprocess.run([sys.executable, str(SOURCE)] + argv,
                              capture_output=True, text=True, env=env, encoding="utf-8",
                              errors="replace")

    def test_the_default_register_survives_cp1252(self):
        out = self._run(["--help"])
        self.assertEqual(out.returncode, 0,
                         "the persona cannot print on a non-UTF-8 console:\n%s"
                         % (out.stderr or "")[-600:])
        self.assertNotIn("UnicodeEncodeError", out.stderr or "")

    def test_plain_survives_too_and_stays_ascii(self):
        out = self._run(["--plain", "--help"])
        self.assertEqual(out.returncode, 0)
        self.assertFalse(any(ord(ch) > 127 for ch in out.stdout),
                         "plain output is not ASCII, so a non-UTF-8 console cannot render it")

    def test_the_no_command_path_survives(self):
        """This path prints a line from the voice, so it is the shortest reproduction."""
        out = self._run([])
        self.assertEqual(out.returncode, 0)
        self.assertNotIn("UnicodeEncodeError", out.stderr or "")

    def test_a_utf8_console_still_gets_real_japanese(self):
        """The fix must not degrade the normal case into replacement characters."""
        out = self._run(["--help"], encoding="utf-8")
        self.assertEqual(out.returncode, 0)
        self.assertNotIn("\ufffd", out.stdout,
                         "the voice arrived as replacement characters on a UTF-8 console")
        self.assertTrue(any(ord(ch) > 0x2FFF for ch in out.stdout))
