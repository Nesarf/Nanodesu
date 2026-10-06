# Nanodesu 1.7.4

Two hardening items that turned out to be one genuine defect each.

## Device names are not filenames

`NUL`, `CON`, `PRN`, `AUX`, `COM1`-`COM9` and `LPT1`-`LPT9` address devices on Windows, so writing to one
either fails or **does not create a file at all**. `confining()` sanitised traversal, drive letters and
wildcards and said nothing about these — and **an extraction that reports a member as written while
nothing was created is worse than a refusal**, because the report is what the operator keeps.

They are now prefixed rather than replaced, because a replacement merges names.

**And the first version of that fix escaped every leading underscore, which broke ordinary names.** A
test caught it. The escaping is now confined to device names, which leaves one ambiguity: `NUL` and
`_NUL` reach the same place. **Stated rather than solved** — it needs an archive holding both, and
repairing names nobody has is the worse trade. There is a test that pins the documented case.

## A bare name is no longer resolved

`shutil.which` resolved `python3.12` off PATH and the result was executed — **so an archive's own version
string decided which executable this tool would start, and the archive is the untrusted input.**

That lookup is gone from both places it existed. **Nothing about the archive gets to choose a program
that runs.**

What is given up is small and bounded: the built-in table covers 3.7 through 3.14, so the only cost is
reading the exact magic for a version the table does not know. `NANODESU_PYTHON` still works, and is the
supported way to say where an interpreter lives — **precisely because naming it is a decision a person
makes rather than one a file makes.**

## Testing

**180 tests** (was 172), green on Python 3.9, 3.12, 3.13 and 3.14. Eight new: device names are prefixed,
ordinary names stay distinct, the ambiguity is the documented one and only that, traversal defences
survive, no source file calls `shutil.which`, a bare template is refused, and the table is what makes
refusing affordable.
