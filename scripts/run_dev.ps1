# FloodWatch Development Startup Script for Windows PowerShell
# Usage:
#   .\scripts\run_dev.ps1 -Backend
#   .\scripts\run_dev.ps1 -Frontend
#   .\scripts\run_dev.ps1 -All

param (
    [switch]$Backend,
    [switch]$Frontend,
    [switch]$All
)

$Root = Split-Path -Parent $PSScriptRoot

if ($Backend -or $All) {
    Write-Host "Starting FloodWatch Backend on http://127.0.0.1:8000..." -ForegroundColor Cyan
    Set-Location "$Root\backend"
    if (Test-Path ".venv\Scripts\Activate.ps1") {
        & ".venv\Scripts\Activate.ps1"
    }
    uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
}

if ($Frontend -or $All) {
    Write-Host "Starting FloodWatch Frontend on http://localhost:5173..." -ForegroundColor Green
    Set-Location "$Root\frontend"
    npm run dev
}

if (-not ($Backend -or $Frontend -or $All)) {
    Write-Host "Specify -Backend, -Frontend, or -All" -ForegroundColor Yellow
}
