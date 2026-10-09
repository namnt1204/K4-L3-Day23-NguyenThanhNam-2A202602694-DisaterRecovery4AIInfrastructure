# scripts/windows/drill2.ps1
# Windows-native Drill 2 Orchestration for Lab Day 23 (Disaster Recovery for AI Infrastructure)
# Executes end-to-end continuous ingestion, replication, traffic loadgen, health check,
# chaos injection (netblock on Region A), automated runbook failover to Region B,
# and RTO/RPO validation.

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $ProjectRoot

$PythonExe = Join-Path $ProjectRoot "myenv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    $PythonExe = "python.exe"
}

Write-Host "=== GIAI DOAN A: PRE-FLIGHT CHECKS ===" -ForegroundColor Cyan

# 1. Kiem tra cac dich vu
$respA = curl.exe -s http://127.0.0.1:8001/readyz
$respB = curl.exe -s http://127.0.0.1:8002/healthz
$respEdge = curl.exe -s http://127.0.0.1:8080/edge/state

if (-not $respA -or -not ($respA -match '"ready":\s*true')) {
    throw "Pre-flight failed: Region A (port 8001) is not ready: $respA"
}
if (-not $respB -or -not ($respB -match '"alive":\s*true')) {
    throw "Pre-flight failed: Region B (port 8002) is not alive: $respB"
}
if (-not $respEdge) {
    throw "Pre-flight failed: Edge (port 8080) is not responding"
}
Write-Host "Services responding: Region A (8001), Region B (8002), Edge (8080)" -ForegroundColor Green

# 2. Reset Region B: chua ready truoc DR
Write-Host "Resetting Region B to initial unready state..."
& $PythonExe state/seed_vectors.py --region b --docs 0 --weights-mb 0 | Out-Null
if (Test-Path "state/region-b/weights/model.bin") {
    Remove-Item "state/region-b/weights/model.bin" -Force
}
$bReadyResp = curl.exe -s http://127.0.0.1:8002/readyz
if ($bReadyResp -match '"ready":\s*true') {
    throw "Pre-flight failed: Region B must NOT be ready before failover: $bReadyResp"
}
Write-Host "Region B is confirmed NOT ready (ready: false)" -ForegroundColor Green

# 3. Dam bao active_region = a
Set-Content -Path "edge/active_region" -Value "a" -Encoding ascii -NoNewline
Write-Host "Active region set to 'a'" -ForegroundColor Green

# 4. Tach biet cac log file cua drill 2
Remove-Item "reports/drill-2-withdr.jsonl" -ErrorAction SilentlyContinue
Remove-Item "reports/measure-drill-2.json" -ErrorAction SilentlyContinue
Remove-Item "reports/health-events.jsonl" -ErrorAction SilentlyContinue

Write-Host "Pre-flight checks passed successfully.`n" -ForegroundColor Green

Write-Host "=== GIAI DOAN B: RUNNING PARALLEL PROCESSES ===" -ForegroundColor Cyan

# 1. Chay ingest va replicate
Write-Host "Starting continuous ingest on Region A (rate=0.5 doc/s, duration=150s)..."
$pIngest = Start-Process -FilePath $PythonExe `
    -ArgumentList "state/ingest.py", "--region", "a", "--rate", "0.5", "--duration", "150" `
    -WorkingDirectory $ProjectRoot -PassThru -WindowStyle Hidden

Write-Host "Starting replication loop (--every 30 --duration 150 --backend fs)..."
$pReplicate = Start-Process -FilePath $PythonExe `
    -ArgumentList "state/replicate.py", "--every", "30", "--duration", "150", "--backend", "fs" `
    -WorkingDirectory $ProjectRoot -PassThru -WindowStyle Hidden

# 2. Doi snapshot khoi tao xong (MANIFEST.json ton tai)
Write-Host "Waiting for initial snapshot MANIFEST.json..."
$snapTimeout = [DateTime]::UtcNow.AddSeconds(20)
while (-not (Test-Path "state/_replica/dr-artifacts/MANIFEST.json") -and ([DateTime]::UtcNow -lt $snapTimeout)) {
    Start-Sleep -Milliseconds 500
}
if (-not (Test-Path "state/_replica/dr-artifacts/MANIFEST.json")) {
    Stop-Process -Id $pIngest.Id -Force -ErrorAction SilentlyContinue
    Stop-Process -Id $pReplicate.Id -Force -ErrorAction SilentlyContinue
    throw "Initial snapshot MANIFEST.json not found within timeout"
}
Write-Host "Initial snapshot confirmed. Allowing 4s for initial live ingestion..." -ForegroundColor Green
Start-Sleep -Seconds 4

