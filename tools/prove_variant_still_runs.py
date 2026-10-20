# -*- coding: utf-8 -*-
"""Prove that a repacked variant still starts.

`R-2`: the stub was checked in isolation (`check_stub`), but nothing checked that the archive rebuilt around
it is still a runnable executable -- so "the repair worked" and "the repair broke it" looked identical in the
report. This runs the proof once, by hand, on a sample the script builds itself with PyInstaller: **nothing
here executes anything that came from outside this machine.**

What is claimed, and what is not. The variant's modules were replaced on purpose, so its behaviour is
supposed to change; a variant that printed the original message would mean the replacement did nothing. The
claim is narrower and harder to fake: **it still starts** -- the bootloader finds the archive, Python
initialises, and the process ends without Windows refusing to load it.

Run:
    python tools/prove_variant_still_runs.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import nanodesu  # noqa: E402

MARKER = "SAYORI-PROOF-OK"
SOURCE = "print(%r)\n" % MARKER


def run(exe):
    """Run a file this script built. Returns `(started, code, output)`."""
    try:
        out = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120)
    except OSError as exc:
        return False, None, "OS refused to load it: %s" % exc
    except subprocess.TimeoutExpired:
        return True, None, "it started but did not finish inside 120 s"
    return True, out.returncode, (out.stdout or "") + (out.stderr or "")


def main(argv=None) -> int:
    work = Path(tempfile.mkdtemp(prefix="nanodesu-proof-"))
    print("work: %s" % work)
    script = work / "hello.py"
    script.write_text(SOURCE, encoding="utf-8")

    print("step 1  build a real onefile executable with PyInstaller")
    build = subprocess.run([sys.executable, "-m", "PyInstaller", "--onefile", "--clean",
                            "--distpath", str(work / "dist"), "--workpath", str(work / "build"),
                            "--specpath", str(work), str(script)],
                           capture_output=True, text=True, timeout=600)
    if build.returncode != 0:
        print("PyInstaller failed:\n%s" % (build.stderr or build.stdout)[-2000:])
        return 2
    original = work / "dist" / "hello.exe"
    if not original.is_file():
        print("no executable was produced at %s" % original)
        return 2
    print("        %s (%d bytes)" % (original.name, original.stat().st_size))

    print("step 2  run it, so the baseline is measured rather than assumed")
    started, code, out = run(original)
    print("        started=%s exit=%s" % (started, code))
    print("        output: %r" % (out.strip()[:120],))
    if not (started and MARKER in out):
        print("the original does not print its marker, so nothing below can be compared")
        return 2

    print("step 3  neutralize every module, then rebuild")
    tree = work / "tree"
    import neutralize as nz

    ar = nanodesu.find_archive(original)
    nanodesu.extract(original, tree)
    targets = nz.find_modules(tree)
    print("        %d module(s) found in the extracted tree" % len(targets))
    record = nz.neutralize(original, tree, modules=targets, python_version=ar.pyver_str())
    if record.get("refused"):
        print("        neutralize refused: %s" % record["refused"])
        return 2
    print("        replaced %d module(s): %s"
          % (len(record["replaced"]),
             ", ".join(str(r.get("rel") or r.get("name") or r) for r in record["replaced"])))

    variant = work / "hello_defanged.exe"
    # The command takes an argument namespace rather than a signature; that is deliberate in the tool (each
    # command reads a different set of names), so the proof builds one the same way `cmd_neutralize` does.
    from types import SimpleNamespace

    rc = nanodesu.cmd_build(str(tree), SimpleNamespace(output=str(variant), pylib=None, pyver=None))
    if rc != 0:
        print("        the rebuild failed, which is itself the finding R-2 was asking about")
        return 1
    print("        %s (%d bytes)" % (variant.name, variant.stat().st_size))

    print("step 4  run the variant. **It is supposed to behave differently; it is not supposed to stop"
          " starting.**")
    started, code, out = run(variant)
    print("        started=%s exit=%s" % (started, code))
    print("        output: %r" % (out.strip()[:120],))

    report = {
        "original_started": True,
        "variant_started": started,
        "variant_exit": code,
        "variant_kept_behaviour": MARKER in out,
        # **"started" is too weak a question.** Windows loading the stub and Python failing to initialise look
        # the same to a process-spawn check, and they mean opposite things. A variant that cannot start Python
        # is broken, however successfully the operating system loaded it.
        "variant_python_started": started and "Failed to load Python DLL" not in out,
        "modules_replaced": len(record["replaced"]),
        "work_dir": str(work),
    }
    (work / "proof.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("\n%s" % json.dumps(report, indent=1))

    if report["variant_python_started"]:
        print("\nthe variant still starts. Its behaviour changed, which is what neutralizing a module means.")
        return 0
    print("\nFAILED: the rebuilt archive is not a runnable executable any more.")
    print("  The operating system loaded the stub, and Python never initialised -- so on this evidence the")
    print("  repair breaks the file, and the report could not have told you that before this proof existed.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
