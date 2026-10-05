# Nanodesu! 1.6.0

`neutralize` — a defanged variant, and the first feature in this project that writes a *modified*
copy of a sample. It took three attempts to make it work, and the first two failed silently.

## What it does

```bash
python nanodesu.py neutralize app.exe -o work/ --in-pyz --modules payload
```

Replaces named Python modules inside the PYZ with a stub that does nothing, then repacks. The result is
`app_defanged.exe`.

## It is a variant. Never a cure.

**This tool will not call the output clean, safe or fixed, and that is the mechanism rather than
caution.** Editing an archive changes its hash and invalidates its signature, so the variant stops
matching threat intelligence and AV caches — **it will always scan clean, not because it is clean but
because nobody has seen it.** A tool that produced such files and called the result safe would be
manufacturing false confidence at scale.

Demonstrated during testing, on this machine: an instrumented sample and its defanged variant were
both handed to Windows Defender, and **both came back clean** — because neither is known malware.
`clean` meant *unrecognised*, which is precisely the trap.

Guaranteed, and tested:

* **The original is never modified, moved or deleted.** It is the only thing a real engine can still
  judge. Asserted against file size and mtime, and a mismatch is a hard failure.
* **The payload bytes are gone, not merely unreferenced.** The PYZ is rebuilt rather than patched in
  place. Verified by reading the variant's own PYZ back and confirming the payload's marker string is
  absent.
* **Every change is recorded** in `_neutralize_record.json`: dotted name, bytes before and after.
* **The stub is compiled by the interpreter matching the archive** where one is found (3.12 here,
  matched rather than assumed), and loaded back before anything is replaced.

Not done, and said out loud each run:

* **It does not make the program work.** Stubbing a module the program depends on **breaks it.**
  Verified: the test sample died with `AttributeError: module 'payload' has no attribute 'run'`.
  Removing a capability and preserving a program are different goals; this does the first.
* **It proves nothing about the rest of the file.** Stubbing a module proves that module no longer
  runs, and nothing else.
* **It cannot do this for machine code.** It works because a PyInstaller payload lives at the Python
  level. A hostile DLL or a shellcode blob has no equivalent small, checkable act.

## Three attempts, and why the first two produced nothing

Both failures were **silent successes** — the command reported "replaced 1 module" and wrote a
variant, and the payload ran anyway. Only building a real sample, running it, and checking for a side
effect caught them.

1. **It edited the wrong thing entirely.** The first version replaced files in the expanded
   `_pyz_modules/` directory, but `build` repacks the PYZ *file* and never looks inside that
   directory. The change never reached the archive.
2. **The header length was wrong.** The next version rebuilt the PYZ but declared the header as 13
   bytes instead of 17 — the 5 reserved bytes left out — which was caught by the header-length
   assertion before a malformed archive could be written. The constant is now *measured off a real
   PYZ* (first payload offset = 17) rather than recalled.

The lesson is the one this project keeps relearning: **a fixture built from the same understanding as
the code proves nothing.** The verification that mattered was an instrumented sample whose only
behaviour was a visible side effect — build it, run it, confirm the marker appears, defang it, run it
again, confirm the marker is gone.

## Testing

**124 tests** (was 105), green on Python 3.9, 3.12, 3.13 and 3.14. `test_neutralize.py` adds 19:
the payload bytes are absent afterwards, a dry run writes nothing, an unmatched module name is
reported rather than swallowed, a non-PYZ is refused, a missing interpreter is not a crash, the
dotted-name mapping is right (getting it wrong is invisible — the rebuild finds nothing and reports
success), and the source never describes the output as safe.
