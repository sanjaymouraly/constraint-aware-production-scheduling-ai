<#
.SYNOPSIS
    Start the Vite development server (Windows PowerShell).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\start-frontend.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $Root 'frontend')

if (-not (Test-Path 'node_modules')) {
    throw 'node_modules is missing - run scripts\setup.ps1 first.'
}

if (-not $env:VITE_API_TARGET) { $env:VITE_API_TARGET = 'http://127.0.0.1:8000' }
$Host_ = if ($env:HOST) { $env:HOST } else { '0.0.0.0' }
$Port = if ($env:PORT) { $env:PORT } else { '5173' }

npm run dev -- --host $Host_ --port $Port
