# -*- coding: utf-8 -*-
"""The persona never reaches a machine-readable output.

That sentence appears in four documents and, until this file, was verified nowhere. It is the same shape as
the 1.7.0 gap: the parts existed, the wiring was asserted, and nothing checked it.

So the check is mechanical rather than argued. Take every line the persona can say, switch the register on,
produce the machine-readable artifacts, and require that none of those lines appears in any of them.

**Only the persona half of each pair is checked.** Every entry is `(Lilith, plain)`, and the plain half is
deliberately allowed in human output when the persona is off — asserting on it would assert the opposite of
the design.

And an absence test is worth nothing unless the thing could have been present, so one test here pins the
other direction: that `say` really does return a line from the tables when the register is on.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("nanodesu_voice", ROOT / "nanodesu.py")
nano = importlib.util.module_from_spec(spec)
sys.modules["nanodesu_voice"] = nano
spec.loader.exec_module(nano)

# The archive fixture lives beside this test; importing a sibling test module for its helpers is the
# house idiom here (test_api.py does the same), and it keeps one definition of "a small valid archive".
spec = importlib.util.spec_from_file_location("tn_voice", HERE / "test_nanodesu.py")
tn = importlib.util.module_from_spec(spec)
sys.modules["tn_voice"] = tn
spec.loader.exec_module(tn)


def persona_lines():
    """Every line the persona can say, in either register -- the persona half of each pair only."""
    out = []
    for table in (nano.PROSE, nano.NSFW_PROSE):
        for entry in table.values():
            out.append(entry[0] if isinstance(entry, tuple) else entry)
    return [line for line in out if isinstance(line, str) and line]


class TestNoPersonaInMachineOutput(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.lines = persona_lines()
        self.assertGreaterEqual(len(self.lines), 8,
                                "the prose tables were not found, so nothing would be checked")
        self.addCleanup(nano.set_plain, False)
        self.addCleanup(nano.set_nsfw, False)

    def _artifacts(self):
        """The machine-readable things this tool produces, as text.

        **Both writers are covered, because there are two.** The manifest is written once by `cmd_extract`
        and once by the library `extract()`, and they are separate code — measured while breaking this test
        on purpose: injecting a persona line into the CLI writer left this test green, because it only ever
        called the API. A guard that covers one of two writers is a guard over half the surface.
        """
        import contextlib
        import io

        src = tn.make_archive(self.dir / "a.exe", tn.SAMPLE)

        api_out = self.dir / "api"
        nano.extract(src, api_out)

        cli_out = self.dir / "cli"
        with contextlib.redirect_stdout(io.StringIO()):
            rc = nano.main(["extract", str(src), "-o", str(cli_out)])
        self.assertEqual(rc, 0, "the extract command failed, so its manifest was never written")

        data = json.dumps({
            "info": nano.inspect_archive(src),
            "entries": nano.list_entries(src),
            "verify": nano.verify_archive(src),
        }, ensure_ascii=False, indent=1)
        return {
            "api manifest": (api_out / "_archive_manifest.json").read_text(encoding="utf-8"),
            "cli manifest": (cli_out / "_archive_manifest.json").read_text(encoding="utf-8"),
            "api data": data,
        }

    def test_no_persona_line_reaches_an_artifact(self):
        artifacts = self._artifacts()          # produced once; the registers must not change what is written
        # **Collect every leak before asserting**, rather than failing on the first one. The first version
        # used `assertNotIn` inside the loop, so a leak in one artifact ended the subtest and the other
        # artifacts were never examined -- which reads as "one artifact is affected" when the truth may be
        # "all of them are". A failure message should describe the whole surface, not the first pixel of it.
        leaks = []
        for register in ("plain", "persona", "nsfw"):
            nano.set_plain(register == "plain")
            nano.set_nsfw(register == "nsfw")
            for name, text in artifacts.items():
                for line in self.lines:
                    if line in text:
                        leaks.append((register, name, line))
        self.assertEqual(
            leaks, [],
            "%d persona line(s) reached a machine-readable artifact: %s"
            % (len(leaks), leaks[:4]))

    def test_the_register_really_is_on(self):
        """An absence test is worthless if the thing could never have been there.

        With the persona on, `say` must return a line from the tables; with plain on, the other half. A
        `say` that returned the key itself -- or the plain half -- would make the assertion above pass for
        the wrong reason.
        """
        nano.set_plain(False)
        nano.set_nsfw(False)
        persona = nano.say("verified")
        nano.set_plain(True)
        plain = nano.say("verified")
        self.assertIn(persona, self.lines, "say() is not returning a line from the tables")
        self.assertNotIn(plain, self.lines, "the plain half leaked into the persona half")
        self.assertNotEqual(persona, plain)


if __name__ == "__main__":
    unittest.main()
