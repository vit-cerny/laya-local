# laya-up.ps1 - one command to make Laya (local Jev) ready: model check, Chrome for browser use,
# optional model warmup, optional Laya Console GUI. Pure ASCII for PowerShell 5.1.
#
# Usage:
#   .\scripts\laya-up.ps1                  # verify + start Chrome
#   .\scripts\laya-up.ps1 -Warmup          # also preload the model (~35s, then first use is instant)
#   .\scripts\laya-up.ps1 -Console         # also start the manual-prompt GUI at http://127.0.0.1:8768
#   .\scripts\laya-up.ps1 -Warmup -Console # everything
param(
    [switch]$Warmup,
    [switch]$Console,
    [switch]$Dashboard,
    [switch]$Sandbox
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Uv = "C:\Users\witek\AppData\Local\Programs\Python\Python314\Scripts\uv.exe"
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Model = Join-Path $Root "models\laya-typed-decisions\model.safetensors"
$Chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$Profile = Join-Path $env:USERPROFILE ".cache\jev-chrome-profile"

Write-Host "== Laya (local Jev) startup =="

if (-not (Test-Path $Python)) {
    Write-Host "[1/4] venv missing - running uv sync (first run takes a while)..."
    & $Uv sync --directory $Root
} else {
    Write-Host "[1/4] venv OK"
}

if (-not (Test-Path $Model)) {
    Write-Host "[2/4] WARNING: $Model not found."
    Write-Host "       Download it (803 MB, sha256 4fa56de7...) into models\laya-typed-decisions\:"
    Write-Host "       https://huggingface.co/convaiinnovations/laya/tree/main/typed-decisions"
    Write-Host "       (or point JEV_LAYA_MODEL in .env at any local laya checkpoint dir)"
} else {
    Write-Host "[2/4] model OK: $Model"
}

# 3. dependency + GPU probe (fast, no model load)
try {
    $probe = & $Python -c "import torch; print('torch', torch.__version__, '| cuda', torch.cuda.is_available(), '|', (torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'cpu'))"
    Write-Host "[3/4] $probe"
} catch {
    Write-Host "[3/4] dependency probe failed: $_"
    Write-Host "       run: uv sync --directory $Root"
}

# 4. Chrome on 9222 for browser use (dedicated profile, never the default one)
if ($Sandbox) {
    Write-Host "[4/4] launching hardened sandbox browser (JEV_SANDBOX=1)..."
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "browser-sandbox.ps1")
} else {
    $alive = $false
    try { $null = Invoke-RestMethod "http://127.0.0.1:9222/json/version" -TimeoutSec 2; $alive = $true } catch { }
    if (-not $alive) {
        if (Test-Path $Chrome) {
            Start-Process $Chrome -ArgumentList "--remote-debugging-port=9222", "--user-data-dir=$Profile", "--no-first-run", "--no-default-browser-check", "about:blank"
            Start-Sleep -Seconds 3
            try { $null = Invoke-RestMethod "http://127.0.0.1:9222/json/version" -TimeoutSec 3; Write-Host "[4/4] Chrome started on 9222 (jev profile)" }
            catch { Write-Host "[4/4] WARNING: started Chrome but 9222 not answering yet" }
        } else {
            Write-Host "[4/4] WARNING: Chrome not found at $Chrome - set JEV_CHROME to a Chromium fork"
        }
    } else {
        $activeProfile = & $Python -c "from jev_browser import active_profile; print(active_profile() or 'unknown')" 2>$null
        Write-Host "[4/4] Chrome already on 9222 (profile: $activeProfile)"
        if ($activeProfile -match "sandbox") {
            Write-Host "      NOTE: the sandboxed profile is active. For the normal profile, close Chrome on 9222 first (or run: .\scripts\browser-sandbox.ps1 to switch profiles)."
        }
    }
}

if ($Warmup) {
    Write-Host "== warming Laya model (~35s first load) =="
    & $Python -c "import os; os.environ['JEV_DECISION']='laya'; from laya_ask import agent; agent(); print('model warm: first decision is instant')"
}

if ($Console) {
    Write-Host "== starting Laya Console at http://127.0.0.1:8768 =="
    Start-Process $Uv -ArgumentList "run", "--env-file", "$Root\.env", "$Root\laya_console.py" -WorkingDirectory $Root
}

# optional control dashboard (toggles + goal runner + autonomous)
if ($Dashboard) {
    Write-Host "== starting Laya Control Center at http://127.0.0.1:8769 =="
    Start-Process $Uv -ArgumentList "run", "--env-file", "$Root\.env", "$Root\laya_dashboard.py" -WorkingDirectory $Root
}

Write-Host ""
Write-Host "Ready. Use Laya in opencode:"
Write-Host "  1. restart opencode (MCP 'jev' reloads; add JEV_LAYA_PREWARM=1 to your env/launch to skip first-call load)"
Write-Host "  2. tools: jev_search (browser use), jev_cu (computer use), laya_ask (typed decisions)"
Write-Host "CLI without opencode:"
Write-Host "  uv run --env-file .env python jev_cu.py --goal '...' --go     (computer use)"
Write-Host "  uv run --env-file .env python examples/run.py --url URL --goal '...'   (browser use)"
Write-Host "  uv run --env-file .env python laya_ask.py '...' --preset triage"
Write-Host "  uv run --env-file .env python laya_voice.py                   (voice control)"
Write-Host "GUI: http://127.0.0.1:8769 (control center, if -Dashboard was used)"
Write-Host "     http://127.0.0.1:8768 (goal console, if -Console was used)"
Write-Host "     uv run jev  ->  http://127.0.0.1:8766 (browser agent inspector)"