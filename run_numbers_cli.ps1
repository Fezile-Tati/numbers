# RUN_NUMBERS_CLI.ps1 - Launch the isolated NUMBERS CLI (branded Hermes) for Intersession.
# NUMBERS is stock Hermes with the Angel MCP server preregistered. It runs against
# its own isolated home (.numbers-home) so the operator's personal Hermes profile
# (~/AppData/Local/hermes) is never touched.
#
# Full CLI passthrough: `numbers <anything>` maps to `hermes <anything>` —
#   numbers            -> chat (default when no args)
#   numbers model      -> pick / add a provider (all stock Hermes providers)
#   numbers auth add … -> add your own provider key
#   numbers config / mcp / skill / cron / ...  all work identically to Hermes.

param(
    [string]$Token = $env:NUMBERS_AGENT_TOKEN
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Isolated home for NUMBERS (both names point at the same dir).
$env:HERMES_HOME  = "$ScriptDir\.numbers-home"
$env:NUMBERS_HOME = "$ScriptDir\.numbers-home"
if ($Token) {
    $env:NUMBERS_AGENT_TOKEN = $Token
}

# Isolation guard: NUMBERS must NEVER use the Intersession app's own AI keys.
# Those are app-exclusive infrastructure (TTS, grading, moderation). Strip them
# from the child environment so only the user's own provider keys are visible.
foreach ($appKey in @("GEMINI_API_KEY", "DEEPSEEK_API_KEY",
                       "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
                       "ANGEL_CLOUD_API_KEY")) {
    if (Test-Path "env:$appKey") { Remove-Item "env:$appKey" }
}

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  NUMBERS 21:4-9 - Intersession Agent (branded Hermes CLI)" -ForegroundColor Green
Write-Host "  Home:   $env:HERMES_HOME" -ForegroundColor Gray
Write-Host "  Angel:  https://127.0.0.1:3000" -ForegroundColor Gray
Write-Host "============================================================" -ForegroundColor Cyan

# Make numbers_ext importable for the `python -m` sign-in / token helpers below.
if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$ScriptDir;$env:PYTHONPATH"
} else {
    $env:PYTHONPATH = "$ScriptDir"
}

# NUMBERS-owned subcommands are handled by numbers_ext (they must not collide
# with Hermes' own `auth`/`config` commands). Everything else passes straight
# through to the full Hermes CLI, so `numbers model`, `numbers mcp`, etc. work.
$first = if ($args.Count -ge 1) { "$($args[0])".ToLower() } else { "" }
$rest = if ($args.Count -ge 1) { $args[1..($args.Count - 1)] } else { @() }

switch ($first) {
    { $_ -in @("signin", "sign-in", "login", "logout") } {
        python -m numbers_ext.device_auth $first
        exit $LASTEXITCODE
    }
    "token" {
        python -m numbers_ext.tokens @rest
        exit $LASTEXITCODE
    }
    "import-hermes" {
        python -m numbers_ext.import_hermes --force
        exit $LASTEXITCODE
    }
    "" {
        # First run only: offer to import providers from an existing Hermes
        # install. Guarded + never blocks startup on failure.
        python -m numbers_ext.import_hermes --offer 2>$null
        # No arguments: start an interactive chat, matching prior behaviour.
        python "$ScriptDir\cli.py" chat
        exit $LASTEXITCODE
    }
    default {
        python "$ScriptDir\cli.py" @args
        exit $LASTEXITCODE
    }
}
