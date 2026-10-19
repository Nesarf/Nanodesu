# -*- coding: utf-8 -*-
"""A window drawn from a panel layout this project embeds, without importing the project that made it.

**Why this file exists instead of an import.** The family rule is that the shared pixel layer is *produced
once and embedded*, and that each tool stays self-contained at run time -- 来源 ≠ 依赖. So this reads
`panel.json` (exported by Ichiwatashi) and paints it here; nothing reaches into another repository, and the
layout is a file this tree owns. `panel.json` is byte-identical across the tools that embed it, which is what
makes "all of them draw one layout" a checkable fact rather than a claim about intentions.

**The layout carries no title.** The bar's geometry is shared; the words in it are the tool's. A title baked
into the file would make each tree's copy differ, and then comparing checksums would no longer answer the one
question worth asking -- is this the same panel?

**Nothing here is a second opinion about the panel.** Every rectangle comes from the file. If this renderer
computed the frame for itself, two implementations of one window would exist and nobody could say which was
right -- which is exactly the defect the panel layer fixed by making its renderer and its checker walk one
definition.

**Three things it refuses rather than mangles**, because each is a way a window can lie:

* a checksum that does not match -- an edited layout must not draw almost right;
* a title that does not fit the bar -- a bar that silently shows a different word is worse than no window;
* a body line wider than the content -- a clipped line is a statement the report never made.

Three entry points, because three things need one: `text()` for a log or a test, `image()` for a PNG, and
`open_window()` for a person.
"""
from __future__ import annotations

import hashlib
import json

__all__ = [
    "FORMAT", "FONT_FORMAT", "PanelLayout", "load", "load_font", "canonical",
    "text", "image", "open_window",
]


#: The version of the file this module understands. A reader refuses what it does not know rather than
#: guessing which fields still mean what they meant.
FORMAT = "ichiwatashi.panel/1"

