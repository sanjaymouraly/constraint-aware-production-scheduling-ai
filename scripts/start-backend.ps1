<#
.SYNOPSIS
    Start the FastAPI backend (Windows PowerShell).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\start-backend.ps1
    powershell -ExecutionPolicy Bypass -File scripts\start-backend.ps1 -Reload
#>
[CmdletBinding()]
param(
    [switch]$Reload
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Venv = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Venv)) {
    throw 'The virtual environment is missing - run scripts\setup.ps1 first.'
}

$Host_ = if ($env:HOST) { $env:HOST } else { '0.0.0.0' }
$Port = if ($env:PORT) { $env:PORT } else { '8000' }

Set-Location backend
$arguments = @('app.main:app', '--host', $Host_, '--port', $Port)
if ($Reload) { $arguments += '--reload' }

& $Venv -m uvicorn @arguments
