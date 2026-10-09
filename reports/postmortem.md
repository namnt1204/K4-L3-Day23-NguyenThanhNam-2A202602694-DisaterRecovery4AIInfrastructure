# Postmortem - DR Drill Lab Day 23

Tai lieu phan tich su co theo chuan Blameless Postmortem (Section 4 "Sau Failover: Blameless Postmortem").
Nguyen tac: tap trung vao he thong, quy trinh va co che van hanh cho phep su co dien ra, khong quy trach nhiem ca nhan.

## 1. Timeline (tat ca cac dong deu co evidence path:line)

| ISO time | Su kien | Evidence |
|---|---|---|
| 2026-10-09T05:28:22 | Outage bat dau (chaos netblock Region A) | `chaos/chaos-events.jsonl:5` |
| 2026-10-09T05:28:22 | User dau tien bi anh huong (request loi dau tien, 503) | `reports/drill-2-withdr.jsonl:25` |
| 2026-10-09T05:28:37 | Health check phat hien va ghi alert UNHEALTHY sau 3 lan probe | `reports/health-events.jsonl:2` |
| 2026-10-09T05:28:46 | Operator xac nhan cutover va kich hoat runbook failover | `reports/runbook-run.jsonl:3` |
| 2026-10-09T05:28:46 | Snapshot restore hoan tat (vector DB va model weights nap vao B) | `reports/failover-events.jsonl:18` |
| 2026-10-09T05:28:53 | Region B warm-up hoan tat va ready (WARMUP timer ket thuc) | `reports/failover-events.jsonl:20` |
| 2026-10-09T05:28:53 | DNS cutover hoan tat (edge/active_region chuyen sang b) | `reports/failover-events.jsonl:21` |
| 2026-10-09T05:28:54 | Resolved: Request dau tien thanh cong tu Region B (RTO hoan tat) | `reports/drill-2-withdr.jsonl:39` |

## 2. RTO/RPO do duoc vs muc tieu - gap o buoc nao?

- RTO muc tieu: 300.0s | do duoc: `32.1s` | gap: `-267.9s` (vuot muc tieu 267.9s, dap ung SLA)
- RPO muc tieu: 300.0s | do duoc: `10.0s` (`5` doc bi mat) | gap: `-290.0s` (vuot muc tieu 290.0s)
- **Buoc ton nhieu giay nhat:**
  1. `Health-check detection floor (14.9s)`: Chiem 46.4% tong RTO.
     Vi sao: Health checker phai poll 3 lan lien tiep voi interval 5.0s va timeout 2.0s de dam bao outage la that, chong flapping khi co network glitch tam thoi.
  2. `Operator confirmation & runbook probe delay (9.5s)`: Chiem 29.6% tong RTO.
     Vi sao: Khoang tre do chu ky polling orchestration va Step 1 runbook phai probe primary 3 lan de xac minh truoc khi thuc hien cutover.
  3. `GPU pool warm-up (6.6s)`: Chiem 20.6% tong RTO.
     Vi sao: Thoi gian doi WARMUP_SECONDS=5.0s cung voi qua trinh load model weights va vector statistics.

## 3. Root cause (5 Whys)

Neu day la mot outage that ngoai production, phan tich 5 Whys cho thay:
- **Why 1:** Tai sao user nhan HTTP 503 khi gui yeu cau toi Edge proxy?
  Vi Region A khong phan hoi yeu cau suy luan (inference request bi timeout).
- **Why 2:** Tai sao Region A khong phan hoi?
  Vi ket noi mang toi Region A bi co lap hoan toan (network partition duoc mo phong boi chaos netblock).
- **Why 3:** Tai sao he thong khong tu dong dinh tuyen sang Region B ngay tuc thi?
  Vi kien truc Active-Passive yeu cau co che xac minh tinh san sang (readiness) va dong bo trang thai truoc khi cutover de tranh 503 kep (double failure) hoac suy luan sai weights.
- **Why 4:** Tai sao Region B ban dau chua san sang phuc vu?
  Vi Region B duoc duy tri o che do warm standby (chua scale pool toi da va chua co ban sao vector DB moi nhat) nham toi uu chi phi ha tang tinh toan va GPU.
- **Why 5:** Tai sao phai trai qua quy trinh 5 buoc phuc hoi nghiem ngat?
  Vi ha tang AI Inference co su phu thuoc phuc tap giua Model Weights, Vector DB schema va GPU resources. Neu DNS cutover duoc thuc hien truoc khi model duoc nap va GPU warm up, he thong se ngay lap tuc bi crash va gay mat du lieu.

## 4. Action items (co owner + deadline)

| # | Action | Owner | Deadline | Giam RTO/RPO bao nhieu giay |
|---|---|---|---|---|
| 1 | Tich hop webhook alert tu Health Checker sang Runbook Failover de loai bo polling latency | SRE Lead | 2026-10-25 | Giam ~6.0s RTO |
| 2 | Trien khai WAL/CDC Streaming Replication cho Vector DB thay vi snapshot dinh ky | Data Infra Engineer | 2026-11-15 | Giam ~8.0s RPO (dua RPO < 2s) |
| 3 | Pre-warm model weights vao shared memory tren Standby Host de giam thoi gian scale pool | AI Platform Engineer | 2026-11-01 | Giam ~4.0s RTO |

## 5. Ba cau hoi bat buoc tra loi

1. **`interval x threshold` cua ban la bao nhieu giay? No chiem bao nhieu % RTO?**
   - Voi `interval = 5.0s` va `threshold = 3`, detect floor ly thuyet la `15.0s`. Thoi gian phat hien thuc te do duoc la `14.9s`.
   - Ty le chiem trong tong RTO: `14.9s / 32.1s = 46.4%`. Day la thanh phan lon nhat trong toan bo thoi gian downtime cua he thong.

2. **Neu ha interval xuong 1s, RTO giam may giay - va ban tra gia gi (Section 4 flapping)?**
   - Neu ha interval xuong 1s (giu threshold = 3), detect floor giam tu 15s xuong 3s, tuc la giam duoc `12s RTO`.
   - **Cai gia phai tra (Trade-off):** Nguy co flapping rat cao. Bat ky mot dao dong mang ngan han (network jitter), GC pause cua tien trinh Python/Torch, hoac CPU spike trong 2-3 giay cung se bi coi la outage. He thong se failover sai lam (false positive), gay ngat ket noi user dang chay, ton kem chi phi scale GPU tai Region B, va nguy co gay split-brain du lieu giua hai region.

3. **Neu outage keo dai 6 gio va region chinh mat du lieu vinh vien, `docs_lost` cua ban co nghia gi voi khach hang?**
   - Trong dot drill, `docs_lost = 5 documents` (tuong ung `10.0s` RPO). Day la nhung van ban / embeddings duoc nguoi dung nap vao trong 10 giay truoc outage ma chua kip replicate vao snapshot object store.
   - Neu Region A bi pha huy hoan toan sau 6 gio, 5 tai lieu nay bi mat vinh vien. Doi voi khach hang, he thong AI se khong the tra loi hoac tra cuu dua tren cac van ban nay, tao ra hien tuong ao giac (hallucination) hoac thieu hut thong tin.
   - De khac phuc, doanh nghiep phai co he thong Event Log / Message Queue ben ngoai (vi du Kafka / SQS co luu giu 7 ngay) de thuc hien replay lai cac tai lieu bi mat vao Region B.
