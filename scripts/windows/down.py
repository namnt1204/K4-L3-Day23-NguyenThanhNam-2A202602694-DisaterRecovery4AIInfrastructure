"""Clean shutdown for Lab Day 23 services on Windows.
Reads stored PIDs, resumes suspended processes before termination,
only stops lab-owned processes (preventing killing unrelated Python processes),
and cleans up stale PID files.
"""
import os
import pathlib
import sys
import psutil

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
PID_DIR = REPO_ROOT / "run"


def is_lab_process(proc: psutil.Process) -> bool:
    """Verifies that the process actually belongs to the lab stack."""
    try:
        cmdline = " ".join(proc.cmdline()).lower()
        exe = proc.exe().lower()
        cwd = proc.cwd().lower()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False

    repo_str = str(REPO_ROOT).lower()
    if repo_str in cmdline or repo_str in cwd:
        return True
    if "myenv" in exe or "myenv" in cmdline:
        return True
    if "run_service" in cmdline or "serving.app" in cmdline or "edge.proxy" in cmdline:
        return True
    return False


def stop_all():
    if not PID_DIR.exists():
        print("all stopped")
        return

    pid_files = list(PID_DIR.glob("*.pid"))
    for pid_file in pid_files:
        try:
            content = pid_file.read_text(encoding="ascii").strip()
            if not content:
                pid_file.unlink(missing_ok=True)
                continue
            pid = int(content)
        except Exception:
            pid_file.unlink(missing_ok=True)
            continue

        if psutil.pid_exists(pid):
            try:
                proc = psutil.Process(pid)
                if is_lab_process(proc):
                    # CRITICAL ON WINDOWS: Resume before terminating if process was suspended
                    try:
                        proc.resume()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass

                    try:
                        proc.terminate()
                        proc.wait(timeout=3)
                    except psutil.TimeoutExpired:
                        proc.kill()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                    print(f"Stopped {pid_file.stem} (PID {pid})")
                else:
                    print(f"PID {pid} does not match lab process signature. Skipping.")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        pid_file.unlink(missing_ok=True)

    print("all stopped")


if __name__ == "__main__":
    stop_all()
