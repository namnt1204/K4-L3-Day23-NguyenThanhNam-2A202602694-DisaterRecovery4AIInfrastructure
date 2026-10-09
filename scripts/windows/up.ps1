# scripts/windows/up.ps1 - Windows native launcher for Lab Day 23
# Starts Region A (8001), Region B (8002), and Edge Proxy (8080)
# Uses Python in myenv without requiring Docker.

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

if (-not (Test-Path $Python) -and $Python -ne "python.exe") {
    Write-Error "Khong tim thay Python tai $Python. Vui long kiem tra virtualenv 'myenv'."
    exit 1
}

# 2. Ensure directories exist
$RunDir = Join-Path $RepoRoot "run"
$ReportsDir = Join-Path $RepoRoot "reports"
New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
New-Item -ItemType Directory -Force -Path $ReportsDir | Out-Null

$WarmupSeconds = if ($env:WARMUP_SECONDS) { $env:WARMUP_SECONDS } else { "6" }
$EdgeTtlSeconds = if ($env:EDGE_TTL_SECONDS) { $env:EDGE_TTL_SECONDS } else { "5" }

# Helper to check if a service is already alive
function Test-ServiceAlive {
    param(
        [string]$Port,
        [string]$Endpoint
    )
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:${Port}/${Endpoint}" -UseBasicParsing -TimeoutSec 1 -ErrorAction Stop
        return ($resp.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Start-LabService {
    param(
        [string]$Name,
        [string]$ServiceType,
        [string]$Region,
        [int]$Port,
        [string]$Warmup,
        [string]$Ttl,
        [string]$Endpoint
    )

    $pidFile = Join-Path $RunDir "$Name.pid"
    $logFile = "run/$Name.log"

    # Check if process is already running to avoid duplicates
    if (Test-Path $pidFile) {
        $existingPidRaw = (Get-Content $pidFile -ErrorAction SilentlyContinue | Out-String).Trim()
        if ($existingPidRaw -match '^\d+$') {
            $existingPid = [int]$existingPidRaw
            $proc = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
            if ($proc -and (Test-ServiceAlive -Port $Port -Endpoint $Endpoint)) {
                Write-Host "  $Name (port $Port, PID $existingPid): DANG CHAY (bo qua khoi dong trung)" -ForegroundColor Yellow
                return
            }
        }
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    }

    # Build command line
    if ($ServiceType -eq "serving") {
        $cmdLine = "`"$Python`" `"$RepoRoot\scripts\windows\run_service.py`" --service serving --region $Region --port $Port --warmup $Warmup --log-file `"$RepoRoot\$logFile`" --pid-file `"$pidFile`""
    } else {
        $cmdLine = "`"$Python`" `"$RepoRoot\scripts\windows\run_service.py`" --service edge --port $Port --ttl $Ttl --log-file `"$RepoRoot\$logFile`" --pid-file `"$pidFile`""
    }

    # Launch detached via Win32_Process to avoid job-object lifetime constraints
    $wmi = [wmiclass]"Win32_Process"
    $startup = [wmiclass]"Win32_ProcessStartup"
    $startupConfig = $startup.CreateInstance()
    $startupConfig.ShowWindow = 0 # SW_HIDE
    $res = $wmi.Create($cmdLine, $RepoRoot, $startupConfig)
    if ($res.ReturnValue -ne 0) {
        Write-Error "Khong the khoi dong $Name. Ma loi: $($res.ReturnValue)"
        exit 1
    }

    $realPid = $res.ProcessId
    Set-Content -Path $pidFile -Value "$realPid" -Encoding Ascii -NoNewline
    Write-Host "  Khoi dong $Name (port $Port, PID $realPid)"
}

Write-Host "==> Khoi dong lab services..." -ForegroundColor Cyan

Start-LabService -Name "region-a" -ServiceType "serving" -Region "a" -Port 8001 -Warmup $WarmupSeconds -Endpoint "healthz"
Start-LabService -Name "region-b" -ServiceType "serving" -Region "b" -Port 8002 -Warmup $WarmupSeconds -Endpoint "healthz"
Start-LabService -Name "edge" -ServiceType "edge" -Port 8080 -Ttl $EdgeTtlSeconds -Endpoint "edge/state"

# 3. Wait and verify health endpoints (up to 10s)
Write-Host "Cho service len (toi da 10s)..."
$allUp = $true
$targets = @(
    @{ Name = "region-a"; Port = 8001; Endpoint = "healthz" },
    @{ Name = "region-b"; Port = 8002; Endpoint = "healthz" },
    @{ Name = "edge";     Port = 8080; Endpoint = "edge/state" }
)

foreach ($target in $targets) {
    $isUp = $false
    for ($i = 1; $i -le 10; $i++) {
        if (Test-ServiceAlive -Port $target.Port -Endpoint $target.Endpoint) {
            $isUp = $true
            break
        }
        Start-Sleep -Seconds 1
    }

    if ($isUp) {
        Write-Host "  $($target.Name) (port $($target.Port)): UP" -ForegroundColor Green
    } else {
        Write-Host "  $($target.Name) (port $($target.Port)): KHONG PHAN HOI -- xem run/$($target.Name).log" -ForegroundColor Red
        $allUp = $false
    }
}

if (-not $allUp) {
    Write-Error "MOT SO SERVICE CHUA LEN -- doc log trong run/ truoc khi tiep tuc"
    exit 1
}

Write-Host "==> Trang thai Edge Proxy hien tai:" -ForegroundColor Cyan
try {
    $edgeState = Invoke-RestMethod -Uri "http://127.0.0.1:8080/edge/state" -TimeoutSec 2
    $edgeState | ConvertTo-Json -Compress | Write-Host
} catch {
    Write-Warning "Khong the lay trang thai tu http://127.0.0.1:8080/edge/state"
}
Write-Host "==> Stack khoi dong thanh cong!" -ForegroundColor Green
