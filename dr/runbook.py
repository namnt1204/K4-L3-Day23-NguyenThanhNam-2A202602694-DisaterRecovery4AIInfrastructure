"""BƯỚC 3c — SINH VIÊN VIẾT. Tự động hoá runbook §4 "Runbook: Region Chính Down".

7 bước trên slide, mỗi bước 1 dòng log có ts. Log này CHÍNH LÀ timeline của postmortem.
  1 xac_nhan_outage          — probe cả 2 region, đừng tin 1 lần fail (dùng nhiều lần
                              hoặc gọi health_checker.probe nếu đã viết xong 3a)
  2 thong_bao_incident       — ts của dòng này là mốc "operator biết tin", LUÔN LUÔN
                              SAU t_outage trong chaos-events (không thể trùng — operator
                              không thể biết ngay giây outage xảy ra). Ghi cả 2 ts vào
                              log để postmortem tính được "độ trễ thông báo".
  3 scale_gpu_pool           — gọi HÀM `failover.failover(...)` MỘT LẦN DUY NHẤT. Hàm
                              đó tự làm đủ 5 bước con (verify/restore/scale/wait/cutover)
                              và tự ghi log riêng vào reports/failover-events.jsonl.
  4 verify_state_replica     — KHÔNG gọi lại failover — chỉ ĐỌC kết quả (vector count +
                              weights ở region phụ) từ dict mà bước 3 trả về, để log vào
                              runbook-run.jsonl cho postmortem đọc 1 chỗ duy nhất.
  5 dns_cutover              — cũng chỉ đọc lại: kết quả cutover có ok hay không.
  6 verify_golden_signals    — 10 request thật vào region phụ: p95 latency + error rate
  7 post_incident            — elapsed_s + lệnh đo RTO

BÁN TỰ ĐỘNG, KHÔNG FULL-AUTO (§4: "failover đầu tiên nên là bán tự động — alert +
1-click confirm — tránh flapping gây failover 2 chiều liên tục"). Mặc định phải hỏi
người vận hành confirm; --auto chỉ dùng trong CI/khi chấm điểm.

Chạy:  python dr/runbook.py --primary a --target b --backend fs
"""
import argparse
import json
import pathlib
import sys
import time

import httpx

sys.path.insert(0, ".")
from dr import failover as fo  # noqa: E402
from dr import health_checker as hc  # noqa: E402

LOG = pathlib.Path("reports/runbook-run.jsonl")
URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}