# 3. Chay loadgen va health_checker
Write-Host "Starting traffic loadgen (duration=100s, rps=2, out=reports/drill-2-withdr.jsonl)..."
$pTraffic = Start-Process -FilePath $PythonExe `
    -ArgumentList "loadgen/traffic.py", "--duration", "100", "--rps", "2", "--out", "reports/drill-2-withdr.jsonl" `
    -WorkingDirectory $ProjectRoot -PassThru -WindowStyle Hidden

Write-Host "Starting health checker (interval=5s, threshold=3, duration=100s, out=reports/health-events.jsonl)..."
$pHealth = Start-Process -FilePath $PythonExe `
    -ArgumentList "dr/health_checker.py", "--interval", "5", "--threshold", "3", "--duration", "100", "--out", "reports/health-events.jsonl" `
    -WorkingDirectory $ProjectRoot -PassThru -WindowStyle Hidden

Write-Host "Parallel processes running.`n" -ForegroundColor Green

Write-Host "=== GIAI DOAN C: CHAOS AND RECOVERY ===" -ForegroundColor Cyan

# 1. Cho 12s de traffic co baseline requests on dinh va health checker hoan tat chu ky khoi tao
Write-Host "Waiting 12s for baseline traffic and health checker stabilization..."
Start-Sleep -Seconds 12

# 2. Gay outage Region A bang netblock
Write-Host "Injecting chaos: netblock Region A (--mock)..." -ForegroundColor Yellow
& $PythonExe scripts/windows/chaos.py --region a --mode netblock --mock

# 3. Cho Health Checker phat hien UNHEALTHY cho Region A
Write-Host "Waiting for Health Checker to record UNHEALTHY transition for Region A..." -ForegroundColor Yellow
$detected = $false
$detectTimeout = [DateTime]::UtcNow.AddSeconds(45)
while (-not $detected -and ([DateTime]::UtcNow -lt $detectTimeout)) {
    Start-Sleep -Milliseconds 500
    if (Test-Path "reports/health-events.jsonl") {
        $lines = Get-Content "reports/health-events.jsonl" -ErrorAction SilentlyContinue
        foreach ($line in $lines) {
            if ($line -match '"region":\s*"a"' -and $line -match '"to":\s*"UNHEALTHY"') {
                $detected = $true
                break
            }
        }
    }
}

if (-not $detected) {
    Write-Host "Health checker did not detect UNHEALTHY within timeout!" -ForegroundColor Red
} else {
    Write-Host "Health Checker successfully detected Region A UNHEALTHY!" -ForegroundColor Green
}

# 4. Kich hoat Runbook tu dong (Step 1-7)
Write-Host "Executing automated DR runbook: dr/runbook.py --primary a --target b --backend fs --auto..." -ForegroundColor Cyan
& $PythonExe dr/runbook.py --primary a --target b --backend fs --auto

# 5. Cho loadgen ket thuc de thu thap day du du lieu do
Write-Host "Waiting for traffic generator to finish remaining window (total duration 100s)..."
$pTraffic.WaitForExit()
Write-Host "Traffic generator completed." -ForegroundColor Green

Write-Host "`n=== GIAI DOAN D: MEASUREMENT & AUDIT ===" -ForegroundColor Cyan

# Chay tools/measure_rto.py
Write-Host "Measuring RTO/RPO from collected drill logs..."
& $PythonExe -c "import subprocess, sys; out = subprocess.check_output([sys.executable, 'tools/measure_rto.py', '--loadgen', 'reports/drill-2-withdr.jsonl', '--target-rto', '300'], text=True); open('reports/measure-drill-2.json', 'w', encoding='utf-8').write(out); print(out)"


# Khoi phuc Region A
Write-Host "`nRestoring Region A..."
& $PythonExe scripts/windows/chaos.py restore --region a

# Cleanup cac tien trinh nen con lai
Stop-Process -Id $pIngest.Id -Force -ErrorAction SilentlyContinue
Stop-Process -Id $pReplicate.Id -Force -ErrorAction SilentlyContinue
Stop-Process -Id $pHealth.Id -Force -ErrorAction SilentlyContinue

Write-Host "=== DRILL 2 COMPLETED SUCCESSFULLY ===" -ForegroundColor Green
