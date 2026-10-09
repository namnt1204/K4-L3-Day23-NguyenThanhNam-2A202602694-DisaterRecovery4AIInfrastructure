"""BƯỚC 3b — SINH VIÊN VIẾT. Cutover sang region phụ.

5 bước, THỨ TỰ QUAN TRỌNG (§2 Kiến Trúc Tham Chiếu: DNS/LB, compute, state là 3 lớp riêng):
  1_verify_target    — /v1/state của region phụ: weights? vector count? pool_state?
  2_restore_snapshot — gọi state/snapshot.py get + state/snapshot.py rpo()
                       Log BẮT BUỘC: rpo_seconds, docs_lost, embed_model_version.
                       (§3: "backup index nhưng quên backup embedding model version
                        -> index không tương thích khi restore")
  3_scale_pool       — ghi "full" vào state/region-<t>/pool_state (warm -> full)
  4_wait_ready       — POLL /readyz tới khi 200. Region phụ có WARMUP_SECONDS —
                       đây là GPU pool warm-up của §4, nó nằm trong RTO của bạn.
  5_dns_cutover      — ghi region đích vào edge/active_region

BẪY: nếu bạn đổi edge/active_region TRƯỚC bước 4, user sẽ nhận 503 từ CẢ HAI region
và RTO của bạn dài hơn, không ngắn hơn. Nếu bước 4 timeout -> ABORT, KHÔNG cutover.

Mỗi bước ghi 1 dòng vào reports/failover-events.jsonl với ts + step.
Không có dòng 5_dns_cutover = tools/measure_rto.py không tìm được t_cutover = mất điểm.

Chạy:  python dr/failover.py --target b --backend fs
"""
import argparse
import json
import pathlib
import sys
import time

import httpx

sys.path.insert(0, ".")
from state import snapshot  # noqa: E402

URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}
LOG = pathlib.Path("reports/failover-events.jsonl")


def emit(**kw):
    """Append 1 dòng JSONL có ts + iso vào LOG, và print ra stdout."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    t = time.time()
    rec = {"ts": t, "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)), **kw}
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
        f.flush()
    print("FAILOVER", json.dumps(rec))
    return rec


def state_of(region: str, timeout: float = 2.0) -> dict:
    """Lấy trạng thái hiện tại (/v1/state) của 1 region."""
    try:
        r = httpx.get(f"{URL[region]}/v1/state", timeout=timeout)
        if r.status_code == 200:
            return r.json()
        return {"region": region, "error": f"status_{r.status_code}"}
    except Exception as e:
        return {"region": region, "error": type(e).__name__}


def failover(target: str, backend: str, wait: float) -> dict:
    """5 bước failover đúng thứ tự tham chiếu."""
    primary = "b" if target == "a" else "a"

    # 1. 1_verify_target
    target_state = state_of(target)
    emit(step="1_verify_target", target=target, primary=primary,
         backend=backend, target_state=target_state)

    # 2. 2_restore_snapshot
    try:
        snap_meta = snapshot.get(target, backend)
    except (Exception, SystemExit) as e:
        err_msg = str(e) or type(e).__name__
        emit(step="2_restore_snapshot", target=target, backend=backend,
             ok=False, error=err_msg)
        return {
            "ok": False,
            "target": target,
            "backend": backend,
            "step_failed": "2_restore_snapshot",
            "error": f"Snapshot restore failed: {err_msg}",
        }

    primary_db = pathlib.Path(f"state/region-{primary}/vectors.sqlite")
    restored_db = pathlib.Path(f"state/region-{target}/vectors.sqlite")
    rpo_info = snapshot.rpo(primary_db, restored_db)
    rpo_seconds = rpo_info.get("rpo_seconds")
    docs_lost = rpo_info.get("docs_lost")
    embed_model_version = snap_meta.get("embed_model_version", "unknown")

    emit(step="2_restore_snapshot", target=target, backend=backend,
         rpo_seconds=rpo_seconds, docs_lost=docs_lost,
         embed_model_version=embed_model_version, snapshot_meta=snap_meta)

    # 3. 3_scale_pool
    pool_file = pathlib.Path(f"state/region-{target}/pool_state")
    pool_file.parent.mkdir(parents=True, exist_ok=True)
    pool_file.write_text("full", encoding="ascii")
    emit(step="3_scale_pool", target=target, pool_state="full",
         note="warm -> full, GPU warmup timer started")

    # 4. 4_wait_ready
    ready = False
    t0 = time.time()
    last_reasons = []

    while time.time() - t0 < wait:
        try:
            resp = httpx.get(f"{URL[target]}/readyz", timeout=2.0)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ready"):
                    ready = True
                    break
                last_reasons = data.get("reasons", [])
            else:
                try:
                    last_reasons = resp.json().get("reasons", [f"status_{resp.status_code}"])
                except Exception:
                    last_reasons = [f"status_{resp.status_code}"]
        except Exception as e:
            last_reasons = [type(e).__name__]
        time.sleep(0.5)

    waited_s = round(time.time() - t0, 2)

    if not ready:
        emit(step="4_wait_ready", target=target, ok=False, waited_s=waited_s,
             reasons=last_reasons, error="Target region failed to become ready before timeout")
        return {
            "ok": False,
            "target": target,
            "backend": backend,
            "step_failed": "4_wait_ready",
            "rpo_seconds": rpo_seconds,
            "docs_lost": docs_lost,
            "embed_model_version": embed_model_version,
            "waited_s": waited_s,
            "reasons": last_reasons,
            "error": f"Target region-{target} not ready after {waited_s}s",
        }

    emit(step="4_wait_ready", target=target, ok=True, waited_s=waited_s)

    # 5. 5_dns_cutover
    active_file = pathlib.Path("edge/active_region")
    active_file.parent.mkdir(parents=True, exist_ok=True)
    active_file.write_text(target, encoding="ascii")
    current_active = active_file.read_text(encoding="ascii").strip()

    if current_active != target:
        emit(step="5_dns_cutover", target=target, ok=False,
             error=f"Pointer mismatch: expected {target}, got {current_active}")
        return {
            "ok": False,
            "target": target,
            "backend": backend,
            "step_failed": "5_dns_cutover",
            "error": "Failed to update edge/active_region pointer",
        }

    emit(step="5_dns_cutover", target=target, active_region=current_active,
         ok=True, note="DNS cutover complete")

    final_state = state_of(target)
    return {
        "ok": True,
        "target": target,
        "backend": backend,
        "rpo_seconds": rpo_seconds,
        "docs_lost": docs_lost,
        "embed_model_version": embed_model_version,
        "waited_s": waited_s,
        "active_region": current_active,
        "vector_count": final_state.get("count"),
        "weights": final_state.get("weights"),
        "pool_state": final_state.get("pool_state"),
        "state": final_state,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--target", default="b", choices=["a", "b"])
    p.add_argument("--backend", default="fs", choices=["fs", "minio"])
    p.add_argument("--wait", type=float, default=60)
    a = p.parse_args()
    print(json.dumps(failover(a.target, a.backend, a.wait), indent=2))
