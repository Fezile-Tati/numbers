# Restore Numbers (numbers) after an SSD swap

This repo (`Fezile-Tati/numbers`, branch `main`) is the NUMBERS 21:4-9 CLI — a
rebranded, update-safe fork of the Hermes agent CLI. It ships the **prebuilt
dashboard** (`hermes_cli/web_dist/`) so a fresh clone runs without a Node build.
Secrets and the sign-in token are NOT here — restore them from the private
`backup-config` repo.

> `numbers update` is fork-locked (only ever pulls this fork, never NousResearch
> upstream). Maintainers who want to merge upstream set `NUMBERS_MAINTAINER=1`.

## 1. Prerequisites
- Python 3.11+ (for the `.venv`), Git.
- Node.js ≥ 22.22 **only if** you need to rebuild the dashboard (the prebuilt
  `web_dist` is committed, so normally you don't).
- Go 1.26.x (to rebuild the MCP/`hermes.exe` binaries — they are git-ignored).

## 2. Clone
```
git clone https://github.com/Fezile-Tati/numbers.git numbers
cd numbers
git checkout main                # or: git checkout backup-pre-ssd-<date>
```

## 3. Python venv + launcher path
The launcher prepends the fork root to `PYTHONPATH` so `hermes_cli` +
`numbers_ext` resolve to this checkout.
```
python -m venv .venv
.venv\Scripts\pip install -e .           # or the project's documented install
```

## 4. Install / deploy the CLI
Run the installer from the Intersession repo (it deploys the skin, the Angel MCP
binary, config, and the `numbers.cmd` launcher into %LOCALAPPDATA%\numbers):
```
# from the otherway checkout:
powershell -File scripts\install_numbers_cli.ps1
```
The installer also seeds `%LOCALAPPDATA%\numbers\docs\` — drop your
`Numbers-Documentation.pdf` there (served by the dashboard /docs page).

## 5. Rebuild the git-ignored binaries
`*.exe` is ignored, so rebuild the Angel MCP server (and, if used, the vendored
harness):
```
# from otherway:
go build -o numbers-dist\artifacts\numbers-mcp-win-x64.exe .\cmd\numbers-mcp
```

## 6. Restore secrets (from the private backup-config repo)
Copy back into `%LOCALAPPDATA%\numbers` (the isolated NUMBERS_HOME):
- `backup-config/numbers/secrets/.env`          → `%LOCALAPPDATA%\numbers\.env`
- `backup-config/numbers/secrets/agent-token`   → `%LOCALAPPDATA%\numbers\agent-token`
(These hold your provider keys and the hub sign-in token.)

## 7. Run
```
numbers                      # chat CLI
numbers dashboard            # web dashboard on http://127.0.0.1:2149
```
If sign-in is stale: run `/sign-in` in the CLI (hub default
`https://127.0.0.1:3000`; override `NUMBERS_HUB_URL`).

## 8. Rebuild the dashboard (only if you changed web/src)
```
npm install --workspace web --engine-strict=false
npm run build -w web         # outputs to hermes_cli/web_dist/
```

## Not restored from here (rebuild)
- `node_modules/`, `.venv/`, `*.exe` — rebuilt by the steps above.
- Provider keys live in the restored `.env`; nothing secret is in this repo.
