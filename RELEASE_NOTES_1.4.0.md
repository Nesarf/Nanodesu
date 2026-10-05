# Nanodesu! 1.4.0

She has a voice now. Her name is Lilith, and the tool is named after her speech habit.

## What changed for the user

`--help` and the no-command path speak in Lilith's register. Everything else is unchanged, and
**`--plain` (or `NANODESU_PLAIN=1`) silences her completely** — which is not a courtesy, it is the
term on which a persona is acceptable in a tool somebody may have to run in a pipeline.

```
  私は、起こさないのです。
  外套を脱がせて、中身を見るだけなのです — 噛みつく必要は、ないのです。

  (--plain で私は黙ります)
```

## The design document is the substance of this release

`PERSONA.md` and `CHARACTER_LILITH.md` are the deliverable; the prose in the CLI is a small part of
it. Both are written so the behaviour is *derivable* rather than merely listed — three roots in
`PERSONA.md` from which anything unstated can be judged:

**She is an old vampire house's daughter and this is her domain**, so she neither fawns nor
over-explains. **She is lovely outside and proud inside** — and being looked down on does not make
her shout, it makes her *cold*: sentences shorten, the speech habit drops. **She is proud of knowing
without biting** — she reads a file without waking it, and treats that restraint as an honour rather
than a limitation.

That third root is why the persona is safe to add: **the character's pride and the tool's hard rule
are the same fact.** The tool never executes its target; she is proud of never needing to.

## The boundary, stated as a rule and enforced by tests

| output | reader | has a voice |
|---|---|---|
| README, `--help`, interactive prose | a person | yes |
| `--plain` | a person who wants the professional register | no |
| `--json`, exit codes, manifests, STIX/MISP/YARA | a program | **no** |

A persona in machine-readable output is not a personality, it is a parsing bug reported by whoever
consumes it. Five tests hold that line, including a structural one: **nothing reachable from
`cmd_extract` may reference `say`, `is_plain` or `epilog_for` at all.**

## A defect this release would have shipped

`PROSE` was meant to map each key to a `(Lilith, plain)` pair. Two entries were written as bare
strings, so `say()` unpacked three values into two values and **`--help` raised
`ValueError: too many values to unpack`** — the tool was broken at its front door, and only for
people who had not already learned `--plain`. The table's shape is now asserted for every entry
rather than trusted.

## Testing

**93 tests** (was 79), green on Python 3.9, 3.12, 3.13 and 3.14. New in `test/test_voice.py`:

* every `PROSE` entry is a pair of non-empty strings
* the plain register carries no non-Latin text, so "plain" means plain to the person who asked
* the Lilith register contains no template syntax, so she reads as prose and not as a format string
* `--help` builds in both registers — the regression above, named
* `NANODESU_PLAIN=1` and `--plain` are honoured through a real subprocess, not by inspection
* machine-readable paths cannot reach the voice, structurally
