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

import argparse
import io
import json
import marshal
import os
import shutil
import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

MAGIC = b"MEI\x0c\x0b\x0a\x0b\x0e"
COOKIE_FORMAT = "!8sIIII64s"
COOKIE_LEN = struct.calcsize(COOKIE_FORMAT)          # 88
TOC_ENTRY_FORMAT = "!IIIIBc"
TOC_ENTRY_LEN = struct.calcsize(TOC_ENTRY_FORMAT)    # 18
PKG_HEADER_LEN = 88          # PKG header size (one cookie length)
PYC_HEADER_LEN = 16          # 3.7+ .pyc header: magic(4)+flags(4)+mtime(4)+size(4)
PYZ_MAGIC = b"PYZ\0"
NULB = b"\x00"

TC_BINARY = "b"
TC_BINARY_DEP = "x"
TC_BINARY_EMBED = "z"
TC_DATA = "d"
TC_SOURCE = "s"
TC_MODULE = "m"
TC_MODULE_DEP = "M"
TC_PYZ = "z"             # PYZ archive - LOWERCASE z; uppercase Z is a plain zipfile
TC_ZIPFILE = "Z"
TC_RUNTIME = "R"
TC_OPTION = "o"
# NOTE: a PYZ archive uses LOWERCASE 'z'; uppercase 'Z' is a plain zipfile entry.
# Missing the lowercase 'z' once caused the TOC walk to stop before its last entry,
# so repacked archives had no PYZ and refused to start. Keep both characters.
VALID_TYPES = set("bxdsmMZRzon")

