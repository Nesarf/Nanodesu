# Nanodesu! 1.3.2

A format compatibility matrix, and an archive this tool could not read.

## The archive it could not read

**Any PyInstaller build carrying a splash screen failed to parse.** `VALID_TYPES` omitted `l`, the
splash-resource code that has existed since **4.10**, and an unrecognised code does not lose one
entry — it stops the table walk, which leaves nothing to parse and rejects the whole archive.

Reproduced against a real 836-entry archive by changing a single entry's code to `l`, which is
entirely valid:

```
entry 1 has unsupported type code 'l' (not in MRZbdmnosxz); table truncated here
```

So the failure was total, and its message was about an unsupported type code — nothing about
splashes. Fixed, and `test_format_matrix.py` now asserts the accepted set contains **every** code
PyInstaller has ever defined, and **nothing else**.

## Three constants disagreed with upstream, and one named a code that does not exist

| constant | value | upstream | upstream meaning |
|---|---|---|---|
| `TC_BINARY_DEP` | `x` | `ARCHIVE_ITEM_DATA` | data |
| `TC_BINARY_EMBED` | `z` | `ARCHIVE_ITEM_PYZ` | PYZ |
| `TC_DATA` | `d` | `ARCHIVE_ITEM_DEPENDENCY` | dependency |
| `TC_RUNTIME` | `R` | *none* | no version defines `R` |

The values were always right, so the code behaved correctly — but a reader who trusts a name called
`TC_BINARY_DEP` inside a `d` branch is being set up to make a mistake. The names now follow
PyInstaller's own header, and `TC_SPLASH` / `TC_SYMLINK` exist rather than being anonymous letters.

## `FORMAT_MATRIX.md`

**Every fact in it is read out of PyInstaller's own sources** for nine versions spanning the three
format generations — 3.6, 4.0, 4.10, 5.0, 5.13.2, 6.0.0, 6.11.1, 6.16.0, 6.22.3 — from sdists
fetched from PyPI. Nothing is written from memory and nothing is inferred from samples.

The finding that matters for a reader:

| generation | versions | TOC record | cookie |
|---|---|---|---|
| 1 | 3.6, 4.0 | `!iiiiBB` | `!8siiii64s` |
| 2 | 4.10 – 6.0 | `!iIIIBB` | `!8sIIii64s` |
| 3 | 6.11+ | `!IIIIBc` | `!8sIIII64s` |

**The record sizes never changed** — a TOC record is 18 bytes and a cookie is 88 in every
generation. The layouts are byte-for-byte identical; only the signedness of the integer fields
differs, and a real archive never carries a negative offset or length. That is why one parser reads
all three, and why the bounds checks catch corruption under either reading.

`tools/format_matrix.py` regenerates the table, so the document is reproducible rather than
transcribed.

## Testing

**79 tests** (was 61), green on Python 3.9, 3.12, 3.13 and 3.14. The matrix tests assert that the
code and the document agree, which is the only thing that keeps a table like this honest a year
later.