def step(n: int, name: str, **kw):
    """Ghi 1 dòng {ts, iso, step, name, ...} vào LOG."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    t = time.time()
    rec = {
        "ts": t,
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)),
        "step": n,
        "name": name,
        **kw,
    }
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
        f.flush()
    print("RUNBOOK", json.dumps(rec))
    return rec


def confirm(auto: bool, msg: str) -> bool:
    """auto=True -> True; ngược lại hỏi y/N. Mặc định là No."""
    if auto:
        print(f"{msg} [auto=True -> CONFIRMED]")
        return True
    try:
        ans = input(f"{msg} [y/N]: ").strip().lower()
        return ans in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def run(primary: str, target: str, backend: str, auto: bool) -> dict:
    """7 bước runbook theo đúng chuẩn §4."""
    t_runbook_start = time.time()

    # =========================================================================
    # BƯỚC 1: xac_nhan_outage
    # Kiểm tra primary không ready bằng nhiều lần probe, bảo đảm target còn alive
    # =========================================================================
    primary_probes = []
    for _ in range(3):
        ok, reason = hc.probe(primary, timeout=2.0)
        primary_probes.append((ok, reason))
        if ok:
            break
        time.sleep(0.5)

    primary_down = not any(p[0] for p in primary_probes)

    try:
        target_alive = httpx.get(f"{URL[target]}/healthz", timeout=2.0).status_code == 200
    except Exception:
        target_alive = False

    if not primary_down:
        step(1, "xac_nhan_outage", confirmed=False, primary=primary, target=target,
             primary_probes=primary_probes,
             error="Primary region van dang hoat dong binh thuong, khong trigger failover")
        return {"ok": False, "step": "xac_nhan_outage", "error": "Primary region is still ready"}

    if not target_alive:
        step(1, "xac_nhan_outage", confirmed=False, primary=primary, target=target,
             error="Target region khong song (/healthz fail), tu choi failover de tranh double outage")
        return {"ok": False, "step": "xac_nhan_outage", "error": "Target region is not alive"}

    step(1, "xac_nhan_outage", confirmed=True, primary=primary, target=target,
         primary_probes=primary_probes, target_alive=True)

    # =========================================================================
    # BƯỚC 2: thong_bao_incident
    # Hỏi người vận hành confirm (nếu không --auto), ghi nhận timestamp thông báo
    # =========================================================================
    if not confirm(auto, f"XAC NHAN INCIDENT: Region '{primary}' down. Bat dau failover sang '{target}'?"):
        step(2, "thong_bao_incident", confirmed=False, aborted_by="operator_rejected")
        return {"ok": False, "step": "thong_bao_incident", "error": "Operator aborted failover"}

    t_operator = time.time()

    # Đọc timestamp t_outage từ chaos/chaos-events.jsonl nếu có
    t_outage = None
    chaos_file = pathlib.Path("chaos/chaos-events.jsonl")
    if chaos_file.exists():
        try:
            records = [json.loads(line) for line in chaos_file.read_text(encoding="utf-8").splitlines() if line.strip()]
            kills = [k for k in records if k.get("action") == "kill" and k.get("region") == primary]
            if kills:
                t_outage = kills[-1].get("ts")
        except Exception:
            pass

    notice_delay = round(t_operator - t_outage, 2) if t_outage else None
    step(2, "thong_bao_incident", confirmed=True, t_operator=t_operator,
         t_outage=t_outage, notice_delay_s=notice_delay)

    # =========================================================================
    # BƯỚC 3: scale_gpu_pool
    # Gọi failover.failover(...) DUY NHẤT 1 LẦN để làm 5 bước con
    # =========================================================================
    fo_res = fo.failover(target=target, backend=backend, wait=60.0)
    if not fo_res.get("ok"):
        step(3, "scale_gpu_pool", ok=False, error=fo_res.get("error"), failover_result=fo_res)
        return {"ok": False, "step": "scale_gpu_pool", "error": f"Failover failed: {fo_res.get('error')}", "failover_result": fo_res}

    step(3, "scale_gpu_pool", ok=True, target=target, waited_s=fo_res.get("waited_s"))

    # =========================================================================
    # BƯỚC 4: verify_state_replica
    # Đọc kết quả từ fo_res: vector count, weights, rpo_seconds, docs_lost
    # =========================================================================
    vector_count = fo_res.get("vector_count") or 0
    weights_ok = bool(fo_res.get("weights"))
    rpo_s = fo_res.get("rpo_seconds")
    docs_lost = fo_res.get("docs_lost")
    embed_ver = fo_res.get("embed_model_version")

    replica_valid = (vector_count > 0) and weights_ok
    step(4, "verify_state_replica", target=target, vector_count=vector_count,
         weights=weights_ok, rpo_seconds=rpo_s, docs_lost=docs_lost,
         embed_model_version=embed_ver, replica_valid=replica_valid)

    # =========================================================================
    # BƯỚC 5: dns_cutover
    # Xác nhận kết quả cutover từ fo_res và con trỏ edge/active_region
    # =========================================================================
    active_file = pathlib.Path("edge/active_region")
    current_dns = active_file.read_text(encoding="ascii").strip() if active_file.exists() else None
    cutover_confirmed = (current_dns == target) and (fo_res.get("active_region") == target)

    step(5, "dns_cutover", target=target, current_dns=current_dns,
         cutover_confirmed=cutover_confirmed)

    # =========================================================================
    # BƯỚC 6: verify_golden_signals
    # Gửi 10 HTTP requests thật tới target region, đo latency và error rate
    # =========================================================================
    latencies = []
    errors = 0
    total_reqs = 10
    infer_url = f"{URL[target]}/v1/infer"

    for i in range(total_reqs):
        t_req_start = time.time()
        try:
            resp = httpx.get(infer_url, params={"q": f"hoa don thang {i + 1}"}, timeout=3.0)
            lat_ms = round((time.time() - t_req_start) * 1000, 1)
            if resp.status_code == 200 and resp.json().get("region") == target:
                latencies.append(lat_ms)
            else:
                errors += 1
                latencies.append(lat_ms)
        except Exception:
            errors += 1
            latencies.append(round((time.time() - t_req_start) * 1000, 1))

    latencies.sort()
    # P95 với 10 phần tử: lấy phần tử thứ 10 (chỉ số 9)
    p95_latency_ms = latencies[int(len(latencies) * 0.95)] if latencies else None
    error_rate = round(errors / total_reqs, 2)

    step(6, "verify_golden_signals", target=target, requests_sent=total_reqs,
         errors=errors, error_rate=error_rate, p95_latency_ms=p95_latency_ms,
         latencies_ms=latencies)

    # =========================================================================
    # BƯỚC 7: post_incident
    # Ghi nhận elapsed_s và lệnh đo RTO chính thức
    # =========================================================================
    elapsed_s = round(time.time() - t_runbook_start, 2)
    measure_cmd = "python tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300"

    step(7, "post_incident", elapsed_s=elapsed_s, target=target, primary=primary,
         status="failover_completed", rto_measurement_cmd=measure_cmd,
         note="Runbook hoan tat. Chay measure_rto.py de xac nhan ket qua RTO chinh thuc.")

    return {
        "ok": True,
        "elapsed_s": elapsed_s,
        "primary": primary,
        "target": target,
        "rpo_seconds": rpo_s,
        "docs_lost": docs_lost,
        "embed_model_version": embed_ver,
        "golden_signals": {
            "p95_latency_ms": p95_latency_ms,
            "error_rate": error_rate,
        },
        "measure_cmd": measure_cmd,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--primary", default="a")
    p.add_argument("--target", default="b")
    p.add_argument("--backend", default="fs", choices=["fs", "minio"])
    p.add_argument("--auto", action="store_true")
    a = p.parse_args()
    print(json.dumps(run(a.primary, a.target, a.backend, a.auto), indent=2))
