# Nanodesu! 1.7.6

A packaging patch. Nothing in the tool behaves differently.

## The sdist shipped a test suite that could not run

The 1.7.5 sdist carried `test/test_*.py` and nothing else from that directory. Unpacking the published
sdist and running its suite on Linux gave **eight modules failing at import** on a missing
`spawn_guard.py` and **three tests erroring** on documents that were never shipped
(`FORMAT_MATRIX.md`, `PERSONA.md`).

`PERSONA.md` is the one worth naming. `test_voice_wiring` compares a sentence in the document with the
same sentence in the code — the check that keeps the persona from drifting. An sdist without the
document ships the test that guards it and cannot run it.

## What changed

`MANIFEST.in` names what the tests need: the whole `test/` directory, `tools/`, `FORMAT_MATRIX.md` and
`PERSONA.md`.

## Verified on the artifact

Unpack the built sdist somewhere that is not the repository, then run the suite there:

    nanodesu 1.7.6 sdist, Linux: 210 tests, OK (skipped=1)

The skip is a fixture this host cannot build, the same one the repository run skips.
