#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Nanodesu! - a PyInstaller onefile unpacker / repacker. Standard library only.

Takes a PyInstaller onefile executable apart, all the way down. Layout:

    [bootloader stub][CArchive: header + embedded payload + TOC][88-byte cookie]

Cookie and TOC layout follow the documented PyInstaller CArchive format:

    cookie  = '!8sIIII64s'
              magic(8) | pkg_len(4) | toc_off(4) | toc_len(4) | pyver(4) | pylib(64)
    TOC entry = '!IIIIBc' - 18-byte header, then a variable-length name
                padded to a multiple of 16 bytes
              entry_length(4) | offset(4) | length(4) | uncompressed_length(4)
              | compression_flag(1) | typecode(1) | name[...]

Two conversions worth remembering (both were bugs once - do not change them back):

    archive start  base = cookie_pos + 88 - pkg_len
    TOC position        = base + toc_off

pyver is major*100+minor (3.12 -> 312).

Subcommands:
    info      overview: python version, sizes, entry count
    ls        list the table of contents (filter, sort, top-N)
    tree      directory tree with per-branch sizes
    search    search entry names, optionally entry contents
    cat       dump one entry to stdout or a file
    extract   unpack everything and rebuild the runtime directory layout
    pyz       further unpack a PYZ archive into per-module bytecode
    verify    decompress every entry and check its length
    build     repack an extracted directory back into an executable

The name comes from the author's catchphrase.

No network access, and the target program is never executed.

Examples:
    python nanodesu.py info    path/to/app.exe
    python nanodesu.py tree    path/to/app.exe --depth 2
    python nanodesu.py ls      path/to/app.exe --sort size --top 30
    python nanodesu.py extract path/to/app.exe -o out/app
    python nanodesu.py pyz     out/app/PYZ.pyz -o out/app/_pyz_modules
    python nanodesu.py build   out/app -o out/app_repacked.exe
