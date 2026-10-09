# Runbook 1 trang — Region Chính Down (AI Serving Infrastructure)

> **Mục tiêu:** Quy trình chuẩn xử lý sự cố khi Region chính (Region A) ngừng hoạt động. Runbook được thiết kế để kỹ sư trực ban (On-call Engineer) có thể thực hiện chính xác lúc 3 giờ sáng trên Windows 10 / PowerShell với môi trường `myenv`.

---

## 1. Bảng 7 Bước Vận Hành Chuẩn Hóa

| # | Bước | Lệnh PowerShell / Python | Biết là xong khi | Ai làm | Điều kiện Abort / Dừng lại |
|---|---|---|---|---|---|
| **1** | **Xác nhận outage** | `.\myenv\Scripts\python.exe scripts\windows\chaos.py status` | `a.ready=false` (hoặc timeout 3 lần liên tiếp) VÀ `b.alive=true` | On-call Engineer | Nếu Region A vẫn phản hồi `/readyz=200` (lỗi thoáng qua), hoặc nếu Region B đã sập (tránh double outage). |
| **2** | **Mở incident & Xác nhận** | `.\myenv\Scripts\python.exe dr\runbook.py --primary a --target b --backend fs` | On-call xác nhận `y`, log ghi vào `reports/runbook-run.jsonl` với `t_operator` | On-call Engineer | Operator nhập `N` hoặc hủy bỏ lệnh. |
| **3** | **Scale GPU & Kích hoạt Failover** | *(Tự động chạy trong Bước 2)*<br>Thủ công: `.\myenv\Scripts\python.exe dr\failover.py --target b --backend fs` | `reports/failover-events.jsonl` ghi nhận `1_verify_target`, `2_restore_snapshot`, `3_scale_pool` | Hệ thống tự động / On-call | Không tìm thấy snapshot hợp lệ trên backend (`state/_replica`), hoặc lỗi file system. |
| **4** | **Xác minh State Replica** | `curl.exe -s http://127.0.0.1:8002/v1/state` | `count >= 200`, `weights: true`, `rpo_seconds` và `docs_lost` được ghi nhận | On-call Engineer | `count=0` hoặc `weights: false` (dữ liệu không đồng bộ sang được). |
| **5** | **DNS / LB Cutover** | `curl.exe -s http://127.0.0.1:8080/edge/state` | `active_region: "b"`, `/readyz` của Region B trả về `200 OK` | Hệ thống tự động / On-call | **Target chưa Ready:** Nếu Bước 4 timeout chưa trả 200, tuyệt đối KHÔNG đổi active_region (tránh 503 từ cả 2 phía). |
| **6** | **Xác minh Golden Signals** | `1..10 \| % { curl.exe -s http://127.0.0.1:8080/v1/infer }` | 10/10 request trả `200 OK`, `error_rate=0.0%`, `p95_latency < 500ms`, answer bắt đầu `"[b] ..."` | On-call Engineer | `error_rate > 20%` hoặc timeout liên tục. Cần kiểm tra lại log `run/region-b.log`. |
| **7** | **Đo RTO & Lập Postmortem** | `.\myenv\Scripts\python.exe tools\measure_rto.py --loadgen reports\drill-2-withdr.jsonl --target-rto 300` | Output trả về `rto_verdict: "PASS"` (RTO ≤ 300s), thu thập đủ evidence | Incident Commander / On-call | Không có (bước phân tích sau sự cố). |

---

## 2. Chính Sách Rollback (Failback Ngược về Region A)

> **CẢNH BÁO QUAN TRỌNG:** Tuyệt đối **KHÔNG** bật tính năng tự động failback (No full-auto failback). Full-auto không có circuit breaker sẽ gây hiện tượng flapping giữa 2 region, phá hủy toàn bộ kết nối và tràn bộ đệm DNS cache (§4 Anti-Patterns).

### 2.1. Quyền quyết định Rollback
- Quyết định trả lưu lượng về Region A thuộc **DUY NHẤT** về thẩm quyền của **Incident Commander (IC)** hoặc **On-call Lead được ủy quyền**.
- Kỹ sư on-call cấp dưới không được tự ý kích hoạt chuyển vùng ngược lại.

### 2.2. Điều kiện Tiên Quyết để Kích Hoạt Rollback
1. **Region A đã hoạt động ổn định hoàn toàn**: Endpoint `/healthz` và `/readyz` của Region A phải trả về mã `200 OK` liên tục trong ít nhất **15 phút**.
2. **Đồng bộ dữ liệu hai chiều (Reverse Replication)**: Mọi dữ liệu mới được ingest vào Region B trong giai đoạn sự cố phải được sao lưu và restore ngược về Region A:
   ```powershell
   # 1. Snapshot dữ liệu mới từ Region B
   .\myenv\Scripts\python.exe state\snapshot.py put --region b --backend fs
   # 2. Khôi phục dữ liệu vào Region A
   .\myenv\Scripts\python.exe state\snapshot.py get --region a --backend fs
   ```
3. **Xác nhận không mất mát dữ liệu**: `docs_lost` giữa Region B và Region A phải bằng `0`.
4. **GPU Pool của Region A ở trạng thái `full`**: Không còn nằm trong thời gian đếm ngược warm-up.

### 2.3. Quy trình Thực Thi Rollback Thủ Công
```powershell
# 1. Đổi DNS pointer tại Edge Proxy về Region A (viết 1 byte ASCII chuẩn)
[System.IO.File]::WriteAllText("$PWD\edge\active_region", "a", [System.Text.Encoding]::ASCII)

# 2. Xác nhận trạng thái Edge
curl.exe -i http://127.0.0.1:8080/edge/state

# 3. Kiểm tra Golden Signals trên Region A (10 requests)
1..10 | % { curl.exe -s http://127.0.0.1:8080/v1/infer }
```

---

## 3. Các Nguyên Tắc An Toàn & Rào Chắn Vận Hành (Operational Guardrails)

1. **Chống chạy song song (Single Execution Lock):** Tuyệt đối không chạy hai tiến trình failover hoặc runbook đồng thời. Nếu nghi ngờ có tiến trình đang chạy dở, kiểm tra qua Task Manager hoặc file khóa trước khi kích hoạt.
2. **Phân biệt Tự Động vs Thủ Công:**
   - **Chế độ tự động chuẩn (`dr/runbook.py`):** Dành cho vận hành tổng thể, tự động điều phối 7 bước và đảm bảo tính liên tục của timeline postmortem.
   - **Lệnh thủ công:** Chỉ sử dụng để tra cứu trạng thái (`status`, `v1/state`) hoặc can thiệp khẩn cấp khi pipeline tự động gặp trục trặc.
3. **Cờ `--auto` chỉ dùng cho CI/Drill:** Trong vận hành sản xuất thực tế, luôn chạy `dr/runbook.py` ở chế độ mặc định để yêu cầu con người xác nhận (`y/N`), ngăn ngừa báo động giả từ các đợt micro-outage.
