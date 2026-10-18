# -*- coding: utf-8 -*-
"""A window drawn from a panel layout this project embeds, without importing the project that made it.

**Why this file exists instead of an import.** The family rule is that the shared pixel layer is *produced
once and embedded*, and that each tool stays self-contained at run time -- 来源 ≠ 依赖. So this reads
`panel.json` (exported by Ichiwatashi) and paints it here; nothing reaches into another repository, and the
layout is a file this tree owns.

**What the file carries, and why the checksum matters.** The layout rectangles, the frame's bevel rule, the
close mark's pixels, the title's room, one key per role, and a colour and a character per key. The checksum
covers the canonical JSON, so a layout that has been edited by hand after export is refused rather than drawn
wrongly -- **an embedded fact is only useful if you can tell it from a modified one.**

**Nothing here is a second opinion about the panel.** Every rectangle comes from the file. If this renderer
computed the frame for itself, two implementations of one window would exist and nobody could say which was
right -- which is exactly the defect the panel layer fixed by making its renderer and its checker walk one
definition.

Three entry points, because three things need one: `text()` for a log or a test, `image()` for a PNG, and
`open_window()` for a person.
"""
from __future__ import annotations

import hashlib
import json

__all__ = ["FORMAT", "PanelLayout", "load", "canonical", "text", "image", "open_window"]


#: The version of the file this module understands. A reader refuses what it does not know rather than
#: guessing which fields still mean what they meant.
FORMAT = "ichiwatashi.panel/1"

#: Glyphs are 5 wide and 7 tall with one pixel of spacing, matching the font the file names in `chars`.
GLYPH_W, GLYPH_H = 5, 7
CELL_W, CELL_H = 6, 8


