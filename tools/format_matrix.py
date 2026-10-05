#!/usr/bin/env python3
"""Produce the matrix by reading the named constants, not by pattern-hunting for literals.

The previous attempt searched for format-looking strings and got a plausible but incomplete answer:
the TOC format is assigned to `_TOC_ENTRY_FORMAT` in some versions and appears as a bare literal in
others, so a regex found it in three versions out of nine and reported the rest as MIXED -- which
looks like a real finding rather than a gap in the search.

So this reads the constants a reader would actually be written against:
  _TOC_ENTRY_FORMAT / _TOC_ENTRY_LENGTH   the table-of-contents record
  the cookie format                        from the ARCHIVE_COOKIE pack/unpack
  ARCHIVE_ITEM_*                           what the bootloader accepts
and computes the encoded entry length with struct.calcsize so the number is measured, not assumed.
"""
from __future__ import annotations

import json
import re
import struct
import tarfile
import os
from pathlib import Path

import argparse

CACHE = Path(os.environ.get("PYINSTALLER_SRC_CACHE",
                            Path.home() / ".cache" / "pyinstaller-src"))
OUT = Path(r"E:\DaShaoHuo\cache\tmp\pyinstaller-matrix.json")
VERSIONS = ["3.6", "4.0", "4.10", "5.0", "5.13.2", "6.0.0", "6.11.1", "6.16.0", "6.22.3"]


def member(tar, suffix):
    for m in tar.getmembers():
        if m.isfile() and m.name.endswith(suffix):
            fh = tar.extractfile(m)
            if fh:
                return fh.read().decode("utf-8", "replace")
    return None


def collect(version):
    row = {"version": version}
    tb = CACHE / ("pyinstaller-%s.tar.gz" % version)
    if not tb.is_file():
        row["error"] = "sdist not present"
        return row
    with tarfile.open(tb, "r:gz") as tar:
        readers = member(tar, "PyInstaller/archive/readers.py") or ""
        writers = member(tar, "PyInstaller/archive/writers.py") or ""
        header = member(tar, "bootloader/src/pyi_archive.h") or ""
    py = readers + "\n" + writers

    # The TOC record, by its constant name where it has one and by the only literal of its shape
    # where it does not.
    m = re.search(r"_TOC_ENTRY_FORMAT\s*=\s*['\"]([^'\"]+)['\"]", py)
    fmt = m.group(1) if m else None
    if fmt is None:
        cands = set(re.findall(r"['\"](!\s*[iI]{3,4}\s*B\s*[Bc])['\"]", py))
        fmt = cands.pop() if len(cands) == 1 else None
    row["toc_format"] = fmt
    if fmt:
        try:
            row["toc_entry_len"] = struct.calcsize(fmt)
        except struct.error:
            row["toc_entry_len"] = None

    # The cookie: the only format with a 64s tail.
    cookies = sorted(set(re.findall(r"['\"](!8s[^\s'\"]*64s)['\"]", py)))
    row["cookie_format"] = cookies[0] if len(cookies) == 1 else (cookies or None)
    if isinstance(row["cookie_format"], str):
        try:
            row["cookie_len"] = struct.calcsize(row["cookie_format"])
        except struct.error:
            row["cookie_len"] = None

    # The magic, from the writer's constant.
    m = re.search(r"archive_magic\s*=\s*b?['\"]([^'\"]+)['\"]", writers)
    row["archive_magic"] = m.group(1) if m else None

    # What the bootloader will accept. No trailing semicolon on these lines -- requiring one
    # matched nothing and made six versions look as though they had no type codes.
    items = re.findall(r"ARCHIVE_ITEM_\w+\s+'(\w)'", header)
    row["typecodes"] = "".join(sorted(set(items)))

    # A reader needs to know whether the name field is padded to 16 and whether a name length can
    # be derived from entry_length.
    row["name_padded_to_16"] = bool(re.search(r"padded to multiple of 16|padded to a multiple of 16",
                                               header))
    return row


rows = [collect(v) for v in VERSIONS]
OUT.write_text(json.dumps(rows, indent=1), encoding="utf-8")

print("%-9s %-12s %-6s %-14s %-6s %-14s %s"
      % ("version", "toc fmt", "len", "cookie fmt", "len", "magic", "typecodes"))
for r in rows:
    if r.get("error"):
        print("%-9s %s" % (r["version"], r["error"]))
        continue
    print("%-9s %-12s %-6s %-14s %-6s %-14s %s"
          % (r["version"], r.get("toc_format") or "MIXED", r.get("toc_entry_len"),
             r.get("cookie_format") or "MIXED", r.get("cookie_len"),
             r.get("archive_magic", "?"), r.get("typecodes", "?")))

print("\n=== changes, in order ===")
prev = {}
for r in rows:
    if r.get("error"):
        continue
    for key in ("toc_format", "cookie_format", "archive_magic", "typecodes"):
        cur = r.get(key)
        if key in prev and prev[key] != cur:
            print("  %-9s %-15s %s -> %s" % (r["version"], key, prev[key], cur))
        prev[key] = cur

print("\n=== generations a reader must handle ===")
gens = {}
for r in rows:
    if r.get("error"):
        continue
    gens.setdefault((r.get("toc_format"), r.get("cookie_format")), []).append(r["version"])
for (t, c), vs in gens.items():
    print("  toc=%-10s cookie=%-13s %s" % (t, c, ", ".join(vs)))


# Fetching is separate from reading so the tool works with an existing cache and no network.
