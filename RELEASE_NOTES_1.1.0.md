# Nanodesu! 1.1.0 — security release

**If you downloaded the `nanodesu.exe` from 1.0.0 or 1.0.1, replace it.** Those builds carry
the path traversal bug described below.

## The bug: extraction could write outside the output directory

Entry names come from the archive, and an archive is a file someone else built. Those names
were used to build output paths without being checked, so an entry named

```
../../OUTSIDE/pwned.txt
```

wrote above the directory you gave it — anywhere you have permission to write. This was
found by running the attack, not by reading the code.

For a tool whose entire purpose is to open untrusted files, this is the worst class of bug
it can have: a crafted PyInstaller executable could place files wherever it liked on the
machine of the person inspecting it.

**Nine vectors are now blocked and regression-tested**, each of which was tried against the
old code: `../` levels, absolute paths, drive-absolute paths (`C:/x` reduces to the
drive-relative `C:x`, which escapes when joined, so colons are stripped too), backslash
separators, embedded empty segments, UNC paths, a bare `..`, and deep nesting.

Repaired, not dropped. Dropping the entry would be silent data loss — worse than useless in
an analysis tool — and it would break the repack. Instead the path is confined, the repair is
printed, and it is recorded per entry in the manifest, because **an archive that tried this
is a finding about the archive**:

```
  ! 1 entry name(s) tried to leave the output directory and were confined:
      '../../OUTSIDE/pwned.txt' -> OUTSIDE/pwned.txt.pyc
```

## Verified unchanged

836 files round-trip with identical SHA-256 against the same 138 MB archive as before, and
21 tests pass on Python 3.9, 3.12, 3.13 and 3.14. Fixing the write path without changing what
it writes is the point.

## Hardening

* **CI actions are pinned to commit SHAs**, not movable tags — a tag is a pointer that can be
  retagged under you, a SHA cannot. `persist-credentials` is disabled and the workflow token
  is read-only.
* **`SECURITY.md`** states the safety property (the target is never executed), how to report
  privately, the untrusted-input class to look for, and what this tool explicitly does **not**
  defend against.
* **Private vulnerability reporting is enabled** on the repository.

## Install

```bash
pip install .            # or use the attached standalone nanodesu.exe
```
