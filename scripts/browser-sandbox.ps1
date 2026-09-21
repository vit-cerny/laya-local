# browser-sandbox.ps1 - launch the hardened, isolated opencode browser on 9222.
# Uses a separate profile (jev-*-sandbox-profile) that never sees your normal browsing
# session, with extensions/sync/updater/telemetry disabled. Profile isolation, not an OS
# sandbox. Reset it at any time (wipes the sandbox profile) with -Reset.
#
# Usage:
#   .\scripts\browser-sandbox.ps1            # start the sandboxed browser (JEV_SANDBOX=1)
#   .\scripts\browser-sandbox.ps1 -Reset     # wipe the sandbox profile, then start fresh
param(
    [switch]$Reset
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"

if ($Reset) {
    Write-Host "wiping sandbox profile..."
    & $Python "$Root\jev_mcp.py" --wipe-sandbox
}

$env:JEV_SANDBOX = "1"
& $Python -c "from jev_browser import ensure_sandbox_chrome, find_browser, sandbox_profiles, active_profile; b, s = ensure_sandbox_chrome(); print('browser :', b); print('state   :', s); print('profile :', active_profile()); print('sandbox :', [str(p) for p in sandbox_profiles()])"

Write-Host ""
Write-Host "Sandboxed opencode browser is live on port 9222 (JEV_SANDBOX=1)."
Write-Host "All jev_search / examples/run.py calls now use this isolated profile."
Write-Host "Reset anytime:  .\scripts\browser-sandbox.ps1 -Reset"