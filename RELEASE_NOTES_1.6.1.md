# Nanodesu! 1.6.1

A boundary notice, and the false sentence it caught in its own first draft.

## What was missing

The tool unpacks unknown executables and reported what it found **without ever saying what it does
not establish**. That is the same over-reading the project refuses elsewhere, arrived at from the
other side: `extract` succeeded, therefore I have the source; the repack is byte-identical, therefore
it is clean; nothing errored, therefore nothing is hidden.

`BOUNDARY_NOTICE` is now **one constant attached to every result**, printed by `info` and readable
directly with `--boundary`. Not a paragraph in a README that nobody opens — a structure that travels
with the output.

## The false line, and why it is worth reporting

The first draft began:

> ~~"Nothing here creates a process, and no path in it can be made to."~~

**That was untrue, and the test written to check it is what found out.** `_magic_for` launches a
Python interpreter to ask for its own bytecode magic number, whenever a `.pyc` header is needed. It
never touches the archive under audit — but a subprocess is a subprocess, and a boundary notice that
is false in its **first line** is worse than no notice at all.

The line now says what actually happens, and the claim is **checkable rather than merely worded
carefully**: a test walks every `subprocess.run` call site and requires each to pass `-c` with a
magic-number query, and a second test requires that the archive under audit never reaches a
subprocess at all.

## What it now says

* It never executes, loads or launches the archive it reads — and names the one process it *can*
  create.
* It does **NOT** address whether the program is safe, what it does when run, or whether the
  extracted bytes are what the author intended. **Extraction reports structure, not intent.**
* It reads [the table of contents, stored entry bytes, the PYZ]. It is **NOT** a decompiler, **NOT** a
  malware detector, and **NOT** a packer for anything but the archive it came from.
* **A clean extraction is NOT proof of anything.** A byte-identical repack proves *fidelity* and not
  safety; a bare marshalled code object is **not source**; and **content that never appears in the
  table of contents is invisible to this tool entirely.**

Every line names a specific over-reading, because a general caution gets skimmed.

## Where this came from

The shape is taken from the Tor/Firefox posture auditor (Aragami), which attaches its own notice
verbatim to **every** return path — error paths included, for the reason that a failed query
returning an empty list looks a great deal like "nothing found".

## Testing

**141 tests** (was 124), green on Python 3.9, 3.12, 3.13 and 3.14. `test_boundary.py` adds 17: the
notice is a list of complete sentences, it names each specific over-reading, the wrapper does not
mutate its caller's dict, the constant is one shared object rather than a copy per call, `info`
carries it, the source never claims the output is safe — and the process claim is verified against
the actual call sites.
