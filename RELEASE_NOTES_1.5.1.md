# Nanodesu! 1.5.1

The voice broke the tool on Windows, and the fix is a reconfiguration plus the test that was missing.

## What happened

1.5.0 was published, and its own executable then failed a sanity check on the build machine:

```
UnicodeEncodeError: 'charmap' codec can't encode characters in position 3085-3096
```

On Windows `sys.stdout` is bound to the **console code page** — cp1252 on a stock CI runner — and
the persona writes Japanese. **Every line the tool printed through all of 1.3.x was ASCII**, so
nothing had ever exercised this. **101 passing tests said nothing, because they all ran where stdout
was already UTF-8.**

## The fix

The three text streams are reconfigured to UTF-8 with `errors="replace"`, and only when the voice is
actually on:

* **not** in `--plain`, because plain output is what a script parses and it must not depend on a
  persona decision
* **not** for `--json` and friends, which never gained a persona in the first place
* `errors="replace"` rather than `strict`, because **an unprintable character must never be the
  thing that stops a security analysis**

## Reproduced before it was fixed

The failure was reproduced exactly rather than assumed, by disabling the fix and forcing the
encoding:

| condition | result |
|---|---|
| persona + cp1252, **fixed** | rc 0, Japanese printed |
| persona + cp1252, **fix disabled** | rc 1, `UnicodeEncodeError ... position 3085-3096` |

That is the CI message character for character, which is what makes this a fix rather than a guess.

## Testing

**105 tests** (was 101), green on Python 3.9, 3.12, 3.13 and 3.14. Four new ones check the voice
under a forced non-UTF-8 console through a real subprocess — the default register, `--plain`, the
no-command path — and one checks the opposite direction: on a UTF-8 console the Japanese must arrive
intact and **not** as replacement characters, so the fix cannot quietly degrade the normal case.
