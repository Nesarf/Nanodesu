# Nanodesu 1.7.5

A window, and three defects that the first version of it left behind.

## `--window`: the report, drawn instead of printed

`info`, `ls`, `tree` and `verify` can draw their output into a pixel-art window instead of printing
it. **The lines are captured from the command that already writes them**, not re-formatted for the
window, so the two views cannot disagree about what a command said.

**The frame is not this project's invention.** `panel.json` and `font.json` are exported by
[Ichiwatashi](https://github.com/Nesarf/Ichiwatashi), the family's shared pixel layer, and both carry
a checksum over their own contents — a layout edited after export is refused rather than drawn almost
right. They are embedded files, not a dependency: nothing is imported from another repository at run
time, so this stays one tool that needs nothing.

The system title bar is not used. The panel draws its own, dragging uses the title rectangle from the
layout, and the `x` in the corner is a real control whose nine pixels the file names. A report longer
than 46 rows pages instead of clipping, with `1/3` at the right of the bar, because a clipped report
is a statement the command never made. A title that does not fit is refused with both numbers.

`pip install nanodesu[window]` brings the image library. Without it the flag prints the report and
says why; the base install stays empty, as before.

## Three things the first version of that got wrong

**The window could not open for anyone who installed.** `panel_window` was not in `py-modules`, and
neither JSON file was declared anywhere, so a built wheel carried no way to open a window at all — it
worked from a clone and nowhere else. A flat `py-modules` distribution cannot carry data and
`package-data` applies to packages, so the fix is three declarations: the module as a module, the two
files as `data-files` under `share/nanodesu`, and a resolver that looks beside the module first and in
the install prefix second. **Verified by installing the built wheel in a clean virtual environment and
resolving both files from a directory that is not this repository** — the same check that caught
`unpack.py` missing from a published release.

**The renderer upper-cased its input.** The shared font carries 88 glyphs and 26 of them are lower
case, so the table had grown an alphabet that could never be drawn. The same defect was measured in
the PowerShell consumer, where the table's keys were case-insensitive as well: one shared asset,
dropped at the last step, twice.

**`--window` was accepted everywhere and ignored where it meant nothing.** `extract --window` exited
0 having opened nothing. It is refused now, naming the four commands that do produce a report — an
accepted flag that does nothing leaves the caller believing a window opened.

## Body text got its own colour

Body text was drawn in the title bar's ink — in all three renderers, this one included — so changing
the bar's colour silently restyled every report and nothing anywhere said the body was borrowing. The
shared layout now names that job (`content_text`), and this reader asks for it first and falls back to
the bar's colour for any layout exported before the role existed, so the two sides of an embedding
upgrade on their own schedule.

## Two marks became words

A tick and a cross in the output were the family discipline's named shape: a judgement carried by a
glyph rather than by a word. They now read `ok` and `FAIL`. Nothing asserted either, so the change was
measured rather than argued about.

## Testing

**201 tests** (was 180), green on Python 3.9, 3.12, 3.13 and 3.14. Twenty-one of them are the window's —
there were none before, which is why the three defects above survived a release. They are mostly about
what happens *before* a window opens, because opening one blocks in `mainloop`, and a suite that hangs
is worse than a suite that fails.

Two tests exist because the first draft of this release would have passed without them: the wheel is
checked for carrying the module and both data files (a local tree has them either way), and the
rendered body text is compared on pixels rather than on the property — a property can be right while
the renderer ignores it.

A third was written after the release notes claimed something the code did not do. These notes said
that without the `window` extra the flag "prints the report and says why"; **measured, it raised
`ModuleNotFoundError` straight out of the flag**, so the person who asked for a window got a traceback
and no report. The fallback was implemented, then the sentence became true, and a test now holds it:
the command's own exit code survives, the reason is printed, and the report is printed under it.

---

### What is signed, and what it is worth here

The `.whl` and `.tar.gz` on PyPI carry no Authenticode signature: `pip` verifies them against the index
hashes, which is the stronger claim and does not depend on trusting anybody's root.

**The Windows executable attached to this release is not signed.** The certificate's private key does
not exist in CI, deliberately, so an automated release cannot produce a signed executable. The 1.7.4
asset was signed because that was done locally for it; nothing here changes that, and no future release
will carry a signature unless someone runs the local pipeline.
