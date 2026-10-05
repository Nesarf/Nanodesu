# Nanodesu! 1.5.0

A third register, off by default, and the reason it is a separate document.

## Three registers now

| register | how to get it |
|---|---|
| **default** | — (Lilith, warm) |
| **`--plain`** | no persona at all |
| **`--nsfw`** | Lilith, and the subject of an analysis is something she plays with |

**The default did not move.** The adult prose is reachable only by passing the flag or setting
`NANODESU_NSFW=1`, so someone who never asked for it never sees it. And **`--plain` overrides
`--nsfw`**, which is not arbitrary: `--plain` is a promise that the output is impersonal, so silence
outranks heat.

## Why it is a separate document and a separate table

There was a tempting version of this where the adult lines went into `PERSONA.md` behind a flag. It
is the wrong shape. **The moment "she can be lewd" is written into the main specification, everyone
who reads that specification receives it** — and what they are holding is a security tool. They
should not have to switch off something they never switched on.

So: `PERSONA.md` and `CHARACTER_LILITH.md` stay clean, and `PERSONA.nsfw.md` is where the rest lives.

## The design, which is the substance

**Her target is the sample. Always.** Not the reader.

That single rule is what makes this something worth building rather than something to be embarrassed
about. The tool examines *hostile, packed, lying files*; she does not pry them open and she does not
bite them — she **dissolves them in comfort until they lose their shape and hand over what is
inside**. Which is the completion of the character's foundation: *she is proud of not needing to
bite.* Not restraint. A better method.

Four beats, and they are already the tool's own four steps:

| her | the analysis |
|---|---|
| 踩（probe） | 探查 |
| 夹（press） | 确认结构 |
| 化（dissolve） | 解包 |
| 交出（hand over） | 报告 |

**So this is not a skin over the tool.** It gives the steps the tool already has a form you can feel.

And because the object is a file, nothing is executed, nothing is moved, and **her "play" *is* the
report** — written through that metaphor. The person running it watches an expert work; they are not
the one being worked on.

## A defect found while wiring it

`say()` returns the key **itself** when neither table has it. Asking for an adult line with the
register off therefore printed the literal string `handed_over` — the character would have said
`handed_over` in the middle of a sentence.

Three keys in the normal table had the same shape from the other direction: their plain value *was*
the key name (`"unpacking": (…, "unpacking")`). Indistinguishable at the call site from the failure.

Fixed both. And the tests now check **every key in every register** rather than the ones anyone
thought to look at.

## Testing

**101 tests** (was 93), green on Python 3.9, 3.12, 3.13 and 3.14. New: the adult register is off by
default, `--plain` beats `--nsfw`, the adult lines differ from the normal ones for every key, and no
key anywhere can leak its own name into the output.
