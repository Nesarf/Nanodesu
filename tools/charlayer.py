# -*- coding: utf-8 -*-
"""charlayer.py - a layered character-cell canvas built on Qt.

What it is for
--------------
Any effect that draws styled text onto a fixed character grid - per-character
jitter, glyph corruption, scanline highlighting, horizontal displacement or
progressive reveal - is a grid operation, not a widget operation. Plain labels
cannot express those cheaply. This module keeps a char grid per layer and only
repaints the cells that actually changed, so a mostly-static background costs
nothing per frame.

Design notes
------------
* Layers hold `Cell` objects (one character plus an optional colour).
* `CharStack` paints layers in order. Layers marked static are rendered once
  into a cached QPixmap and reused until invalidated, which keeps per-frame
  work proportional to the moving content rather than the whole grid.
* `paint()` returns the number of repainted cells per layer, so the caching can
  be observed directly instead of assumed.
* The module imports without Qt: every cell operation is pure Python, so the
  logic can be tested on machines without a Qt install (`python charlayer.py`).

Coordinates are character cells: `(col, row)`. Pixel geometry comes from
`cell_size` and `origin`, so the same character data can be re-rendered at any
window size.

Typical use
-----------
    stack = CharStack(cols=60, rows=20, cell_size=16, font=font, bg=bg_color)
    bg = stack.add("bg", static=True)
    fx = stack.add("fx", static=False)

    bg.draw_text(2, 1, "TITLE", color)
    fx.draw_section(4, 6, RenderSection("SOME TEXT", color=white))
    fx.jitter(chance=0.25, glyphs="01#@%&*<>/\\")   # glyph corruption
    fx.shift_row(11, 3)                              # displaced scanline

    stack.paint(painter)                             # inside paintEvent
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Iterable, Sequence

try:
    from PySide6.QtCore import QPoint, QRect, Qt
    from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPixmap
    _QT = True
except Exception:                                   # allow pure-logic testing without Qt installed
    _QT = False
    QPoint = QRect = Qt = None
    QColor = QFont = QFontMetrics = QPainter = QPixmap = None


# --------------------------------------------------------------------------- #
# Cells and runs
# --------------------------------------------------------------------------- #

@dataclass
class Cell:
    """One character cell: a glyph and an optional colour."""
    char: str = " "
    color: object = None            # QColor, or None to use the default colour


@dataclass
class RenderSection:
    """A run of styled text.

    Styles are plain QColor values plus an optional per-character colour callback,
    which is what position-dependent effects such as scanlines and gradients need.
    """
    text: str
    color: object = None
    bold: bool = False
    italic: bool = False
    color_at: object = None         # callable(index, char) -> QColor | None

    def cell_at(self, i: int) -> Cell:
        ch = self.text[i]
        col = self.color
        if callable(self.color_at):
            col = self.color_at(i, ch) or col
        return Cell(ch, col)

    def __len__(self) -> int:
        return len(self.text)


# --------------------------------------------------------------------------- #
# One layer
# --------------------------------------------------------------------------- #

class CharLayer:
    """A single layer's character grid. Every write is clipped, never raises."""

    def __init__(self, cols: int, rows: int, cell_size: int = 18,
                 origin: tuple[int, int] = (0, 0), font=None):
        self.cols = cols
        self.rows = rows
        self.cell_size = cell_size
        self.origin = origin
        self.font = font
        self.cells: list[Cell] = [Cell() for _ in range(cols * rows)]
        self.dirty: set[int] = set(range(cols * rows))
        self.visible = True
        self.opacity = 1.0
        self._pixmap = None
        self._pixmap_valid = False

    # -- indexing ---- #

    def _idx(self, col: int, row: int) -> int:
        return row * self.cols + col

    def inside(self, col: int, row: int) -> bool:
        return 0 <= col < self.cols and 0 <= row < self.rows

    def get(self, col: int, row: int) -> Cell | None:
        return self.cells[self._idx(col, row)] if self.inside(col, row) else None

    def set(self, col: int, row: int, char: str, color=None) -> None:
        if not self.inside(col, row):
            return
        i = self._idx(col, row)
        self.cells[i] = Cell(char, color)
        self.dirty.add(i)
        self._pixmap_valid = False

    # -- bulk writes ---- #

    def clear(self, color=None) -> None:
        self.cells = [Cell(" ", color) for _ in range(self.cols * self.rows)]
        self.dirty = set(range(self.cols * self.rows))
        self._pixmap_valid = False

    def draw_text(self, col: int, row: int, text: str, color=None) -> None:
        """Write text from (col, row); a newline starts a new line."""
        c, r = col, row
        for ch in text:
            if ch == "\n":
                c, r = col, r + 1
                continue
            self.set(c, r, ch, color)
            c += 1

    def draw_section(self, col: int, row: int, section: RenderSection) -> None:
        for i in range(len(section)):
            self.set(col + i, row, section.text[i], section.cell_at(i).color)

    def draw_lines(self, col: int, row: int, lines: Iterable[str], color=None) -> None:
        for k, line in enumerate(lines):
            self.draw_text(col, row + k, line, color)

    def overlay_text(self, col: int, row: int, text: str, color=None,
                     only_over: bool = False) -> None:
        """Overlay text, optionally only onto cells that already hold a glyph."""
        c, r = col, row
        for ch in text:
            if ch == "\n":
                c, r = col, r + 1
                continue
            if only_over:
                cell = self.get(c, r)
                if cell is None or cell.char == " ":
                    c += 1
                    continue
            self.set(c, r, ch, color)
            c += 1

    def blit(self, other: "CharLayer", col: int = 0, row: int = 0,
             only_over: bool = False) -> None:
        """Copy another layer's contents onto this one."""
        for r in range(other.rows):
            for c in range(other.cols):
                src = other.cells[other._idx(c, r)]
                if src.char == " " and only_over:
                    continue
                self.set(col + c, row + r, src.char, src.color)

    # -- effects ---- #

    def jitter(self, chance: float = 0.12, glyphs: str = "01#@%&*+=<>/\\",
               skip_space: bool = True, rng: random.Random | None = None) -> int:
        """Randomly replace glyphs, one cell at a time.Returns the number of replaced characters.

        Character-level rather than widget-level: individual columns or only
        non-blank cells can be targeted.
        """
        r = rng or random
        n = 0
        for i, cell in enumerate(self.cells):
            if cell.char == " " and skip_space:
                continue
            if r.random() < chance:
                self.cells[i] = Cell(r.choice(glyphs), cell.color)
                self.dirty.add(i)
                n += 1
        if n:
            self._pixmap_valid = False
        return n

    def shift_row(self, row: int, by: int, wrap: bool = True) -> None:
        """Shift a whole row horizontally."""
        if not (0 <= row < self.rows) or by == 0:
            return
        line = self.cells[row * self.cols:(row + 1) * self.cols]
        if wrap:
            by %= self.cols
        new = [Cell() for _ in range(self.cols)]
        for c in range(self.cols):
            src = c - by
            if 0 <= src < self.cols:
                new[c] = line[src]
        self.cells[row * self.cols:(row + 1) * self.cols] = new
        self.dirty.update(range(row * self.cols, (row + 1) * self.cols))
        self._pixmap_valid = False

    def shift_block(self, row_start: int, row_count: int, by: int) -> None:
        for r in range(row_start, row_start + row_count):
            self.shift_row(r, by)

    def reveal(self, upto_col: int) -> None:
        """Progressive reveal: keep only the first `upto_col` columns."""
        for r in range(self.rows):
            for c in range(self.cols):
                keep = c < upto_col if upto_col >= 0 else c >= -upto_col
                if not keep:
                    self.set(c, r, " ")

    def dim_scanline(self, row: int, height: int = 3, factor: float = 0.45) -> None:
        """Darken a band of rows, as a scanline would."""
        for r in range(row, row + height):
            if not (0 <= r < self.rows):
                continue
            for c in range(self.cols):
                i = self._idx(c, r)
                cell = self.cells[i]
                if cell.color is None or not hasattr(cell.color, "lighter"):
                    continue
                self.cells[i] = Cell(cell.char, cell.color.darker(int(100 / max(factor, 0.01))))
                self.dirty.add(i)
        self._pixmap_valid = False

    # -- rendering ---- #

    def pixmap(self, bg=None, scheme=None):
        """Render this layer to a QPixmap, updating only dirty cells.Returns None when Qt is unavailable."""
        if not _QT:
            return None
        w = self.cols * self.cell_size
        h = self.rows * self.cell_size
        if self._pixmap is None or self._pixmap.width() != w or self._pixmap.height() != h:
            self._pixmap = QPixmap(w, h)
            self._pixmap.fill(bg if bg is not None else Qt.transparent)
            self.dirty = set(range(self.cols * self.rows))
            self._pixmap_valid = False
        if self._pixmap_valid and not self.dirty:
            return self._pixmap

        painter = QPainter(self._pixmap)
        if self.font is not None:
            painter.setFont(self.font)
        fm = QFontMetrics(painter.font())
        default_color = scheme if scheme is not None else QColor(255, 255, 255)
        for i in self.dirty:
            c, r = divmod(i, self.cols)
            x = self.origin[0] + c * self.cell_size
            y = self.origin[1] + r * self.cell_size
            painter.setCompositionMode(QPainter.CompositionMode_Source)
            painter.fillRect(QRect(x, y, self.cell_size, self.cell_size),
                             bg if bg is not None else Qt.transparent)
            cell = self.cells[i]
            if cell.char == " ":
                continue
            painter.setPen(cell.color if cell.color is not None else default_color)
            painter.drawText(QRect(x, y, self.cell_size, self.cell_size),
                             Qt.AlignCenter, cell.char)
        painter.end()
        self.dirty.clear()
        self._pixmap_valid = True
        return self._pixmap

    def invalidate(self) -> None:
        self._pixmap_valid = False
        self.dirty = set(range(self.cols * self.rows))

    # -- Filling from a glyph map ---- #

    def from_glyph_map(self, cols: int, rows: int, picker) -> None:
        """Fill the layer from picker(col, row); an empty string leaves the cell blank.

        Useful for turning a source image into a glyph map before applying effects.
        """
        for r in range(rows):
            for c in range(cols):
                ch = picker(c, r)
                if ch:
                    self.set(c, r, ch)


