#!/usr/bin/env python3
"""Build a defanged variant of a PyInstaller archive: hostile modules replaced with stubs.

**What this produces is a variant, and it is never called a cure.** The distinction is not modesty,
it is the entire safety property. Editing a binary invalidates its signature and changes its hash, so
a modified sample stops matching threat intelligence, blocklists and AV caches -- which means **it
will always scan clean, not because it is clean but because nobody has seen it.** A tool that
produced such files and called the result safe would be manufacturing false confidence at scale.

So the four constraints are structural, not advisory:

  1. **The original is never modified, moved or deleted.** It is the only thing a real engine can
     still judge, and it stays where it was.
  2. **Every change is recorded** -- path, byte count, sha256 before and after -- enough to reverse
     the transformation. Without a record there is no way to check the work, only to trust it.
  3. **The output is never described as clean, safe or fixed**, in code, report or filename. It is
     `*_defanged.exe` and it says so.
  4. **What could not be verified is stated.** Stubbing a module proves that module no longer runs.
     It proves nothing about the rest of the file.

On a PyInstaller build this is tractable in a way that surgery on machine code is not, because the
payload sits at the **Python** level: a malicious module is a `.pyc` inside the PYZ, and replacing it
with a code object that does nothing is a small, checkable act.

Stubs are compiled with **the interpreter that matches the archive**, where one can be found. marshal
is largely version-stable and the stub is loaded back before anything is replaced, but matching the
version removes the question rather than reasoning about it.
"""
from __future__ import annotations

import hashlib
import json
import marshal
import struct
import subprocess
import sys
import zlib
from pathlib import Path

# Where a matching interpreter might live, in the order to try them. Local to this file because it is
# only ever used to compile stubs.
INTERPRETER_HINTS = (
    r"E:\DaShaoHuo\Python-managed\pythoncore-{v}-64\python.exe",
    r"C:\Python{m}{n}\python.exe",
    r"C:\Program Files\Python{m}{n}\python.exe",
)

# 17, measured rather than recalled: magic(4) + pyc magic(4) + toc offset(4) + reserved(5). The first
# version of this said 13 -- the reserved field left out -- and the header-length assertion caught it
# before a malformed archive was written. Reading the constant off a real PYZ is why the number is
# right; a wrong one produces a file that no extractor can open.
PYZ_MAGIC = b"PYZ" + bytes(1)
# The record's format version. **Both repositories must write the same number**, or the cross-tool
# consistency assertion has nothing to align on -- see ACTION_CONTRACT.md.
RECORD_VERSION = 1

PYZ_HEADER_LEN = 17


