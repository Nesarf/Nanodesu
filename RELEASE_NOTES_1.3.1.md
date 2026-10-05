# Nanodesu! 1.3.1

The first release made by pushing a tag.

## Releases are now made by CI

`.github/workflows/release.yml` fires on a `v*` tag and produces the two artifacts this project
has: the package on PyPI, and the standalone Windows executable attached to the GitHub release.

**PyPI publication uses Trusted Publishing, not a token.** The runner asks GitHub for an OIDC
token, PyPI verifies it came from this repository, this workflow file and the environment named
`pypi`, and issues a short-lived upload credential. **Nothing secret is stored anywhere.**

### The guards, and what each one prevents

| guard | what it prevents |
|---|---|
| the tag must equal the version in `pyproject.toml` | a release whose tag disagrees is one nobody can find by version afterwards |
| the built wheel's contents are printed | a build that succeeds says nothing about what is inside it |
| the notes file must exist for the version | a release nobody can review later |
| the executable is run with `--help` before it is attached | attaching an artifact that does not start |

The second one is not ceremony. **1.3.0 shipped without `unpack.py`** — the entire implementation
behind `--unpack` — and the build, `twine check` and the install **all reported success**. Only
listing the files inside the wheel from an installed environment showed it. The list is now part of
the release, which is why the same class of omission cannot reach the index unnoticed again.

## The standalone executable from CI is unsigned

**And the workflow appends that fact to the release notes rather than leaving it to be discovered.**

The `.whl` and `.tar.gz` on PyPI are the primary artifact and need no signature: `pip` verifies them
against the index. The Windows `.exe` attached by CI carries **no Authenticode signature**, because
the signing certificate's private key deliberately does not exist in CI — a key that a workflow can
read is a key an attacker who can open a pull request can read.

Signed builds are produced locally through the project's own signing pipeline, which uses the
S.M.Y.T. code signing certificate. If a signature matters for your use, build from source or take
the PyPI package.

## Project links

`[project.urls]` is now declared, so the PyPI page links to the repository, the issue tracker and
the changelog instead of being a dead end — which is a poor first impression for a project whose
purpose is to be useful to whoever just found it.

## Testing

61 tests, green on Python 3.9, 3.12, 3.13 and 3.14.