# --------------------------------------------------------------------------- #
# Stacking layers
# --------------------------------------------------------------------------- #

class CharStack:
    """Named collection of layers; static layers use the pixmap cache."""

    def __init__(self, cols: int, rows: int, cell_size: int = 18,
                 font=None, bg=None):
        self.cols = cols
        self.rows = rows
        self.cell_size = cell_size
        self.font = font
        self.bg = bg
        self.layers: list[tuple[str, CharLayer, bool]] = []   # (name, layer, is_static)

    def add(self, name: str, static: bool = False, opacity: float = 1.0) -> CharLayer:
        layer = CharLayer(self.cols, self.rows, self.cell_size, font=self.font)
        layer.opacity = opacity
        self.layers.append((name, layer, static))
        return layer

    def get(self, name: str) -> CharLayer | None:
        for n, l, _ in self.layers:
            if n == name:
                return l
        return None

    def __getitem__(self, name: str) -> CharLayer:
        layer = self.get(name)
        if layer is None:
            raise KeyError(name)
        return layer

    def paint(self, painter, compositing: bool = True) -> dict:
        """Composite layers in order, reusing the pixmap cache for static ones.

        Returns the number of repainted cells per layer for this frame.
        """
        stats = {}
        for name, layer, static in self.layers:
            if not layer.visible:
                continue
            stats[name] = len(layer.dirty)
            if static and layer._pixmap_valid:
                painter.drawPixmap(layer.origin[0], layer.origin[1], layer._pixmap)
                continue
            pm = layer.pixmap(bg=self.bg)
            if pm is not None:
                if layer.opacity < 1.0:
                    painter.setOpacity(layer.opacity)
                painter.drawPixmap(layer.origin[0], layer.origin[1], pm)
                if layer.opacity < 1.0:
                    painter.setOpacity(1.0)
        return stats

    def clear_dynamic(self) -> None:
        for _, layer, static in self.layers:
            if not static:
                layer.clear()


