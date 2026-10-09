# Lab Day 23 — Disaster Recovery for AI Infrastructure (Windows Adapter)

> **Lưu ý quan trọng**: Đây là **Windows Adapter** dành cho môi trường Windows 10 native, PowerShell và Python 3.11 (không dùng WSL, không bắt buộc Docker). Môi trường bare Linux chính thức của bài lab dùng Bash và tín hiệu POSIX (`SIGSTOP`, `SIGCONT`, `SIGKILL`). Adapter này giúp bạn hoàn thành toàn bộ bài lab và các bài tập DR trên Windows một cách chính xác mà không làm thay đổi các quy tắc và bài test chấm điểm gốc.

---

## 1. Yêu cầu & Chuẩn bị môi trường

- **Hệ điều hành**: Windows 10 / 11
- **PowerShell**: Windows PowerShell 5.1 hoặc PowerShell 7+
- **Python**: 3.11 trong virtual environment `myenv`
- **Thư viện phụ trợ**: `psutil` (được liệt kê trong `requirements-windows.txt`)

Cài đặt dependencies (nếu chưa cài):
```powershell
.\myenv\Scripts\python.exe -m pip install -r requirements-windows.txt
```

---

## 2. Các lệnh thao tác chính (PowerShell Copy-Paste)

### Bước 2.1 — Dọn dẹp (Clean)
Dọn dẹp an toàn các state và log sinh ra trong quá trình chạy lab trước đó:
```powershell
.\scripts\windows\clean.ps1
```

### Bước 2.2 — Khởi tạo dữ liệu (Seed)
Khởi tạo dữ liệu cho Region A (200 docs, có weights) và Region B (0 docs, không weights), đồng thời trỏ Edge Proxy về Region A:
```powershell
.\myenv\Scripts\python.exe state\seed_vectors.py --region a --docs 200
.\myenv\Scripts\python.exe state\seed_vectors.py --region b --docs 0 --weights-mb 0
[System.IO.File]::WriteAllText("$PWD\edge\active_region", "a", [System.Text.Encoding]::ASCII)
```

### Bước 2.3 — Khởi động Stack (Up)
Khởi động Region A (8001), Region B (8002) và Edge Proxy (8080):
```powershell
.\scripts\windows\up.ps1
```

Sau khi chạy, launcher sẽ tự động probe `/healthz` và in trạng thái của Edge Proxy:
```json
{"active_region":"a","ttl_seconds":5.0,"cache_age_s":0.0}
```

### Bước 2.4 — Kiểm tra nhanh (Smoke Test)
Kiểm tra phản hồi thực tế từ các dịch vụ:
```powershell
# Region A Liveness & Readiness (Phải trả về 200)
curl.exe -i http://127.0.0.1:8001/healthz
curl.exe -i http://127.0.0.1:8001/readyz

# Region B Liveness (200) & Readiness (Phải trả về 503 do pool_state=warm, thiếu weights và vectors)
curl.exe -i http://127.0.0.1:8002/healthz
curl.exe -i http://127.0.0.1:8002/readyz

# Edge Proxy state (trỏ tới a)
curl.exe -i http://127.0.0.1:8080/edge/state

# Edge Proxy inference (Phải trả về kết quả do Region A phục vụ)
curl.exe -i http://127.0.0.1:8080/v1/infer
```

### Bước 2.5 — Diễn tập Chaos trên Windows (Gây sự cố & Phục hồi)
Thay vì dùng `chaos/kill_region.py` (vốn gọi POSIX signals không hỗ trợ trên Windows), sử dụng adapter `scripts\windows\chaos.py`:

- **Gây sự cố netblock** (mô phỏng treo kết nối / network drop bằng `psutil.suspend`):
  ```powershell
  .\myenv\Scripts\python.exe scripts\windows\chaos.py --region a --mode netblock --mock
  ```

- **Kiểm tra trạng thái hai region**:
  ```powershell
  .\myenv\Scripts\python.exe scripts\windows\chaos.py status
  ```

