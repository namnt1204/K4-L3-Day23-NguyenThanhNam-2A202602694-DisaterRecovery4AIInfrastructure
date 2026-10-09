# scripts/windows/clean.ps1 - Safe cleanup for Lab Day 23 on Windows
# Only cleans state and logs when no processes are using them.
# Does NOT touch source code or non-lab files.

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $RepoRoot

# Safety check: Verify we are in the correct repository
if (-not (Test-Path (Join-Path $RepoRoot "serving\app.py")) -or -not (Test-Path (Join-Path $RepoRoot "edge\proxy.py"))) {
    Write-Error "Safety check failed: Khong tim thay serving\app.py hoac edge\proxy.py. Dung cleanup de tranh xoa nham."
    exit 1
}

Write-Host "==> Dung cac tien trinh lab truoc khi cleanup..." -ForegroundColor Cyan
& "$PSScriptRoot\down.ps1"

# Verify ports are released
Start-Sleep -Seconds 1
$busyPorts = @()
foreach ($port in @(8001, 8002, 8080)) {
    try {
        $tcp = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
        if ($tcp) {
            $busyPorts += $port
        }
    } catch {}
}

if ($busyPorts.Count -gt 0) {
    Write-Warning "Cac cong sau van dang bi chiem: $($busyPorts -join ', '). Vui long kiem tra process truoc khi xoa."
}

Write-Host "==> Dang don dep state va logs cua lab..." -ForegroundColor Cyan

# 1. Clean state directories
foreach ($dir in @("state\region-a", "state\region-b", "state\_replica")) {
    $targetPath = Join-Path $RepoRoot $dir
    if (Test-Path $targetPath) {
        Remove-Item -Path $targetPath -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "  Da xoa $dir"
    }
}

# 2. Clean edge active region pointer
$edgeActive = Join-Path $RepoRoot "edge\active_region"
if (Test-Path $edgeActive) {
    Remove-Item -Path $edgeActive -Force -ErrorAction SilentlyContinue
    Write-Host "  Da xoa edge\active_region"
}

# 3. Clean run directory files (keep .gitkeep)
$runDir = Join-Path $RepoRoot "run"
if (Test-Path $runDir) {
    Get-ChildItem -Path $runDir -Exclude ".gitkeep" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "  Da lam sach run/"
}

# 4. Clean generated drill reports (ONLY .jsonl and .json, PRESERVE .md templates and documentation)
$reportsDir = Join-Path $RepoRoot "reports"
if (Test-Path $reportsDir) {
    Get-ChildItem -Path $reportsDir -Include @("*.jsonl", "*.json") -Recurse | Remove-Item -Force -ErrorAction SilentlyContinue
    Write-Host "  Da xoa cac file log JSON/JSONL trong reports/"
}

# 5. Clean chaos events log
$chaosLog = Join-Path $RepoRoot "chaos\chaos-events.jsonl"
if (Test-Path $chaosLog) {
    Remove-Item -Path $chaosLog -Force -ErrorAction SilentlyContinue
    Write-Host "  Da xoa chaos\chaos-events.jsonl"
}

Write-Host "==> Don dep hoan tat an toan!" -ForegroundColor Green
