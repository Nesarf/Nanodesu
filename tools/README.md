# tools

Optional extras that are useful alongside the main tool.

## charlayer.py

A layered character-cell canvas for Qt: several grids of characters, each layer
repaintable on its own, with a cached pixmap for layers that rarely change.
Built for per-character text effects — glyph corruption, horizontal
displacement, scanline darkening, progressive reveal, overlays.

```python
from charlayer import CharStack, RenderSection

stack = CharStack(cols=60, rows=20, cell_size=16, font=font, bg=bg_color)
bg = stack.add("bg", static=True)     # cached, repainted only when dirty
fx = stack.add("fx", static=False)    # repainted every change

bg.draw_text(2, 1, "TITLE", color)
fx.draw_section(4, 6, RenderSection("SOME TEXT", color=white))
fx.jitter(chance=0.25, glyphs="01#@%&*<>/\\")
fx.shift_row(11, 3)

stack.paint(painter)                  # returns repainted cells per layer
```

`paint()` returning `{'bg': 0, 'fx': 37}` means the cached layer cost nothing
this frame. No Qt is required to exercise the logic:

```bash
python charlayer.py
```

## format_matrix.py

Regenerates `FORMAT_MATRIX.md` from PyInstaller's own sources. Fetches the sdists for a list of
versions from PyPI (cached under `PYINSTALLER_SRC_CACHE`, default `~/.cache/pyinstaller-src`) and
reads the constants a reader would be written against: `_TOC_ENTRY_FORMAT`, the cookie format, and
the `ARCHIVE_ITEM_*` type codes.

```bash
python tools/format_matrix.py
```

## Why this source is written in English, and stripped of paths

An earlier state of this project was written in Chinese with the author's own directory layout in
it — absolute paths, a named personal workspace, a named project. A one-off script rewrote the
source: translated the prose, removed the paths, and dropped references to other projects.

**That script is not kept here**, for two reasons. It hard-coded the path of a source tree that no
longer exists, so it would not run; and its job cannot be done twice — the canonical source is
already in the state it produced, and running it again would be a no-op at best.

What replaced it is process rather than tooling: CI builds and publishes on a tag, and
`test_version.py` keeps the version from drifting. The reason the code reads as it does is recorded
here, which is the part that was actually worth keeping.