"""

from __future__ import annotations

from types import SimpleNamespace

import boundary

import argparse
import io
import json
import marshal
import os
import re
import shutil
import struct
import sys
import zlib
from dataclasses import dataclass, field
from pathlib import Path

MAGIC = b"MEI\x0c\x0b\x0a\x0b\x0e"
COOKIE_FORMAT = "!8sIIII64s"
COOKIE_LEN = struct.calcsize(COOKIE_FORMAT)          # 88
TOC_ENTRY_FORMAT = "!IIIIBc"
TOC_ENTRY_LEN = struct.calcsize(TOC_ENTRY_FORMAT)    # 18
# Kept in step with pyproject.toml. A caller that loads this module by path -- which is how
# triage finds it -- has no package metadata to read, so the version has to live in the source.
def _version() -> str:
    """The version of the installed distribution, or of this file.

    A caller may load this module **by path** rather than importing it -- the sibling tool finds
    `nanodesu.py` on disk and loads it that way -- and then there is no installed distribution to
    ask, so the constant is the fallback for that case. When it *is* installed, the metadata wins,
    which is what keeps this number from drifting away from `pyproject.toml`.
    """
    try:
        from importlib.metadata import version as _dist_version
        return _dist_version("nanodesu")
    except Exception:
        return _SOURCE_VERSION


# Kept only for the by-path case. When the package is installed this value is not used.
_SOURCE_VERSION = "1.6.2"
VERSION = _version()

PKG_HEADER_LEN = 88          # PKG header size (one cookie length)

# How much a single entry is allowed to decompress to, regardless of what the archive declares.
# The declaration is a claim by whoever built the archive, so it can only be used to check the
# result -- never to authorise the allocation. Without a limit of our own, an archive that
# honestly declares 20 GB gets 20 GB attempted, and the honesty is the attack.
MAX_ENTRY_BYTES = 2 << 30
PYC_HEADER_LEN = 16          # 3.7+ .pyc header: magic(4)+flags(4)+mtime(4)+size(4)
PYZ_MAGIC = b"PYZ\0"
NULB = b"\x00"

# These names follow PyInstaller's own header (bootloader/src/pyi_archive.h, `ARCHIVE_ITEM_*`).
# Three of them used to disagree with it -- "x" was called a binary dependency, "z" a binary embed,
# and "d" data -- while the values were right, so the code worked and only the reading of it was
# wrong. `R` had no counterpart in any version at all.
TC_BINARY = "b"          # ARCHIVE_ITEM_BINARY
TC_DEPENDENCY = "d"      # ARCHIVE_ITEM_DEPENDENCY
TC_PYZ = "z"             # ARCHIVE_ITEM_PYZ -- LOWERCASE z; uppercase Z is a plain zipfile
TC_ZIPFILE = "Z"         # ARCHIVE_ITEM_ZIPFILE
TC_PACKAGE = "M"         # ARCHIVE_ITEM_PYPACKAGE (a package's __init__)
TC_MODULE = "m"          # ARCHIVE_ITEM_PYMODULE
TC_SOURCE = "s"          # ARCHIVE_ITEM_PYSOURCE
TC_DATA = "x"            # ARCHIVE_ITEM_DATA
TC_OPTION = "o"          # ARCHIVE_ITEM_RUNTIME_OPTION
TC_SPLASH = "l"          # ARCHIVE_ITEM_SPLASH -- since 4.10
TC_SYMLINK = "n"         # ARCHIVE_ITEM_SYMLINK -- since 6.0
# A PYZ archive uses LOWERCASE 'z'; uppercase 'Z' is a plain zipfile entry. Both are
# required in the accepted set: omitting lowercase 'z' stops the TOC walk before its last
# entry, leaving the repacked archive without a PYZ and unable to start.
# Every code PyInstaller defines, across every version, and no invented ones. `l` was
# missing, so an archive carrying a splash screen failed to parse with a message about
# an unsupported type code. A code that is real must never stop the table walk.
VALID_TYPES = set("bdzZMmsxonl")

TYPE_NAMES = {
    TC_BINARY: "binary", TC_DATA: "binary(dep)", TC_DATA: "data",
    TC_SOURCE: "source", TC_MODULE: "module", TC_PACKAGE: "module(dep)",
    TC_PYZ: "pyz", TC_ZIPFILE: "zipfile", TC_OPTION: "runtime-hook",
    TC_OPTION: "option", "n": "symlink",
    "Z": "zipfile", "n": "symlink",
}


@dataclass
class Entry:
    name: str
    offset: int          # data offset, relative to the archive start
    csize: int           # stored (compressed) size
    usize: int           # uncompressed size
    cmprs: int           # compression flag
    tcode: str
    raw_pos: int = 0     # absolute position of the data in the file

    @property
    def tname(self) -> str:
        return TYPE_NAMES.get(self.tcode, "unknown(%r)" % self.tcode)

    @property
    def stored_size(self) -> int:
        return self.csize if self.cmprs else self.usize


@dataclass
class Archive:
    path: Path
    file_size: int
    cookie_pos: int
    pkg_len: int
    toc_off: int
    toc_size: int
    pyver: int
    pylib: str
    base_pos: int
    toc: list
    options: list
    integrity: list = field(default_factory=list)
    """Everything the parser found structurally wrong, as facts rather than exceptions.

    A malformed archive is a finding about the file, not a reason to refuse to describe it: this
    is an analysis tool, and 'the table of contents is truncated' is exactly the sort of thing
    somebody pointing it at an unknown file wants to be told. So the parse records and continues,
    and the CLI surfaces it.
    """

    @property
    def stub_size(self) -> int:
        return self.base_pos

    @property
    def total_payload(self) -> int:
        return sum(e.usize for e in self.toc)

    def pyver_str(self) -> str:
        v = self.pyver
        if v == 0:
            return "n/a"
        major, minor = divmod(v, 100)
        return "%d.%d" % (major, minor)


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def _inflate(data: bytes, limit: int | None = None) -> bytes:
    """Decompress, refusing to build more than `limit` bytes.

    A declared uncompressed size is a claim by whoever built the archive, so it cannot be the
    only thing standing between a small file and an allocation of whatever that claim says. The
    previous version called zlib.decompress and compared lengths only afterwards, so a 10 MB
    entry declaring 20 GB would have tried to allocate the 20 GB first. Streaming with a cap
    turns that into a refusal rather than a memory event.

    `limit=None` keeps the old behaviour, for callers with no declared size to check against.
    """
    for wbits in (15, -15, 31):
        obj = zlib.decompressobj(wbits)
        try:
            out = obj.decompress(data, limit if limit is not None else 0)
        except zlib.error:
            continue
        if obj.unconsumed_tail:
            raise zlib.error(
                "entry decompresses to more than the %d bytes allowed (its declared size is a "
                "claim, not a guarantee)" % (limit,))
        try:
            out += obj.flush()
        except zlib.error:
            continue
        return out
    raise zlib.error("could not decompress")


def find_archive(path) -> Archive:
    path = Path(path)
    if not path.exists():
        raise SystemExit("no such file: %s" % path)
    if path.is_dir():
        raise SystemExit("that is a directory, not an archive: %s" % path)
    size = path.stat().st_size
    with open(path, "rb") as f:
        f.seek(max(0, size - 8192))
        tail = f.read()
    idx = tail.rfind(MAGIC)
    if idx < 0:
        raise SystemExit("not a PyInstaller archive (no MEI cookie found): %s" % path)
    cookie_pos = size - len(tail) + idx
    with open(path, "rb") as f:
        f.seek(cookie_pos)
        cookie = f.read(COOKIE_LEN)
    if len(cookie) < COOKIE_LEN:
        raise SystemExit("truncated cookie: %s" % path)

    _magic, pkg_len, toc_off, toc_size, pyver, raw_lib = struct.unpack(COOKIE_FORMAT, cookie)
    pylib = raw_lib.split(NULB, 1)[0].decode("latin1", "replace")
    # As in the reference reader: pkg_start = cookie_end - archive_length, where
    # archive_length includes the 88-byte cookie itself. Therefore:
    #   base = cookie_pos + 88 - pkg_len
    base = cookie_pos + COOKIE_LEN - pkg_len
    if not (0 <= base <= cookie_pos):
        raise SystemExit("implausible pkg_len (base=%#x); not a onefile build?" % base)

    with open(path, "rb") as f:
        f.seek(base + toc_off)
        data = f.read(toc_size)

    toc, options = [], []
    cur = 0
    n = len(data)
    integrity = []

    # The region an entry's payload is allowed to live in, as absolute file offsets. Everything
    # the TOC claims is checked against these before it is used, because a claimed offset or
    # length is attacker-controlled: a value past the cookie would have read into the 88-byte
    # header, and one far past the end would have been handed to zlib before anyone noticed.
    payload_end = base + pkg_len - COOKIE_LEN      # == cookie_pos, measured

    if toc_off < 0 or toc_off + toc_size > pkg_len - COOKIE_LEN:
        integrity.append("table of contents at %#x+%d lies outside the archive payload"
                         % (toc_off, toc_size))

    while cur + TOC_ENTRY_LEN <= n:
        (elen, eoff, dlen, ulen, cflag, tc) = struct.unpack(
            TOC_ENTRY_FORMAT, data[cur:cur + TOC_ENTRY_LEN])
        name_len = elen - TOC_ENTRY_LEN
        if elen <= TOC_ENTRY_LEN or name_len <= 0 or cur + TOC_ENTRY_LEN + name_len > n:
            integrity.append("entry %d has an implausible length (%d); table truncated here"
                             % (len(toc) + 1, elen))
            break
        if elen % 16 != 0:
            # The writer pads every record to a multiple of 16, so a record that is not is
            # either corrupt or a deliberate attempt to desynchronise the walk.
            integrity.append("entry %d length %d is not 16-byte aligned; table truncated here"
                             % (len(toc) + 1, elen))
            break
        try:
            tc_s = tc.decode("ascii")
        except UnicodeDecodeError:
            integrity.append("entry %d has a non-ASCII type code; table truncated here"
                             % (len(toc) + 1))
            break
        if tc_s not in VALID_TYPES:
            integrity.append("entry %d has unsupported type code %r (not in %s); table truncated "
                             "here" % (len(toc) + 1, tc_s, "".join(sorted(VALID_TYPES))))
            break
        raw_name = data[cur + TOC_ENTRY_LEN: cur + TOC_ENTRY_LEN + name_len]
        name = raw_name.rstrip(NULB).decode("utf-8", "replace")
        cur += TOC_ENTRY_LEN + name_len
        if cflag not in (0, 1):
            integrity.append("entry %r has compression flag %d, which the format does not define"
                             % (name, cflag))
        entry_end = base + eoff + dlen
        # Entry data begins at `base`, not after the header: measured on a real archive, the first
        # entry has offset 0 and the bytes there decompress to a valid code object whose length
        # equals its declared size. Requiring offset >= 88 would have been an invented shape
        # requirement, and it flagged a perfectly good file -- twice, because the first correction
        # only moved the wrong bound instead of checking what the offset actually means.
        #
        # So the invariant is what the reader relies on and nothing more: the claimed bytes must
        # be inside the archive and must not run into the table of contents.
        entry_limit = base + min(toc_off, pkg_len - COOKIE_LEN)
        if eoff < 0 or dlen < 0 or ulen < 0 or entry_end > entry_limit:
            integrity.append("entry %r claims bytes %#x..%#x, past the end of the data region "
                             "(%#x)" % (name, base + eoff, entry_end, entry_limit))
        if tc_s == TC_OPTION:
            options.append(name)
            continue
        toc.append(Entry(name=name, offset=eoff, csize=dlen, usize=ulen,
                         cmprs=cflag, tcode=tc_s))
    if cur != n and not integrity:
        integrity.append("table of contents is %d bytes but %d were consumed"
                         % (n, cur))
    if not toc:
        # Say why. "could not parse" on its own is the least useful thing to tell somebody who
        # pointed this at an unknown file, and the specific reason is already known here.
        detail = ("\n  - " + "\n  - ".join(integrity[:5])) if integrity else ""
        raise SystemExit("could not parse the table of contents (toc_off=%#x, toc_len=%d)%s"
                         % (toc_off, toc_size, detail))

    for e in toc:
        e.raw_pos = base + e.offset
    return Archive(path=path, file_size=size, cookie_pos=cookie_pos, pkg_len=pkg_len,
                   toc_off=toc_off, toc_size=toc_size, pyver=pyver, pylib=pylib,
                   base_pos=base, toc=toc, options=options, integrity=integrity)


def read_entry(ar: Archive, e: Entry, verify_len: bool = True) -> bytes:
    with open(ar.path, "rb") as f:
        f.seek(e.raw_pos)
        blob = f.read(e.stored_size)
    if len(blob) != e.stored_size:
        raise IOError("truncated read of %s (%d/%d)" % (e.name, len(blob), e.stored_size))
    if e.cmprs:
        # Two independent bounds, because they defend against different things: ours stops an
        # absurd entry even when the archive is honest about its size, and the declared size stops
        # a stream that is larger than the archive claims (which is the interesting failure).
        if e.usize > MAX_ENTRY_BYTES:
            raise IOError("%s declares %d bytes, over the %d-byte per-entry limit"
                          % (e.name, e.usize, MAX_ENTRY_BYTES))
        cap = min(e.usize + (1 << 20), MAX_ENTRY_BYTES) if e.usize else MAX_ENTRY_BYTES
        blob = _inflate(blob, cap)
    if verify_len and len(blob) != e.usize:
        raise IOError("size mismatch after decompressing %s (%d/%d)"
                      % (e.name, len(blob), e.usize))
    return blob


# --------------------------------------------------------------------------- #
# .pyc wrapping: archives store bare marshalled code objects, while decompilers
# generally expect a .pyc header in front of them.
# --------------------------------------------------------------------------- #

PYC_MAGIC_BY_VER = {
    "3.7": "420d0d0a", "3.8": "550d0d0a", "3.9": "610d0d0a", "3.10": "6f0d0d0a",
    "3.11": "a70d0d0a", "3.12": "cb0d0d0a", "3.13": "f30d0d0a", "3.14": "2b0e0d0a",
}
# Ask a matching local interpreter for the exact magic when possible, else use the table.
# Where a matching interpreter might live. Only used to read the exact bytecode
# magic for a target version; anything not found here falls back to the table below.
# Override with the NANODESU_PYTHON environment variable if your interpreters live
# somewhere else (use {ver} as the version placeholder, e.g. ".../python-{ver}/python.exe").
PY_EXE_TEMPLATES = (
    "python{ver}",
    "python{verm}{verm}",
    "C:/Python{verm}{verm}/python.exe",
    "C:/Program Files/Python{verm}{verm}/python.exe",
)


def _magic_for(ver_str: str):
    """Return the 4-byte .pyc magic for a version, or None if it cannot be determined."""
    import subprocess

    override = os.environ.get("NANODESU_PYTHON")
    templates = (override,) + PY_EXE_TEMPLATES if override else PY_EXE_TEMPLATES
    for tmpl in templates:
        try:
            exe = tmpl.format(ver=ver_str, verm=ver_str.replace(".", "")[:2]
                              if "." in ver_str else ver_str[:2])
        except (KeyError, IndexError):
            continue
        if not os.path.exists(exe):
            found = shutil.which(exe)          # a bare name may live on PATH
            if not found:
                continue
            exe = found
        try:
            out = subprocess.run(
                [exe, "-c", "import importlib.util;print(importlib.util.MAGIC_NUMBER.hex())"],
                capture_output=True, text=True, timeout=15).stdout.strip()
        except Exception:
            continue
        if len(out) == 8:
            try:
                return bytes.fromhex(out)
            except ValueError:
                continue
    hexs = PYC_MAGIC_BY_VER.get(ver_str)
    return bytes.fromhex(hexs) if hexs else None


def pyc_magic_for(ar: Archive):
    return _magic_for(ar.pyver_str())


def wrap_pyc(code_blob: bytes, magic: bytes) -> bytes:
    """Wrap a bare marshalled code object into a valid .pyc payload.

    Layout follows PEP 552: magic + flags + mtime + size.
    """
    return magic + struct.pack("<III", 0, 0, 0) + code_blob


# --------------------------------------------------------------------------- #
# Layout
# --------------------------------------------------------------------------- #

def layout_path(e: Entry, internal_dir: str = "_internal") -> Path:
    """Map an entry to its position in the runtime directory layout.

    A onefile build unpacks almost everything into a contents directory, named by
    the 'pyi-contents-directory' OPTION entry (usually _internal). Only a few
    entries stay next to the executable:
      - typecode 's' (top-level source)   -> top level
      - everything else (modules, PYZ, dependencies) -> contents directory
    The resulting tree matches what the bootloader itself extracts at runtime,
    which also gives the repacker a faithful layout to work from.
    """
    name = e.name.replace("\\", "/").lstrip("/")
    # Bootstrap modules, sources and the PYZ are looked up at the archive root.
    if e.tcode in (TC_SOURCE, TC_MODULE, TC_PACKAGE, TC_PYZ):
        return Path(name)
    return Path(internal_dir) / name


def flat_name(e: Entry) -> str:
    safe = e.name.replace("\\", "__").replace("/", "__")
    return "%s__%s" % (e.tname.replace("(", "_").replace(")", ""), safe)


def confining(
    out_root: Path,
    rel: Path,
    original_name: str,
    *,
    strip_drive: bool = False,
) -> Path:
    """Clamp an entry name so it cannot be written outside out_root.

    Entry names come from the archive, so they are untrusted input, and a name like
    '../../OUTSIDE/x' would otherwise write wherever the user has permission -- confirmed by
    test before this was added. An archive is a data structure someone else built; a tool
    whose whole purpose is to open untrusted files must not let that structure address the
    filesystem.

    Colons go too, not just separators: 'C:/Windows/x' reduces to the relative path
    'C:Windows/x', and on Windows a drive-relative path escapes out_root when joined to it.
    That was the second escape found by testing, after '..' was handled.

    The entry is repaired rather than dropped. Dropping would be silent data loss, which is
    worse than useless in an evidence tool, and it would break the round trip. Repairing
    keeps extraction faithful and repacking byte-exact; the caller records what changed.
    """
    parts = []
    for piece in rel.parts:
        if piece in ("", ".", ".."):
            continue
        piece = re.sub(r'[\\/:*?"<>|]', "_", piece)
        piece = piece.strip(" .")
        if piece:
            parts.append(piece)
    clamped = Path(*parts) if parts else Path("_unnamed")
    return clamped


def inside(root: Path, candidate: Path) -> bool:
    """True when candidate really resolves inside root, as a belt to the clamp's braces."""
    try:
        root_r = root.resolve()
        cand_r = (root / candidate).resolve()
    except OSError:
        return False
    return cand_r == root_r or root_r in cand_r.parents


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return ("%d B" % n) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024
    return "%.1f TB" % n


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def cmd_info(ar: Archive, args) -> int:
    print("file        : %s" % ar.path)
    print("total size  : %s   (%d bytes)" % (human(ar.file_size), ar.file_size))
    print("Python      : %s  (%s)" % (ar.pyver_str(), ar.pylib or "n/a"))
    print("stub        : %s" % human(ar.stub_size))
    print("PKG length  : %s" % human(ar.pkg_len))
    print("archive at  : %#x      cookie @ %#x" % (ar.base_pos, ar.cookie_pos))
    print("TOC         : %#x, %s, %d entries"
          % (ar.toc_off, human(ar.toc_size), len(ar.toc)))
    print("payload     : %s uncompressed in total" % human(ar.total_payload))
    if ar.options:
        print("OPTION      : %s" % ", ".join(ar.options))
    print()
    print("boundary")
    print(boundary.format_boundary())
    return 0


