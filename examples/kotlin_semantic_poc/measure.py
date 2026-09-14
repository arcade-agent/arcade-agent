"""Measure the harness and descendant RSS sum (shared pages may be double-counted)."""
import json
import subprocess
import time
from pathlib import Path

root = Path(__file__).resolve().parent
started = time.monotonic()
peak = 0
samples = 0
with (root / "results.json").open("w") as output:
    process = subprocess.Popen(["node", "run.cjs"], cwd=root, stdout=output)
    try:
        while process.poll() is None:
            rows = subprocess.check_output(["ps", "-eo", "pid=,ppid=,rss="], text=True)
            table = [tuple(map(int, row.split())) for row in rows.splitlines() if row.strip()]
            descendants = {process.pid}
            while True:
                expanded = descendants | {pid for pid, parent, _ in table if parent in descendants}
                if expanded == descendants:
                    break
                descendants = expanded
            peak = max(peak, sum(rss for pid, _, rss in table if pid in descendants))
            samples += 1
            if time.monotonic() - started > 420:
                process.terminate()
                raise TimeoutError("Harness exceeded 420 seconds")
            time.sleep(0.05)
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
(root / "results-memory.json").write_text(json.dumps({
    "wall_seconds": time.monotonic() - started,
    "peak_process_tree_rss_sum_kib": peak,
    "samples": samples,
    "note": "Node plus descendants; sampled ~50ms; shared pages may be counted multiple times",
    "exit_code": process.returncode,
}, indent=2))
raise SystemExit(process.returncode)
