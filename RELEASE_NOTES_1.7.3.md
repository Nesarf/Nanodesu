# Nanodesu 1.7.3

Three decompression caps instead of one, and the PYZ gets its own.

## The per-entry bound was not a bound

`MAX_ENTRY_BYTES` was **2 GiB**, which is not a limit for an analysis tool. **Three entries at 1.9 GiB
were each individually "within limit" and together are six gigabytes of decompressed output from a file
that might be a tenth of that size.**

**The interesting failure was never one huge entry — it was a column of merely large ones, which no
single check could see.** So there are now three:

| cap | value | why it exists separately |
|---|---|---|
| `MAX_ENTRY_BYTES` | **256 MiB** | any single entry |
| `MAX_TOTAL_BYTES` | **1 GiB** | the column of large ones |
| `MAX_OUTPUT_FILES` | **8192** | many tiny entries cost nothing in bytes and everything in inodes |

**The total is charged for every entry read, compressed or not** — an uncompressed entry still lands in
memory and still counts against what this extraction is allowed to produce.

## The PYZ had no ceiling of its own

It is a file *inside* the archive: the whole thing was read into one blob and sliced, so **every
CArchive bound could be satisfied while the region handed to `marshal.loads()` was chosen by the file
rather than by this tool.**

`MAX_PYZ_BYTES` (256 MiB), `MAX_PYZ_TOC_BYTES` (64 MiB) and `MAX_PYZ_RECORDS` (1,000,000), **checked
before parsing rather than after.**

## Lifting them is explicit, and named after what it does

`--allow-huge`, for one run, with the help text saying what it costs:

> *lift the decompression caps (per-entry, total and PYZ) for this run only. **Only for an archive you
> already trust***

**Deliberately awkward to reach.** The caps exist because a malformed or hostile archive can ask for
more than the machine has, and *"temporarily lift them"* is the normal way that a protection quietly
stops being one. **Naming the flag after what it does keeps the act visible in a shell history**, and
there is a test that the default is not lifted.

## Testing

**172 tests** (was 166), green on Python 3.9, 3.12, 3.13 and 3.14. Six new: the per-entry cap is an
analysis-tool value, the total and count exist, the total is charged for every read, the PYZ ceilings
exist and are checked before parsing, the flag is explicit and stripped from argv, and the default is
not lifted.