def _select(ar: Archive, args):
    ents = list(ar.toc)
    if getattr(args, "type", None):
        want = set(args.type)
        ents = [e for e in ents if e.tcode in want]
    if getattr(args, "filter", None):
        pat = args.filter.lower()
        ents = [e for e in ents if pat in e.name.lower()]
    if getattr(args, "sort", None) == "size":
        ents.sort(key=lambda e: -e.usize)
    elif getattr(args, "sort", None) == "name":
        ents.sort(key=lambda e: e.name.lower())
    if getattr(args, "top", 0):
        ents = ents[:args.top]
    return ents


def cmd_ls(ar: Archive, args) -> int:
    ents = _select(ar, args)
    print("%-14s%12s%12s  %-5s %s"
          % ("type", "raw", "stored", "zlib", "name"))
    for e in ents:
        print("%-14s%12d%12d  %-5s %s"
              % (e.tname, e.usize, e.csize, "zlib" if e.cmprs else "-", e.name))
    print("\n%d shown of %d entries, %s uncompressed in total"
          % (len(ents), len(ar.toc), human(sum(e.usize for e in ents))))
    return 0


def cmd_tree(ar: Archive, args) -> int:
    node = {}
    for e in ar.toc:
        cur = node
        parts = e.name.replace("\\", "/").split("/")
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur.setdefault("__files__", []).append(e)

    def count(d):
        n = len(d.get("__files__", []))
        s = sum(e.usize for e in d.get("__files__", []))
        for k, v in d.items():
            if k != "__files__":
                cn, cs = count(v)
                n += cn
                s += cs
        return n, s

    def walk(d, prefix="", depth=0):
        for name in sorted(k for k in d if k != "__files__"):
            sub = d[name]
            cnt, sz = count(sub)
            print("%s%s/   (%d files, %s)" % (prefix, name, cnt, human(sz)))
            if depth + 1 < args.depth:
                walk(sub, prefix + "    ", depth + 1)

    walk(node)
    files = node.get("__files__", [])
    if files:
        print("\n-- top-level files, largest first (up to %d) --" % min(20, len(files)))
        for e in sorted(files, key=lambda x: -x.usize)[:20]:
            print("  %-14s %12d  %s" % (e.tname, e.usize, e.name))
    return 0


def cmd_search(ar: Archive, args) -> int:
    needle = args.pattern
    low = needle.lower()
    hits = [e for e in ar.toc if low in e.name.lower()]
    print("== %d name matches ==" % len(hits))
    for e in hits[:200]:
        print("  %-14s%12d  %s" % (e.tname, e.usize, e.name))
    if args.content:
        print("\n== content matches for %r ==" % needle)
        cnt_hits = 0
        for e in ar.toc:
            try:
                blob = read_entry(ar, e, verify_len=False)
            except Exception:
                continue
            c = blob.lower().count(needle.encode().lower())
            if c:
                cnt_hits += 1
                print("  %6d hits  %s" % (c, e.name))
        print("  %d files matched" % cnt_hits)
    return 0


