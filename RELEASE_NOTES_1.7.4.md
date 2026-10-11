# Nanodesu 1.7.4

Two hardening items that turned out to be one genuine defect each.

## Device names are not filenames

`NUL`, `CON`, `PRN`, `AUX`, `COM1`-`COM9` and `LPT1`-`LPT9` address devices on Windows, so writing to one
either fails or **does not create a file at all**. `confining()` sanitised traversal, drive letters and
wildcards and said nothing about these — and **an extraction that reports a member as written while
nothing was created is worse than a refusal**, because the report is what the operator keeps.

They are now prefixed rather than replaced, because a replacement merges names.

**And the first version of that fix escaped every leading underscore, which broke ordinary names.** A
test caught it. The escaping is now confined to device names, which leaves one ambiguity: `NUL` and
`_NUL` reach the same place. **Stated rather than solved** — it needs an archive holding both, and
repairing names nobody has is the worse trade. There is a test that pins the documented case.

## A bare name is no longer resolved

`shutil.which` resolved `python3.12` off PATH and the result was executed — **so an archive's own version
string decided which executable this tool would start, and the archive is the untrusted input.**

That lookup is gone from both places it existed. **Nothing about the archive gets to choose a program
that runs.**

What is given up is small and bounded: the built-in table covers 3.7 through 3.14, so the only cost is
reading the exact magic for a version the table does not know. `NANODESU_PYTHON` still works, and is the
supported way to say where an interpreter lives — **precisely because naming it is a decision a person
makes rather than one a file makes.**

## Testing

**180 tests** (was 172), green on Python 3.9, 3.12, 3.13 and 3.14. Eight new: device names are prefixed,
ordinary names stay distinct, the ambiguity is the documented one and only that, traversal defences
survive, no source file calls `shutil.which`, a bare template is refused, and the table is what makes
refusing affordable.


---

### What is signed, and what a signature is worth here

The `.whl` and `.tar.gz` on PyPI are the primary artifacts and carry no Authenticode signature:
`pip` verifies them against the index hashes, which is a stronger guarantee than a signature on a file
nobody is obliged to check. Nothing about that has changed.

**The Windows executable attached to this release is now signed**, and the distinction that matters is
where the key lives: **it is signed locally, not in CI.** The certificate's private key deliberately
does not exist in CI, so an automated release cannot produce a signed executable and **no future release
will carry one unless someone runs the local pipeline.** This release's asset is what it is because
that was done for it, not because a workflow will keep doing it.

| what | value |
|---|---|
| signer | `O=S.M.Y.T., CN=S.M.Y.T. Code Signing` |
| certificate thumbprint | `4EA54E72A1EEFAE280B99631F7680DBFF461C4F1` |
| timestamp | `http://timestamp.digicert.com`, so the signature outlives the certificate |
| SHA-256 | `F0C7F3EAE805DD5A85B9E4714A6335C92288295D04B5E4B201484B383812A7B2` |
| the same value, as a sidecar | `nanodesu.exe.sha256`, which `sha256sum -c nanodesu.exe.sha256` accepts |

**What that proves, and what it does not.** *On a machine that trusts the S.M.Y.T. root* — this one —
the signature verifies and Windows attributes the file to S.M.Y.T. rather than to an unknown publisher.
**On anyone else's machine it will not verify, and SmartScreen will still warn about the download.** The
certificate is self-signed, so its root of trust is S.M.Y.T.'s own, and a stranger's Windows has no
reason to accept it.

The signature does prove two things to anyone, without needing the certificate to be trusted at all:
which key signed these bytes, and that these bytes have not changed since. **If a verifying signature is
what you need, the PyPI package is the path** — `pip`'s hash check is the stronger claim and it does not
depend on trusting anybody's root.
