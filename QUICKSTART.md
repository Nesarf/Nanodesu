# Quick start

Someone hands you a `.exe`. It might be a portable app, a game utility, a "crack", or
something that arrived with no explanation. You want to know what it is **before** you run it.

That is what Nanodesu! is for. It opens a PyInstaller-packaged program and shows you what is
inside — without running it. Nothing in this tool executes the target.

## Try it in three commands

```bash
python nanodesu.py info    some-app.exe          # what is this?
python nanodesu.py ls      some-app.exe --top 10 # what is inside?
python nanodesu.py extract some-app.exe -o out   # give me all of it
```

Real output from a 8.1 MB program:

```
file        : some-app.exe
total size  : 8.1 MB   (8487307 bytes)
Python      : 3.12  (python312.dll)
stub        : 364.5 KB
PKG length  : 7.7 MB
archive at  : 0x5b200      cookie @ 0x818133
TOC         : 0x7bc173, 3.4 KB, 62 entries
payload     : 18.2 MB uncompressed in total
OPTION      : pyi-contents-directory _internal
```

Read that as an answer to "what am I about to run?":

* it is a **Python 3.12** program, packaged by PyInstaller
* it carries **62 files**, 18.2 MB once unpacked
* the biggest single thing inside is a Python runtime, not the program itself

Then look at what is actually in there:

```
type                   raw      stored  zlib  name
binary             6945272     2548450  zlib  python312.dll
binary             5231472     1856009  zlib  libcrypto-3.dll
binary             1377496      663851  zlib  ucrtbase.dll
```

## Why opening it beats running it

Static unpacking has a property that no scanner and no sandbox has: **the file cannot
defend itself, because it never runs.**

* No code from the program executes, so it cannot hide its processes, install a driver,
  drop a second stage, or phone home.
* No antivirus definition is needed. You see the contents on the first encounter, with no
  wait for someone else to upload a sample.
* No sandbox to escape. There is nothing to escape from — unpacking is reading bytes.

That last point matters more than it sounds. Modern malware is built to run unobserved and
to notice when it is being watched. A tool that never runs the target has no such weakness.
It is also why the honest limit is worth stating plainly: **if a payload only ever exists in
memory and never touches disk, there is no file to open.** Then this tool cannot help, and
neither can any other file-based one.

## The asymmetry this is meant to break

Malware authors assume that their sample will eventually be analysed — that is what
antivirus vendors do, and it is why detection often arrives after the damage. What they do
not assume is that the person on the receiving end can look inside the file themselves,
before running it.

That assumption is worth breaking, and it costs nothing: the same `.exe` that is opaque to
you is just a data structure to a program that knows the format.

## If it is not a PyInstaller program

Nanodesu! will say so instead of guessing. For everything else, use
[Triage](https://github.com/Nesarf/triage), which identifies the file by its actual bytes,
names the language and packer, extracts indicators, and — like this tool — never executes
the target.

## What else is in the box

| Command | Use |
|---|---|
| `info` | python version, sizes, entry count — the fastest "what is this" |
| `ls` | list the contents, filter/sort/top-N |
| `tree` | directory tree with sizes, if you want to see the shape |
| `search` | find an entry by name, or search inside entry contents |
| `cat` | write one entry to stdout, e.g. a config file |
| `extract` | unpack everything (`--pyc` also makes the bytecode decompiler-ready) |
| `verify` | prove the archive is intact |
| `pyz` | unpack the PYZ archive into per-module bytecode |
| `build` | put an extracted tree back together, byte for byte |

A satisfying one to try: **unpack Nanodesu! with Nanodesu!**

```bash
python nanodesu.py extract nanodesu.exe -o self
python nanodesu.py build self -o rebuilt.exe
./rebuilt.exe --help      # it still runs
```

## Next

* [`README.md`](README.md) — the full picture, including the format details
* [`tools/charlayer.py`](tools/charlayer.py) — a layered character-canvas renderer that
  ships with this repo
* [Triage](https://github.com/Nesarf/triage) — the static-triage sibling for unknown files
  in general

No network access. No dependencies beyond Python 3.9+. Nothing here runs the program you
are inspecting.
