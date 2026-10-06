# Nanodesu 1.7.2

Bytes appended after the archive are now a fact the tool states.

## What was invisible, and why that was the wrong thing to be invisible

`[bootloader][CArchive][cookie][???]` was identified as a PyInstaller archive and **nothing was said
about `???`.**

**PyInstaller does not write past its own cookie.** So when something is there, it was put there on
purpose — and the usual reasons are **a second payload, a configuration blob, or an encrypted stage.**
A reader told *"this is a PyInstaller archive"* was told about the envelope and **nothing about the
extra weight taped to it**.

## What it does now

`Archive` carries `archive_end`, `overlay_size` and `has_overlay`, plus `overlay_facts()` for a report
to consume. `info` prints the line **either way**:

```
overlay     : 896 B  (archive ends at 0x2d1, file ends at 0x651)
              896 byte(s) follow the end of the archive. PyInstaller does not write there itself,
              so the region was added deliberately -- it often holds a second payload, a
              configuration blob or an encrypted stage. This tool does not interpret it.
```

and, on a clean archive:

```
overlay     : none  (the archive ends where the file does)
```

**Reported at zero too, because "none" is an answer to a question somebody asked** — a line that only
appears when it is interesting teaches a reader that its absence means nothing.

## It measures the region and stops there

The note names the plausible contents **without asserting one**, and says so explicitly. **Measuring
is a fact; saying what is in it would be a claim this parser cannot support.** The tool reports `896
bytes, added deliberately` and leaves the interpretation to whatever comes next.

## Testing

**166 tests** (was 161), green on Python 3.9, 3.12, 3.13 and 3.14. Five new: a clean archive reports
none, appended bytes are measured with the arithmetic checked, the note says what it will not claim, the
archive end is where the cookie ends, and `info` prints the line whether or not there is an overlay.

## Standing rule recorded with this release

**All art and UI for both tools is pixel art.** It serves three things already written down:
auditability (a pixel pipeline is reproducible and leaves no third-party traces), portability (small,
dependency-free assets for a tool that is one script), and **it is an addition to the adult register
rather than a restraint on it.** No exceptions, because an exception splits the style and then nobody
remembers where the line was.
