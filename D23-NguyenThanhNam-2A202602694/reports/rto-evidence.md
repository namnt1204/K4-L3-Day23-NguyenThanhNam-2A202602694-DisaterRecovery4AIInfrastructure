# RTO/RPO Evidence - Lab Day 23

Quy tac duy nhat: moi con so o day phai tro duoc ve mot dong log that (`duong/dan.jsonl:so_dong`).
Moi bang chung deu duoc do bang timestamp thuc tu Drill 1 va Drill 2 tren moi truong thuc nghiem.

## 1. Drill 1 - khong co DR (baseline)

| Chi so | Gia tri | Cach do | Evidence |
|---|---|---|---|
| t_outage | `2026-10-09T04:48:13` | chaos kill | `chaos/chaos-events.jsonl:3` |
| Request fail dau tien | `+0.0s` | dong ok:false dau tien sau t_outage | `reports/drill-1-nodr.jsonl:17` |
| Request thanh cong sau do | khong co | khong co dong ok:true nao sau t_outage | `reports/measure-drill-1.json` |
| RTO | `NO_RECOVERY` | `tools/measure_rto.py` | `reports/measure-drill-1.json` |

Ket luan Drill 1: Khi Region A gap outage va khong co co che DR, toan bo request sau do deu fail (14/14 requests fail). He thong khong the tu phuc hoi (NO_RECOVERY).

## 2. Drill 2 - co DR

| Moc | +giay tu t_outage | Cach do | Evidence |
|---|---|---|---|
| t_outage (moc 0) | 0.0s | `action:kill` | `chaos/chaos-events.jsonl:5` |
| User thay loi dau tien | +0.1s | dong `ok:false` dau | `reports/drill-2-withdr.jsonl:25` |
| Health check phat hien | +14.9s | `to:UNHEALTHY, region:a` | `reports/health-events.jsonl:2` |
| Snapshot restore xong | +24.5s | `step:2_restore_snapshot` | `reports/failover-events.jsonl:18` |
| Region phu ready | +31.1s | `step:4_wait_ready` | `reports/failover-events.jsonl:20` |
| DNS cutover | +31.1s | `step:5_dns_cutover` | `reports/failover-events.jsonl:21` |
| **RTO do duoc** | **+32.1s** | dong `ok:true` dau sau loi | `reports/drill-2-withdr.jsonl:39` |

| Chi so | Do duoc | Muc tieu (slide 1) | Verdict | Evidence |
|---|---|---|---|---|
| RTO - Inference API | `32.1s` | 300.0s (5 phut) | PASS | `reports/measure-drill-2.json` |
| RPO - Vector DB | `10.0s` / `5` doc | 300.0s (5 phut) | PASS | `reports/failover-events.jsonl:18` |

## 3. RTO cua toi gom nhung gi (bat buoc - phan cham diem hieu bai)

| Thanh phan | Giay | No den tu dau | Giam duoc bang cach nao |
|---|---|---|---|
| Health-check detect floor | 14.9s | `interval_s x threshold` trong `reports/health-events.jsonl:2` (3 lan timeout 2.0s lien tiep de chong flapping) | Ha interval xuong (vi du 2s) hoac threshold xuong 2 (tang nguy co flapping) |
| Operator & Runbook confirmation delay | 9.5s | Tu health detect den khi runbook restore bat dau: orchestration trigger va Step 1 xac minh outage 3 probe tranh false positive trong `reports/runbook-run.jsonl:2` | Dung event-driven webhook thay vi polling orchestration; toi uu timeout probe xac minh |
| Snapshot restore | 0.1s | 2_restore -> 3_scale trong `reports/failover-events.jsonl:18` (copy file SQLite vectors va model weights) | Dung snapshot cap storage volume (EBS/ZFS copy-on-write) hoac CDC/WAL streaming replication |
| GPU pool warm-up | 6.6s | `waited_s` o `4_wait_ready` trong `reports/failover-events.jsonl:20` (WARMUP_SECONDS=5.0s timer + kiem tra model weights & vector stats) | Duy tri hot standby pool hoac giu weights nap san trong bo nho / VRAM |
| DNS/LB TTL cache | 1.0s | `t_recovered - t_cutover` tu `reports/failover-events.jsonl:21` den `reports/drill-2-withdr.jsonl:39` (cache edge proxy reload) | Ha TTL cache xuong 1s hoac chu dong invalidate / purge cache tai LB ngay sau cutover |

Tong cong RTO: `14.9s + 9.5s + 0.1s + 6.6s + 1.0s = 32.1s`, doi soat khop 100% voi ket qua do tu loadgen dong ho `32.1s` trong `reports/measure-drill-2.json`.
