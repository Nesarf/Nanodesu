"""The boundary that travels with every result Nanodesu produces.

Modelled on the same rule the Tor/Firefox auditor uses: **one constant, attached to every path a
caller can take, errors included.** Attached in a single convenient place it would be missing from
exactly the results a careless reader is most likely to over-read.

What it guards against here is specific to this tool. Nanodesu unpacks unknown executables, so a
successful run produces a directory full of somebody else's code and a report saying what is in it.
The obvious over-readings are:

    "I extracted it, so I have the source"          -- bytecode is not source
    "The repack is byte-identical, so it is clean"  -- that is fidelity, not safety
    "It unpacked without errors, so nothing is hidden"
                                                    -- the interesting payloads are the ones that
                                                       are NOT stored as archive entries

Each line below exists because it is a thing a reader would otherwise assume.
"""
from __future__ import annotations

BOUNDARY_NOTICE = [
    "This tool never executes, loads or launches the archive it reads. The only process it can "
    "create is a Python interpreter asked for its own bytecode magic number, and only when a .pyc "
    "header is needed.",
    "It does NOT address: whether the program is safe, what it does when run, or whether the "
    "extracted bytes are what its author intended. Extraction reports structure, not intent.",
    "It reads [the CArchive table of contents, stored entry bytes, and the PYZ]. It is NOT a "
    "decompiler, NOT a malware detector, and NOT a packer for anything except the archive it "
    "came from.",
    "A clean extraction is NOT proof of anything. Specifically: a byte-identical repack proves "
    "fidelity and not safety; a bare marshalled code object is not source; and content that never "
    "appears in the table of contents is invisible to this tool entirely.",
]


def with_boundary(result: dict) -> dict:
    """Attach the notice to a result dict.

    Wrapped rather than remembered. The one place it is easy to forget is the error path, and an
    error result that reads like a clean one is the failure this whole module exists to prevent.
    """
    if isinstance(result, dict):
        result = dict(result)
        result["boundary_notice"] = BOUNDARY_NOTICE
    return result


def format_boundary() -> str:
    """The notice as text, for a console."""
    return "\n".join("  - %s" % line for line in BOUNDARY_NOTICE)
