<#
.SYNOPSIS
    connect-claude-code.ps1 — point Claude Code at the workshop AI gateway
    (native Windows PowerShell port of connect-claude-code.sh).

.DESCRIPTION
    You will be given a personal key in class (it looks like:  sk-xxxxxxxx ).
    Run this once with that key and it will install Claude Code (if needed),
    wire it to the workshop gateway, and launch it.

    Works on Windows PowerShell 5.1 and PowerShell 7+.
    macOS / Linux / WSL / Git-Bash: use connect-claude-code.sh instead.

.PARAMETER Key
    Your workshop key (starts with sk-). If omitted, the script asks for it
    (or reads $env:WORKSHOP_KEY).

.PARAMETER Persist
    Also remember the connection in your PowerShell profile so new terminals
    are connected automatically. Do NOT use this on a shared lab machine — it
    writes your personal key into $PROFILE for the next user to inherit.

.EXAMPLE
    .\connect-claude-code.ps1 sk-your-key

.EXAMPLE
    .\connect-claude-code.ps1 sk-your-key -Persist
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)][string]$Key,
    [switch]$Persist
)

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# ---- workshop settings (instructor: change here if the gateway/model moves) --
$Gateway = 'https://ai.simonian.online'                      # no /v1 — Claude Code adds the path
$Model   = if ($env:WORKSHOP_MODEL) { $env:WORKSHOP_MODEL } else { 'gemma26' }
$MaxOut  = if ($env:CLAUDE_CODE_MAX_OUTPUT_TOKENS) { $env:CLAUDE_CODE_MAX_OUTPUT_TOKENS } else { '8192' }
# -----------------------------------------------------------------------------

function Write-Ok   { param([string]$m) Write-Host "v $m" -ForegroundColor Green }
function Write-Warn { param([string]$m) Write-Host "! $m" -ForegroundColor Yellow }
function Write-Err  { param([string]$m) Write-Host "x $m" -ForegroundColor Red }
function Write-Info { param([string]$m) Write-Host ". $m" -ForegroundColor DarkGray }

# ---- 1. resolve the key (arg, env, or prompt) -------------------------------
if (-not $Key) { $Key = $env:WORKSHOP_KEY }
if (-not $Key) { $Key = Read-Host 'Paste your workshop key (handed out in class)' }
$Key = "$Key".Trim()
if (-not $Key) { Write-Err "No key given. Ask the instructor for your key and run again."; exit 1 }
if ($Key -notlike 'sk-*') { Write-Warn "That doesn't look like a workshop key (they start with 'sk-'). Continuing anyway." }

# ---- 2. preflight: does the key work against the gateway? -------------------
Write-Host ""
Write-Info "Checking your key against $Gateway ..."
$body = @{ model = $Model; max_tokens = 4; messages = @(@{ role = 'user'; content = 'hi' }) } | ConvertTo-Json -Compress -Depth 5
$code = 0; $respBody = ''
try {
    $resp = Invoke-WebRequest -Uri "$Gateway/v1/messages" -Method Post -TimeoutSec 20 -UseBasicParsing `
        -Headers @{ Authorization = "Bearer $Key"; 'anthropic-version' = '2023-06-01' } `
        -ContentType 'application/json' -Body $body
    $code = [int]$resp.StatusCode
    $respBody = [string]$resp.Content
} catch {
    if ($_.Exception.Response) { try { $code = [int]$_.Exception.Response.StatusCode } catch {} }
    if ($_.ErrorDetails -and $_.ErrorDetails.Message) { $respBody = [string]$_.ErrorDetails.Message }
}

switch ($code) {
    200 { Write-Ok "Your key works and the gateway answered. You're good to go." }
    { $_ -in 401, 403 } {
        if ($respBody -match 'outside_workshop_hours') {
            Write-Ok "Your key is VALID."
            Write-Warn "The gateway is closed right now — it only opens during your class hours."
            Write-Info "Re-run during the session and it will connect. (This is expected outside class.)"
        } else {
            Write-Err "The gateway rejected your key (HTTP $code). It may be mistyped or expired."
            Write-Info "Double-check the key with your instructor, then run this script again."
            exit 1
        }
    }
    0 { Write-Err "Couldn't reach $Gateway. Check your internet connection and try again."; exit 1 }
    default {
        Write-Warn "Unexpected response (HTTP $code) — setup will continue, but tell the instructor if 'claude' fails."
        if ($respBody) { Write-Host ($respBody.Substring(0, [Math]::Min(300, $respBody.Length))) }
    }
}

