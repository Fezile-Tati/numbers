# Bundled NUMBERS runtime build

The latest frozen build of this fork is published as a **GitHub Release**
(tag `numbers-runtime-latest`). It is not committed to the repo. It is
Windows x64 (x86-64) and self-contained (Python, Node and ripgrep inside).
Only **one** build exists at a time. Every run of
`pray\scripts\build_numbers_runtime.ps1` replaces the release and its tag,
and the tag points at the commit the build came from.

Download (the URL never changes):

```
https://github.com/Fezile-Tati/numbers/releases/download/numbers-runtime-latest/numbers-runtime-win-x64.zip
```

Or use the GitHub CLI:

```powershell
gh release download numbers-runtime-latest --repo Fezile-Tati/numbers --dir release
```

This folder is where the build writes the zip locally. `*.zip` and `*.sha256`
here are git-ignored.

## Run it

```powershell
mkdir $env:TEMP\numbers-test -Force
tar -xf release\numbers-runtime-win-x64.zip -C $env:TEMP\numbers-test
& "$env:TEMP\numbers-test\numbers-runtime-win-x64\numbers.cmd"
```

(`tar` ships with Windows 10 and 11 and reads zip files. `Expand-Archive` works
too, but on PowerShell 5.1 it takes many minutes for this many files.)

`numbers.cmd` runs the build with no install. Its state goes in `home\` next
to it. Set `NUMBERS_HOME` to use a different folder. It never touches a
personal Hermes home or an installed `%LOCALAPPDATA%\numbers`.
`BUILD-INFO.txt` records the commit it was built from. To check a download,
compare `Get-FileHash numbers-runtime-win-x64.zip` with the `.sha256` release
asset.

This is the bare harness. The full install (Angel MCP server, `config.yaml`,
the Numbers skin, `numbers` on PATH) is `pray\scripts\install_numbers_cli.ps1`.

## Modify, rebuild, test

1. Edit the overlay in `pray\numbers-dist\overlay\` (the source of truth), not
   `numbers_ext\` here. The build deletes and re-copies `numbers_ext\`.
2. Rebuild from the pray repo. The build runs `numbers_ext\tests` and
   `tests\test_overlay_parity.py` first, and it stops if either fails:
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\build_numbers_runtime.ps1
   ```
3. The build pushes this branch, then replaces the `numbers-runtime-latest`
   release with the new zip. That step needs the GitHub CLI, signed in
   (`winget install --id GitHub.cli`, then `gh auth login`). Pass `-NoPush`
   to keep a build local so you can test it first. `-SkipTests` builds are
   never published.
