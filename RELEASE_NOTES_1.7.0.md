# Nanodesu! 1.7.0

The persona now reaches the commands. She had a table, three registers, a gate and tests — and was
referenced from exactly two places: `--help` and the no-command path. **Every real path was silent.**
All the parts were there and nothing was connected.

## What speaks now

| path | before | now |
|---|---|---|
| `info` on a readable archive | silent | speaks |
| `info` on a **broken** archive | silent | **speaks** |
| `extract` | silent | speaks |
| `--help`, no command | spoke | speaks |

The broken-archive path is the one worth naming. It fails inside `find_archive` and exits **before any
command runs**, so it used to say nothing at all — and it is precisely the path where the tool does not
know what it is looking at. That is the character's own rule as much as the tool's: **refusing to guess
is the honest answer, not a failure.**

## Beats are chosen from evidence, not from mood

    nothing wrong            -> bored     ……普通なのです。つまらないのです。
    a structural problem     -> probing   そこを見るのです / 分からないと言うのが誠実なのです
    a wrapper inside         -> pressed   挟んで離さないのです
    extraction finished      -> handed    ご主人様、中身はこれで全部なのです

Nothing is inferred from a module **name**. A name is not evidence, and treating one as evidence would
quietly turn this into the detector it deliberately is not.

## Two defects found while wiring it

**The register was read from a second copy of the module.** The first version reached `is_plain()` by
importing `nanodesu` — and when `nanodesu.py` runs as a script it is `__main__`, so that import
produced a *second* module object whose register was always the default. **`--plain` did not silence
the voice, only when run as a script, which is the only way it is ever run.** The state is now passed
in, so there is one source of truth.

**The same word was spelled with two different characters in two files.** `nanodesu.py` said
`誠`+U+5B9E (simplified Chinese) and `PERSONA.md` said `誠`+U+5B9F (Japanese). **A terminal renders both
identically, so every reading of the output agreed with itself.** Found by printing code points, not by
looking. `unicodedata` cannot help — both are `CJK UNIFIED IDEOGRAPH` and Unicode carries no
simplified-to-traditional mapping — so the new test checks what can actually be checked: **two files
quoting the same sentence must use the same code points**, compared around the word rather than by
whole-sentence equality, because the document writes `分からない、` with a comma and the code does not.

## Narration cannot break a command

Every entry point is wrapped. The analysis has already succeeded by the time anything speaks, and a
rendering bug **must not** change an exit code or lose a report. Tested by handing the narrator an
object whose every property raises.

## Testing

**155 tests** (was 141), green on Python 3.9, 3.12, 3.13 and 3.14. `test_voice_wiring.py` adds 13: each
beat is chosen from the right evidence, a structural problem outranks a wrapper, the broken-archive
path speaks and `--plain` silences it, the boundary notice survives `--plain` (it is data, not
register), the narrator survives a hostile archive, and the two files agree on the character.

One existing test had to be **narrowed rather than repaired**: it asserted that nothing reachable from
`cmd_extract` could mention the voice, on the reasoning that extraction writes a manifest. **That
premise was wrong** — `cmd_extract` is a human-facing command that writes the manifest as a side
effect. The invariant that matters is that the code which *serialises* machine-readable output cannot
reach the voice, and that is what is checked now.
