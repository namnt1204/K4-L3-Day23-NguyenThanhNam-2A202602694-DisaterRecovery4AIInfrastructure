"""BƯỚC 3a — SINH VIÊN VIẾT. Health checker cho 2 region.

Yêu cầu (đọc §4 "Kiến Trúc Health-Check-Based Failover" + §2 "DNS Failover"):
  1. Poll /readyz của CẢ HAI region mỗi `interval` giây (mặc định 5s).
     Dùng /readyz, KHÔNG dùng /healthz. /healthz chỉ nói "process còn sống" —
     region có process sống nhưng vector DB rỗng thì vẫn không serve được.
  2. Chỉ đổi trạng thái sau `threshold` lần fail LIÊN TIẾP (mặc định 3).
     Một lần fail không phải outage. Đây là chống flapping (§4 Anti-Patterns).
  3. Ghi 1 dòng JSONL MỖI LẦN ĐỔI TRẠNG THÁI (không ghi mỗi lần poll — log sẽ ngập).
     Dòng bắt buộc có: ts, region, to (HEALTHY|UNHEALTHY), reason,
     interval_s, threshold. Thiếu interval_s/threshold thì tools/measure_rto.py
     không tính được detect floor -> mất điểm.

Chạy:  python dr/health_checker.py --interval 5 --threshold 3 --duration 300 \
              --out reports/health-events.jsonl

CÂU HỎI PHẢI TRẢ LỜI TRƯỚC KHI VIẾT (ghi câu trả lời vào reports/postmortem.md):
  interval=5s, threshold=3 -> sớm nhất bạn có thể phát hiện outage là bao nhiêu giây?
  Con số đó nằm TRONG RTO của bạn. Muốn RTO 5 phút thì được phép chọn interval bao nhiêu?
"""
import argparse
import json
import pathlib
import time

import httpx

URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}


def probe(region: str, timeout: float) -> tuple[bool, str]:
    """Trả về (ready, reason). Timeout PHẢI có — netblock làm request treo mãi."""
    url = f"{URL[region]}/readyz"
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url)
        if resp.status_code == 200:
            try:
                body = resp.json()
                if body.get("ready", True):
                    return True, "ready"
                reasons = body.get("reasons", [])
                return False, ",".join(reasons) if reasons else "not_ready"
            except Exception:
                return True, "ready"
        elif resp.status_code == 503:
            try:
                body = resp.json()
                reasons = body.get("reasons", [])
                return False, ",".join(reasons) if reasons else "status_503"
            except Exception:
                return False, "status_503"
        else:
            return False, f"status_{resp.status_code}"
    except httpx.TimeoutException:
        return False, "timeout"
    except httpx.ConnectError:
        return False, "connect_error"
    except Exception as e:
        return False, type(e).__name__


def run(interval: float, timeout: float, threshold: int, duration: float, out: pathlib.Path):
    """Vòng lặp poll + phát hiện transition + ghi JSONL."""
    out = pathlib.Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    states = {"a": "HEALTHY", "b": "HEALTHY"}
    fails = {"a": 0, "b": 0}

    end_time = time.time() + duration
    while time.time() < end_time:
        t_start = time.time()

        for r in ("a", "b"):
            ready, reason = probe(r, timeout)
            if ready:
                fails[r] = 0
                if states[r] != "HEALTHY":
                    states[r] = "HEALTHY"
                    rec = {
                        "event": "state_change",
                        "ts": time.time(),
                        "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time())),
                        "region": r,
                        "to": "HEALTHY",
                        "reason": reason,
                        "interval_s": interval,
                        "threshold": threshold,
                        "consecutive_fails": 0,
                    }
                    with out.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(rec) + "\n")
                        f.flush()
                    print("HEALTH", json.dumps(rec))
            else:
                fails[r] += 1
                if fails[r] >= threshold and states[r] != "UNHEALTHY":
                    states[r] = "UNHEALTHY"
                    rec = {
                        "event": "state_change",
                        "ts": time.time(),
                        "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time())),
                        "region": r,
                        "to": "UNHEALTHY",
                        "reason": reason,
                        "interval_s": interval,
                        "threshold": threshold,
                        "consecutive_fails": fails[r],
                    }
                    with out.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(rec) + "\n")
                        f.flush()
                    print("HEALTH", json.dumps(rec))

        elapsed = time.time() - t_start
        sleep_s = max(0.0, interval - elapsed)
        remaining = end_time - time.time()
        if remaining <= 0:
            break
        time.sleep(min(sleep_s, remaining))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--interval", type=float, default=5.0)
    p.add_argument("--timeout", type=float, default=2.0)
    p.add_argument("--threshold", type=int, default=3)
    p.add_argument("--duration", type=float, default=300)
    p.add_argument("--out", default="reports/health-events.jsonl")
    a = p.parse_args()
    run(a.interval, a.timeout, a.threshold, a.duration, pathlib.Path(a.out))
