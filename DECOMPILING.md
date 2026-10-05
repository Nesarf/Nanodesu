# Getting source back out

`--pyc` gives you valid `.pyc` files, which is where this tool stops: a `.pyc` is bytecode, and
turning bytecode back into Python is a different program's job. This page records what actually
works, measured on this machine rather than assumed, because the answer is less encouraging than it
looks from the outside.

## What was tested

Against `main.pyc` extracted from a **Python 3.12 / PyInstaller 6.x** build:

| tool | result |
|---|---|
| **pycdc** (C++ decompiler, built locally) | **`Bad MAGIC!` — refuses the file outright** |
| **uncompyle6** | does not support 3.12 (it stops at 3.8) |
| **decompyle3** | does not support 3.12 (it stops at 3.8) |
| **PyLingual** | not installed here; its dependency `xdis` is a git dependency and the fetch did not complete |

So on a modern build, **no decompiler available here will turn the extracted modules into source.**
That is worth stating plainly, because "extract the bytecode" sounds like the last step and it is
not.

## The distinction that matters

`pycdc`'s failure on a 3.12 file is a **refusal**, not a partial result. It prints two lines and
stops. A note in an earlier draft of this project described the outcome as "a skeleton with imports
and names", which would have been a much more useful failure and is not what happens — the
difference between a tool that half-works and one that declines is the difference between planning
around it and not.

## What does still work

* **`dis`**, from the standard library. Disassembling the extracted modules is always available and
  needs nothing installed. It is not source, but for understanding control flow — what a function
  calls, what it compares, where a string is used — it is often enough, and it cannot be version-
  blocked.
* **The strings and structure** the bytecode carries: names, constants, module layout. `nanodesu
  search --content` and `ls` reach these without any decompiler at all.
* **A decompiler matching the archive's Python version.** The `.pyc` files carry the version they
  were compiled for; a decompiler that supports *that* version will read them. For a 3.12 build that
  means finding one that claims 3.12 support, which is a moving target worth checking rather than
  assuming.

## Practical advice

Check `python nanodesu.py info <file>` first. The Python version it reports decides whether
decompilation is a realistic next step or a dead end, and knowing that before extracting saves the
detour.
