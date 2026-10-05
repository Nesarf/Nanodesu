# Nanodesu! 1.3.0

A library API, so a caller gets data instead of prose.

## The caller is real

`triage` unpacks PyInstaller samples by loading this module and calling into it. It was calling
`main()` with an argv list — which works, `main()` returns an int and the caller handles the
`SystemExit` — but it means a program is talking to a program through a command line.

```python
import nanodesu

nanodesu.inspect_archive("sample.exe")
# {'entry_count': 836, 'python_version': '3.12', 'integrity': [], ...}

nanodesu.list_entries("sample.exe")          # table of contents, in archive order
nanodesu.read_file("sample.exe", "struct")   # one entry's bytes
nanodesu.verify_archive("sample.exe")        # {'ok': True, 'decompressed': 836, ...}
nanodesu.extract("sample.exe", "out/")       # {'ok': True, 'written': 836, ...}
```

Where a command prints a message and exits, these raise **`PyInstallerError`** carrying the path
it was given, so a caller can tell *"this is not an archive"* from *"this file is missing"*.
`read_file` raises `KeyError` for an absent name. Importing the module prints nothing.

## It is not a second implementation

The API is built on the pure layer — `find_archive`, `read_entry`, `wrap_pyc`, `confining` —
rather than on the `cmd_*` functions, because those already returned data rather than prose. The
parsing, decompression and path handling are **the same functions the commands use**. The two
layers were already separate; this only gives the lower one a public face.

The contract that matters: **`extract()` writes the manifest that `build` accepts.** Extracting
through the API and repacking through the CLI round-trips, and a test holds it rather than a
comment.

## Two bugs the new tests caught

* **Manifest entries were paired with the table of contents by zipping two lists afterwards** —
  which would silently mis-pair every entry after the first failure. Now recorded as each file is
  written, so the manifest cannot describe files that are not there.
* **The module could not be imported by path at all under Python 3.14.** Its dataclasses resolve
  annotations through `sys.modules[cls.__module__]`, which returns `None` for an unregistered
  module. `triage/unpack.py` already registered before executing; the test did not. That is how
  the test found it.

`VERSION` now lives in the source, because a caller loading this by path has no package metadata
to read — which is exactly how `triage` finds it, and why its `tool_version` field was `None`.

## Signed

Built and signed through the S.M.Y.T. release pipeline: build → sign → verify → hash → publish,
each step checked, with the published digest compared against the local file.

```
O=S.M.Y.T., CN=S.M.Y.T. Code Signing     status: Valid
timestamped by a DigiCert RFC3161 responder
```

## Testing

60 tests (was 41), green on Python 3.9, 3.12, 3.13 and 3.14.
