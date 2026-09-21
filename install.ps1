# install.ps1 - one-shot setup for Jev browser use with any MCP-capable LLM harness.
#
#   powershell -ExecutionPolicy Bypass -File install.ps1
#   powershell -ExecutionPolicy Bypass -File install.ps1 -RepoPath C:\path\to\clone
#
# Idempotent: safe to re-run. Never overwrites an existing .env or duplicates MCP entries.
param(
  [string]$RepoPath = "$env:USERPROFILE\Documents\browseruse",
  [switch]$SkipSync,
  [switch]$NoRegister
)
# Deliberate: "Stop" makes PS 5.1 throw on native stderr, which broke `uv sync`.
$ErrorActionPreference = "Continue"

function Step($m) { Write-Host "`n== $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "   ok   $m" -ForegroundColor Green }
function Warn($m) { Write-Host "   warn $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "   FAIL $m" -ForegroundColor Red; exit 1 }

# Writes UTF-8 without BOM. Set-Content defaults to ANSI and -Encoding UTF8 adds a BOM,
# both of which would corrupt the patched Python files.
function Write-Utf8($Path, $Value) {
  [System.IO.File]::WriteAllText($Path, $Value, (New-Object System.Text.UTF8Encoding($false)))
}

# Replaces $Find with $Replace exactly once. $Marker short-circuits on re-runs.
function Patch-File($Path, $Find, $Replace, $Marker) {
  $text = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
  if ($text.Contains($Marker)) { Ok "already patched: $(Split-Path $Path -Leaf)"; return }
  if (-not $text.Contains($Find)) { Warn "patch target not found in $(Split-Path $Path -Leaf); skipped"; return }
  Write-Utf8 $Path $text.Replace($Find, $Replace)
  Ok "patched $(Split-Path $Path -Leaf)"
}

Step "1/7  Python + uv"
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) { Fail "python not on PATH" }
& $python -m uv --version 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
  Warn "uv missing; installing with pip"
  & $python -m pip install --quiet --upgrade uv
  if ($LASTEXITCODE -ne 0) { Fail "could not install uv" }
}
$uvver = (& $python -m uv --version) 2>&1
Ok "$uvver"

Step "2/7  get the jev-ultrafast source"
if (Test-Path "$RepoPath\.git") {
  Ok "repo already present at $RepoPath"
} else {
  $parent = Split-Path $RepoPath -Parent
  New-Item -ItemType Directory -Force -Path $parent | Out-Null
  git clone --depth 1 https://github.com/browser-use/jev-ultrafast.git $RepoPath
  if ($LASTEXITCODE -ne 0) { Fail "git clone failed" }
  Ok "cloned to $RepoPath"
}

Step "3/7  apply the two required patches"
$browser = "$RepoPath\jev_ultrafast\browser.py"
$model   = "$RepoPath\jev_ultrafast\model.py"

# Google Flights fades menus in over ~2-3s. Upstream settles after 2 animation frames
# (~33ms), snapshots the menu at opacity 0, drops its options, and the agent then clicks
# the same control until it blocks. Force a real settle instead.
Patch-File $browser `
  'const autocomplete=action.kind===''fill'' && field?.getAttribute(''role'')===''combobox'';' `
  'const autocomplete=action.kind===''fill'' && field?.getAttribute(''role'')===''combobox'';' `
  'performance.now()-t0>=settle'
Patch-File $browser `
  'let frames=0, stopped=false;' `
  'const t0=performance.now(), settle=autocomplete ? 0 : 3000; let frames=0, stopped=false;' `
  'performance.now()-t0>=settle'
Patch-File $browser `
  'setTimeout(finish,autocomplete ? 200 : 50);' `
  'setTimeout(finish, 3000);' `
  'setTimeout(finish, 3000);'
Patch-File $browser `
  'if (++frames>=2 && (!autocomplete || options.some(e=>{' `
  'if (++frames>=2 && performance.now()-t0>=settle && (!autocomplete || options.some(e=>{' `
  'performance.now()-t0>=settle'

