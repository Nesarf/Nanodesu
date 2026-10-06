# Nanodesu 1.7.1

The neutralize action now has a definition, a record version, and a checkable claim.

## Why this exists at all

**Two repositories each implement this action**, and a cross-tool consistency assertion is meant to hold
them together. That is impossible while nobody has said what the artifact *is* — an assertion that two
things must match has to be told what to compare.

`ACTION_CONTRACT.md` states it: inputs, outputs, the four structural constraints, the record format, and
**which values are comparable across machines and which are not** (paths and interpreter names are not;
the stub hash, the output hash and the entry counts are). It also names what the contract does **not**
promise, because a contract that only lists promises is half a document.

## The record is versioned, and named

`RECORD_VERSION = 1` plus an `action` field. The alignment has to be on something, and *"whatever the
other side happens to write"* is not something.

## Constraint 4 stopped being an assurance

The caveat says the rest of the file *"was not otherwise altered"* — and **nothing checked that.** It was
guaranteed by care rather than by anything a reader could run.

`verify_untouched()` reads the original archive back, hashes every entry, and reports which are
byte-identical and which changed besides the targets — **naming them rather than counting them**,
because *"something else changed"* sends the reader looking while a name tells them whether this output
is a one-place change at all.

**Verified end to end:** an untouched tree reports every entry unchanged; a tampered one names the entry.

## Two corrections, both from actually running it

**The first version called `nd.open_archive`** — a function that does not exist. Because the helper never
raises, the mistake surfaced as `"unverifiable"` rather than as an error: **the exact failure mode this
module exists to prevent.** The API is now read rather than assumed.

**And "never raises" was false.** `find_archive` exits rather than raising when the file is not an
archive, and `SystemExit` derives from `BaseException`, not `Exception` — so it escaped a helper whose
whole purpose is to report failure as data.

## Testing

**161 tests** (was 155), green on Python 3.9, 3.12, 3.13 and 3.14. Six new: an untouched tree reports
everything unchanged, a changed entry is named not counted, the target is excluded, a missing entry is
reported, a failed comparison is not a clean one, and the record carries the version and action name.