TYPE_NAMES = {
    TC_BINARY: "binary", TC_BINARY_DEP: "binary(dep)", TC_DATA: "data",
    TC_SOURCE: "source", TC_MODULE: "module", TC_MODULE_DEP: "module(dep)",
    TC_PYZ: "pyz", TC_ZIPFILE: "zipfile", TC_RUNTIME: "runtime-hook",
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

def _inflate(data: bytes) -> bytes:
    for wbits in (15, -15, 31):
        try:
            return zlib.decompress(data, wbits)
        except zlib.error:
            continue
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
    while cur + TOC_ENTRY_LEN <= n:
        (elen, eoff, dlen, ulen, cflag, tc) = struct.unpack(
            TOC_ENTRY_FORMAT, data[cur:cur + TOC_ENTRY_LEN])
        name_len = elen - TOC_ENTRY_LEN
        if elen <= TOC_ENTRY_LEN or name_len <= 0 or cur + TOC_ENTRY_LEN + name_len > n:
            break
        try:
            tc_s = tc.decode("ascii")
        except UnicodeDecodeError:
            break
        if tc_s not in VALID_TYPES:
            break
        raw_name = data[cur + TOC_ENTRY_LEN: cur + TOC_ENTRY_LEN + name_len]
        name = raw_name.rstrip(NULB).decode("utf-8", "replace")
        cur += TOC_ENTRY_LEN + name_len
        if tc_s == TC_OPTION:
            options.append(name)
            continue
        toc.append(Entry(name=name, offset=eoff, csize=dlen, usize=ulen,
                         cmprs=cflag, tcode=tc_s))
    if not toc:
        raise SystemExit("could not parse the table of contents "
                         "(toc_off=%#x, toc_len=%d)" % (toc_off, toc_size))

    for e in toc:
        e.raw_pos = base + e.offset
    return Archive(path=path, file_size=size, cookie_pos=cookie_pos, pkg_len=pkg_len,
                   toc_off=toc_off, toc_size=toc_size, pyver=pyver, pylib=pylib,
                   base_pos=base, toc=toc, options=options)


def read_entry(ar: Archive, e: Entry, verify_len: bool = True) -> bytes:
    with open(ar.path, "rb") as f:
        f.seek(e.raw_pos)
        blob = f.read(e.stored_size)
    if len(blob) != e.stored_size:
        raise IOError("truncated read of %s (%d/%d)" % (e.name, len(blob), e.stored_size))
    if e.cmprs:
        blob = _inflate(blob)
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
    if e.tcode in (TC_SOURCE, TC_MODULE, TC_MODULE_DEP, TC_PYZ):
        return Path(name)
    return Path(internal_dir) / name


def flat_name(e: Entry) -> str:
    safe = e.name.replace("\\", "__").replace("/", "__")
    return "%s__%s" % (e.tname.replace("(", "_").replace(")", ""), safe)


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
    total = len(ar.toc)
    for i, e in enumerate(ar.toc, 1):
        try:
            blob = read_entry(ar, e, verify_len=False)
        except Exception as ex:
            print("  !! %s: %s" % (e.name, ex), file=sys.stderr)
            fail += 1
            continue
        rel = layout_path(e, internal_dir) if args.layout else Path(flat_name(e))
        if args.pyc_suffix and e.tcode in (TC_MODULE, TC_MODULE_DEP, TC_SOURCE):
            if not rel.name.endswith(".pyc"):
                rel = rel.with_name(rel.name + ".pyc")
        # Remember how many .pyc header bytes were prepended, so the repacker can
        # strip exactly that many. Otherwise the marshalled code object inside the
        # archive is corrupted, and the repacked executable will not start.
        wrapped = 0
        if args.pyc and e.tcode in (TC_MODULE, TC_MODULE_DEP, TC_SOURCE):
            wrapped = PYC_HEADER_LEN
        manifest.append({
            "name": e.name, "tcode": e.tcode, "cmprs": e.cmprs,
            "usize": e.usize, "rel": rel.as_posix(), "pyc_header": wrapped,
        })
        dest = out_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(blob)
        ok += 1
        if args.verbose or i % 200 == 0 or i == total:
            print("  [%d/%d] %s" % (i, total, dest.relative_to(out_root)))
    if args.pyc:
        magic = pyc_magic_for(ar)
        if magic is None:
            print("  ! no .pyc magic available for Python %s, skipping headers"
                  % ar.pyver_str(),
                  file=sys.stderr)
        else:
            fixed = 0
            for e in ar.toc:
                if e.tcode not in (TC_MODULE, TC_MODULE_DEP, TC_SOURCE):
                    continue
                rel = layout_path(e, internal_dir) if args.layout else Path(flat_name(e))
                if not rel.name.endswith(".pyc"):
                    rel = rel.with_name(rel.name + ".pyc")
                dest = out_root / rel
                if not dest.is_file():
                    continue
                blob = dest.read_bytes()
                if blob[:4] != magic:
                    dest.write_bytes(wrap_pyc(blob, magic))
                    fixed += 1
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
        "entries": manifest,
    }
    (out_root / "_archive_manifest.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nextracted %d files (%d failed) -> %s" % (ok, fail, out_root))
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
        # Beware: 0 is an ordinary module and must NOT be skipped - treating it as
        # a namespace package once reduced the output to package __init__ files only.
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
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(code)          # bare marshalled code object, as stored
        ok += 1
    print("extracted bytecode for %d modules -> %s" % (ok, out))
    print("  %d namespace packages (no code object, expected), %d failed"
          % (nspkg, bad))
    if collided:
        print("  %d module path collision(s), written with a flattened name instead:"
              % len(collided))
        for name, other, flat in collided[:10]:
            print("    %s (would have overwritten %s) -> %s" % (name, other, flat))
    print("  note: these are bare marshalled code objects without a .pyc header;")
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
                return TC_BINARY_DEP
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

def build_parser():
    ap = argparse.ArgumentParser(
        prog="nanodesu",
        description="Unpack and repack PyInstaller onefile executables (stdlib only)",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
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
    p.add_argument("--pyc-suffix", action="store_true", default=True,
                   help="append .pyc to module bytecode (default)")
    p.add_argument("--no-pyc-suffix", dest="pyc_suffix", action="store_false")
    p.add_argument("--pyc", action="store_true",
                   help="prepend a valid .pyc header to module bytecode")
    p.add_argument("-v", "--verbose", action="store_true")

    p = sub.add_parser("verify", help="decompress every entry and check lengths"); common(p)

    p = sub.add_parser("pyz", help="unpack a PYZ archive into bytecode"); common(p)
    p.add_argument("-o", "--output")
    p.add_argument("--list", type=int, default=0,
                   help="also list the first N module names")

    p = sub.add_parser("build", help="repack an extracted directory"); common(p)
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--pylib")
    p.add_argument("--pyver", type=int)
    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 0
    if args.cmd == "pyz":
        return cmd_pyz(args.target, args)
    if args.cmd == "build":
        return cmd_build(args.target, args)
    ar = find_archive(args.target)
    return {
        "info": cmd_info, "ls": cmd_ls, "tree": cmd_tree, "search": cmd_search,
        "cat": cmd_cat, "extract": cmd_extract, "verify": cmd_verify,
    }[args.cmd](ar, args)


if __name__ == "__main__":
    sys.exit(main())