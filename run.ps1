# Starts the bot. Be in a run first. Stop it with Shift + \
#   .\run.ps1                       farm forever
#   .\run.ps1 --max-runs 1          one run then stop
#   .\run.ps1 --dry-run             decide but never click

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    Write-Host "No virtual environment here, falling back to system Python." -ForegroundColor Yellow
    Write-Host "If that fails, run install.ps1 first."
    $py = 'python'
}

& $py -m swarmbot.bot @args
