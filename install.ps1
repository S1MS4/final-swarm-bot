# Sets the bot up from nothing. Paste this in PowerShell:
#   irm https://raw.githubusercontent.com/S1MS4/final-swarm-bot/main/install.ps1 | iex
#
# It does not start the bot. You have to be in a run first, see the README.

$ErrorActionPreference = 'Stop'
$repo = 'https://github.com/S1MS4/final-swarm-bot.git'

function Say($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Have($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

function Refresh-Path {
    # winget puts things on PATH, but only for NEW terminals. Pull it in now.
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
}

if (-not (Have 'winget')) {
    Write-Host "No winget on this machine, so I cannot install things for you." -ForegroundColor Yellow
    Write-Host "Grab Python 3.12 from https://www.python.org/downloads/ (tick Add Python to PATH)"
    Write-Host "and Git from https://git-scm.com/downloads, then run this again."
    return
}

if (Have 'python') { Say 'Python already here' } else {
    Say 'Installing Python 3.12'
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    Refresh-Path
}

if (Have 'git') { Say 'Git already here' } else {
    Say 'Installing Git'
    winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements
    Refresh-Path
}

Refresh-Path
foreach ($c in 'python', 'git') {
    if (-not (Have $c)) { throw "$c still not found. Close this window, open a new one, run this again." }
}

if (Test-Path '.\swarmbot') {
    Say 'Already in the repo folder'
} elseif (Test-Path '.\final-swarm-bot\swarmbot') {
    Say 'Repo already cloned'
    Set-Location '.\final-swarm-bot'
} else {
    Say 'Downloading the bot'
    git clone --depth 1 $repo
    Set-Location '.\final-swarm-bot'
}

Say 'Building the virtual environment'
if (-not (Test-Path '.\.venv')) { python -m venv .venv }

# Called directly, so PowerShell's execution policy never gets in the way.
$py = Resolve-Path '.\.venv\Scripts\python.exe'

Say 'Installing the libraries, this is the slow part'
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -r requirements.txt

& $py -c "import cv2, numpy, mss, rapidocr_onnxruntime, win32gui, keyboard, rapidfuzz, pytest"
Say "Done. You are in $(Get-Location)"

Write-Host ''
Write-Host 'Next:' -ForegroundColor Green
Write-Host '  1. Open Final Swarm, press PLAY, and be IN A RUN. It will not press PLAY for you.'
Write-Host '  2. Maximized window on 1920x1080, graphics on Potato, 60 FPS cap.'
Write-Host '  3. Back here, start it with:'
Write-Host '       .\run.ps1' -ForegroundColor Yellow
Write-Host '     Stop it any time with Shift + \'