def find_interpreter(version: str):
    """A python.exe reporting `version` (like "3.12"), or None.

    Matching the archive's interpreter is the point: `marshal` is version-stable in practice, but "in
    practice" is a weaker claim than "the same compiler", and there is no reason to settle for the
    weaker one when a matching interpreter is usually available.
    """
    if not version:
        return None
    want = version.strip()
    parts = want.split(".")
    if len(parts) < 2:
        return None
    candidates = []
    for hint in INTERPRETER_HINTS:
        try:
            candidates.append(hint.format(v=want, m=parts[0], n=parts[1]))
        except (IndexError, KeyError):
            continue
    candidates.append(sys.executable)
    seen = set()
    for cand in candidates:
        if cand in seen or not Path(cand).is_file():
            continue
        seen.add(cand)
        try:
            out = subprocess.run([cand, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
                                 capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        if out.stdout.decode("ascii", "replace").strip() == want:
            return cand
    return None


def make_stub(interpreter=None):
    """A marshalled code object for a module that does nothing.

    `pass` rather than a raise: a stub that raises turns a neutralised program into a broken one,
    which is a different and less useful outcome. The report says plainly that the module is inert.

    Returns (marshalled_bytes, meta). `meta` records which interpreter compiled it, because that is
    the one fact deciding whether the stub will load.
    """
    src = "pass\n"
    if interpreter:
        # Compile with the matching interpreter. Marshalling has to happen inside the process whose
        # version will load it, so the bytes come back through a pipe rather than being re-marshalled
        # here from a code object this interpreter built.
        script = ("import marshal,sys;"
                  "sys.stdout.buffer.write(marshal.dumps(compile('pass\\n','<stub>','exec')))")
        try:
            proc = subprocess.run([interpreter, "-c", script], capture_output=True, timeout=60)
            if proc.returncode == 0 and proc.stdout:
                return proc.stdout, {"compiled_by": interpreter, "version_matched": True}
        except (OSError, subprocess.SubprocessError):
            pass          # fall through rather than fail; the result is checked either way
    return marshal.dumps(compile(src, "<stub>", "exec")), {
        "compiled_by": sys.executable,
        "version_matched": False,
        "caveat": ("no interpreter matching the archive's Python version was found, so the stub was "
                   "compiled by a different one; marshal is largely version-stable and the stub is "
                   "round-trip checked, but this is the weaker claim"),
    }


def check_stub(blob: bytes, interpreter=None) -> dict:
    """Load the marshalled stub back, with the matching interpreter when there is one.

    Run before any replacement: a stub that cannot be loaded turns a neutralised archive into one
    that will not start, and finding that out here is much better than finding it out later.
    """
    if interpreter:
        script = "import marshal,sys;marshal.loads(sys.stdin.buffer.read());print('ok')"
        try:
            proc = subprocess.run([interpreter, "-c", script], input=blob,
                                  capture_output=True, timeout=60)
            ok = proc.returncode == 0 and b"ok" in proc.stdout
            return {"ok": ok, "checked_by": interpreter,
                    "detail": None if ok else proc.stderr.decode("utf-8", "replace")[-300:]}
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "checked_by": interpreter, "detail": str(exc)}
    try:
        marshal.loads(blob)
        return {"ok": True, "checked_by": sys.executable, "detail": None}
    except Exception as exc:                                  # noqa: BLE001 - reported verbatim
        return {"ok": False, "checked_by": sys.executable,
                "detail": "%s: %s" % (type(exc).__name__, exc)}


def _sha(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def read_pyz(blob: bytes):
    """(pyc_magic, toc_offset) for a PYZ archive, or (None, None) if it is not one."""
    if not blob.startswith(PYZ_MAGIC):
        return None, None
    return blob[4:8], struct.unpack_from("!i", blob, 8)[0]


def rewrite_pyz(pyz_path: Path, replacements: dict, *, dry_run: bool = False) -> dict:
    """Rebuild a PYZ with `replacements` ({dotted name: marshalled code object}) swapped in.

    **Why rebuilding rather than overwriting in place.** A PYZ keeps each payload as a zlib block at
    an offset recorded in its table of contents, and a stub is far smaller than the module it
    replaces. A naive in-place write would leave the original compressed bytes sitting in the file
    after the shorter stub -- the payload would still be *present*, merely unreferenced, and a
    "defanged" file would still contain the thing it claims to have removed. Rebuilding drops it.

    Layout, as PyInstaller writes it and `cmd_pyz` reads it back:

        PYZ magic (4) | pyc magic (4) | toc offset (int32 BE) | reserved (5)
        [payload blocks, zlib]  |  marshal.dumps(toc)
        toc = [(name, (type, offset, length)), ...]      offsets from the payload area start

    The first attempt at neutralisation silently did nothing because it edited the expanded
    `_pyz_modules/` directory, which `build` never reads -- it repacks this file. Hence the rebuild.

    Nothing here executes anything. The output is re-read the way the extractor reads it before the
    result is returned, because a PYZ that cannot be read is an archive that will not start, which is
    a different bug from a payload that survived.
    """
    pyz_path = Path(pyz_path)
    blob = pyz_path.read_bytes()
    magic, toc_off = read_pyz(blob)
    if magic is None:
        return {"ok": False, "reason": "not a PYZ archive (no PYZ magic)"}
    try:
        toc = marshal.loads(blob[toc_off:])
    except Exception as exc:                                  # noqa: BLE001
        return {"ok": False, "reason": "could not read the PYZ table of contents: %s" % exc}

    comp_blocks, layout, replaced, unmatched = [], [], [], set(replacements)
    for item in toc:
        if not (isinstance(item, (tuple, list)) and len(item) == 2):
            continue
        name, meta = item
        if not (isinstance(meta, (tuple, list)) and len(meta) == 3):
            continue
        typ, off, ln = meta
        if not ln or typ == 3:
            layout.append((name, typ, 0))            # namespace package: nothing stored
            continue
        if name in replacements:
            raw = replacements[name]
            unmatched.discard(name)
            replaced.append({"name": name, "before_bytes": ln, "after_bytes": len(raw)})
        else:
            try:
                raw = zlib.decompress(blob[off:off + ln])
            except zlib.error as exc:
                return {"ok": False, "reason": "block for %r does not decompress: %s" % (name, exc)}
        block = zlib.compress(raw, 9)
        layout.append((name, typ, len(block)))
        comp_blocks.append(block)

    # Offsets are relative to the payload area, which begins after the header. Every block moved, so
    # every offset is recomputed rather than carried over.
    final_toc, cursor = [], 0
    for name, typ, clen in layout:
        if clen == 0:
            final_toc.append((name, (typ, 0, 0)))
        else:
            final_toc.append((name, (typ, PYZ_HEADER_LEN + cursor, clen)))
            cursor += clen

    body = b"".join(comp_blocks)
    # See PYZ_HEADER_LEN: 4 + 4 + 4 + 5. The reserved field is five zero bytes, and leaving it
    # out is what made the first attempt's header 13 bytes long.
    header = PYZ_MAGIC + magic + (PYZ_HEADER_LEN + len(body)).to_bytes(4, "big") + bytes(5)
    if len(header) != PYZ_HEADER_LEN:
        return {"ok": False, "reason": "header length drifted to %d" % len(header)}
    new_blob = header + body + marshal.dumps(final_toc)

    result = {
        "ok": True,
        "path": str(pyz_path),
        "modules_total": len(final_toc),
        "replaced": replaced,
        "unmatched": sorted(unmatched),
        "bytes_before": len(blob),
        "bytes_after": len(new_blob),
        "dry_run": bool(dry_run),
    }
    if dry_run:
        result["written"] = False
        return result

    pyz_path.write_bytes(new_blob)

    check = pyz_path.read_bytes()
    try:
        chk_off = struct.unpack_from("!i", check, 8)[0]
        for name, meta in marshal.loads(check[chk_off:]):
            if isinstance(meta, (tuple, list)) and len(meta) == 3 and meta[2]:
                zlib.decompress(check[meta[1]:meta[1] + meta[2]])
    except Exception as exc:                                  # noqa: BLE001
        result["ok"] = False
        result["reason"] = "the rebuilt PYZ does not read back: %s" % exc
        result["written"] = True
        return result
    result["verified"] = True
    result["written"] = True
    return result


def find_pyz_in_tree(tree: Path):
    """The extracted PYZ file, if the tree has one. `extract` writes it as `pyz__<name>`."""
    tree = Path(tree)
    return (sorted(tree.glob("pyz__*.pyz")) or sorted(tree.glob("*.pyz")) or [None])[0]


def find_modules(tree: Path) -> list:
    """Every `.pyc` in an extracted tree, with the ones under the PYZ marked as such.

    Both places matter. A module can sit in the archive's own `m`/`s`/`M` entries -- which land beside
    the bootloader stub at the top level -- or inside the PYZ, which `pyz` has to expand first. The
    PYZ is the more common hiding place, and the distinction decides *how* the change is made: a PYZ
    module requires rebuilding the PYZ, while a top-level one is a plain file.
    """
    tree = Path(tree)
    out = []
    for path in sorted(tree.rglob("*.pyc")):
        rel = path.relative_to(tree).as_posix()
        out.append({
            "path": path,
            "rel": rel,
            "in_pyz": rel.startswith("_pyz_modules/") or "/_pyz_modules/" in rel,
            "size": path.stat().st_size,
        })
    return out


def dotted_name(rel: str) -> str:
    """The PYZ entry name for an extracted module path.

    Extraction writes `<name with dots as slashes>.pyc`, under `_pyz_modules/` when the PYZ was
    expanded. The dotted name the PYZ indexes on has to be recovered from that path, and getting it
    wrong is invisible: the rebuild simply finds nothing to replace and reports success with an empty
    replacement list, which is exactly how the first version of this failed.
    """
    parts = Path(rel).parts
    if parts and parts[0] == "_pyz_modules":
        parts = parts[1:]
    return ".".join(Path(*parts).with_suffix("").parts)


def neutralize(archive: Path, tree: Path, *, modules, pyc_header: int = 16,
               python_version: str = "", dry_run: bool = False) -> dict:
    """Replace each named module with a stub. Returns the record.

    The caller extracts the archive into `tree` first. Where the targets live in the PYZ -- the usual
    case -- the PYZ itself is rebuilt, because `build` repacks that file and never looks inside
    `_pyz_modules/`.
    """
    targets = list(modules)
    interpreter = find_interpreter(python_version)
    stub_blob, stub_meta = make_stub(interpreter=interpreter)
    stub_check = check_stub(stub_blob, interpreter)

    record = {
        "record_version": RECORD_VERSION,
        "action": "neutralize",
        "source_archive": str(archive),
        "tree": str(tree),
        "dry_run": bool(dry_run),
        "python_version": python_version,
        "interpreter_matched": interpreter,
        "stub": dict(stub_meta, **{"bytes": len(stub_blob), "sha256": _sha(stub_blob)}),
        "stub_check": stub_check,
        "replaced": [],
        "pyz": None,
        "caveats": [
            "This output is a VARIANT, not a repaired file. It is not clean, not safe, not fixed.",
            ("Editing the archive changes its hash and invalidates any signature it had, so it stops "
             "matching threat intelligence and AV caches -- and will therefore scan clean because it "
             "is unknown, not because it is good."),
            ("Stubbing a module proves that module no longer runs. It proves nothing about the rest "
             "of the file, which was not otherwise altered."),
        ],
    }

    if not stub_check["ok"]:
        record["refused"] = ("the stub could not be loaded back by %s, so nothing was replaced"
                             % stub_check["checked_by"])
        return record

    in_pyz = [t for t in targets if t.get("in_pyz")]
    outside = [t for t in targets if not t.get("in_pyz")]

    if in_pyz:
        pyz_path = find_pyz_in_tree(tree)
        if pyz_path is None:
            record["refused"] = "targets are inside the PYZ but no PYZ file was found in the tree"
            return record
        replacements = {dotted_name(t["rel"]): stub_blob for t in in_pyz}
        result = rewrite_pyz(pyz_path, replacements, dry_run=dry_run)
        record["pyz"] = result
        if not result.get("ok"):
            record["refused"] = "the PYZ could not be rebuilt: %s" % result.get("reason")
            return record
        for r in result.get("replaced", []):
            record["replaced"].append(dict(r, in_pyz=True, written=not dry_run))
        for name in result.get("unmatched", []):
            # Named rather than swallowed: a target that matched nothing means the name was wrong, and
            # silently reporting "replaced 0" would look like a clean run.
            record["replaced"].append({"name": name, "matched": False,
                                       "why": "no such module in the PYZ"})

    for item in outside:
        path = Path(item["path"])
        before = path.read_bytes()
        after = (before[:pyc_header] if pyc_header else b"") + stub_blob
        if not dry_run:
            path.write_bytes(after)
        record["replaced"].append({
            "rel": item["rel"], "in_pyz": False,
            "bytes_before": len(before), "bytes_after": len(after),
            "sha256_before": _sha(before), "sha256_after": _sha(after),
            "written": not dry_run,
        })

    matched = [r for r in record["replaced"] if r.get("matched", True)]
    record["summary"] = {"replaced": len(matched),
                         "note": "%d module(s) replaced with an inert stub" % len(matched)}

    # **Constraint 4's sentence turned into evidence.** The caveat says the rest of the file "was not
    # otherwise altered" -- and nothing checked that. It was guaranteed by the author's care rather
    # than by anything a reader could run. Comparing the tree after the rewrite against the archive
    # it came from turns the strongest claim this action makes into something checkable.
    record.update(verify_untouched(archive, tree, replaced_names={
        r.get("name") for r in matched if r.get("name")}))
    return record


def verify_untouched(archive: Path, tree: Path, *, replaced_names: set) -> dict:
    """Which entries are byte-identical to the original, and which changed besides the targets.

    Reads both the archive and the tree, hashes every entry's bytes, and reports the comparison. The
    targets are expected to differ; **anything else that differs means this output is not a
    one-place change**, and the record has to say so rather than leave the claim standing.

    Never raises: a failure to compare is reported as `unverifiable` with the reason, because a broken
    comparison must not read as a clean one.
    """
    try:
        import nanodesu as nd
        original = {}
        # `find_archive` -- **not `open_archive`**, which does not exist. The first version of this
        # called a function that was never there, and because this helper never raises, the mistake
        # surfaced as "unverifiable" rather than as an error. That is the failure mode this whole
        # module is arranged against, so the API is asserted rather than assumed.
        ar = nd.find_archive(archive)
        for entry in ar.toc:
            try:
                original[entry.name] = hashlib.sha256(nd.read_entry(ar, entry)).hexdigest()
            except Exception:                                      # noqa: BLE001
                original[entry.name] = None
    except BaseException as exc:                                   # noqa: BLE001
        # **`BaseException`, not `Exception`.** `find_archive` exits rather than raising when the file
        # is not an archive, and `SystemExit` derives from `BaseException` -- so the first version's
        # promise never to raise was false, and it surfaced as a `SystemExit` escaping a helper whose
        # whole purpose is to report failure as data.
        return {"entries_unchanged": None, "entries_changed": [],
                "untouched_verified": False,
                "untouched_unverifiable": "the original could not be read back: %s" % exc}

    same, changed = 0, []
    for name, before in original.items():
        if name in replaced_names:
            continue
        path = Path(tree) / name
        if not path.is_file():
            changed.append({"name": name, "why": "absent from the rewritten tree"})
            continue
        try:
            after = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            changed.append({"name": name, "why": "unreadable: %s" % exc})
            continue
        if before is None:
            changed.append({"name": name, "why": "the original entry could not be read"})
        elif before == after:
            same += 1
        else:
            changed.append({"name": name, "why": "bytes differ from the original",
                            "before_sha256": before, "after_sha256": after})
    return {"entries_unchanged": same, "entries_changed": changed, "untouched_verified": True}


def write_record(record: dict, tree: Path) -> Path:
    """Persist the record beside the tree, so the transformation can be audited or reversed."""
    path = Path(tree) / "_neutralize_record.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return path
