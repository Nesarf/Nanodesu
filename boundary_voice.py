"""The five beats, rendered over what the analysis actually found.

## The rule this file obeys

**She is a rendering layer, not a source of fact.** Every line below is derived from something the
archive actually reports — `integrity`, the type codes, the wrapper. Nothing here is inferred from a
module *name*, because a name is not evidence and treating one as evidence would quietly turn this
tool into a detector, which it deliberately is not.

**And narration can never fail a command.** Every entry point is wrapped: if the voice raises for any
reason, the analysis has already succeeded and its exit code must not change. A persona that can break
the tool is worse than no persona.

## The five beats, and where they come from

    probing    踩     something is structurally wrong and she is testing it
    pressed    夹     a wrapper is wrapped again, or the table did not parse cleanly
    dissolved  化     it came apart; the payload is out
    handed     交出   report to the master
    bored          nothing unusual -- and she says so

The mapping is from finding to beat, not from mood to beat. A clean ordinary file gets `bored`
because that is genuinely what the analysis found, and pretending otherwise would be the same
over-claiming the boundary notice exists to prevent.
"""
from __future__ import annotations

# No import of the tool itself, deliberately. The first version did `import nanodesu` to reach
# `is_plain()` and `say()` -- and when nanodesu.py runs as a script it is `__main__`, so that import
# produced a *second* module object whose register was always the default. `--plain` therefore did
# not silence the voice, and only when run as a script, which is the only way it is ever run.


def grade(ar) -> dict:
    """What the archive actually reports, in the categories the beats are chosen from.

    Pure function over the Archive object. No I/O, no inference from names.
    """
    from collections import Counter

    types = Counter(getattr(e, "tcode", "?") for e in (ar.toc or []))
    integrity = list(getattr(ar, "integrity", []) or [])
    return {
        "entries": len(ar.toc or []),
        "types": dict(types),
        "integrity": integrity,
        "has_integrity_issue": bool(integrity),
        # A PYZ inside a single-file build is the ordinary shape rather than a finding, so it is
        # counted but not treated as interesting on its own.
        "pyz_entries": types.get("z", 0),
        "python": ar.pyver_str() if hasattr(ar, "pyver_str") else None,
        "pylib": getattr(ar, "pylib", None),
    }


def choose_beat(ar, *, wrapped: bool = False) -> str:
    """The beat the evidence supports. See the module docstring for what each one means.

    Order matters: a structural problem outranks everything, because it is the only category where
    something is actually wrong rather than merely present.
    """
    facts = grade(ar)
    if facts["has_integrity_issue"]:
        return "probing"
    if wrapped:
        return "pressed"
    return "bored"


def speak(ar, *, mode: str, say, plain: bool, wrapped: bool = False) -> list:
    """The lines to print, in the active register. Empty list when the voice is off.

    `say` and `plain` are passed in rather than imported, so there is exactly one source of truth for
    the register -- the caller's. Returns lines rather than printing them, so a caller can place them
    and a test can assert on them without capturing stdout.
    """
    try:
        if plain:
            return []
        beat = choose_beat(ar, wrapped=wrapped)
        lines = [say(beat)]

        if beat == "probing":
            # The count is the finding; the sentence is the voice. Never the other way round.
            lines.append(say("cannot_tell"))
        elif beat == "pressed":
            lines.append(say("dissolved") if mode == "extract" else say("pressed"))
        elif beat == "bored":
            lines.append(say("nothing_found"))

        if mode in ("extract", "neutralize") and beat != "bored":
            lines.append(say("handed_over"))
        return [line for line in lines if line and line != beat]
    except Exception:                                          # noqa: BLE001
        # Deliberately broad. The analysis has already succeeded by the time anything calls this;
        # a rendering bug must not change an exit code or lose a report.
        return []


def emit_refusal(reason: str, *, say, plain: bool) -> None:
    """Speak on a path where the tool could not read the file at all.

    Added because the `probing` beat turned out to be nearly unreachable without it: a structurally
    broken archive fails in `find_archive` and exits before any command runs, so the one path where
    the tool genuinely does not know what it is looking at was the one path that said nothing.

    That is also the character's own rule -- refusing to guess is the honest answer, not a failure --
    so this is where she belongs most, not least.
    """
    try:
        if plain:
            return
        lines = [say("probing"), say("cannot_tell")]
        import sys
        print(file=sys.stdout)
        for line in lines:
            if not line:
                continue
            for physical in str(line).splitlines():
                print("  %s" % physical)
    except Exception:                                          # noqa: BLE001
        return


def emit(ar, *, mode: str, say, plain: bool, wrapped: bool = False) -> None:
    """Print the lines, indented, or nothing at all. Never raises."""
    try:
        lines = speak(ar, mode=mode, say=say, plain=plain, wrapped=wrapped)
        if not lines:
            return
        import sys
        print(file=sys.stdout)
        for line in lines:
            for physical in str(line).splitlines():
                print("  %s" % physical)
    except Exception:                                          # noqa: BLE001
        return
