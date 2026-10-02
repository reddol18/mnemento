"""Turn bench/results/<run>/results.jsonl into the README benchmark tables.

    uv run python -m bench.report --run <run-id> [--out bench/results/<run>/report.md]

Columns (ADR-0008): accuracy on dev and unseen questions separately; time mean / p95 including and
excluding the CLI's process overhead; input/output tokens including and excluding the fixed CLI
overhead (from bench/results/calibration.json); list-price cost reported by the CLI.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

RESULTS = Path(__file__).resolve().parents[1] / "bench" / "results"
SYSTEM_NAMES = {"B0": "B0 full context", "B1": "B1 Claude Code memory (MEMORY.md + Grep/Read)",
                "M": "M Mnemento (template answer)", "Mn": "M+ Mnemento + LLM narration"}


def p95(xs: list[float]) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))] if xs else 0.0


def summarize(rows: list[dict], calib: dict) -> list[dict]:
    over_in = calib.get("overhead_input_tokens", {"plain": 0, "tools": 0})
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["scale"], r["system"])].append(r)
    out = []
    for (scale, system), rs in sorted(groups.items()):
        measurable = [r for r in rs if r.get("error") != "context limit exceeded (not measurable)"]
        if not measurable:
            out.append({"scale": scale, "system": system, "not_measurable": True})
            continue
        def acc(s):  # noqa: E306
            xs = [r for r in measurable if r["set"] == s]
            return (sum(r["correct"] for r in xs), len(xs))
        wall = [r.get("wall_ms", 0) / 1000 for r in measurable]
        llm = [u for r in measurable for u in r.get("llm", [])]
        per_q_over = defaultdict(float)
        for r in measurable:
            per_q_over[id(r)] = sum((u.get("overhead_ms") or 0) for u in r.get("llm", [])) / 1000
        no_over = [r.get("wall_ms", 0) / 1000 - per_q_over[id(r)] for r in measurable]
        tin = sum(u["input_tokens"] + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                  for u in llm)
        tout = sum(u["output_tokens"] for u in llm)
        fixed = over_in["tools" if system == "B1" else "plain"] * len(llm)
        n = len(measurable)
        out.append({
            "scale": scale, "system": system, "n": n,
            "dev": acc("dev"), "unseen": acc("unseen"),
            "time_mean": statistics.mean(wall), "time_p95": p95(wall),
            "time_mean_no_overhead": statistics.mean(no_over), "time_p95_no_overhead": p95(no_over),
            "llm_calls_per_q": len(llm) / n,
            "in_per_q": tin / n, "in_per_q_no_overhead": max(0, tin - fixed) / n,
            "cache_read_per_q": sum(u.get("cache_read_input_tokens", 0) for u in llm) / n,
            "out_per_q": tout / n,
            "cost_per_q": sum(u.get("cost_usd") or 0 for u in llm) / n,
        })
    return out


def render(summary: list[dict], meta: dict) -> str:
    lines = [f"Model `{meta['model']}` via Claude Code CLI · run `{meta['run']}` · frozen `{meta['frozen_hash']}`"
             f" · git `{meta.get('git')}`", "",
             "| scale | system | accuracy dev | accuracy unseen | time mean / p95 (s) | excl. CLI overhead (s) | "
             "LLM calls/q | input tok/q (excl. overhead) | output tok/q | cost/q (USD) |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for s in summary:
        name = SYSTEM_NAMES.get(s["system"], s["system"])
        if s.get("not_measurable"):
            lines.append(f"| {s['scale']:,} | {name} | not measurable (context limit) | | | | | | | |")
            continue
        pct = lambda a: f"{a[0]}/{a[1]} ({a[0] / a[1]:.0%})" if a[1] else "—"  # noqa: E731
        lines.append(
            f"| {s['scale']:,} | {name} | {pct(s['dev'])} | {pct(s['unseen'])} | "
            f"{s['time_mean']:.1f} / {s['time_p95']:.1f} | {s['time_mean_no_overhead']:.1f} / "
            f"{s['time_p95_no_overhead']:.1f} | {s['llm_calls_per_q']:.2f} | {s['in_per_q']:,.0f} "
            f"({s['in_per_q_no_overhead']:,.0f}) | {s['out_per_q']:,.0f} | {s['cost_per_q']:.4f} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    d = RESULTS / args.run
    rows = [json.loads(line) for line in (d / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    calib_path = RESULTS / "calibration.json"
    calib = json.loads(calib_path.read_text()) if calib_path.exists() else {}
    summary = summarize(rows, calib)
    meta = json.loads((d / "meta.json").read_text())
    md = render(summary, meta)
    (Path(args.out) if args.out else d / "report.md").write_text(md, encoding="utf-8")
    (d / "summary.json").write_text(json.dumps(summary, indent=1, default=list), encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