#: The version of the font file. Separate from the panel's, because the two files are produced by two
#: different commands and either can change on its own.
FONT_FORMAT = "ichiwatashi.font/1"

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

    # -- what colour text is drawn in ------------------------------------------------------------

    @property
    def content_ink(self):
        """The colour body text is drawn in: **the layout's own role, or the title's ink if the file predates it.**

        This role exists because of a measured defect rather than a preference: all three renderers -- two
        Python and one PowerShell -- drew body text in `title_text`, so a change to the bar's ink silently
        restyled every report, and nothing anywhere said the body was borrowing.

        **The fallback is not politeness.** A file exported before the role existed has to keep drawing, and it
        does, in the colour it always used -- so upgrading one side of an embedding does not break the other.
        A layout with neither role cannot draw text at all, and says so rather than choosing white quietly.
        """
        for role in ("content_text", "title_text"):
            key = self.key_of.get(role)
            if key and key in self.colours:
                return self.colours[key]
        raise ValueError("panel layout has neither content_text nor title_text, so it cannot draw text")

    # -- how much room the body has ------------------------------------------------------------

    @property
    def body_cols(self):
        """Characters per row that fit in the content rectangle, from the rectangle and the cell size.

        **Derived, never assumed.** A 716-wide panel and a 96-wide one differ by exactly this number, and a
        caller that hard-coded either would page wrongly on the other -- the same class of defect as the
        character/pixel unit confusion this reader had to fix once.
        """
        cx0, _cy0, cx1, _cy1 = self.layout["content"]
        return (cx1 - cx0) // CELL_W

    @property
    def body_rows(self):
        """Rows that fit in the content rectangle."""
        _cx0, cy0, _cx1, cy1 = self.layout["content"]
        return (cy1 - cy0) // CELL_H

    def page_count(self, lines):
        """How many pages `lines` needs. **Never zero**, because an empty report is still one page."""
        rows = self.body_rows
        return max(1, -(-len(lines) // rows))

    def page_lines(self, lines, page):
        """The slice drawn for `page`, clamped, so a scroll that runs past the end shows the last page."""
        rows = self.body_rows
        pages = self.page_count(lines)
        page = max(0, min(page, pages - 1))
        return list(lines[page * rows:(page + 1) * rows]), page, pages

    # -- the one definition of what a pixel is -------------------------------------------------

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

    # -- what goes in the title bar ------------------------------------------------------------

    def _marker(self, page, pages):
        """`3/7` when there is more than one page, otherwise nothing.

        **A page indicator is a statement about the report**, so it is drawn only when it is true and it is
        part of what `text()` returns -- a test can read the page count out of the rendering, which is the only
        way a reader could.
        """
        return "%d/%d" % (page + 1, pages) if pages > 1 else ""

    def _title_starts(self, title, marker):
        """Where the title begins and how much room is left once the marker has taken its columns."""
        start, room = self.title_room
        need = len(title or "") * CELL_W
        marker_room = (len(marker) + 1) * CELL_W if marker else 0
        if need > room - marker_room:
            raise ValueError("title %r needs %d px but the title bar has %d%s"
                             % (title, need, room - marker_room,
                                " (after the page marker)" if marker else ""))
        return start, room, need

    # -- the three renderings ------------------------------------------------------------------

    def text_rows(self, lines=None, *, title=None, active=True, page=0):
        """One string per row, one character per logical pixel. For a log, a test, or a fallback."""
        _tx0, ty0, tx1, _ty1 = self.layout["title"]
        cx0, cy0, cx1, cy1 = self.layout["content"]
        rows, page, pages = self.page_lines(lines or [], page)
        marker = self._marker(page, pages)
        start, room, _need = self._title_starts(title, marker)
        grid = [[" "] * self.width for _ in range(self.height)]
        for y in range(self.height):
            for x in range(self.width):
                role = self.role_at(x, y, active=active)
                ch = self.chars.get(self.key_of.get(role)) if role else None
                if ch:
                    grid[y][x] = ch
        # **In a character grid one character is one column.** The first version spaced the title and the
        # body by CELL_W, which is a *pixel* constant, so a 716-wide grid printed the title one letter per
        # six columns. The two renderings need two different units and that is the whole confusion.
        if title:
            for i, ch in enumerate(title):
                x = start + i
                if x < tx1:
                    grid[ty0 + 1][x] = ch
        if marker:
            x0 = start + room - len(marker)
            for i, ch in enumerate(marker):
                if x0 + i < tx1:
                    grid[ty0 + 1][x0 + i] = ch
        self._check_width(rows)
        for row_i, line in enumerate(rows):
            y = cy0 + row_i
            if y >= cy1:
                break
            for col_i, ch in enumerate(line[: (cx1 - cx0)]):
                x = cx0 + col_i
                if x < cx1:
                    grid[y][x] = ch
        return ["".join(r) for r in grid]

    def _check_width(self, rows):
        """Refuse a line that would be clipped. **A cut line is a claim the report never made.**"""
        room = self.body_cols
        for i, line in enumerate(rows):
            if len(line) > room:
                raise ValueError("body line %d is %d characters wide but the content area holds %d; "
                                 "wrap it before drawing rather than losing the end"
                                 % (i, len(line), room))

    def image(self, lines=None, *, title=None, active=True, scale=2, draw_fn=None, page=0):
        """A PIL image. **Filled runs rather than per-pixel writes**, except for the glyphs.

        `SetPixel`-style per-pixel drawing is what the other tool's own renderer notes as the only way to beat
        GDI+'s smoothing -- but PIL does not smooth a pixel, so the reason does not carry over, and a run fill
        is both simpler and the same pixels.
        """
        from PIL import Image

        if not isinstance(scale, int) or scale < 1:
            raise ValueError("scale must be a positive whole number, got %r" % (scale,))
        rows, page, pages = self.page_lines(lines or [], page)
        marker = self._marker(page, pages)
        self._title_starts(title, marker)                 # refuse before drawing anything
        self._check_width(rows)
        im = Image.new("RGBA", (self.width, self.height), self.colours[self.key_of["frame_dark"]])
        px = im.load()
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
        if draw_fn is not None:
            self._draw_text(draw_fn, px, rows, title=title, marker=marker)
        if scale != 1:
            im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
        return im

    def _draw_text(self, draw, px, rows, *, title=None, marker=""):
        """Paint the title, the page marker and the body with whatever glyph table the caller supplied.

        **No font is imported and none is assumed.** A panel needs a 5x7 table and one is embedded beside this
        file; if it is missing, the frame still draws and the text does not -- which is a visible, reportable
        state rather than an import error at start-up.

        **Case is preserved.** An earlier version upper-cased here, which threw away 26 of the 88 glyphs the
        font carries: the shared table grew a lower-case alphabet and this renderer could not draw one letter
        of it. The same defect was measured in the PowerShell renderer, where the table's keys were also
        case-insensitive -- one shared asset, dropped at the last step, twice.
        """
        _tx0, ty0, _tx1, _ty1 = self.layout["title"]
        cx0, cy0, _cx1, _cy1 = self.layout["content"]
        # **Two inks, because they are two jobs.** The bar names the window and the body is what a reader
        # stays for; they used to be one colour, borrowed, which is what `content_text` was added for.
        title_ink = self.colours[self.key_of["title_text"]]
        body_ink = self.content_ink
        start, room, _need = self._title_starts(title, marker)
        if title:
            draw(px, start, ty0 + 1, title, title_ink)
        if marker:
            draw(px, start + room - len(marker) * CELL_W, ty0 + 1, marker, title_ink)
        for row_i, line in enumerate(rows):
            draw(px, cx0, cy0 + row_i * CELL_H, line, body_ink)


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

    #: A box for a character the table does not hold. **Drawn rather than skipped**, so a gap is visible as a
    #: gap and not as a shorter word.
    BOX = [0b11111, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b11111]

    def draw(px, x, y, text, colour):
        for i, ch in enumerate(text):
            g = rows.get(ch)
            if g is None:
                g = BOX
            for r in range(GLYPH_H):
                for c in range(GLYPH_W):
                    if g[r] & (1 << (GLYPH_W - 1 - c)):
                        try:
                            px[x + i * CELL_W + c, y + r] = colour
                        except IndexError:
                            # Past the panel's edge. **Not an error**: a caller may deliberately draw a line
                            # that runs to the border, and the border is where it stops.
                            pass

    return draw


def load(path):
    """Read and validate an embedded layout. **A wrong version or a bad checksum raises here**, before
    anything is drawn -- so a modified file cannot produce a window that looks almost right."""
    with open(path, encoding="utf-8") as fh:
        return PanelLayout(json.load(fh))


def text(layout, lines=None, *, title=None, active=True, page=0):
    """The panel as text, one string per row. The form a test can compare."""
    return layout.text_rows(lines, title=title, active=active, page=page)


def image(layout, lines=None, *, title=None, active=True, scale=2, draw_fn=None, page=0):
    """The panel as a PIL image. `draw_fn` comes from `load_font`; without it the frame draws and the text
    does not, which is a visible state rather than an import error."""
    return layout.image(lines, title=title, active=active, scale=scale, draw_fn=draw_fn, page=page)


def open_window(layout, lines, *, title=None, scale=2, draw_fn=None):
    """Show the panel in a window, with no system chrome.

    **The system title bar is not used**, because the panel already is one and a system bar is neither pixel
    art nor this project's. What the system provided is reimplemented from the layout: dragging uses the
    title bar's rectangle, and clicking the close mark's pixels closes the window.

    **Scrolling is the window's job, and the page marker is how it says so.** A report longer than 46 rows is
    the normal case for anything with findings in it, so the keys are part of the interface rather than a
    nicety: Up/Down one row, PageUp/PageDown one page, Home/End, and the bar carries `page/total` whenever
    there is more than one.

    Needs a display. A test that called this unconditionally would block in `mainloop`, so it is called from a
    command a person runs, never from the suite.
    """
    import tkinter as tk

    from PIL import ImageTk

    root = tk.Tk()
    root.title(title or "panel")
    root.resizable(False, False)
    root.overrideredirect(True)

    state = {"page": 0, "rows": list(lines)}

    def redraw():
        im = layout.image(state["rows"], title=title, scale=scale, draw_fn=draw_fn,
                          page=state["page"])
        photo = ImageTk.PhotoImage(im)
        holder["photo"] = photo
        canvas.itemconfigure(item, image=photo)
        canvas.configure(width=photo.width(), height=photo.height())

    holder = {}
    im0 = layout.image(state["rows"], title=title, scale=scale, draw_fn=draw_fn, page=0)
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

    def move(delta):
        before = state["page"]
        state["page"] = max(0, min(state["page"] + delta, layout.page_count(state["rows"]) - 1))
        if state["page"] != before:
            redraw()

    def on_key(event):
        rows = layout.body_rows
        key = event.keysym
        if key in ("Escape", "q", "Q"):
            root.destroy()
        elif key == "Down":
            move(1)
        elif key == "Up":
            move(-1)
        elif key in ("Next", "space"):
            move(rows)
        elif key == "Prior":
            move(-rows)
        elif key == "Home":
            state["page"] = 0
            redraw()
        elif key == "End":
            state["page"] = layout.page_count(state["rows"]) - 1
            redraw()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", lambda e: drag.update(x=0, y=0))
    root.bind("<Key>", on_key)
    root.mainloop()
