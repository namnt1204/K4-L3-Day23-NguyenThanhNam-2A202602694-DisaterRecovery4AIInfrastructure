# scripts/windows/drill1.ps1 - Chaos Drill 1 (Baseline - No DR) on Windows
# Executes Step 2 of GUIDE.md using Windows native adapters.

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $RepoRoot

# 1. Resolve Python executable
$Python = Join-Path $RepoRoot "myenv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    if ($env:VIRTUAL_ENV) {
        $Python = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
    } else {
        $Python = "python.exe"
    }
}

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  DRILL 1: BASELINE (KHONG CO DISASTER RECOVERY)         " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 2. Check initial stack status
Write-Host "[1/6] Kiem tra stack ban dau..." -ForegroundColor Yellow
$statusJson = & $Python "scripts\windows\chaos.py" status
$status = $statusJson | ConvertFrom-Json

if (-not $status.a.alive -or -not $status.a.ready) {
    Write-Error "Region A chua san sang (alive=$($status.a.alive), ready=$($status.a.ready)). Vui long chay scripts\windows\up.ps1 truoc."
    exit 1
}
if (-not $status.b.alive) {
    Write-Error "Region B chua song (alive=$($status.b.alive)). Vui long chay scripts\windows\up.ps1 truoc."
    exit 1
}
Write-Host "  Region A: UP & READY" -ForegroundColor Green
Write-Host "  Region B: ALIVE (Chua ready - dung thiet ke baseline)" -ForegroundColor Green

# Ensure reports directory exists
$ReportsDir = Join-Path $RepoRoot "reports"
New-Item -ItemType Directory -Force -Path $ReportsDir | Out-Null
$loadgenOut = Join-Path $RepoRoot "reports\drill-1-nodr.jsonl"
if (Test-Path $loadgenOut) {
    Remove-Item $loadgenOut -Force
}

# 3. Start load generator as separate process
Write-Host "[2/6] Khoi dong load generator (duration=40s, rps=2)..." -ForegroundColor Yellow
$loadgenArgs = @(
    "loadgen\traffic.py",
    "--duration", "40",
    "--rps", "2",
    "--out", "reports/drill-1-nodr.jsonl"
)
$loadgenProc = Start-Process -FilePath $Python -ArgumentList $loadgenArgs -WorkingDirectory $RepoRoot -PassThru -NoNewWindow
Write-Host "  Loadgen dang chay voi PID $($loadgenProc.Id)..."

# 4. Wait 8 seconds of normal traffic
Write-Host "[3/6] Doi 8 giay de tao luong traffic on dinh..." -ForegroundColor Yellow
Start-Sleep -Seconds 8

# 5. Induce chaos netblock on Region A
Write-Host "[4/6] Gay su co netblock (suspend) tren Region A..." -ForegroundColor Red
& $Python "scripts\windows\chaos.py" --region a --mode netblock --mock

# 6. Wait for loadgen to complete
Write-Host "[5/6] Cho load generator hoan thanh (khoang 32 giay con lai)..." -ForegroundColor Yellow
$null = $loadgenProc.WaitForExit()
Write-Host "  Load generator da hoan tat!" -ForegroundColor Green

# 7. Measure RTO
Write-Host "[6/6] Do luong RTO bang tools\measure_rto.py..." -ForegroundColor Yellow
$measureOut = & $Python "tools\measure_rto.py" --loadgen "reports/drill-1-nodr.jsonl" --target-rto 300
$measureFile = Join-Path $RepoRoot "reports\measure-drill-1.json"
$measureOut | Set-Content -Path $measureFile -Encoding utf8

Write-Host "=== KET QUA DO RTO DRILL 1 ===" -ForegroundColor Cyan
$measureOut | Write-Host

# 8. Restore Region A
Write-Host "==> Khoi phuc (restore) Region A sau drill..." -ForegroundColor Yellow
& $Python "scripts\windows\chaos.py" restore --region a

Start-Sleep -Seconds 2
try {
    $checkA = Invoke-RestMethod -Uri "http://127.0.0.1:8001/healthz" -TimeoutSec 2
    Write-Host "==> Region A da tro lai trang thai binh thuong (alive=$($checkA.alive))" -ForegroundColor Green
} catch {
    Write-Warning "Region A chua phan hoi ngay sau restore"
}

Write-Host "==> Hoan thanh Drill 1!" -ForegroundColor Green
