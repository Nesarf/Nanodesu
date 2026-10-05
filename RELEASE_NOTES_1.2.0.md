# Nanodesu! 1.2.0 — security release

**Replace the `nanodesu.exe` from any earlier release.** This fixes a path traversal in `pyz`
that the 1.1.0 fix did not cover.

## The escape that was left open

1.1.0 confined the CArchive entry name. The **PYZ module name** — equally attacker-controlled, out
of the same untrusted archive — was not confined at all, and went straight to the filesystem:

| Module name in the PYZ | Where it wrote |
|---|---|
| `C:/abs/PWNED` | `C:bs\PWNED.pyc` (drive-absolute) |
| `/abs/PWNED` | outside the output directory entirely |
| `../../PWNED` | crashed on an invalid path |

One safety boundary, half repaired. Module names now go through the same clamp, and a name that
had to be repaired is reported — an archive that tried to leave the output directory is a finding
about the archive.

## Also fixed

* **Claimed values are checked before use.** Entry offsets, lengths and compression flags come
  from the archive; they are now validated against it, and the table-of-contents walk records what
  it found wrong instead of silently truncating. Structural problems travel into the manifest, so
  a malformed archive still produces a usable description of itself, and a rejection names the
  reason rather than only saying it failed.
* **Decompression is bounded.** A 10 MB entry declaring 20 GB previously got 20 GB attempted. Now
  streamed with a cap (refusal in ~1 ms), plus an absolute ceiling independent of the declared
  size, because an archive that honestly declares an absurd size is still a problem.
* **`--pyc` no longer corrupts clamped entries.** It recomputed the path in a second pass and
  applied the header at the unclamped location: the header landed nowhere while the manifest
  claimed 16 bytes, so a repack stripped 16 bytes that were never added.
* **`build` no longer silently drops clamped entries.** It looked files up by the logical path
  while they had been written to the confined one.

## Verified on the real archive

836 entries, 1:1 SHA-256 after extract → build → extract, no false integrity findings, and the
repacked executable runs.

## One wording correction

The README called the repack "byte for byte". It is not: the payload is recompressed at zlib
level 9, so **entry contents** are identical and the **file** is not — 145,569,460 bytes in,
145,198,417 out on the archive above. That distinction matters when comparing a repacked sample
to a reference by hash, and the README now states it.

## Testing

41 tests (was 21), green on Python 3.9, 3.12, 3.13 and 3.14.

---

## This build is signed

`nanodesu.exe` carries an Authenticode signature with the publisher **S.M.Y.T.**, timestamped by a
DigiCert responder so the signature remains verifiable after the certificate's own expiry.

The certificate is **self-signed**, which means:

* on a machine where `smyt-codesign.cer` is installed as a trusted publisher, Windows reports the
  publisher as S.M.Y.T. and the signature verifies
* on any other machine it is an untrusted signature: it identifies the publisher honestly, but it
  does not remove a SmartScreen warning. Only a certificate from a recognised CA does that, and for
  an organization certificate the CA fills in the organization field from a verified legal entity.

If you want to verify it locally, the public certificate is in the repository's signing directory
alongside the script that produced it. The private key is not published and never will be.

Repacking a signed binary produces an **unsigned** one, because the signature covers the whole PE
file and cannot survive a rewrite of its contents. Sign last.