def cmd_cat(ar: Archive, args) -> int:
    exact = [e for e in ar.toc
             if e.name.replace("\\", "/") == args.name.replace("\\", "/")]
    matches = exact or [e for e in ar.toc if args.name.lower() in e.name.lower()]
    if not matches:
        print("no such entry", file=sys.stderr)
        return 1
    blob = read_entry(ar, matches[0], verify_len=False)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(blob)
        print("√ %s → %s  (%s)" % (matches[0].name, out, human(len(blob))))
    else:
        sys.stdout.buffer.write(blob)
        sys.stdout.buffer.flush()
    return 0


def cmd_extract(ar: Archive, args) -> int:
    out_root = Path(args.output)
    out_root.mkdir(parents=True, exist_ok=True)
    internal_dir = args.internal_dir
    if internal_dir == "_internal":
        for opt in ar.options:
            parts = opt.split()
            if len(parts) >= 2 and parts[0] == "pyi-contents-directory":
                internal_dir = parts[1]
                break
    ok = fail = 0
    manifest = []
    confined = []
    total = len(ar.toc)
    # The header is prepended during the single pass below rather than in a second one. The
    # second pass recomputed the path with the same expression, which meant any entry whose path
    # had been clamped during the first write was looked for at its *unclamped* location: the
    # file was written to one place, the header was applied to nothing, and the manifest still
    # claimed 16 bytes had been added -- so a later repack stripped 16 bytes that were never
    # there and corrupted the entry. One pass cannot disagree with itself.
    pyc_magic = pyc_magic_for(ar) if args.pyc else None
    magic_states = (TC_MODULE, TC_PACKAGE, TC_SOURCE)
    if args.pyc and pyc_magic is None:
        print("  ! no .pyc magic available for Python %s, skipping headers"
              % ar.pyver_str(), file=sys.stderr)

    for i, e in enumerate(ar.toc, 1):
        try:
            blob = read_entry(ar, e, verify_len=False)
        except Exception as ex:
            print("  !! %s: %s" % (e.name, ex), file=sys.stderr)
            fail += 1
            continue
        rel = layout_path(e, internal_dir) if args.layout else Path(flat_name(e))
        if args.pyc_suffix and e.tcode in (TC_MODULE, TC_PACKAGE, TC_SOURCE):
            if not rel.name.endswith(".pyc"):
                rel = rel.with_name(rel.name + ".pyc")
        # Remember how many .pyc header bytes were prepended, so the repacker can
        # strip exactly that many. Otherwise the marshalled code object inside the
        # archive is corrupted, and the repacked executable will not start.
        wrapped = 0
        if pyc_magic is not None and e.tcode in magic_states:
            blob = wrap_pyc(blob, pyc_magic)
            wrapped = PYC_HEADER_LEN
        # The name is untrusted, so the path is clamped before it is written, and the
        # repair is recorded: 'rel' keeps the original shape for an exact repack, while
        # 'safe_rel' is where the bytes actually go.
        safe_rel = confining(out_root, rel, e.name)
        if safe_rel.as_posix() != rel.as_posix() or not inside(out_root, safe_rel):
            confined.append((e.name, safe_rel.as_posix()))
        manifest.append({
            "name": e.name, "tcode": e.tcode, "cmprs": e.cmprs,
            "usize": e.usize, "rel": rel.as_posix(), "safe_rel": safe_rel.as_posix(),
            "confined": safe_rel.as_posix() != rel.as_posix(),
            "pyc_header": wrapped,
        })
        dest = out_root / safe_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(blob)
        ok += 1
        if args.verbose or i % 200 == 0 or i == total:
            print("  [%d/%d] %s" % (i, total, dest.relative_to(out_root)))
    if args.pyc and pyc_magic is not None:
        fixed = sum(1 for m in manifest if m["pyc_header"])
        if fixed:
            print("added a valid .pyc header to %d modules (Python %s)"
                  % (fixed, ar.pyver_str()))
    with open(ar.path, "rb") as f:
        stub = f.read(ar.base_pos)
    stub_path = out_root / (ar.path.stem + ".stub")
    stub_path.write_bytes(stub)
    meta = {
        "source": str(ar.path),
        "python": ar.pyver_str(),
        "pylib": ar.pylib,
        "pkg_len": ar.pkg_len,
        "toc_off": ar.toc_off,
        "options": ar.options,
        "internal_dir": internal_dir,
        "stub": stub_path.name,
        # Whatever the parser found structurally wrong is carried here rather than thrown, so a
        # damaged archive still produces a usable description of itself.
        "integrity": list(getattr(ar, "integrity", []) or []),
        "entries": manifest,
    }
    (out_root / "_archive_manifest.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nextracted %d files (%d failed) -> %s" % (ok, fail, out_root))
    if confined:
        # Said out loud, not folded away: an archive that tried to address files outside the
        # output directory is a finding about the archive, not an inconvenience.
        print("  ! %d entry name(s) tried to leave the output directory and were confined:"
              % len(confined))
        for original, placed in confined[:10]:
            print("      %r -> %s" % (original, placed))
    if meta["integrity"]:
        print("  ! %d structural problem(s) recorded in the manifest:"
              % len(meta["integrity"]))
        for note in meta["integrity"][:10]:
            print("      %s" % note)
    print("bootloader stub saved as %s (%s)" % (stub_path, human(len(stub))))
    print("manifest written to _archive_manifest.json (used by 'build')")
    return 0 if fail == 0 else 2


def cmd_verify(ar: Archive, args) -> int:
    bad = 0
    for i, e in enumerate(ar.toc, 1):
        try:
            read_entry(ar, e, verify_len=True)
        except Exception as ex:
            bad += 1
            print("  ✗ %s: %s" % (e.name, ex), file=sys.stderr)
        if i % 500 == 0:
            print("  ... verified %d/%d" % (i, len(ar.toc)))
    if bad:
        print("%d entries failed" % bad)
        return 2
    print("all %d entries decompressed and their lengths match" % len(ar.toc))
    return 0


# --------------------------------------------------------------------------- #
# PYZ
# --------------------------------------------------------------------------- #