# --------------------------------------------------------------------------- #
# Pure-logic self-test (no Qt needed)
# --------------------------------------------------------------------------- #

def _selftest() -> int:
    """Qt-free logic self-test: writes, clipping, jitter, shifting, reveal."""
    problems = []

    def check(cond, msg):
        if not cond:
            problems.append(msg)

    l = CharLayer(10, 3)
    l.draw_text(0, 0, "HELLO")
    check([c.char for c in l.cells[:5]] == list("HELLO"), "draw_text wrote the wrong cells")
    check(l.get(5, 0).char == " ", "untouched cell should stay blank")

    l.set(-1, 0, "X")            # out-of-range writes are clipped, never raised
    l.set(99, 99, "X")
    check(l.get(0, 0).char == "H", "an out-of-range write corrupted existing content")

    l2 = CharLayer(10, 3)
    l2.draw_text(0, 0, "abcdef")
    l2.shift_row(0, 2)
    check([c.char for c in l2.cells[:6]] == [" ", " ", "a", "b", "c", "d"],
          "shift_row produced the wrong result: %r" % [c.char for c in l2.cells[:6]])

    l3 = CharLayer(10, 3)
    l3.draw_text(0, 0, "abcdefghij")
    l3.reveal(4)
    check([c.char for c in l3.cells[:10]] == list("abcd      ") + [],
          "reveal truncated incorrectly: %r" % [c.char for c in l3.cells[:10]])

    l4 = CharLayer(20, 2)
    l4.draw_text(0, 0, "ABCDEFGHIJKLMNOPQRST")
    before = "".join(c.char for c in l4.cells[:20])
    n = l4.jitter(chance=1.0, glyphs="0")
    after = "".join(c.char for c in l4.cells[:20])
    check(n == 20 and after == "0" * 20, "jitter(1.0) should replace every non-blank cell")

    s = RenderSection("AB", color="red")
    check(s.cell_at(0).color == "red", "RenderSection colour lookup failed")

    if problems:
        print("self-test failed on %d check(s):" % len(problems))
        for p in problems:
            print("  ✗ " + p)
        return 1
    print("self-test passed: clipping, shifting, reveal, jitter and styled runs all behave")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())