"""Windows-native Chaos Adapter for Lab Day 23.
Replaces Unix SIGSTOP/SIGCONT with psutil.Process.suspend() and .resume()
to simulate network partition (netblock) and process kill (stop) on Windows 10 native.

Maintains exact JSONL log schema in chaos/chaos-events.jsonl for compatibility with
tools/measure_rto.py and tests/test_rto_evidence.py.
"""
import argparse
import json
import os
import pathlib
import time
import httpx
import psutil

EVENTS = pathlib.Path("chaos/chaos-events.jsonl")
PID_DIR = pathlib.Path("run")
URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}


def event(**kw):
    EVENTS.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.time(), "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()), **kw}
    with EVENTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    print("CHAOS", json.dumps(rec))
    return rec


def is_ready(region: str, timeout=1.5) -> bool:
    try:
        return httpx.get(f"{URL[region]}/readyz", timeout=timeout).status_code == 200
    except Exception:
        return False


def is_alive(region: str, timeout=1.5) -> bool:
    try:
        return httpx.get(f"{URL[region]}/healthz", timeout=timeout).status_code == 200
    except Exception:
        return False


def pid_of(region: str) -> int | None:
    f = PID_DIR / f"region-{region}.pid"
    if not f.exists():
        return None
    try:
        pid = int(f.read_text(encoding="ascii").strip())
    except Exception:
        return None
    if not psutil.pid_exists(pid):
        return None
    try:
        p = psutil.Process(pid)
        if p.is_running() and p.status() != psutil.STATUS_ZOMBIE:
            return pid
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None
    return None


def kill(region: str, mode: str, backend: str, force_both: bool, mock: bool):
    other = "b" if region == "a" else "a"
    other_alive = is_alive(other)

    # Nguyên tắc an toàn: từ chối nếu region còn lại không alive
    if not other_alive and not force_both:
        event(action="refused", region=region, mode=mode, backend=backend, mock=mock,
              other_region=other, other_alive=other_alive, forced_both=force_both,
              reason=f"region-{other} khong phan hoi /healthz -> giet region-{region} nua "
                     f"la double outage, RTO do duoc se vo nghia")
        raise SystemExit(
            f"CHAN LAI: region-{other} dang khong sống. Chạy `restore --region {other}` trước.\n"
            f"(Muốn ép: --i-really-want-both, nhưng drill sẽ bị đánh dấu INVALID.)")

    ev = event(action="kill", region=region, mode=mode, backend=backend, mock=mock,
               other_region=other, other_alive=other_alive, forced_both=force_both,
               note="t_outage_start — moc 0 cua RTO clock")

    pid = pid_of(region)
    if pid is None:
        raise SystemExit(f"khong tim thay PID cua region-{region} trong {PID_DIR}")

    proc = psutil.Process(pid)
    if mode == "netblock":
        # Windows-native netblock: suspend process threads -> TCP socket stops responding
        proc.suspend()
    else:  # mode == "stop"
        try:
            proc.resume()
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except psutil.TimeoutExpired:
            proc.kill()

    return ev


def restore(region: str, backend: str):
    pid = pid_of(region)
    if pid:
        try:
            proc = psutil.Process(pid)
            proc.resume()
            return event(action="restore", region=region, backend=backend,
                         method="psutil_resume", pid=pid)
        except Exception as e:
            return event(action="restore", region=region, backend=backend,
                         method="failed_resume", error=str(e))

    return event(action="restore", region=region, backend=backend, method="need_manual_start",
                 note="process da bi terminate hoac khong chay, chay scripts/windows/up.ps1 lai")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Windows-native Chaos Adapter for Lab Day 23")
    p.add_argument("cmd", nargs="?", default="kill", choices=["kill", "restore", "status"])
    p.add_argument("--region", default="a", choices=["a", "b"])
    p.add_argument("--mode", default="netblock", choices=["stop", "netblock"])
    p.add_argument("--backend", default="bare", choices=["bare", "docker"])
    p.add_argument("--mock", action="store_true",
                   help="pin tham so thoi gian -> cham diem reproducible; ham y --backend bare")
    p.add_argument("--i-really-want-both", action="store_true")
    a = p.parse_args()

    backend = a.backend or ("bare" if a.mock else "bare")

    if a.cmd == "status":
        print(json.dumps({r: {"alive": is_alive(r), "ready": is_ready(r)} for r in "ab"}, indent=2))
    elif a.cmd == "restore":
        restore(a.region, backend)
    else:
        kill(a.region, a.mode, backend, a.i_really_want_both, a.mock)