- **Phục hồi (Restore) Region A**:
  ```powershell
  .\myenv\Scripts\python.exe scripts\windows\chaos.py restore --region a
  ```

- **Gây sự cố stop** (terminate process):
  ```powershell
  .\myenv\Scripts\python.exe scripts\windows\chaos.py --region a --mode stop --mock
  ```

### Bước 2.6 — Chạy Drill 1 (Baseline - Không có Disaster Recovery)
Chạy kịch bản Drill 1 tự động: phát 40s traffic, gây sự cố netblock sau 8s, đo lường RTO (kết quả mong đợi: `NO_RECOVERY`) và tự động restore Region A:
```powershell
.\scripts\windows\drill1.ps1
```

Kiểm tra kết quả bằng unit test chấm điểm:
```powershell
.\myenv\Scripts\python.exe -m pytest tests\test_rto_evidence.py::test_drill1_ton_tai_va_khong_phuc_hoi -v
```

### Bước 2.7 — Dừng Stack (Down)
Dừng toàn bộ các dịch vụ lab an toàn (chỉ dừng các process của lab, tự động resume process nếu đang bị suspend trước khi terminate):
```powershell
.\scripts\windows\down.ps1
```

---

## 3. Khác biệt kỹ thuật giữa Windows Adapter và Linux gốc

| Thành phần | Linux / Unix gốc (`scripts/*_bare.sh`) | Windows Adapter (`scripts/windows/*`) | Lý do kỹ thuật trên Windows |
|---|---|---|---|
| **Khởi động nền** | Bash background `&` và `nohup` | `Start-Process` + `run_service.py` | Windows không có cơ chế `fork`. `run_service.py` đảm bảo PID thực được lưu và stdout/stderr được chuyển hướng qua `os.dup2`. |
| **Ghi file pointer** | `printf a > edge/active_region` | PowerShell `[System.IO.File]::WriteAllText(..., ASCII)` | Tránh PowerShell tự ý thêm UTF-16 BOM hoặc ký tự `\r\n`. |
| **Netblock Chaos** | Gửi `signal.SIGSTOP` tới PID | Gọi `psutil.Process(pid).suspend()` | Windows OS không có POSIX signals `SIGSTOP`/`SIGCONT`. `psutil.suspend()` tạm dừng mọi thread của tiến trình Windows. |
| **Restore Chaos** | Gửi `signal.SIGCONT` tới PID | Gọi `psutil.Process(pid).resume()` | Khôi phục luồng thực thi an toàn của tiến trình trên Windows. |
| **Dừng an toàn (Down)** | `kill -CONT` rồi `kill -9` | `proc.resume()` rồi `proc.terminate()` qua `psutil` | Nếu một tiến trình đang bị suspend trên Windows, terminate trực tiếp có thể gây khóa handle socket hoặc file SQLite. |
| **Schema log sự kiện** | `chaos/chaos-events.jsonl` | Giữ nguyên 100% schema chuẩn | Đảm bảo `tools/measure_rto.py` và `tests/test_rto_evidence.py` đọc chính xác không bị lỗi. |

---

## 4. Những hạn chế còn lại so với Bare Linux

1. **POSIX Signal semantics**: Windows không có POSIX process signals thực thụ. Việc giả lập qua `psutil.suspend()`/`resume()` hoạt động ở tầng Windows Thread Scheduling thay vì Unix kernel signal handler.
2. **File locking khi tiến trình đang chạy**: Trên Windows, SQLite database file và log file có thể bị khóa độc quyền (file locking) khi tiến trình còn mở. Vì vậy script `clean.ps1` luôn yêu cầu tắt tiến trình trước khi dọn dẹp file state.
3. **Môi trường chính thức của môn học**: Bộ chấm điểm gốc và các bài lab mẫu trong tài liệu môn học được thiết kế cho Linux bare/Docker. Windows Adapter này là giải pháp chuyển đổi cho sinh viên thực hành trên Windows 10 native.
