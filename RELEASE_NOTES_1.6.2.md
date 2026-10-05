# Nanodesu! 1.6.2

Documentation for the boundary notice and the persona, which shipped without either being mentioned.

## The gap

`--boundary` appeared **zero** times in the README, `--plain` and `--nsfw` likewise, and there was no
description of what the persona is or how to turn it off. A user could install this tool, meet a
Japanese-speaking character in `--help`, and have no documented way to know she was intentional or how
to silence her.

## What is documented now

* **`--boundary` and `--plain` in `Usage`**, because a flag nobody can find is a feature nobody has.
* **A section on what this tool does not establish**, with the four lines verbatim and the reason each
  one names a specific over-reading rather than cautioning in general.
* **A section on the persona and the switch that turns it off** — `--plain` (also `NANODESU_PLAIN=1`)
  and `--nsfw` (off unless asked for, `--plain` overriding it), plus the note that machine-readable
  output never carries a voice and that the adult register lives in its own document.
* **The real test count: 141**, and two tests named because they check *claims* rather than behaviour —
  the boundary notice's first line, and the `PROSE` table's shape.

## Testing

**141 tests**, unchanged — this release is documentation.
