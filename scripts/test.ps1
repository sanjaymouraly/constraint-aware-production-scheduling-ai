<#
.SYNOPSIS
    Run the complete quality gate (Windows PowerShell).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\test.ps1
    powershell -ExecutionPolicy Bypass -File scripts\test.ps1 -Slow
#>
[CmdletBinding()]
param(
    [switch]$Slow
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) {
    throw 'The virtual environment is missing - run scripts\setup.ps1 first.'
}


Write-Host '==> Backend lint' -ForegroundColor Cyan
Push-Location backend
try {
    & $Python -m ruff check app tests
    if ($LASTEXITCODE -ne 0) { throw 'Ruff lint failed.' }
    & $Python -m ruff format --check app tests
    if ($LASTEXITCODE -ne 0) { throw 'Ruff format check failed.' }
    Write-Host '==> Backend type check' -ForegroundColor Cyan
    & $Python -m mypy app
    if ($LASTEXITCODE -ne 0) { throw 'Mypy failed.' }
    Write-Host '==> Backend tests' -ForegroundColor Cyan
    if ($Slow) { & $Python -m pytest -q } else { & $Python -m pytest -q -m 'not slow' }
    if ($LASTEXITCODE -ne 0) { throw 'The backend tests failed.' }
} finally {
    Pop-Location
}

Write-Host '==> Frontend lint' -ForegroundColor Cyan
Push-Location frontend
try {
    npm run lint
    if ($LASTEXITCODE -ne 0) { throw 'ESLint failed.' }
    Write-Host '==> Frontend type check' -ForegroundColor Cyan
    npm run typecheck
    if ($LASTEXITCODE -ne 0) { throw 'TypeScript type check failed.' }
    Write-Host '==> Frontend component tests' -ForegroundColor Cyan
    npm run test
    if ($LASTEXITCODE -ne 0) { throw 'Vitest failed.' }
    Write-Host '==> Frontend production build' -ForegroundColor Cyan
    npm run build
    if ($LASTEXITCODE -ne 0) { throw 'The production build failed.' }
} finally {
    Pop-Location
}

Write-Host ''
Write-Host 'All checks passed.' -ForegroundColor Green
