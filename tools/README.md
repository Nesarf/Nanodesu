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
