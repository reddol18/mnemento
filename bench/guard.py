"""External cost guard for a benchmark run (the runner's own --max-cost is the first line of defence).

    uv run python -m bench.guard --run <run-id> --cap 15 --match "bench.run"

Every 20 s it sums the list-price cost recorded in the run's results.jsonl since the guard started;
over the cap it kills the matching benchmark processes. It exits when no matching process is left.

Counting processes excludes the guard itself and everything it spawned (the v0 guard counted the
PowerShell process it launched to list processes, whose command line contains the pattern, and so
never exited).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

RESULTS = Path(__file__).resolve().parents[1] / "bench" / "results"


def descendants(pid: int, parents: dict[int, int]) -> set[int]:
    """All processes whose ancestor chain contains `pid` (`parents`: pid -> parent pid)."""
    out: set[int] = set()
    changed = True
    while changed:
        changed = False
        for child, parent in parents.items():
            if child not in out and (parent == pid or parent in out):
                out.add(child)
                changed = True
    return out


def matching(processes: list[dict], pattern: str, self_pid: int) -> list[int]:
    """Pids whose command line contains `pattern`, excluding `self_pid`, its descendants and its
    ancestors (the shell that started the guard also has the pattern in its command line)."""
    parents = {p["pid"]: p["ppid"] for p in processes}
    excluded = {self_pid} | descendants(self_pid, parents)
    pid = self_pid
    while pid in parents and parents[pid] not in excluded:  # ancestors
        pid = parents[pid]
        excluded.add(pid)
    return [p["pid"] for p in processes if pattern in (p.get("cmd") or "") and p["pid"] not in excluded]


def list_processes() -> list[dict]:
    if sys.platform == "win32":
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress"],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        rows = json.loads(out.stdout or "[]")
        return [{"pid": r["ProcessId"], "ppid": r["ParentProcessId"], "cmd": r.get("CommandLine") or ""} for r in rows]
    out = subprocess.run(["ps", "-eo", "pid=,ppid=,args="], capture_output=True, text=True)
    procs = []
    for line in out.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) >= 2:
            procs.append({"pid": int(parts[0]), "ppid": int(parts[1]), "cmd": parts[2] if len(parts) > 2 else ""})
    return procs


def spent_since(results: Path, offset: int) -> float:
    if not results.exists():
        return 0.0
    lines = results.read_text(encoding="utf-8").splitlines()[offset:]
    total = 0.0
    for line in lines:
        r = json.loads(line)
        calls = r.get("llm", [])[-1:] if r["system"] == "Mn" else r.get("llm", [])
        total += sum((u.get("cost_usd") or 0) for u in calls)
    return total


def kill(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        os.kill(pid, 9)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--cap", type=float, required=True)
    ap.add_argument("--match", default="bench.run")
    ap.add_argument("--interval", type=float, default=20)
    args = ap.parse_args()
    results = RESULTS / args.run / "results.jsonl"
    offset = len(results.read_text(encoding="utf-8").splitlines()) if results.exists() else 0
    time.sleep(args.interval)
    while True:
        spent = spent_since(results, offset)
        pids = matching(list_processes(), args.match, os.getpid())
        if spent > args.cap:
            for pid in pids:
                kill(pid)
            print(f"cost cap hit: ${spent:.2f} > ${args.cap:.2f}; killed {pids}", flush=True)
            return
        if not pids:
            print(f"run ended: ${spent:.2f} spent since the guard started", flush=True)
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
