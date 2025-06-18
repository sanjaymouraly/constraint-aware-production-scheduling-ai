<#
.SYNOPSIS
    Intelligent Production Scheduling AI - one shot setup (Windows PowerShell).

.DESCRIPTION
    Creates the virtual environment, installs the backend and frontend
    dependencies, copies .env.example to .env and seeds the demo factory.

.PARAMETER NoSeed
    Skip the demo data seeding.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
#>
[CmdletBinding()]
param(
    [switch]$NoSeed
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host '==> Creating the virtual environment' -ForegroundColor Cyan
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw 'The Python launcher (py) was not found. Install Python 3.11 or newer from python.org.'
}
py -3.12 -m venv .venv 2>$null
if ($LASTEXITCODE -ne 0) {
    py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create the Python virtual environment.' }
}

$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { throw 'The virtual environment was not created.' }

Write-Host '==> Installing the backend dependencies' -ForegroundColor Cyan
& $Python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'Failed to upgrade pip.' }
& $Python -m pip install -r backend\requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed.' }

Write-Host '==> Installing the frontend dependencies' -ForegroundColor Cyan
Push-Location frontend
try {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw 'npm was not found. Install Node.js 20 or newer from nodejs.org.'
    }
    npm install
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
} finally {
    Pop-Location
}

if (-not (Test-Path '.env')) {
    Write-Host '==> Creating .env from .env.example' -ForegroundColor Cyan
    Copy-Item '.env.example' '.env'
}

if (-not $NoSeed) {
    Write-Host '==> Seeding the demo factory (medium scale)' -ForegroundColor Cyan
    Push-Location backend
    try {
        & $Python -m app.cli reset --scale medium
        if ($LASTEXITCODE -ne 0) { throw 'Demo data seeding failed.' }
    } finally { Pop-Location }
}

Write-Host ''
Write-Host 'Setup complete.' -ForegroundColor Green
Write-Host '  Terminal 1:  .\.venv\Scripts\Activate.ps1 ; cd backend ; uvicorn app.main:app --reload --port 8000'
Write-Host '  Terminal 2:  cd frontend ; npm run dev'
Write-Host '  Then open http://localhost:5173 and sign in with admin / admin.'
