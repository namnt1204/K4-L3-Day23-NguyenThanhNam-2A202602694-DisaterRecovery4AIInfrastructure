"""Helper runner for starting FastAPI / Uvicorn services cleanly on Windows.
Runs directly in the target Python process, ensuring PID tracking is exact,
redirects stdout/stderr to the specified log file, and configures environment variables.
"""
import argparse
import os
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))
os.chdir(str(REPO_ROOT))

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Windows Service Runner")
    parser.add_argument("--service", choices=["serving", "edge"], required=True)
    parser.add_argument("--region", default="a", choices=["a", "b"])
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--warmup", type=float, default=6.0)
    parser.add_argument("--ttl", type=float, default=5.0)
    parser.add_argument("--log-file", type=str, required=True)
    parser.add_argument("--pid-file", type=str, required=True)
    args = parser.parse_args()

    # 1. Write the real PID of this process
    pid_path = pathlib.Path(args.pid_file).resolve()
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(os.getpid()), encoding="ascii")

    # 2. Redirect stdout and stderr to the log file at low level (C fd) and Python level
    log_path = pathlib.Path(args.log_file).resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "a", buffering=1, encoding="utf-8")
    os.dup2(log_file.fileno(), 1)
    os.dup2(log_file.fileno(), 2)
    sys.stdout = log_file
    sys.stderr = log_file

    # 3. Configure environment variables for the service
    if args.service == "serving":
        os.environ["REGION"] = args.region
        os.environ["STATE_DIR"] = f"state/region-{args.region}"
        os.environ["WARMUP_SECONDS"] = str(args.warmup)
        uvicorn.run("serving.app:app", host="127.0.0.1", port=args.port, log_level="warning")
    else:
        os.environ["EDGE_TTL_SECONDS"] = str(args.ttl)
        os.environ["ACTIVE_REGION_FILE"] = "edge/active_region"
        os.environ["REGION_A_URL"] = "http://127.0.0.1:8001"
        os.environ["REGION_B_URL"] = "http://127.0.0.1:8002"
        uvicorn.run("edge.proxy:app", host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
