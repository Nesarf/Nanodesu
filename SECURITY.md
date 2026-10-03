# Security policy

## What this tool does with the files you give it

Nanodesu! reads an archive and writes files. It **never executes the program it opens**, and
that is the whole of its safety argument: unpacking is a byte-level problem, so a file that
is never run cannot act. There is no `CreateProcess` call in the code and no flag that adds
one. If you find a path that runs the target, that is a serious bug and I want to hear about
it.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting on this repository
(**Security → Report a vulnerability**). It opens a confidential channel; please do not file
a public issue for something exploitable.

Useful in a report:

* what an attacker controls (an archive, a file name, the command line, the environment)
* the smallest input that shows the problem
* what the tool does that it should not — file written outside the output directory, memory
  or CPU consumed without bound, code executed, data disclosed

I aim to acknowledge within a few days and to ship a fix with a test that fails without it.

## Things worth knowing before you send an archive

* **Extraction is confined to the output directory.** An entry named `../../x` is repaired
  rather than followed, and the repair is reported on stdout and recorded in
  `_archive_manifest.json`. This was a real escape, fixed in the commit that added the
  regression tests for it.
* **Names from the archive are untrusted input.** If you find any other place where an entry
  name becomes a path, that is a bug of the same class.
* **A malformed archive should fail, not hang.** A crafted length field should not produce an
  unbounded read or an unbounded allocation. Reports of either are welcome.
* **Decompiling output is not source.** `--pyc` produces valid bytecode files for a
  decompiler, but a decompiler's output is a hypothesis. Do not treat it as the original
  code, and do not run what it produces.

## What this tool does not defend against

* **Anything already running on your machine.** It is a file-format tool, not an endpoint
  security product.
* **An archive that is a genuine program you then choose to run.** Nanodesu! tells you what
  is inside; the decision to execute it is yours, and it is made outside this tool.
* **A payload that only ever exists in memory.** If it never touches disk, there is no file
  to open — no file-based tool can help with that.

## Supply chain

* No dependencies beyond the Python standard library, so there is no dependency tree to
  compromise.
* No network access at any point: the tool never fetches anything, including reputation data.
* CI actions are pinned to commit SHAs, and the CI token is read-only.
* Releases are built from the tagged commit; you can verify by unpacking the released
  executable with the tool itself.
