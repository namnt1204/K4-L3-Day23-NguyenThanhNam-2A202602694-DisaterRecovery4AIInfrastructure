# scripts/package_submission.ps1
# Script đóng gói bài nộp Lab Day 23 theo đúng format cá nhân:
# D23-HoVaTen-MSSV/
# ├── dr/
# │   ├── health_checker.py
# │   ├── failover.py
# │   └── runbook.py
# └── reports/
#     ├── rto-evidence.md
#     ├── runbook.md
#     └── postmortem.md

param (
    [string]$HoVaTen,
    [string]$MSSV
)

if (-not $HoVaTen) {
    $HoVaTen = Read-Host "Nhap Ho va Ten (khong dau, vi du: NguyenThanhNam)"
}
if (-not $MSSV) {
    $MSSV = Read-Host "Nhap Ma So Sinh Vien (MSSV, vi du: 22010001)"
}

$Folder = "D23-$HoVaTen-$MSSV"
Write-Host "Dang tao thu muc nop bai: $Folder" -ForegroundColor Cyan

# Tao cau truc thu muc
New-Item -ItemType Directory -Path "$Folder\dr" -Force | Out-Null
New-Item -ItemType Directory -Path "$Folder\reports" -Force | Out-Null

# Copy dung 6 files theo yeu cau
Copy-Item "dr\health_checker.py" -Destination "$Folder\dr\health_checker.py" -Force
Copy-Item "dr\failover.py" -Destination "$Folder\dr\failover.py" -Force
Copy-Item "dr\runbook.py" -Destination "$Folder\dr\runbook.py" -Force

Copy-Item "reports\rto-evidence.md" -Destination "$Folder\reports\rto-evidence.md" -Force
Copy-Item "reports\runbook.md" -Destination "$Folder\reports\runbook.md" -Force
Copy-Item "reports\postmortem.md" -Destination "$Folder\reports\postmortem.md" -Force

Write-Host "Da copy 6 files vao $Folder:" -ForegroundColor Green
Get-ChildItem -Recurse $Folder | Select-Object FullName

# Tao them file zip tien cho nop bai LMS / Canvas
$ZipPath = "$Folder.zip"
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
Compress-Archive -Path "$Folder\*" -DestinationPath $ZipPath
Write-Host "Da dong goi file zip: $ZipPath" -ForegroundColor Green
