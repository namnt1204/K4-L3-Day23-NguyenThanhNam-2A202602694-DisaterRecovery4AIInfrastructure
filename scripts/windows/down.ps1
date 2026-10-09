# scripts/windows/down.ps1 - Graceful shutdown for Windows native stack
# Resumes suspended processes before termination, checks process identity,
# and removes expired PID files.

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $RepoRoot

$Python = Join-Path $RepoRoot "myenv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    if ($env:VIRTUAL_ENV) {
        $Python = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
    } else {
        $Python = "python.exe"
    }
}

& $Python "$PSScriptRoot\down.py"
