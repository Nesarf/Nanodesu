# -*- coding: utf-8 -*-
"""The version must not drift between the module and the package metadata.

Run:
    python -m unittest discover -s test -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


class TestVersionDoesNotDrift(unittest.TestCase):
    """The module and the package must not disagree about what version this is.

    It happened: the module reported 0.8.0 while pyproject.toml said 1.3.0, across five releases.
    Nothing caught it because nothing compared them -- the constant was only ever read by code that
    did not care, and the packaging metadata was only ever read by tools that never looked at the
    module. Importing the built wheel and asking it is what found it.
    """

    def test_the_source_constant_matches_pyproject(self):
        import re
        here = Path(__file__).resolve().parent.parent
        pyproject = (here / "pyproject.toml").read_text(encoding="utf-8")
        declared = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M).group(1)
        module = None
        for candidate in (here / "torikago.py", here / "nanodesu.py"):
            if candidate.is_file():
                module = candidate.read_text(encoding="utf-8")
                break
        self.assertIsNotNone(module, "no module found to compare")
        source = re.search(r'^_SOURCE_VERSION\s*=\s*"([^"]+)"', module, re.M)
        self.assertIsNotNone(source, "_SOURCE_VERSION is missing from the module")
        self.assertEqual(source.group(1), declared,
                         "the module's fallback version and pyproject.toml disagree; this is the "
                         "drift that went unnoticed for five releases")
