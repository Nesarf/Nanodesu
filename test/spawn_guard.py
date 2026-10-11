# -*- coding: utf-8 -*-
"""Is this machine willing to start a process right now, and a skip when it is not.

**Why this exists here, and why it is the fourth tool to need it.** Under load this machine
intermittently refuses to create a process, and the refusal arrives as an exception from inside
`subprocess`:

    OSError: [WinError 6] 句柄无效。      (subprocess.py, in _execute_child)

Every test that starts a subprocess then fails, and **the failure is indistinguishable from a real
product defect** unless the test says which it is. Measured across the four tools in this family: the
same suite run repeatedly reports a different number of failures each time (4, 5, 12, 15 in one
sitting for this repository), and every one of them is this exception.

The other three already handle it:

  * `triage` — `spawn_failed` in `scan_with_clamav` / `scan_with_defender`, plus `require_spawn` in
    `test/test_feed.py`, imported by `test_handoff.py` (382 tests).
  * `ichiwatashi` — the same helper, applied to its one subprocess-dependent test.
  * `volcano-separator` — the failures there are the same shape and are documented in its handover.

So this module is the fourth implementation of one idea, and it is shared rather than copied five
times because there are five spawning test files here.

**What it deliberately does not do is hide a failure.** The probe is a real spawn of this very
interpreter: if that works, the test runs and any failure is the product's. If it does not, the test
is *skipped*, not passed — a skip is visible in the summary and a pass is not.
"""
from __future__ import annotations

import subprocess
import sys
import unittest

__all__ = ["can_spawn", "require_spawn", "spawn_required"]


def can_spawn() -> bool:
    """Whether this interpreter can start a child process at all, right now.

    The probe is the narrowest possible spawn: this same interpreter running `pass`. It needs no
    fixture, no path and no archive, so a failure here can only be the machine's.

    **Probed per call, not cached, and the first version of this file cached it.** That looked like a
    reasonable optimisation -- the condition is environmental, so why ask twice -- and it produced a
    run with 14 failures: the cache was probed while the machine was willing, the spawns later in the
    same run were not, and every one of them failed instead of skipping. The condition is
    *intermittent*, so a cached True is a claim about a moment that the next moment can falsify. A
    cached False could only ever cause a skip, but a cached True causes exactly the failure this
    module exists to remove.

    The cost is one ~30 ms spawn per guarded test. Paid deliberately.
    """
    try:
        proc = subprocess.run([sys.executable, "-c", "pass"], capture_output=True, timeout=30)
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def require_spawn(test: unittest.TestCase, what: str) -> None:
    """Skip `test` when the machine will not spawn, naming what could not be exercised.

    Called at the top of a test rather than decorating the class, because these classes mix spawning
    and non-spawning tests: `TestTheVoiceSurvivesANonUtf8Console` builds an environment and runs the
    CLI, while the rest of `test_voice.py` only reads tables. Skipping a whole class would throw away
    the checks that would have run.
    """
    if not can_spawn():
        test.skipTest(
            "this machine refused to start a process ([WinError 6] under load), so %s could not be "
            "exercised; this is the environment, not the product" % what)


def spawn_required(what: str):
    """The same, as a decorator, for a test that is nothing but a subprocess run."""
    def deco(fn):
        def wrapper(self, *a, **k):
            require_spawn(self, what)
            return fn(self, *a, **k)
        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper
    return deco
