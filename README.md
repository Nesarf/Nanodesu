# Nanodesu!

[![tests](https://github.com/Nesarf/Nanodesu/actions/workflows/tests.yml/badge.svg)](https://github.com/Nesarf/Nanodesu/actions/workflows/tests.yml)

Unpack and repack PyInstaller single-file executables — all the way down.

Standard library only. No network access, and the target program is never
executed. The name is a fan catchphrase; it carries no technical meaning.

---

## What it does

A PyInstaller onefile executable is laid out like this:

```
[ bootloader stub ][ CArchive: header + payload + table of contents ][ 88-byte cookie ]
```

`Nanodesu!` reads that structure, takes every embedded file out of it, and can
put the whole thing back together again — byte for byte.

| Command | Purpose |
|---|---|
| `info` | summarise the archive: python version, sizes, entry count |
| `ls` | list the table of contents (filter, sort, top-N) |
| `tree` | directory tree with per-branch sizes |
| `search` | search entry names, optionally entry contents |
| `cat` | write one entry to stdout or a file |
| `extract` | unpack everything and rebuild the runtime directory layout |
| `verify` | decompress every entry and check its length |
| `pyz` | unpack a PYZ archive into per-module bytecode |
| `build` | repack an extracted directory back into an executable |

## Install

Nothing to install: it is a single file using only the standard library.

```bash
python nanodesu.py info path/to/app.exe
```

Requires Python 3.9 or newer.

## Usage

```bash
# overview
python nanodesu.py info    path/to/app.exe

# explore
python nanodesu.py tree    path/to/app.exe --depth 2
python nanodesu.py ls      path/to/app.exe --sort size --top 30
python nanodesu.py search  path/to/app.exe "PySide6" --content
python nanodesu.py cat     path/to/app.exe main -o main.pyc

# unpack: rebuilds the runtime layout and writes _archive_manifest.json
python nanodesu.py extract path/to/app.exe -o out/app --pyc

# check the archive is intact
python nanodesu.py verify  path/to/app.exe

# unpack the PYZ archive inside it (if the build has one)
# modules come out as valid .pyc files; --bare keeps the stored bytes untouched
python nanodesu.py pyz     out/app/PYZ.pyz -o out/app/_pyz_modules

# put it back together
python nanodesu.py build   out/app -o out/app_repacked.exe --pylib python312.dll
```

`extract` writes `_archive_manifest.json` alongside the files. `build` prefers
that manifest, which is what makes the repack faithful: original names, original
type codes, original compression flags and the original entry order. Without a
manifest it falls back to inferring everything from the directory.

`build` takes two optional switches, both recovered automatically when omitted:

| Switch | Meaning | Default |
|---|---|---|
| `--pylib NAME` | value written into the cookie's python library field | the `python3*.dll` found in the tree |
| `--pyver N` | python version, as `major*100 + minor` (312 for 3.12) | parsed from that library name |

Both are recorded in the manifest, so repacking an `extract` output needs no
flags at all.

### The `--pyc` flag

Archives store python modules as bare marshalled code objects, with no `.pyc`
header. `extract --pyc` prepends a valid header so decompilers and other tooling
accept them directly; the same applies to `pyz`, which derives the header from
the PYZ archive's own bytecode magic. The number of prepended bytes is recorded in the manifest, and
`build` strips exactly that many again — so the same extracted tree both feeds
decompilers and repacks cleanly.

To find the exact bytecode magic for a target version, the tool looks for a
matching interpreter on `PATH` or in a few common locations, then falls back to
a built-in table. Point it somewhere specific with:

```bash
NANODESU_PYTHON="C:/tools/python-{ver}/python.exe" python nanodesu.py extract ...
```

`{ver}` is replaced by the version string, e.g. `3.12`.

## Verified behaviour

Against a 138.8 MB onefile build (PyInstaller 6.x, Python 3.12, PySide6):

* **836 entries extracted, 0 failures**
* `verify` decompresses all 836 entries and their lengths match
* unpack → repack → unpack again reproduces **all 836 files byte for byte**
  (SHA-256 identical, including the embedded PYZ archive)
* the repacked executable **starts and runs normally**

## Testing

The test suite builds small synthetic archives in a temporary directory, so it
runs in well under a second and needs no sample executable:

```bash
python -m unittest discover -s test -v
```

It covers archive parsing, the `extract` → `build` round trip, the `--pyc`
header handling, contents-directory placement, PYZ extraction, and the error
paths. The three format details listed under "Three details that break
everything if changed back" each have a dedicated regression test.

Continuous integration runs the suite on Linux and Windows across Python
3.9, 3.12, 3.13 and 3.14 (see `.github/workflows/tests.yml`).

## Format notes

The format below was derived from the on-disk layout. The repacking rules are the
constraints that make the write path correct: each one corresponds to a way a repack
fails, so none of them is optional.

| Item | Detail |
|---|---|
| cookie | `!8sIIII64s`, 88 bytes: `magic(8) pkg_len(4) toc_off(4) toc_len(4) pyver(4) pylib(64)` |
| archive start | `base = cookie_pos + 88 - pkg_len` — `pkg_len` **includes** the 88-byte cookie |
| TOC entry | `!IIIIBc`: 18-byte header followed by a NUL-padded name |
| `entry_length` | total size of the record including the 18-byte header, padded to a multiple of 16 |
| `offset` | measured from the archive start, as is `toc_offset` |
| compression | flag `0` = stored, `1` = zlib over the whole blob |
| type codes | `b` binary, `x` dependency, `m` module, `s` source, `z` **PYZ archive**, `Z` plain zipfile, `o` option, `d` data, `n` symlink |
| option entries | value lives in the name, payload length is zero, e.g. `pyi-contents-directory _internal` |
| module payloads | bare `marshal` code objects; `extract --pyc` and `pyz` add a valid `.pyc` header |
| PYZ archive | `PYZ\0` + 4-byte bytecode magic + `int32` TOC offset + 5 reserved bytes; TOC is a marshalled list; item types `0` module, `1` package, `2` legacy data, `3` namespace package |
| repacking | `pkg_len = len(PKG body) + 88`; `toc_off = 88 + payload length`; entry offsets are relative to the PKG start |

### Three details that break everything if changed back

1. **A PYZ archive uses lowercase `z`.** Uppercase `Z` is a plain zipfile entry.
   The parser must accept lowercase `z`. If it does not, the table walk stops
   before its final entry; the repacked executable then has no PYZ archive and
   refuses to start with `PYZ archive entry not found in the TOC!`.
2. **PYZ item type `0` is an ordinary module.** Type `3` is the namespace
   package with no code object. Skipping type `0` as if it were a namespace
   package silently reduces the output to package `__init__` files only.
3. **Headers prepended by `--pyc` must be stripped on repack**, using the
   per-entry `pyc_header` length from the manifest. Otherwise the marshalled
   code object stored in the archive is corrupted.

## Notes and limits

* **Onefile builds only.** The file must end with the `MEI` cookie. A onedir
  executable carries the same PKG section, but only the bootstrap half of it.
* Other packers (Nuitka, py2exe, cx_Freeze, ...) are not supported.
* Decompiling bytecode is a separate problem: code objects are tied to the
  interpreter version that produced them. `--pyc` produces valid input for
  decompilers, but the output of any decompiler should be treated as a
  hypothesis, not as the original source.
* Repacking a large archive is I/O bound and can take a few minutes on slow
  storage.
* A module and a package can claim the same path inside a PYZ archive (for
  example `utils` and `utils.sub`). The extractor detects that and writes the
  later one under a flattened name rather than overwriting it.
* `pyz` writes valid `.pyc` files (the header is derived from the PYZ bytecode
  magic, so nothing is guessed). Use `--bare` for the untouched stored bytes.
* Non-archive input, missing paths and directories all fail with a one-line
  message and exit status 1; Python tracebacks are not shown to the CLI user.

## Tools

* `tools/charlayer.py` — a layered character-cell canvas for Qt, used for
  per-character text effects (jitter, displacement, scanlines, reveal). It has
  a Qt-free logic self-test: `python tools/charlayer.py`.

## License

MIT — see `LICENSE`.