# ---- 3. make sure Claude Code is installed ----------------------------------
Write-Host ""
if (Get-Command claude -ErrorAction SilentlyContinue) {
    Write-Ok "Claude Code already installed ($((claude --version 2>$null | Select-Object -First 1)))."
} else {
    Write-Info "Installing Claude Code ..."
    try {
        Invoke-RestMethod -Uri 'https://claude.ai/install.ps1' -UseBasicParsing | Invoke-Expression
    } catch {
        Write-Warn "claude.ai/install.ps1 failed: $($_.Exception.Message)"
    }
    # Make a freshly-installed binary discoverable in THIS session before we
    # judge whether the install actually worked.
    $localBin = Join-Path $env:USERPROFILE '.local\bin'
    if ((Test-Path $localBin) -and ($env:Path -notlike "*$localBin*")) { $env:Path = "$localBin;$env:Path" }

    # Fall back to npm only if the installer didn't produce a usable 'claude'.
    # Never assume success from the installer's exit — resolve the command.
    if (-not (Get-Command claude -ErrorAction SilentlyContinue) -and (Get-Command npm -ErrorAction SilentlyContinue)) {
        Write-Info "Installer didn't put 'claude' on PATH — trying npm ..."
        npm install -g '@anthropic-ai/claude-code'
        if ((Test-Path $localBin) -and ($env:Path -notlike "*$localBin*")) { $env:Path = "$localBin;$env:Path" }
    }

    if (Get-Command claude -ErrorAction SilentlyContinue) {
        Write-Ok "Claude Code installed."
    } else {
        Write-Warn "Couldn't confirm 'claude' on PATH in this terminal."
        Write-Info "It may have installed but need a NEW terminal — or install it by hand (see setup/CLAUDE-CODE.md)."
    }
}

# ---- 4. point Claude Code at the workshop gateway ---------------------------
# (Environment changes made here persist in the current PowerShell session.)
$env:ANTHROPIC_BASE_URL        = $Gateway
$env:ANTHROPIC_AUTH_TOKEN      = $Key        # skips Claude Code's normal login
$env:ANTHROPIC_MODEL           = $Model      # the model your prompts use
$env:ANTHROPIC_SMALL_FAST_MODEL = $Model     # background tasks use the same model
$env:CLAUDE_CODE_MAX_OUTPUT_TOKENS = $MaxOut
Write-Ok "Claude Code is now pointed at the workshop ($Model via $Gateway)."

# ---- 5. optionally remember it in the PowerShell profile --------------------
if ($Persist) {
    $marker = '# >>> workshop claude code >>>'
    $endmk  = '# <<< workshop claude code <<<'
    try {
        $dir = Split-Path -Parent $PROFILE
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
        $existing = if (Test-Path $PROFILE) { Get-Content -LiteralPath $PROFILE } else { @() }
        $out = [System.Collections.Generic.List[string]]::new()
        $skip = $false
        foreach ($l in $existing) {
            if ($l -eq $marker) { $skip = $true; continue }
            if ($l -eq $endmk)  { $skip = $false; continue }
            if (-not $skip) { $out.Add($l) }
        }
        $q = { param($s) "'" + ($s -replace "'", "''") + "'" }   # single-quote-safe literal
        $out.Add($marker)
        $out.Add("`$env:ANTHROPIC_BASE_URL = $(& $q $Gateway)")
        $out.Add("`$env:ANTHROPIC_AUTH_TOKEN = $(& $q $Key)")
        $out.Add("`$env:ANTHROPIC_MODEL = $(& $q $Model)")
        $out.Add("`$env:ANTHROPIC_SMALL_FAST_MODEL = $(& $q $Model)")
        $out.Add("`$env:CLAUDE_CODE_MAX_OUTPUT_TOKENS = $(& $q $MaxOut)")
        $out.Add($endmk)
        Set-Content -LiteralPath $PROFILE -Value $out -Encoding UTF8
        Write-Ok "Saved to $PROFILE — new PowerShell terminals will already be connected."
        Write-Info "To undo: delete the block between the '>>> workshop claude code >>>' markers in that file."
    } catch {
        Write-Warn "Couldn't update your PowerShell profile — env is set for THIS session only."
    }
}

# ---- 6. launch --------------------------------------------------------------
Write-Host ""
Write-Info "Starting Claude Code — type your request in plain English, or 'exit' to quit."
Write-Host ""
if (Get-Command claude -ErrorAction SilentlyContinue) {
    claude
} else {
    Write-Err "Claude Code isn't on your PATH yet. Open a NEW terminal and type 'claude'."
    exit 1
}