def cmd_pyz(target: str, args) -> int:
    p = Path(target)
    if not p.is_file():
        print("not found: %s" % p, file=sys.stderr)
        return 1
    blob = p.read_bytes()
    if not blob.startswith(PYZ_MAGIC):
        ar = find_archive(target)
        zs = [e for e in ar.toc if e.tcode == TC_PYZ]
        if not zs:
            print("this archive contains no PYZ entry", file=sys.stderr)
            return 1
        blob = read_entry(ar, zs[0], verify_len=False)
        print("pulled %s out of the archive (%s)" % (zs[0].name, human(len(blob))))

    pyc_magic = blob[4:8]
    toc_off = struct.unpack_from("!i", blob, 8)[0]
    try:
        toc = marshal.loads(blob[toc_off:])
    except Exception as ex:
        print("could not read the PYZ table of contents (%s)" % ex, file=sys.stderr)
        return 1
    print("PYZ holds %d module records, TOC @%d, bytecode magic=%s"
          % (len(toc), toc_off, pyc_magic.hex()))

    out = Path(args.output) if args.output else p.parent / (p.stem + "_extracted")
    out.mkdir(parents=True, exist_ok=True)
    ok = nspkg = bad = 0
    confined = []
    names = []
    taken: dict = {}          # relative output path -> module name that claimed it
    collided: list = []
    for item in toc:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            continue
        name, meta = item
        if not isinstance(meta, (tuple, list)) or len(meta) != 3:
            continue
        # PYZ item types: 0=module, 1=package (__init__), 2=legacy data (unused),
        #                 3=implicit namespace package (no code object).
        # Type 0 is an ordinary module and must not be skipped: treating it as a
        # namespace package reduces the output to package __init__ files only.
        typ, off, ln = meta
        names.append(name)
        if not ln or typ == 3:
            nspkg += 1
            continue
        try:
            code = zlib.decompress(blob[off:off + ln])
        except zlib.error:
            bad += 1
            continue
        rel = name.replace(".", "/") + ".pyc"
        # Distinct dotted modules can map onto the same file path when a module and
        # a package share a name (e.g. 'utils' as a module and 'utils.sub' as a
        # package). Never let one silently overwrite the other: fall back to a
        # flattened name and report it.
        if rel in taken and taken[rel] != name:
            flat = name.replace(".", "_") + ".pyc"
            collided.append((name, taken[rel], flat))
            rel = flat
        taken[rel] = name
        # The module name comes from the PYZ table of contents, which comes from an archive
        # someone else built, so it is untrusted input in exactly the way an entry name is.
        # Without this, the same escape that the CArchive path was fixed for stayed open here:
        # a module named 'C:/abs/x' produced the destination 'C:\abs\x.pyc', and a name of
        # '/abs/x' landed outside the output directory entirely. One boundary, half repaired.
        safe_rel = confining(out, Path(rel), name)
        if safe_rel.as_posix() != rel:
            confined.append((name, safe_rel.as_posix()))
        if not inside(out, safe_rel):
            bad += 1
            continue
        dest = out / safe_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        # The PYZ header already carries the bytecode magic, so a valid .pyc can be written
        # without guessing. Default to that and keep the raw form behind --bare, so both
        # "feed it to a decompiler" and "give me the stored bytes" are one flag apart.
        if args.bare or not pyc_magic:
            dest.write_bytes(code)
        else:
            dest.write_bytes(wrap_pyc(code, pyc_magic))
        ok += 1
    print("extracted bytecode for %d modules -> %s" % (ok, out))
    print("  %d namespace packages (no code object, expected), %d failed"
          % (nspkg, bad))
    if confined:
        # A module name that tried to leave the output directory is a fact about the archive,
        # not an inconvenience: say which and where it went instead.
        print("  ! %d module name(s) tried to leave the output directory and were confined:"
              % len(confined))
        for original, placed in confined[:10]:
            print("      %r -> %s" % (original, placed))
    if collided:
        print("  %d module path collision(s), written with a flattened name instead:"
              % len(collided))
        for name, other, flat in collided[:10]:
            print("    %s (would have overwritten %s) -> %s" % (name, other, flat))
    if args.bare or not pyc_magic:
        print("  note: raw marshalled code objects, without a .pyc header (--bare)")
    else:
        print("  each file carries a valid .pyc header derived from the PYZ bytecode magic;")
        print("  use --bare to keep the stored bytes untouched")
    if args.list:
        for name in sorted(names)[:args.list]:
            print("   ", name)
    return 0


# --------------------------------------------------------------------------- #
# Repacking
# --------------------------------------------------------------------------- #

def _guess_pylib(root: Path) -> str:
    for cand in root.rglob("python3*.dll"):
        return cand.name
    return "python312.dll"


def _guess_pyver(pylib: str) -> int:
    digits = "".join(c for c in pylib if c.isdigit())
    if len(digits) >= 3:
        return int(digits[:3])
    if len(digits) == 2:
        return int(digits + "0")
    return 312


def cmd_build(root: str, args) -> int:
    root = Path(root)
    stubs = sorted(root.glob("*.stub"))
    if not stubs:
        print("no .stub file in this directory ('extract' writes one)", file=sys.stderr)
        return 1
    stub = stubs[0].read_bytes()

    files = []
    manifest_path = root / "_archive_manifest.json"
    if manifest_path.is_file():
        meta = json.loads(manifest_path.read_text(encoding="utf-8"))
        missing = 0
        internal = meta.get("internal_dir") or ""
        for item in meta.get("entries", []):   # keep the original order; it matters
            # Where the bytes are, not where they would have gone unclamped. Extraction writes to
            # safe_rel and records it; reading `rel` means any entry whose path had to be clamped
            # is simply not found, and the repack silently drops it -- which is how this went
            # unnoticed: every ordinary archive has rel == safe_rel, so only a confined entry
            # exposed it. `name` still carries the original for the table of contents.
            on_disk = item.get("safe_rel") or item["rel"]
            fp = root / on_disk
            if not fp.is_file() and item.get("safe_rel") and item["safe_rel"] != item["rel"]:
                # Manifests written before safe_rel existed, or files moved by hand.
                fp = root / item["rel"]
            if not fp.is_file():
                missing += 1
                continue
            blob = fp.read_bytes()
            cut = int(item.get("pyc_header") or 0)
            if cut:
                blob = blob[cut:]
            # Stored names never carry the contents-directory prefix, because the
            # OPTION entry already tells the runtime where to put them. Keep the
            # prefix and the files land one directory too deep at runtime.
            name = item["name"]
            if internal:
                for sep in ("/", "\\"):
                    if name.startswith(internal + sep):
                        name = name[len(internal) + 1:]
                        break
            files.append((name, blob, item["tcode"], bool(item["cmprs"])))
        if missing:
            print("  ! %d manifest entries are missing from the directory, skipped"
                  % missing, file=sys.stderr)
        # OPTION entries carry their value in the name, with a zero-length payload.
        for opt in meta.get("options", []):
            files.append((opt, b"", TC_OPTION, False))
        print("repacking %d entries from _archive_manifest.json "
              "(original names, types and flags preserved)" % len(files))
    else:
        def guess_tcode(rel):
            base_name = rel.rsplit("/", 1)[-1]
            if "/" in rel or base_name.endswith((".pyd", ".dll")):
                return TC_DATA
            return TC_BINARY

        for fp in sorted(root.rglob("*")):
            if not fp.is_file() or fp.suffix == ".stub":
                continue
            if fp.name == "_archive_manifest.json":
                continue
            rel = fp.relative_to(root).as_posix()
            if rel.startswith("_internal/"):
                rel = rel[len("_internal/"):]
            files.append((rel, fp.read_bytes(), guess_tcode(rel), None))
        print("no manifest; repacking %d entries inferred from the directory"
              % len(files))

    payload = io.BytesIO()
    toc = []
    for name, blob, tcode, force_cmprs in files:
        comp = zlib.compress(blob, 9)
        if force_cmprs is None:
            cmprs = 1 if len(comp) < len(blob) else 0
        else:
            cmprs = 1 if force_cmprs else 0
        body = comp if cmprs else blob
        off = payload.tell()          # relative to the payload; the PKG header is added later
        payload.write(body)
        toc.append((name, off, len(body), len(blob), cmprs, tcode))

    toc_raw = io.BytesIO()
    for name, off, csize, usize, cmprs, tcode in toc:
        off += PKG_HEADER_LEN
        nb = name.encode("utf-8")
        # entry_length = 18 + len(name) + 1, padded up to a multiple of 16. The name
        # field therefore occupies entry_length - 18 bytes, including NUL and padding.
        entry_len = TOC_ENTRY_LEN + len(nb) + 1
        if entry_len % 16:
            entry_len += 16 - (entry_len % 16)
        name_field = entry_len - TOC_ENTRY_LEN
        toc_raw.write(struct.pack(TOC_ENTRY_FORMAT, entry_len, off,
                                  csize, usize, cmprs, tcode.encode()))
        toc_raw.write(nb.ljust(name_field, NULB))
    toc_blob = toc_raw.getvalue()

    # Cookie semantics, as read by the bootloader:
    #   pkg_start   = cookie_end - pkg_length
    #   payload at  = pkg_start + entry.offset
    #   TOC at      = pkg_start + toc_offset
    # The observed PKG body is [88-byte header][payload][TOC]. Both entry.offset and
    # toc_offset are measured from the PKG start, so the 88-byte header is included.
    header = bytes(PKG_HEADER_LEN)   # zero-filled PKG header
    toc_off = PKG_HEADER_LEN + len(payload.getvalue())
    pkg_body = header + payload.getvalue() + toc_blob
    # pkg_len includes the 88-byte cookie itself; leave it out and reading the file
    # back lands exactly 88 bytes away from where it should.
    pkg_len = len(pkg_body) + COOKIE_LEN
    pylib = args.pylib or _guess_pylib(root)
    pyver = args.pyver or _guess_pyver(pylib)
    cookie = struct.pack(COOKIE_FORMAT, MAGIC, pkg_len, toc_off,
                         len(toc_blob), pyver,
                         pylib.encode("latin1")[:64].ljust(64, NULB))

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as f:
        f.write(stub)
        f.write(pkg_body)
        f.write(cookie)
    print("repacked -> %s  (%s)" % (out, human(out.stat().st_size)))
    print("  %d entries, pylib=%s, pyver=%d, toc_len=%d"
          % (len(files), pylib, pyver, len(toc_blob)))
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
# Library API
#
# Everything above is a command-line program: the cmd_* functions print prose for a person and
# return an exit code. That is the right shape for a CLI and the wrong shape for a caller, and
# a caller that wraps it -- capturing stdout and parsing the text back -- is coupled to the
# wording of a report. Reword a line and the caller breaks.
#
# So these functions are built on the pure layer instead (find_archive, read_entry, wrap_pyc,
# confining), which has always returned data rather than prose. They are not a second
# implementation of the format: the parsing, decompression and path handling they use are the
# same functions the commands use.
#
# They never execute the target. Nothing in this file creates a process.
# --------------------------------------------------------------------------- #

