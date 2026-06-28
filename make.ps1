<#
.SYNOPSIS
    Native Windows task runner for this repo — the PowerShell equivalent of the
    Makefile, so Windows users don't need `make`.

.DESCRIPTION
    Targets:
      install     Create the .venv virtualenv and install requirements.txt
      test        Run the test suite (pytest)
      run         Run the bot locally via polling (needs .env)
      deploy-pa   Deploy to PythonAnywhere (needs .env + PowerShell 7; see scripts\pa_deploy.ps1)
      claude      Connect Claude Code to the workshop gateway (passes args through)
      help        Show this list (default)

.EXAMPLE
    .\make.ps1 install

.EXAMPLE
    .\make.ps1 run

.EXAMPLE
    .\make.ps1 claude sk-your-key
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)][string]$Target = 'help',
    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]$Rest
)

$ErrorActionPreference = 'Stop'
$RepoRoot = $PSScriptRoot
Set-Location -LiteralPath $RepoRoot

$VenvPy = Join-Path $RepoRoot '.venv\Scripts\python.exe'

function Show-Help {
    Write-Host "Usage: .\make.ps1 <target>" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  install     Create .venv and install requirements.txt"
    Write-Host "  test        Run the test suite (pytest)"
    Write-Host "  run         Run the bot locally via polling (needs .env)"
    Write-Host "  deploy-pa   Deploy to PythonAnywhere (needs .env + PowerShell 7)"
    Write-Host "  claude      Connect Claude Code, e.g. .\make.ps1 claude sk-your-key"
    Write-Host "  help        Show this message"
}

function Assert-Venv {
    if (-not (Test-Path -LiteralPath $VenvPy)) {
        Write-Host "ERROR: .venv not found. Run '.\make.ps1 install' first." -ForegroundColor Red
        exit 1
    }
}

function Assert-Env {
    if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot '.env'))) {
        Write-Host "ERROR: .env not found. Copy .env.example to .env first." -ForegroundColor Red
        exit 1
    }
}

function Invoke-Native {
    # Run a native command; abort if it exits non-zero. Mirrors make's "first
    # failing recipe line stops the build" — $ErrorActionPreference = 'Stop'
    # does NOT cover native executables (only cmdlets), so check $LASTEXITCODE.
    param([Parameter(Mandatory)][scriptblock]$Cmd)
    & $Cmd
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: command failed (exit $LASTEXITCODE)." -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

switch ($Target.ToLower()) {
    'install' {
        # Prefer the Windows 'py' launcher; fall back to 'python' on PATH.
        if (Get-Command py -ErrorAction SilentlyContinue) { Invoke-Native { py -3 -m venv .venv } }
        elseif (Get-Command python -ErrorAction SilentlyContinue) { Invoke-Native { python -m venv .venv } }
        else { Write-Host "ERROR: Python not found. Install Python 3.13 from python.org and re-run." -ForegroundColor Red; exit 1 }
        Invoke-Native { & $VenvPy -m pip install --upgrade pip }
        Invoke-Native { & $VenvPy -m pip install -r requirements.txt }
    }
    'test' {
        Assert-Venv
        Invoke-Native { & $VenvPy -m pytest tests\ -v }
    }
    'run' {
        Assert-Venv
        Assert-Env
        & $VenvPy run_local.py
    }
    'deploy-pa' {
        Assert-Env
        $deploy = Join-Path $RepoRoot 'scripts\pa_deploy.ps1'
        # pa_deploy.ps1 needs PowerShell 7. If we're on 5.1 but pwsh exists, use it.
        if ($PSVersionTable.PSVersion.Major -lt 7 -and (Get-Command pwsh -ErrorAction SilentlyContinue)) {
            Invoke-Native { & pwsh -NoProfile -File $deploy }
        } else {
            # In-process .ps1 call: its own `exit <code>` propagates the failure.
            & $deploy
        }
    }
    'claude' {
        $connect = Join-Path $RepoRoot 'setup\connect-claude-code.ps1'
        & $connect @Rest
    }
    default { Show-Help }
}
