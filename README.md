# Nanodesu!

[![tests](https://github.com/Nesarf/Nanodesu/actions/workflows/tests.yml/badge.svg)](https://github.com/Nesarf/Nanodesu/actions/workflows/tests.yml)

Unpack and repack PyInstaller single-file executables — all the way down.

Standard library only. No network access, and the target program is never
executed. The name is a fan catchphrase; it carries no technical meaning.

**New here?** → [`QUICKSTART.md`](QUICKSTART.md) opens with the situation this tool is for
and three commands you can run right now.

---

## What it does

A PyInstaller onefile executable is laid out like this:

```
[ bootloader stub ][ CArchive: header + payload + table of contents ][ 88-byte cookie ]
```

`Nanodesu!` reads that structure, takes every embedded file out of it, and can
put the whole thing back together again — every entry's **contents** identical,
with the table of contents preserved in its original shape.

Two different things get called "faithful", and they are worth separating:

| Claim | True? |
|---|---|
| every entry decompresses to the same bytes, TOC order/types/flags preserved, repacked exe runs | **yes** — this is what the tests check |
| the repacked file is **bit-identical** to the original | **no** — the payload is recompressed with zlib level 9, so the compressed bytes differ even when nothing was modified |

The distinction matters when you are comparing a repacked sample to a reference by hash: the
contents will match and the file will not.

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
# what this tool does not do, and one flag that silences the persona
python nanodesu.py --boundary
python nanodesu.py --plain info path/to/app.exe

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

**Getting source back out is a separate problem, and on a modern build a harder one than it
appears.** Measured here against a Python 3.12 build, `pycdc` refuses the extracted modules
outright, and `uncompyle6`/`decompyle3` support nothing past 3.8. See
[DECOMPILING.md](DECOMPILING.md) — including what does still work.

To find the exact bytecode magic for a target version, the tool looks for a
matching interpreter on `PATH` or in a few common locations, then falls back to
a built-in table. Point it somewhere specific with:

```bash
NANODESU_PYTHON="C:/tools/python-{ver}/python.exe" python nanodesu.py extract ...
```

`{ver}` is replaced by the version string, e.g. `3.12`.

## As a library

The commands print prose for a person and return an exit code. That is the right shape for a
CLI and the wrong shape for a caller — anything that captured that output and parsed it back
would be coupled to the wording of a report. So there is a small API that returns data instead:

```python
import nanodesu

nanodesu.inspect_archive("sample.exe")
# {'entry_count': 836, 'python_version': '3.12', 'stub_size': 382464,
#  'integrity': [], 'total_uncompressed': 240251704, ...}

nanodesu.list_entries("sample.exe")      # [{'name': 'struct', 'tcode': 'm', ...}, ...]
nanodesu.read_file("sample.exe", "struct")   # b'...'   (KeyError if there is no such entry)
nanodesu.verify_archive("sample.exe")    # {'ok': True, 'decompressed': 836, 'problems': []}
nanodesu.extract("sample.exe", "out/")   # {'ok': True, 'written': 836, 'confined': [], ...}
```

| function | returns |
|---|---|
| `inspect_archive(target)` | the facts `info` prints, as a dict |
| `list_entries(target)` | the table of contents, in archive order |
| `read_file(target, name)` | one entry's bytes |
| `verify_archive(target, read=True)` | what `verify` reports, as data (`read=False` skips decompression) |
| `extract(target, out_dir, pyc=True, ...)` | a summary, having written a repackable tree |

Where a command would print a message and exit, these raise **`PyInstallerError`** — which
carries the path it was given — so a caller can tell "this is not an archive" from "this file
is missing". `read_file` raises `KeyError` for an absent name.

`extract()` writes the same `_archive_manifest.json` the command writes, and the manifest it
produces is the one `build` accepts: extracting through the API and repacking through the CLI
round-trips. That is a contract, and a test holds it.

Importing the module prints nothing. Nothing here executes the target.

## Verified behaviour

Against a 138.8 MB onefile build (PyInstaller 6.x, Python 3.12, PySide6):

* **836 entries extracted, 0 failures**
* `verify` decompresses all 836 entries and their lengths match
* unpack → repack → unpack again reproduces **all 836 entry contents byte for byte**
  (the repacked file itself is not bit-identical: see the note above)
  (SHA-256 identical, including the embedded PYZ archive)
* the repacked executable **starts and runs normally**

## Testing

The test suite builds small synthetic archives in a temporary directory, so it
runs in well under a second and needs no sample executable:

```bash
python -m unittest discover -s test -v
```

**141 tests.** It covers archive parsing, the `extract` → `build` round trip, the `--pyc` header
handling, contents-directory placement, PYZ extraction, the error paths, `neutralize`, and the
boundary notice. The three format details listed under "Three details that break everything if
changed back" each have a dedicated regression test.

Two of them are worth naming because they check claims rather than behaviour:

* **The boundary notice's first line is asserted, not trusted.** Every `subprocess.run` call site must
  pass `-c` with a magic-number query, and the archive under audit must never reach a subprocess. The
  first draft of that sentence claimed "nothing here creates a process" and the test found it false.
* **Every `PROSE` entry must be a pair of strings.** One was a bare string, which made `--help` raise
  `ValueError: too many values to unpack` — broken at the front door, and only for anyone who had not
  already learned `--plain`.

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

## What this tool does not establish

```bash
python nanodesu.py --boundary
```

Four lines, and they travel with **every** result — `info` prints them without being asked, and
`--boundary` lets you read them on purpose:

```
This tool never executes, loads or launches the archive it reads. The only process it can
create is a Python interpreter asked for its own bytecode magic number, and only when a .pyc
header is needed.
It does NOT address: whether the program is safe, what it does when run, or whether the
extracted bytes are what its author intended. Extraction reports structure, not intent.
It reads [the CArchive table of contents, stored entry bytes, and the PYZ]. It is NOT a
decompiler, NOT a malware detector, and NOT a packer for anything except the archive it
came from.
A clean extraction is NOT proof of anything. Specifically: a byte-identical repack proves
fidelity and not safety; a bare marshalled code object is not source; and content that never
appears in the table of contents is invisible to this tool entirely.
```

Each line names a specific thing a reader would otherwise assume. **A general caution gets skimmed;
"a byte-identical repack proves fidelity and not safety" does not.**

The first line is a claim, so it is **checked rather than worded carefully**: a test walks every
`subprocess.run` call site and requires each to pass `-c` with a magic-number query, and another
requires that the archive under audit never reaches a subprocess at all. The first draft of that line
said "nothing here creates a process" — **and the test written for it found that to be false**, because
`--pyc` does launch an interpreter to ask for its own magic number. A boundary notice that is untrue
in its first line is worse than no notice.

## The persona, and the switch that turns it off

`--help` and the no-command path speak as **Lilith** (`PERSONA.md`, `CHARACTER_LILITH.md`). The
intended register, in short: warm outside, cold when something is lying, and proud of never needing to
bite — she reads a file without waking it and treats that restraint as an honour rather than a
limitation.

| | |
|---|---|
| **`--plain`** | no persona at all; also `NANODESU_PLAIN=1` |
| **`--nsfw`** | an adult register, **off unless asked for**; `--plain` overrides it |

Machine-readable output never carries a voice, and the adult register lives in its own document
(`PERSONA.nsfw.md`) so that someone who wants a security tool is not handed something else.

## `neutralize` — a defanged variant, not a cure

```bash
python nanodesu.py neutralize app.exe -o work/ --in-pyz --modules payload
```

Replaces named Python modules inside the PYZ with a stub that does nothing, then repacks. The result
is `app_defanged.exe`.

**It is a variant. It is never clean, safe or fixed, and this tool will not call it any of those.**
That is not caution, it is the mechanism: editing an archive changes its hash and invalidates its
signature, so the variant **stops matching threat intelligence and AV caches** — meaning it will
always scan clean, **not because it is clean but because nobody has seen it**. A tool that produced
such files and called the result safe would be manufacturing false confidence.

What it does guarantee:

* **The original is never modified, moved or deleted.** It stays exactly where it was; it is the only
  thing a real engine can still judge.
* **The payload bytes are gone, not just unreferenced.** The PYZ is rebuilt rather than patched in
  place, so the original compressed block does not survive after the shorter stub.
* **Every change is recorded** in `_neutralize_record.json` — dotted name, bytes before and after —
  so the transformation can be audited.
* **The stub is compiled by the interpreter matching the archive** where one is found, and loaded back
  before anything is replaced.

What it does **not** do, and the report says so each time:

* **It does not make the program work.** Stubbing a module the program depends on **breaks it** —
  verified: a sample that called into the stubbed module died with `AttributeError`. Removing the
  malicious capability and keeping the program running are different goals, and this does the first.
* **It does not prove the rest of the file is harmless.** Stubbing a module proves that module no
  longer runs, and nothing else.
* **It cannot do this for machine code.** This works because a PyInstaller payload lives at the Python
  level. A hostile DLL or shellcode has no equivalent small, checkable act, and claiming otherwise
  would be a lie about coverage.

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
