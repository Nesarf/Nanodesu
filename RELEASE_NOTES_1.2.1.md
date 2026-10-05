# Nanodesu! 1.2.1

The 1.2.0 security fixes, published through the new release pipeline.

## What is different from 1.2.0

**Nothing in the tool.** This is the same code, built and signed by
`smyt-release.ps1`, which runs build → sign → verify → hash → publish with every step
checked rather than assumed:

| step | what is verified |
|---|---|
| build | the file exists after PyInstaller reports success |
| sign | `signtool` exits 0 **and a timestamp was actually obtained** |
| verify | the signature is present and the subject is S.M.Y.T. |
| hash | recorded before and after signing, since Authenticode changes the file |
| publish | **GitHub's reported digest equals the local one** |

The last check is the one that earns its place. An upload that truncated or re-encoded would
otherwise be indistinguishable from a good one, and the point of publishing a hash is that it
matches.

## Signed

```
subject : O=S.M.Y.T., CN=S.M.Y.T. Code Signing
status  : Valid
timestamp : DigiCert RFC3161 responder
```

No country field, deliberately: S.M.Y.T. claims no nationality, and writing one would be a false
statement of exactly the kind a signature exists to prevent.

Self-signed, and that is a position rather than a shortcoming. A certificate authority writes the
organization field from a **verified legal entity** — it asks for company registration papers — so
an unaligned academic seminar is not merely unable to afford one, it is outside what that system
describes. On a machine that trusts this certificate the publisher reads **S.M.Y.T.**; on any other
machine the signature is untrusted and does not remove a SmartScreen warning. What it proves is that
the build came from S.M.Y.T. and has not been altered since, and the signing record carries the
SHA-256 to check that against.

Sign last, always: the signature covers the whole PE file and cannot survive a rewrite. Repacking a
signed binary yields an unsigned one.