# OpenCode Go (and any client following its spec) needs a stable session id and a real
# user agent, else every text call is HTTP 400 MissingSessionID.
Patch-File $model `
  "def post_json(url, key, body):`n    for attempt in range(3):`n        try:`n            response = CLIENT.post(url, json=body, headers={""Authorization"": f""Bearer {key}"")" `
  "def post_json(url, key, body, extra_headers=None):`n    for attempt in range(3):`n        try:`n            response = CLIENT.post(url, json=body, headers={""Authorization"": f""Bearer {key}"": **{**(extra_headers or {})})" `
  'extra_headers=None'
Patch-File $model `
  "    started = time.perf_counter()`n    result = post_json(`n        base + ""/chat/completions"",`n        key,`n        {" `
  "    extra = (`n        {`n            ""x-opencode-session"": os.environ.get(""TEXT_MODEL_SESSION"", ""jev-ultrafast""),`n            ""User-Agent"": ""jev-ultrafast/0.1.0"",`n        }`n        if ""opencode.ai"" in base`n        else None`n    )`n    started = time.perf_counter()`n    result = post_json(`n        base + ""/chat/completions"",`n        key,`n        {" `
  'x-opencode-session'
Patch-File $model `
  "            ],`n        },`n    )" `
  "            ],`n        },`n        extra,`n    )" `
  'x-opencode-session'

Step "4/7  install dependencies"
if (-not $SkipSync) {
  & $python -m uv sync --directory $RepoPath 2>&1 | Out-Null
  if ($LASTEXITCODE -ne 0) { Fail "uv sync failed" }
  Ok "uv sync complete"
  & $python -m uv add --directory $RepoPath mcp 2>&1 | Out-Null
  if ($LASTEXITCODE -ne 0) { Fail "uv add mcp failed" }
  Ok "mcp SDK present"
}
Copy-Item "$RepoPath\..\browseruse\jev_mcp.py" "$RepoPath\jev_mcp.py" -ErrorAction SilentlyContinue

Step "5/7  credentials"
if (Test-Path "$RepoPath\.env") {
  Ok ".env exists; left untouched"
} else {
  Copy-Item "$RepoPath\.env.example" "$RepoPath\.env"
  Warn "created .env - fill in TEXT_MODEL_API_KEY; TYPESAFE_API_KEY only if JEV_DECISION=typesafe (laya is local, no key needed)"
}

Step "6/7  register the MCP server"
$py = "$RepoPath\.venv\Scripts\python.exe"
$server = "$RepoPath\jev_mcp.py"
if ($NoRegister) { Warn "skipped by -NoRegister" } else {
  $oc = "$env:USERPROFILE\.config\opencode\opencode.jsonc"
  if (Test-Path $oc) {
    $t = [System.IO.File]::ReadAllText($oc, [System.Text.Encoding]::UTF8)
    if ($t.Contains('"jev"')) { Ok "opencode already registered" }
    elseif ($t.Contains('"mcp": {')) {
      $block = '"mcp": {' + "`n    ""jev"": { ""type"": ""local"", ""command"": [""$($py -replace '\\','\\')"", ""$($server -replace '\\','\\')""], ""enabled"": true, ""timeout"": 600000 },"
      Write-Utf8 $oc $t.Replace('"mcp": {', $block)
      Ok "registered in opencode (restart opencode)"
    } else { Warn "could not find the mcp block in opencode.jsonc" }
  } else { Warn "opencode config not found" }

  $cx = "$env:USERPROFILE\.codex\config.toml"
  if (Test-Path $cx) {
    $t = Get-Content -LiteralPath $cx -Raw
    if ($t.Contains('[mcp_servers.jev]')) { Ok "Codex already registered" }
    else {
      $block = "`n[mcp_servers.jev]`ncommand = '$py'`nargs = ['$server']`nstartup_timeout_sec = 120`n"
      Add-Content -LiteralPath $cx -Value $block
      Ok "registered in Codex (restart Codex)"
    }
  } else { Warn "Codex config not found" }
}

Step "7/7  verify"
& $python -m uv run --directory $RepoPath pytest -q 2>&1 | Select-Object -Last 3
& $python -m uv run --directory $RepoPath ruff check . 2>&1 | Select-Object -Last 2

Write-Host "`nDone." -ForegroundColor Green
Write-Host "  1. put TEXT_MODEL_API_KEY in $RepoPath\.env (TYPESAFE_API_KEY only if JEV_DECISION=typesafe)"
Write-Host "  2. restart opencode / Codex so they pick up the jev MCP server"
Write-Host "  3. live stats: python jev_mcp.py --serve   ->  http://127.0.0.1:8767"