__all__ = [
    # what a caller should reach for
    "inspect_archive", "list_entries", "read_file", "verify_archive", "extract",
    "PyInstallerError",
    # the pieces a caller building on top may legitimately need
    "find_archive", "read_entry", "wrap_pyc", "Archive", "Entry",
]


class PyInstallerError(Exception):
    """Raised when an archive cannot be read or an operation cannot be completed.

    The commands convert this kind of condition into a message and an exit code. A caller
    should get an exception it can catch, with the same explanation attached.
    """

    def __init__(self, message: str, *, path=None):
        super().__init__(message)
        self.path = path


def inspect_archive(target) -> dict:
    """Describe an archive without extracting anything.

    Returns a plain dict -- the same facts `info` prints, minus the formatting.
    """
    try:
        ar = find_archive(Path(target))
    except SystemExit as exc:
        raise PyInstallerError(str(exc), path=target) from exc
    return {
        "path": str(ar.path),
        "file_size": ar.file_size,
        "python_version": ar.pyver_str() if hasattr(ar, "pyver_str") else str(ar.pyver),
        "pylib": ar.pylib,
        "stub_size": ar.stub_size,
        "pkg_len": ar.pkg_len,
        "base_pos": ar.base_pos,
        "cookie_pos": ar.cookie_pos,
        "toc_off": ar.toc_off,
        "toc_size": ar.toc_size,
        "entry_count": len(ar.toc),
        "options": list(ar.options),
        "integrity": list(ar.integrity),
        "total_uncompressed": sum(e.usize for e in ar.toc),
    }


def list_entries(target) -> list:
    """The table of contents, as dicts. Order is the archive's own, which matters for repacking."""
    try:
        ar = find_archive(Path(target))
    except SystemExit as exc:
        raise PyInstallerError(str(exc), path=target) from exc
    return [{
        "name": e.name,
        "tcode": e.tcode,
        "compressed": bool(e.cmprs),
        "stored_size": e.stored_size,
        "size": e.usize,
        "offset": e.offset,
    } for e in ar.toc]


def verify_archive(target, *, read=True) -> dict:
    """Check the table of contents against the file.

    `read=True` decompresses every entry and compares lengths, which is what `verify` does;
    `read=False` only checks the structure, which is fast and catches a truncated file.
    """
    try:
        ar = find_archive(Path(target))
    except SystemExit as exc:
        raise PyInstallerError(str(exc), path=target) from exc

    problems = []
    checked = 0
    if read:
        for e in ar.toc:
            try:
                blob = read_entry(ar, e)
            except Exception as exc:
                problems.append({"name": e.name, "problem": str(exc)})
                continue
            if len(blob) != e.usize:
                problems.append({"name": e.name,
                                 "problem": "decompressed to %d bytes, declared %d"
                                            % (len(blob), e.usize)})
            else:
                checked += 1
    return {
        "ok": not problems and not ar.integrity,
        "path": str(ar.path),
        "entries": len(ar.toc),
        "decompressed": checked,
        "problems": problems,
        "integrity": list(ar.integrity),
    }


