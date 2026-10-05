# PyInstaller format compatibility

What this parser reads, what PyInstaller has actually emitted, and where the two differ.

**Every fact here is read out of PyInstaller's own sources for that version**, not written from
memory and not inferred from samples. The sdists are fetched from PyPI, and the values come from
`PyInstaller/archive/readers.py`, `PyInstaller/archive/writers.py` and
`bootloader/src/pyi_archive.h`.

| | |
|---|---|
| versions examined | 3.6, 4.0, 4.10, 5.0, 5.13.2, 6.0.0, 6.11.1, 6.16.0, 6.22.3 |
| format generations found | **three** |
| `typecodes accepted` | `bdzZMmsxonl` |

Regenerate with the extractor described in *Reproducing this table* below; the numbers here came
from that run and are checked against the code by `test/test_format_matrix.py`.

---

## The three generations

| generation | versions | TOC record | cookie | what changed |
|---|---|---|---|---|
| **1** | 3.6, 4.0 | `!iiiiBB` | `!8siiii64s` | — |
| **2** | 4.10, 5.0, 5.13.2, 6.0.0 | `!iIIIBB` | `!8sIIii64s` | TOC `offset`/`length` became **unsigned**; cookie `toc_offset`/`toc_length` became **unsigned**; splash code `l` appeared |
| **3** | 6.11.1, 6.16.0, 6.22.3 | `!IIIIBc` | `!8sIIII64s` | every integer field became **unsigned**; the typecode became an explicit `c` rather than `B`; symlink code `n` appeared at 6.0 |

**The record sizes never changed.** A TOC record is **18 bytes** and a cookie is **88 bytes** in
every generation. The layouts are byte-for-byte the same; only the signedness of the integer fields
and the encoding of the final byte differ.

That is the central fact for a reader: **one parser can read all three**, provided it does not
depend on signedness. A negative `offset` or `length` in a real archive is meaningless either way,
so reading the fields as unsigned and validating the bounds catches corruption just as well — which
is what this parser does. It declares the **generation 3** formats, because unsigned is the correct
reading for a field that must never be negative, and the bounds checks reject the same malformed
input under either.

## Type codes

Read from `ARCHIVE_ITEM_*` in `bootloader/src/pyi_archive.h`. The *since* column is the first of the
examined versions that defines each code.

| code | name | meaning | since |
|---|---|---|---|
| `b` | `ARCHIVE_ITEM_BINARY` | binary | 3.6 |
| `d` | `ARCHIVE_ITEM_DEPENDENCY` | dependency | 3.6 |
| `z` | `ARCHIVE_ITEM_PYZ` | PYZ archive (frozen Python code) | 3.6 |
| `Z` | `ARCHIVE_ITEM_ZIPFILE` | plain zipfile | 3.6 |
| `M` | `ARCHIVE_ITEM_PYPACKAGE` | Python package (`__init__`) | 3.6 |
| `m` | `ARCHIVE_ITEM_PYMODULE` | Python module | 3.6 |
| `s` | `ARCHIVE_ITEM_PYSOURCE` | Python source script | 3.6 |
| `x` | `ARCHIVE_ITEM_DATA` | data | 3.6 |
| `o` | `ARCHIVE_ITEM_RUNTIME_OPTION` | runtime option | 3.6 |
| `l` | `ARCHIVE_ITEM_SPLASH` | splash resource | **4.10** |
| `n` | `ARCHIVE_ITEM_SYMLINK` | symbolic link | **6.0** |

Note that `z` is the PYZ and `Z` is a zipfile. Confusing the two was a real defect in this project
once already, which is why both are listed with their full names.

## What went wrong here, and why this document exists

**`l` was missing from the accepted set.** Every archive carrying a splash screen — available since
4.10 — failed to parse. Not by losing one entry: the table walk stops on an unrecognised code, so
the archive was rejected outright, with a message about an unsupported type code that said nothing
about splashes.

Verified against a real 836-entry archive by changing one entry's code to `l`, which is entirely
valid:

```
entry 1 has unsupported type code 'l' (not in MRZbdmnosxz); table truncated here
```

**Three constants disagreed with upstream names** while their values were correct:

| constant | value | upstream name | upstream meaning |
|---|---|---|---|
| `TC_BINARY_DEP` | `x` | `ARCHIVE_ITEM_DATA` | data |
| `TC_BINARY_EMBED` | `z` | `ARCHIVE_ITEM_PYZ` | PYZ |
| `TC_DATA` | `d` | `ARCHIVE_ITEM_DEPENDENCY` | dependency |
| `TC_RUNTIME` | `R` | *none* | no version defines `R` |

The code behaved correctly throughout — a wrong name is not a wrong value — but a reader who trusts
a name called `TC_BINARY_DEP` in a `d` branch is being set up to make a mistake.

## Reading an archive whose generation is unknown

1. Read the cookie: 88 bytes ending at the file's last 88 bytes. Its `magic` is
   `MEI\014\013\012\013\016` in every generation.
2. Read `pkg_length`, `toc_offset`, `toc_length` **as unsigned**. A generation 1 or 2 cookie packs
   some of them signed, but a real archive never carries a negative value, so unsigned reads the
   same bytes and the same numbers.
3. Walk the TOC in 18-byte records. `entry_length` includes the name field and is padded to a
   multiple of 16, so the name length is `entry_length - 18`.
4. Accept every code in the table above. **An unrecognised code is a different thing from an
   invalid one** — refusing a real code costs the whole archive.

## Reproducing this table

Fetch the sdists and read the constants:

```
https://pypi.org/pypi/pyinstaller/<version>/json      -> the sdist URL
  PyInstaller/archive/writers.py    _TOC_ENTRY_FORMAT, archive_magic, archive_type_codes
  PyInstaller/archive/readers.py    the same formats, on the read side
  bootloader/src/pyi_archive.h      ARCHIVE_ITEM_*, struct TOC_ENTRY, struct ARCHIVE_COOKIE
```

Two mistakes made while building this are worth keeping, because both produced plausible-looking
output rather than errors:

* the format strings are **not** in the C source — the C side declares structs and Python derives
  the pack format from them, so searching the bootloader for `!8sIIII64s` finds nothing
* a regex for the type codes required a trailing semicolon that the definitions do not have, so
  six of nine versions appeared to define no type codes at all

When the extractor reports a gap, suspect the extractor before concluding anything about PyInstaller.