def canonical(obj):
    """The JSON the checksum covers: sorted keys, no spaces, no ASCII escaping.

    Canonical rather than "whatever `json.dumps` gives", because key order and spacing are exactly what two
    writers disagree about -- and the checksum has to be reproducible on the other side of a language boundary.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class PanelLayout:
    """One embedded layout, validated once and then only read."""

    def __init__(self, body):
        if body.get("format") != FORMAT:
            raise ValueError("panel layout has format %r; this reader speaks %r"
                             % (body.get("format"), FORMAT))
        want = body.get("checksum")
        got = hashlib.sha256(canonical({k: v for k, v in body.items() if k != "checksum"})
                             .encode("utf-8")).hexdigest()
        if want != got:
            raise ValueError("panel layout does not match its own checksum (file says %s, content is %s)"
                             % (want, got))
        self.body = body
        self.width, self.height = body["size"]
        self.layout = {k: tuple(v) for k, v in body["layout"].items()}
        self.key_of = dict(body["keys"])                    # role -> key
        self.colours = {k: tuple(v) for k, v in body["colours"].items()}
        self.chars = dict(body["chars"])
        self.close_pixels = {(int(p[0]), int(p[1])) for p in body["close_pixels"]}
        self.title_room = tuple(body["title_room"])
        self.bevel = dict(body.get("bevel_rule") or {})

    # -- the one definition of what a pixel is ------------------------------------------------

    def role_at(self, x, y, *, active=True):
        """The role a pixel carries, walking the same order the panel layer documents.

        Outer frame, then the bevel, then the close mark, then the content, the title and the well. A pixel is
        attributed to the outermost part whose rectangle contains it, which is the order a reader looks at the
        panel.
        """
        def inside(rect):
            if rect is None:
                return False
            x0, y0, x1, y1 = rect
            return x0 <= x < x1 and y0 <= y < y1

        frame = self.layout.get("frame")
        if not inside(frame):
            return None
        ring = int(self.bevel.get("ring_offset", 1) or 1)
        lit = self.bevel.get("lit_sides") or []
        if self.bevel and ring >= 1:
            dist = min(x - frame[0], y - frame[1], frame[2] - 1 - x, frame[3] - 1 - y)
            if dist == 0:
                return self.bevel.get("outer_ring", "frame_dark")
            if dist == ring:
                # **A corner lies on two sides, and the lit one wins** -- the same convention the panel layer
                # states, and the reason it is stated is that a corner is where two implementations diverge.
                on_top = (y - frame[1]) == ring
                on_left = (x - frame[0]) == ring
                if (on_top and "top" in lit) or (on_left and "left" in lit):
                    return self.bevel.get("inner_ring", "frame_light")
                return self.bevel.get("outer_ring", "frame_dark")
        if (x, y) in self.close_pixels:
            return "close_mark"
        if inside(self.layout.get("content")):
            return "content"
        if inside(self.layout.get("title")):
            return "title" if active else "title_inactive"
        if inside(self.layout.get("well")):
            return "well"
        return self.bevel.get("outer_ring", "frame_dark")

    # -- the three renderings ----------------------------------------------------------------

    def text_rows(self, lines=None, *, title=None, active=True):
        """One string per row, one character per logical pixel. For a log, a test, or a fallback."""
        tx0, ty0, tx1, _ty1 = self.layout["title"]
        cx0, cy0, cx1, cy1 = self.layout["content"]
        grid = [[" "] * self.width for _ in range(self.height)]
        for y in range(self.height):
            for x in range(self.width):
                role = self.role_at(x, y, active=active)
                ch = self.chars.get(self.key_of.get(role)) if role else None
                if ch:
                    grid[y][x] = ch
        # **In a character grid one character is one column.** The first version spaced the title and the
        # body by CELL_W, which is a *pixel* constant, so a 716-wide grid printed "N A N O D E S U" across 56
        # columns. The two renderings need two different units and that is the whole confusion.
        if title:
            start, room = self.title_room
            # the room is in pixels; in columns it is the same count divided by the cell width
            if len(title) * CELL_W > room:
                raise ValueError("title %r needs %d px but the title bar has %d"
                                 % (title, len(title) * CELL_W, room))
            for i, ch in enumerate(title.upper()):
                x = start + i
                if x < tx1:
                    grid[ty0 + 1][x] = ch
        for row_i, line in enumerate(lines or []):
            y = cy0 + row_i
            if y >= cy1:
                break
            for col_i, ch in enumerate(line[: (cx1 - cx0)]):
                x = cx0 + col_i
                if x < cx1:
                    grid[y][x] = ch
        return ["".join(r) for r in grid]

    def image(self, lines=None, *, title=None, active=True, scale=2, glyphs=None, draw_fn=None):
        """A PIL image. **Filled rectangles rather than per-pixel writes**, except for the glyphs.

        `SetPixel`-style per-pixel drawing is what the other tool's own renderer notes as the only way to beat
        GDI+'s smoothing -- but PIL does not smooth a `putpixel`, so the reason does not carry over, and a
        rectangle fill is both simpler and the same pixels.
        """
        from PIL import Image

        if not isinstance(scale, int) or scale < 1:
            raise ValueError("scale must be a positive whole number, got %r" % (scale,))
        im = Image.new("RGBA", (self.width, self.height), self.colours[self.key_of["frame_dark"]])
        px = im.load()
        # fill by runs, which is what the rectangles are: a row of one role is one span
        for y in range(self.height):
            x = 0
            while x < self.width:
                role = self.role_at(x, y)
                if role is None:
                    x += 1
                    continue
                start = x
                while x < self.width and self.role_at(x, y) == role:
                    x += 1
                colour = self.colours.get(self.key_of.get(role))
                if colour:
                    for xx in range(start, x):
                        px[xx, y] = colour
        self.draw_fn = draw_fn
        if draw_fn is not None:
            self._draw_text(px, lines or [], title=title)
        if scale != 1:
            im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
        return im

    def _draw_text(self, px, lines, *, title=None):
        """Paint the title and the body with whatever glyph table the caller supplied.

        **No font is imported and none is assumed.** A panel needs a 5x7 table and one is embedded beside this
        file; if it is missing, the frame still draws and the text does not -- which is a visible, reportable
        state rather than an import error at start-up. The first version of this method imported a module that
        does not exist, which is the same class of mistake as calling a function before writing it.
        """
        draw = self.draw_fn
        if draw is None:
            return

        tx0, ty0, tx1, _ty1 = self.layout["title"]
        cx0, cy0, cx1, cy1 = self.layout["content"]
        if title:
            start, room = self.title_room
            if len(title) * CELL_W > room:
                raise ValueError("title %r needs %d px but the title bar has %d"
                                 % (title, len(title) * CELL_W, room))
            draw(px, start, ty0 + 1, title, self.colours[self.key_of["title_text"]])
        ink = self.colours[self.key_of["title_text"]]
        for row_i, line in enumerate(lines):
            y = cy0 + row_i * CELL_H
            if y >= cy1:
                break
            for col_i, ch in enumerate(line[: (cx1 - cx0) // CELL_W]):
                draw(px, cx0 + col_i * CELL_W, y, ch, ink)


FONT_FORMAT = "ichiwatashi.font/1"


def load_font(path):
    """Read the embedded 5x7 table and return a `draw(px, x, y, text, colour)` closure.

    **The same encoding as the other tool's own font module** -- one hex digit per row, bit 4 the leftmost of
    five columns -- which is why this is 20 lines and not a translation layer. The checksum is verified for the
    same reason the layout's is: an embedded fact is only useful if a modified one can be told apart.
    """
    with open(path, encoding="utf-8") as fh:
        body = json.load(fh)
    if body.get("format") != FONT_FORMAT:
        raise ValueError("font has format %r; this reader speaks %r" % (body.get("format"), FONT_FORMAT))
    want = body.get("checksum")
    got = hashlib.sha256(canonical({k: v for k, v in body.items() if k != "checksum"})
                         .encode("utf-8")).hexdigest()
    if want != got:
        raise ValueError("font does not match its own checksum")
    rows = {ch: [int(h[i * 2:i * 2 + 2], 16) for i in range(GLYPH_H)]
            for ch, h in body["glyphs"].items()}

    def draw(px, x, y, text, colour):
        for i, ch in enumerate(text.upper()):
            g = rows.get(ch) or rows.get(ch.lower()) or rows.get(ch.upper())
            if g is None:
                # **A missing glyph draws a box rather than nothing**, so a gap is visible as a gap and not as
                # a shorter word. Same rule as the other tool's fallback.
                g = [0b11111, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b11111]
            for r in range(GLYPH_H):
                for c in range(GLYPH_W):
                    if g[r] & (1 << (GLYPH_W - 1 - c)):
                        xx, yy = x + i * CELL_W + c, y + r
                        if 0 <= xx < px.__self__.width if hasattr(px, "__self__") else True:
                            try:
                                px[xx, yy] = colour
                            except IndexError:
                                pass

    return draw


def load(path):
    """Read and validate an embedded layout. **A wrong version or a bad checksum raises here**, before
    anything is drawn -- so a modified file cannot produce a window that looks almost right."""
    with open(path, encoding="utf-8") as fh:
        return PanelLayout(json.load(fh))


def text(layout, lines=None, *, title=None, active=True):
    """The panel as text, one string per row. The form a test can compare."""
    return layout.text_rows(lines, title=title, active=active)


def image(layout, lines=None, *, title=None, active=True, scale=2, glyphs=None, draw_fn=None):
    """The panel as a PIL image. `draw_fn` comes from `load_font`; without it the frame draws and the text
    does not, which is a visible state rather than an import error."""
    return layout.image(lines, title=title, active=active, scale=scale, glyphs=glyphs, draw_fn=draw_fn)


def open_window(layout, lines, *, title=None, scale=2, glyphs=None, draw_fn=None):
    """Show the panel in a window, with no system chrome.

    **The system title bar is not used**, because the panel already is one and a system bar is neither pixel
    art nor this project's. What the system provided is reimplemented from the layout: dragging uses the
    title bar's rectangle, and clicking the close mark's pixels closes the window.

    Needs a display. A test that called this unconditionally would block in `mainloop`, so it is called from a
    command a person runs, never from the suite.
    """
    import tkinter as tk

    from PIL import ImageTk

    font_glyphs = glyphs
    root = tk.Tk()
    root.title(title or "nanodesu")
    root.resizable(False, False)
    root.overrideredirect(True)

    def redraw():
        im = layout.image(lines, title=title, scale=scale, glyphs=font_glyphs, draw_fn=draw_fn)
        photo = ImageTk.PhotoImage(im)
        holder["photo"] = photo
        canvas.itemconfigure(item, image=photo)

    holder = {}
    im0 = layout.image(lines, title=title, scale=scale, glyphs=font_glyphs, draw_fn=draw_fn)
    photo0 = ImageTk.PhotoImage(im0)
    holder["photo"] = photo0
    canvas = tk.Canvas(root, width=photo0.width(), height=photo0.height(),
                       highlightthickness=0, bd=0)
    canvas.pack()
    item = canvas.create_image(0, 0, anchor="nw", image=photo0)

    drag = {"x": 0, "y": 0}

    def on_press(event):
        lx, ly = event.x // scale, event.y // scale
        if (lx, ly) in layout.close_pixels:
            root.destroy()
            return
        tx0, ty0, tx1, ty1 = layout.layout["title"]
        if tx0 <= lx < tx1 and ty0 <= ly < ty1:
            drag["x"] = event.x_root - root.winfo_x()
            drag["y"] = event.y_root - root.winfo_y()

    def on_drag(event):
        if drag["x"] or drag["y"]:
            root.geometry("+%d+%d" % (event.x_root - drag["x"], event.y_root - drag["y"]))

    def on_key(event):
        if event.keysym in ("Escape", "q", "Q"):
            root.destroy()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", lambda e: drag.update(x=0, y=0))
    root.bind("<Key>", on_key)
    root.mainloop()