def extract(target, out_dir, *, pyc=True, pyc_suffix=True, layout=False,
            internal_dir="_internal") -> dict:
    """Unpack an archive into a directory tree with a manifest, and report what happened.

    Writes the same layout the `extract` command writes, including `_archive_manifest.json`,
    because that manifest is what makes the result repackable. Returns a summary instead of
    printing one.
    """
    try:
        ar = find_archive(Path(target))
    except SystemExit as exc:
        raise PyInstallerError(str(exc), path=target) from exc

    out_root = Path(out_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    if layout:
        found = ""
        for opt in ar.options:
            parts = opt.split()
            if len(parts) >= 2 and parts[0] == "pyi-contents-directory":
                found = parts[1]
                break
        internal_dir = found or internal_dir

    magic = pyc_magic_for(ar) if pyc else None
    magic_states = (TC_MODULE, TC_PACKAGE, TC_SOURCE)
    written, failed, confined = [], [], []

    for e in ar.toc:
        try:
            blob = read_entry(ar, e, verify_len=False)
        except Exception as exc:
            failed.append({"name": e.name, "problem": str(exc)})
            continue

        rel = layout_path(e, internal_dir) if layout else Path(flat_name(e))
        if pyc_suffix and e.tcode in magic_states and not rel.name.endswith(".pyc"):
            rel = rel.with_name(rel.name + ".pyc")
        header = 0
        if magic is not None and e.tcode in magic_states:
            blob = wrap_pyc(blob, magic)
            header = PYC_HEADER_LEN

        safe_rel = confining(out_root, rel, e.name)
        if safe_rel.as_posix() != rel.as_posix():
            confined.append({"name": e.name, "placed_at": safe_rel.as_posix()})
        dest = out_root / safe_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(blob)
        written.append({"name": e.name, "path": safe_rel.as_posix(), "size": len(blob),
                        "pyc_header": header,
                        # the entry this came from, kept alongside rather than re-paired later
                        "tcode": e.tcode, "cmprs": e.cmprs, "usize": e.usize})

    # The manifest is built from what was actually written, in the archive's order, so a
    # repack cannot disagree with the extraction -- the class of bug that made a second pass
    # over the paths a bad idea.
    manifest = {
        "source": str(ar.path),
        "python": ar.pyver_str() if hasattr(ar, "pyver_str") else str(ar.pyver),
        "pylib": ar.pylib,
        "pkg_len": ar.pkg_len,
        "toc_off": ar.toc_off,
        "options": list(ar.options),
        "internal_dir": internal_dir,
        "stub": "",
        "integrity": list(ar.integrity),
        "entries": [{"name": w["name"], "tcode": w["tcode"], "cmprs": w["cmprs"],
                     "usize": w["usize"], "rel": w["path"], "safe_rel": w["path"],
                     "confined": False, "pyc_header": w["pyc_header"]}
                    for w in written],
    }

    with open(ar.path, "rb") as fh:
        stub = fh.read(ar.base_pos)
    stub_path = out_root / (ar.path.stem + ".stub")
    stub_path.write_bytes(stub)
    manifest["stub"] = stub_path.name
    (out_root / "_archive_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    return {
        "ok": not failed,
        "out_dir": str(out_root),
        "written": len(written),
        "failed": failed,
        "confined": confined,
        "stub": stub_path.name,
        "manifest": "_archive_manifest.json",
        "executed": False,
    }


def read_file(target, name: str) -> bytes:
    """The bytes of one entry, by exact name. Raises KeyError if there is no such entry."""
    try:
        ar = find_archive(Path(target))
    except SystemExit as exc:
        raise PyInstallerError(str(exc), path=target) from exc
    for e in ar.toc:
        if e.name == name:
            return read_entry(ar, e)
    raise KeyError("no entry named %r in %s" % (name, ar.path))


# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# The voice
#
# Lilith speaks in the interactive prose and nowhere else. See PERSONA.md for the character; the
# two rules that decided the shape of this code are:
#
#   * she never claims to have done something she did not do -- the tool does not execute anything,
#     and she is proud of that, so "I looked" must never become "I ran it"
#   * she is silent in everything a program reads, because a persona in JSON is a parsing bug
#
# The register is a rendering choice, not a second source of truth: every line below says exactly
# what the plain line says.
# --------------------------------------------------------------------------- #

_PLAIN = [False]
_NSFW = [False]


def set_plain(value: bool) -> None:
    """Turn Lilith off (or back on). Read once at startup from --plain / NANODESU_PLAIN."""
    _PLAIN[0] = bool(value)


def is_plain() -> bool:
    return _PLAIN[0]


def set_nsfw(value: bool) -> None:
    """Enable the adult register. Off unless explicitly asked for; `--plain` wins if both are set.

    The asymmetry is deliberate: `--plain` is a promise that the output is impersonal, and a persona
    the user cannot silence is one that has been forced on them, so silence outranks heat.
    """
    _NSFW[0] = bool(value)


def is_nsfw() -> bool:
    return _NSFW[0] and not _PLAIN[0]


# name -> (Lilith, plain). Same fact, two registers.
#
# The epilog is not in here: it is composed differently for each register (the plain one keeps the
# module docstring alone), so it lives in `epilog_for` rather than as a pair of strings. Putting it
# here as a bare string made `say()` unpack three values into two -- which is exactly the kind of
# thing this table's shape should make impossible, so the shape is now enforced by a test.
PROSE = {
    "nothing_to_do": (
        "\u2026\u304a\u4ed5\u4e8b\u3092\u304f\u3060\u3055\u3044\u3002"
        "\u4f55\u3082\u6307\u793a\u3055\u308c\u3066\u3044\u306a\u3044\u306e\u3067\u3059\u3002",
        "no command given",
    ),
    "unpacking": (
        "\u5916\u5957\u3092\u8131\u304c\u305b\u308b\u306e\u3067\u3059",
        "unpacking the archive",
    ),
    "repacking": (
        "\u7e70\u308a\u76f4\u3059\u306e\u3067\u3059",
        "repacking the archive",
    ),
    "verified": (
        "\u305f\u3057\u304b\u306b\u898b\u305f\u306e\u3067\u3059",
        "every entry verified",
    ),
    "nothing_found": (
        "\u2026\u2026\u666e\u901a\u306a\u306e\u3067\u3059\u3002"
        "\u3064\u307e\u3089\u306a\u3044\u306e\u3067\u3059\u3002",
        "nothing unusual",
    ),
    "cannot_tell": (
        "\u5206\u304b\u3089\u306a\u3044\u306e\u3067\u3059\u3002"
        "\u5206\u304b\u3089\u306a\u3044\u3068\u8a00\u3046\u306e\u304c"
        "\u8aa0\u5b9e\u306a\u306e\u3067\u3059\u3002",
        "cannot determine",
    ),
    # Plain fallbacks for the adult register's keys. They must exist here: `say` returns the key
    # *itself* when neither table has it, so a code path asking for an adult line while the register
    # is off would print the literal word "handed_over" into the output. A register falling back to
    # something sensible is a requirement, not a nicety.
    "probing": (
        "\u305d\u3053\u3092\u898b\u308b\u306e\u3067\u3059",
        "probing that field",
    ),
    "pressed": (
        "\u3053\u3053\u304b\u3089\u52d5\u304b\u306a\u3044\u306e\u3067\u3059",
        "held in place",
    ),
    "dissolved": (
        "\u3082\u3046\u62b5\u6297\u3057\u306a\u3044\u306e\u3067\u3059",
        "no more resistance",
    ),
    "handed_over": (
        "\u4e2d\u8eab\u306f\u3053\u308c\u3067\u5168\u90e8\u306a\u306e\u3067\u3059",
        "contents complete",
    ),
}


# The adult register: same facts, and the subject of the analysis is something she plays with.
# See PERSONA.nsfw.md for the design -- briefly, the target is always the SAMPLE and never the
# person reading, which is what keeps this a report rather than something aimed at the reader.
# Nothing here is reachable without --nsfw or NANODESU_NSFW=1.
NSFW_PROSE = {
    # probe -> press -> dissolve -> hand over: the same four beats as explore, confirm, unpack,
    # report. The point is not decoration; it is that the analysis already has these steps.
    "probing": (
        "\u305d\u3053\u304c\u5f31\u3044\u306e\u3067\u3059\u304b",
        "probing",
    ),
    "pressed": (
        "\u631f\u3093\u3067\u96e2\u3055\u306a\u3044\u306e\u3067\u3059",
        "held in place",
    ),
    "dissolved": (
        "\u3082\u3046\u7d42\u308f\u308a\u306a\u306e\u3067\u3059",
        "no more resistance",
    ),
    "handed_over": (
        "\u3054\u4e3b\u4eba\u69d8\u3001\u4e2d\u8eab\u306f\u3053\u308c\u3067"
        "\u5168\u90e8\u306a\u306e\u3067\u3059",
        "contents complete",
    ),
}


def say(key: str) -> str:
    """The line for `key` in whichever register is active.

    Every entry is a (Lilith, plain) pair; a bare string here used to reach the unpack and raise,
    which is why a test now checks the shape of every entry rather than trusting the table.
    """
    if _PLAIN[0]:
        entry = PROSE.get(key) or NSFW_PROSE.get(key)
        return entry[1] if entry else key
    if is_nsfw():
        entry = NSFW_PROSE.get(key) or PROSE.get(key)
        return entry[0] if entry else key
    entry = PROSE.get(key)
    if entry is None:
        return key
    return entry[0]


def epilog_for(doc: str) -> str:
    """The help epilog: the module docstring, plus Lilith's closing lines when she is on.

    Kept out of PROSE because the two registers do not differ by one line here -- the plain one is
    the docstring alone, with nothing appended.
    """
    if _PLAIN[0]:
        return doc
    return doc + "\n" + (
        "  \u79c1\u306f\u3001\u8d77\u3053\u3055\u306a\u3044\u306e\u3067\u3059\u3002\n"
        "  \u5916\u5957\u3092\u8131\u304c\u305b\u3066\u3001\u4e2d\u8eab\u3092\u898b\u308b"
        "\u3060\u3051\u306a\u306e\u3067\u3059 \u2014 \u565b\u307f\u3064\u304f\u5fc5\u8981"
        "\u306f\u3001\u306a\u3044\u306e\u3067\u3059\u3002\n"
        "\n"
        "  (--plain \u3067\u79c1\u306f\u9ed9\u308a\u307e\u3059)"
    )


def build_parser():
    ap = argparse.ArgumentParser(
        prog="nanodesu",
        description="Unpack and repack PyInstaller onefile executables (stdlib only)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        # `__doc__` is the module docstring, which is the plain-text description of what this is.
        # It stays in the epilog for --plain and for anyone reading the source; the voice replaces
        # it only when the voice is on.
        epilog=epilog_for(__doc__))
    ap.add_argument("--boundary", action="store_true",
                    help="print what this tool does not do, then exit. It travels with every "
                         "result too; this just lets you read it on purpose")
    ap.add_argument("--plain", action="store_true",
                    help="speak plainly: no persona in the prose (also NANODESU_PLAIN=1)")
    ap.add_argument("--nsfw", action="store_true",
                    help="allow the adult register in the prose; off unless asked for "
                         "(also NANODESU_NSFW=1). --plain overrides it")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("target", help="path to the executable or PYZ archive")

    p = sub.add_parser("info", help="summarise the archive"); common(p)

    p = sub.add_parser("ls", help="list the table of contents"); common(p)
    p.add_argument("--filter", help="keep entries whose name contains this string")
    p.add_argument("--type", nargs="*", help="keep these type codes, e.g. b x m M z s d")
    p.add_argument("--sort", choices=["size", "name"], default="size")
    p.add_argument("--top", type=int, default=0, help="show only the first N entries")

    p = sub.add_parser("tree", help="directory tree with sizes"); common(p)
    p.add_argument("--depth", type=int, default=2)

    p = sub.add_parser("search", help="search names, optionally contents"); common(p)
    p.add_argument("pattern")
    p.add_argument("--content", action="store_true",
                   help="also search entry contents (slower)")

    p = sub.add_parser("cat", help="write one entry to stdout"); common(p)
    p.add_argument("name", nargs="?", help="entry name or a substring of it")
    p.add_argument("-o", "--output")

    p = sub.add_parser("extract", help="unpack the whole archive"); common(p)
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--flat", dest="layout", action="store_false", default=True,
                   help="flat output instead of the runtime directory layout")
    p.add_argument("--internal-dir", default="_internal")
    # Only the negative form is offered. The argument used to be `--pyc-suffix` with
    # `default=True`, which made it a flag that could be passed but changed nothing -- a switch
    # that cannot switch. Named the other way round it reads honestly, and the default is stated.
    p.add_argument("--no-pyc-suffix", dest="pyc_suffix", action="store_false",
                   help="do not append .pyc to module bytecode (default appends it)")
    p.add_argument("--pyc", action="store_true",
                   help="prepend a valid .pyc header to module bytecode")
    p.add_argument("-v", "--verbose", action="store_true")

    p = sub.add_parser("verify", help="decompress every entry and check lengths"); common(p)

    p = sub.add_parser("pyz", help="unpack a PYZ archive into bytecode"); common(p)
    p.add_argument("-o", "--output")
    p.add_argument("--list", type=int, default=0,
                   help="also list the first N module names")
    p.add_argument("--bare", action="store_true",
                   help="write the stored bytes untouched instead of adding a .pyc header")

    p = sub.add_parser("build", help="repack an extracted directory"); common(p)
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--pylib")
    p.add_argument("--pyver", type=int)

    p = sub.add_parser("neutralize",
                       help="build a defanged VARIANT: replace modules with inert stubs. "
                            "The original is never touched and the result is never called safe")
    common(p)
    p.add_argument("-o", "--out", help="working directory (default: alongside the archive)")
    p.add_argument("--modules", nargs="*", metavar="NAME",
                   help="module names to replace (default: every .pyc found)")
    p.add_argument("--in-pyz", action="store_true",
                   help="expand the PYZ first and only replace modules inside it")
    p.add_argument("--dry-run", action="store_true",
                   help="report what would be replaced without writing anything")
    p.add_argument("--force", action="store_true", help="reuse an existing working directory")
    return ap


