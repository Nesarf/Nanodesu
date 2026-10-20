# -*- coding: utf-8 -*-
"""Tests for the window: a panel painted from two files this repository embeds.

**The commit that added `--window` shipped it untested, undeclared and undocumented**, and three defects
followed from that single omission. Each was found by looking, not by a red test:

* `panel_window` was missing from `py-modules` and neither JSON was declared anywhere, so a built wheel
  carried no way to open a window at all -- it worked from a clone and nowhere else;
* the renderer upper-cased its input, which threw away the 26 lower-case glyphs the shared font carries;
* `--window` was accepted on every command and silently dropped where it meant nothing, so
  `extract --window` exited 0 having opened nothing.

So most of what is asserted here happens *before* a window opens -- which is also the only part that can
be: opening one blocks in `mainloop`, and a suite that hangs is worse than a suite that fails.

Run:
    python -m unittest discover -s test -v
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("nanodesu", ROOT / "nanodesu.py")
nano = importlib.util.module_from_spec(spec)
sys.modules["nanodesu"] = nano
spec.loader.exec_module(nano)

import panel_window as pw                                          # noqa: E402

try:
    from PIL import Image
    HAVE_PIL = True
except Exception:                                                  # noqa: BLE001
    HAVE_PIL = False

LAYOUT = pw.load(ROOT / "panel.json")
GLYPH = pw.load_font(ROOT / "font.json")


class TestTheEmbeddedFiles(unittest.TestCase):

    def test_the_layout_validates_its_own_checksum(self):
        body = json.loads((ROOT / "panel.json").read_text(encoding="utf-8"))
        self.assertEqual(body["format"], pw.FORMAT)
        got = hashlib.sha256(pw.canonical({k: v for k, v in body.items() if k != "checksum"}
                                          ).encode("utf-8")).hexdigest()
        self.assertEqual(body["checksum"], got)
        self.assertEqual(tuple(body["size"]), (716, 385))

    def test_the_layout_carries_no_tool_name(self):
        """**The bar's geometry is shared; the words in it are not.** A title baked into the file would
        make each tree's copy differ, and then comparing checksums could no longer answer the one
        question worth asking -- is this the same panel."""
        body = json.loads((ROOT / "panel.json").read_text(encoding="utf-8"))
        self.assertIsNone(body.get("title"),
                          "the exported layout embeds a title, so two tools' copies can never match")

    def test_the_content_geometry_is_derived(self):
        cx0, cy0, cx1, cy1 = LAYOUT.layout["content"]
        self.assertEqual(LAYOUT.body_cols, (cx1 - cx0) // pw.CELL_W)
        self.assertEqual(LAYOUT.body_rows, (cy1 - cy0) // pw.CELL_H)

    def test_the_resolver_finds_the_files_where_they_are(self):
        """One resolver, two locations: beside the module, or in the install's share directory."""
        for name in ("panel.json", "font.json"):
            path = (ROOT / name)
            self.assertTrue(path.is_file(), "%s is not in the repository" % name)

    def test_the_window_extra_is_declared(self):
        """`--window` needs an image library and the base install is deliberately empty (`dependencies
        = []`). A bare install imports `panel_window` fine -- PIL is imported inside the drawing call --
        and then `open_window` raises, so the dependency is real and invisible until the flag is used."""
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        extra = re.search(r"^window\s*=\s*\[([^\]]*)\]", pyproject, re.M)
        self.assertIsNotNone(extra, "no `window` extra, so a pip install can never draw one")
        self.assertIn("pillow", extra.group(1).lower())

    def test_a_wheel_carries_the_module_and_both_files(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        modules = re.search(r"^py-modules\s*=\s*\[(.*?)\]", pyproject, re.S | re.M)
        self.assertIsNotNone(modules)
        self.assertIn('"panel_window"', modules.group(1),
                      "a wheel without this module cannot open a window")
        files = re.search(r"^\[tool\.setuptools\.data-files\](.*?)(?=^\[|\Z)", pyproject, re.S | re.M)
        self.assertIsNotNone(files, "no data-files table, so the JSON cannot reach a wheel")
        self.assertIn("panel.json", files.group(1))
        self.assertIn("font.json", files.group(1))


class TestTheBodyInk(unittest.TestCase):
    """Body text used to be drawn in the title bar's ink.

    All three renderers did it -- two Python and one PowerShell -- so a change to the bar's colour silently
    restyled every report and nothing said the body was borrowing. The layout names the job now, and a file
    exported before the role existed still draws in the colour it always used.
    """

    def test_the_two_inks_are_separate_jobs(self):
        body_key = LAYOUT.key_of["content_text"]
        title_key = LAYOUT.key_of["title_text"]
        self.assertNotEqual(body_key, title_key)
        self.assertEqual(LAYOUT.content_ink, LAYOUT.colours[body_key])
        self.assertNotEqual(LAYOUT.content_ink, LAYOUT.colours[title_key])

    @unittest.skipUnless(HAVE_PIL, "PIL is what the renderer draws with")
    def test_rendered_body_text_uses_that_ink_and_not_the_bar_s(self):
        cx0, cy0, _cx1, _cy1 = LAYOUT.layout["content"]
        _tx0, ty0, _tx1, _ty1 = LAYOUT.layout["title"]
        start, _room = LAYOUT.title_room
        im = pw.image(LAYOUT, ["I"], title="T", scale=1, draw_fn=GLYPH)

        def cell(x, y):
            return [im.getpixel((x + dx, y + dy))
                    for dy in range(pw.CELL_H) for dx in range(pw.CELL_W)]

        body, title = cell(cx0, cy0), cell(start, ty0 + 1)
        self.assertIn(LAYOUT.content_ink, body, "the body was not drawn in the layout's own ink")
        self.assertNotIn(LAYOUT.colours[LAYOUT.key_of["title_text"]], body,
                         "the body is still borrowing the title bar's ink")
        self.assertNotIn(LAYOUT.content_ink, title, "the title is drawn in the body's ink")

    def test_a_file_without_the_role_falls_back_to_the_title_ink(self):
        body = json.loads((ROOT / "panel.json").read_text(encoding="utf-8"))
        body["roles"] = [r for r in body["roles"] if r != "content_text"]
        del body["keys"]["content_text"]
        body["checksum"] = hashlib.sha256(
            pw.canonical({k: v for k, v in body.items() if k != "checksum"}).encode("utf-8")).hexdigest()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "panel.json"
            p.write_text(json.dumps(body), encoding="utf-8")
            old = pw.load(p)
        self.assertNotIn("content_text", old.key_of)
        self.assertEqual(old.content_ink, old.colours[old.key_of["title_text"]])


class TestCaseIsPreserved(unittest.TestCase):
    """The shared font carries 88 glyphs and 26 of them are lower case."""

    def test_the_font_holds_both_cases(self):
        body = json.loads((ROOT / "font.json").read_text(encoding="utf-8"))
        for ch in "abcdefghijklmnopqrstuvwxyz":
            self.assertIn(ch, body["glyphs"])
        self.assertNotEqual(body["glyphs"]["a"], body["glyphs"]["A"])

    @unittest.skipUnless(HAVE_PIL, "PIL is what the renderer draws with")
    def test_lower_case_is_drawn_as_lower_case(self):
        def drawn(text):
            im = Image.new("RGBA", (32, 16), (0, 0, 0, 0))
            GLYPH(im.load(), 0, 0, text, (255, 255, 255, 255))
            return [im.getpixel((x, y))[3] for y in range(8) for x in range(6)]

        self.assertNotEqual(drawn("a"), drawn("A"),
                            "the renderer upper-cases its input, so 26 glyphs can never be drawn")

    def test_a_body_line_keeps_its_case(self):
        _cx0, cy0, _cx1, _cy1 = LAYOUT.layout["content"]
        rows = pw.text(LAYOUT, ["sha256 : CE66F800e2c6"], title="T")
        self.assertIn("e2c6", rows[cy0])
        self.assertIn("CE66", rows[cy0])


class TestPagingInsteadOfClipping(unittest.TestCase):
    """`ls` on a real archive lists more entries than 46 rows, which is why this is not a nicety."""

    def test_one_page_even_when_empty(self):
        self.assertEqual(LAYOUT.page_count([]), 1)

    def test_a_page_holds_what_the_layout_says(self):
        lines = ["entry %d" % i for i in range(200)]
        rows, page, pages = LAYOUT.page_lines(lines, 2)
        self.assertEqual(len(rows), LAYOUT.body_rows)
        self.assertEqual(rows[0], "entry %d" % (2 * LAYOUT.body_rows))
        self.assertEqual((page, pages), (2, 5))

    def test_the_marker_is_drawn_only_when_it_is_true(self):
        _tx0, ty0, _tx1, _ty1 = LAYOUT.layout["title"]
        self.assertNotIn("/", pw.text(LAYOUT, ["x"], title="NANODESU")[ty0 + 1])
        self.assertIn("1/3", pw.text(LAYOUT, ["x"] * 100, title="NANODESU", page=0)[ty0 + 1])

    def test_a_line_wider_than_the_panel_is_refused_not_cut(self):
        with self.assertRaises(ValueError) as ctx:
            pw.text(LAYOUT, ["x" * 200], title="NANODESU")
        self.assertIn("200 characters", str(ctx.exception))
        self.assertIn("118", str(ctx.exception))


class TestTheFlagIsNotAcceptedAndIgnored(unittest.TestCase):

    def test_a_command_without_a_report_refuses_it(self):
        """**Refused by this tool, not by argparse.** The argument is on every subparser, so argparse
        parses it happily and the old code then dropped it; the assertion that matters is that the
        sentence is ours, because an argparse error would mean the flag was never accepted at all."""
        import contextlib
        import io
        # `extract` needs -o and `cat` does not need a name; both would reach the dispatcher.
        for argv in (["extract", "nowhere.exe", "-o", "out", "--window"],
                     ["cat", "nowhere.exe", "--window"]):
            with self.subTest(argv=argv):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    rc = nano.main(argv)
                self.assertEqual(rc, 2)
                self.assertIn("does not produce one", err.getvalue())
                self.assertIn("info, ls, tree, verify", err.getvalue())

    def test_the_reporting_commands_are_the_ones_named(self):
        self.assertEqual(nano.WINDOW_COMMANDS, ("info", "ls", "tree", "verify"))


class TestTheWindowIsWired(unittest.TestCase):
    """Captured, not refactored: a window with its own formatter would be a second answer to "what did
    this command say"."""

    def test_it_captures_the_command_and_asks_for_one_window(self):
        import unittest.mock as mock

        def fake_run(_ar, _args):
            print("line one")
            print("line two")
            return 0

        with mock.patch.object(pw, "open_window") as win:
            rc = nano._run_in_window(fake_run, None, None)
        self.assertEqual(rc, 0)
        self.assertEqual(win.call_count, 1)
        _layout, lines = win.call_args[0]
        kwargs = win.call_args[1]
        self.assertEqual(lines, ["line one", "line two"])
        self.assertEqual(kwargs["title"], "NANODESU")
        self.assertTrue(callable(kwargs["draw_fn"]))

    def test_a_missing_image_library_prints_the_report_instead(self):
        """**Measured, not assumed.** Without the `window` extra the call raised `ModuleNotFoundError`
        straight out of the flag, so somebody who asked for a window got a traceback and no report --
        and the report is the part that matters, because the base install has no dependencies on purpose.
        """
        import contextlib
        import io
        import unittest.mock as mock

        def fake_run(_ar, _args):
            print("the report")
            return 3

        with mock.patch.object(pw, "open_window",
                               side_effect=ModuleNotFoundError("No module named 'PIL'")):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = nano._run_in_window(fake_run, None, None)
        self.assertEqual(rc, 3, "the command's own exit code survives a window failure")
        self.assertIn("No module named", buf.getvalue())
        self.assertIn("the report", buf.getvalue())

    def test_an_unreadable_panel_prints_instead_of_raising(self):
        import unittest.mock as mock

        def fake_run(_ar, _args):
            print("the report")
            return 3

        with mock.patch.object(pw, "load", side_effect=ValueError("edited by hand")):
            import contextlib
            import io
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = nano._run_in_window(fake_run, None, None)
        self.assertEqual(rc, 3, "the command's own exit code survives a window failure")
        self.assertIn("edited by hand", buf.getvalue())
        self.assertIn("the report", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