def cmd_neutralize(target: str, args) -> int:
    """Build a defanged variant. Never touches the original; records every change.

    Deliberately verbose about what it is not: the output is a variant, the sample is unchanged, and
    nothing here certifies the result as safe. See neutralize.py for why that wording is load-bearing
    rather than polite.
    """
    import neutralize as nz

    archive = Path(target)
    if not archive.is_file():
        print("not found: %s" % archive, file=sys.stderr)
        return 1

    # The original is only ever read. Checked here, once, rather than trusted to every call below.
    before = archive.stat()
    ar = find_archive(archive)

    work = Path(args.out) if args.out else archive.parent / (archive.stem + "_neutralize")
    tree = work / "tree"
    if work.exists() and not args.force:
        print("refusing to reuse %s (pass --force)" % work, file=sys.stderr)
        return 1
    work.mkdir(parents=True, exist_ok=True)

    print("extracting (the original is only read) -> %s" % tree)
    # The argument objects are built explicitly rather than faked with an empty class: each command
    # reads a different set of attribute names, and a missing one fails at the point of use with an
    # AttributeError rather than at the point of construction.
    rc = cmd_extract(ar, SimpleNamespace(output=str(tree), pyc=True, pyc_suffix=True,
                                         verbose=False, layout=None, internal_dir=None))
    if rc != 0:
        return rc

    # The PYZ is where a hostile module usually lives, so expand it when asked.
    if args.in_pyz:
        pyzs = sorted(tree.glob("*.pyz"))
        if not pyzs:
            print("no PYZ entry in this archive, so there is nothing to expand", file=sys.stderr)
            return 1
        rc = cmd_pyz(str(pyzs[0]), SimpleNamespace(output=str(tree / "_pyz_modules"),
                                                   list=0, bare=False))
        if rc != 0:
            return rc

    found = nz.find_modules(tree)
    wanted = []
    for item in found:
        if args.modules:
            if item["rel"] not in args.modules and Path(item["rel"]).stem not in args.modules:
                continue
        if args.in_pyz and not item["in_pyz"]:
            continue
        wanted.append(item)

    if not wanted:
        print("no module matched %s -- nothing was replaced"
              % (args.modules or "(all)"), file=sys.stderr)
        print("available (first 20):", file=sys.stderr)
        for item in found[:20]:
            print("   %s%s" % (item["rel"], "   [PYZ]" if item["in_pyz"] else ""), file=sys.stderr)
        return 1

    record = nz.neutralize(archive, tree, modules=wanted,
                           python_version=ar.pyver_str(),
                           dry_run=args.dry_run)
    if record.get("refused"):
        print("refused: %s" % record["refused"], file=sys.stderr)
        return 1

    if not args.dry_run:
        nz.write_record(record, tree)
        print("repacking the variant (the original stays untouched)")
        variant = work / (archive.stem + "_defanged.exe")
        rc = cmd_build(str(tree), SimpleNamespace(output=str(variant),
                                                  pylib=None, pyver=None))
        if rc != 0:
            return rc
        record["variant"] = str(variant)
        nz.write_record(record, tree)

    print()
    print("replaced %d module(s)%s" % (len(record["replaced"]), " (dry run)" if args.dry_run else ""))
    for r in record["replaced"][:12]:
        # PYZ entries are named by dotted module name; top-level ones by path. Both appear here, and
        # assuming the path key crashed the reporter after the work had already succeeded.
        label = r.get("rel") or ("%s  [PYZ]" % r.get("name", "?"))
        print("   %-46s %6d -> %6d bytes" % (label[:46], r.get("before_bytes", 0),
                                              r.get("after_bytes", 0)))
    if len(record["replaced"]) > 12:
        print("   ... and %d more" % (len(record["replaced"]) - 12))
    print()
    if not args.dry_run:
        print("variant : %s" % record.get("variant"))
    print("record  : %s" % (tree / "_neutralize_record.json"))
    print()
    print("This output is a VARIANT, not a repaired file. It is not clean, not safe and not fixed.")
    print("Its hash changed, so it will not match threat intelligence or AV signatures any more --")
    print("it will scan clean because it is UNKNOWN, not because it is good.")
    print("Stubbing a module proves that module no longer runs; it proves nothing about the rest.")
    after = archive.stat()
    if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
        print("WARNING: the original archive changed, which should be impossible", file=sys.stderr)
        return 1
    return 0


def main(argv=None):
    # The register has to be decided before the parser is built, because the parser's own help
    # text is one of the places the voice appears. So the flag is scanned here rather than read
    # from the parsed result.
    raw = list(sys.argv[1:] if argv is None else argv)
    def _on(name):
        return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")

    set_plain("--plain" in raw or _on("NANODESU_PLAIN"))
    # Off unless explicitly asked for. `set_nsfw` itself defers to plain, so passing both silences
    # rather than heating.
    set_nsfw("--nsfw" in raw or _on("NANODESU_NSFW"))
    raw = [a for a in raw if a not in ("--plain", "--nsfw")]

    # The voice is not ASCII, and on Windows `sys.stdout` is bound to the console code page -- cp1252
    # on a stock CI runner -- so printing it raised UnicodeEncodeError and the built executable failed
    # its own sanity check. Configured here rather than at import time so that `--plain`, `--json` and
    # every machine-readable path keeps the stream it would otherwise have had: plain output is what a
    # script parses, and it must not depend on a persona decision.
    #
    # `errors="replace"` rather than `strict`: a character the terminal cannot render must never be
    # the thing that stops an analysis.
    if not _PLAIN[0]:                      # the persona may print; for machines it does not
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError, OSError):
                # A sealed or replaced stream, or an interpreter too old for reconfigure. Printing
                # ASCII still works, and a pre-3.7 interpreter is not worth failing a scan over.
                pass

    ap = build_parser()
    args = ap.parse_args(raw)

    if args.boundary:
        print(boundary.format_boundary())
        return 0
    if not args.cmd:
        ap.print_help()
        if not is_plain():
            print("\n" + say("nothing_to_do"))
        return 0
    if args.cmd == "pyz":
        return cmd_pyz(args.target, args)
    if args.cmd == "neutralize":
        return cmd_neutralize(args.target, args)
    if args.cmd == "build":
        return cmd_build(args.target, args)
    ar = find_archive(args.target)
    return {
        "info": cmd_info, "ls": cmd_ls, "tree": cmd_tree, "search": cmd_search,
        "cat": cmd_cat, "extract": cmd_extract, "verify": cmd_verify,
    }[args.cmd](ar, args)


if __name__ == "__main__":
    sys.exit(main())