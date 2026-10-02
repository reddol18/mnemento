"""Compare interpreter configurations (model / thinking budget) on the development questions.

    uv run python examples/tune_interpreter.py

Writes docs/demo/0003-interpreter-tuning.md/json. Development questions only — the benchmark uses
a separate seeded dataset and unseen questions (ADR-0008).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from mnemento.demo import DEMO_NOW, open_demo
from mnemento.keeper import ClaudeCLIAdapter, Keeper

CONFIGS = [
    ("haiku / default thinking", dict(model="haiku")),
    ("haiku / thinking off", dict(model="haiku", max_thinking_tokens=0)),
    ("sonnet / thinking off", dict(model="sonnet", max_thinking_tokens=0)),
]

VIEWED_NO_RESULT = {"app_o01", "app_o02", "app_o03", "app_s02", "app_s07", "app_s09", "app_s10"}
Q4_EXPECTED = {("2026-09", "top10"): (3, 6), ("2026-09", "top30"): (4, 6),
               ("2026-10", "top10"): (3, 4), ("2026-10", "top30"): (0, 9)}


def q4_ok(ans) -> bool:
    """Correct if some measure reproduces viewed/n for each (month, rate) group."""
    if ans.status != "answered" or ans.result["mode"] != "aggregate":
        return False
    got = {}
    for g in ans.result["groups"]:
        month = next((v for k, v in g["group"].items() if k.endswith("_month")), None)
        rate = g["group"].get("expected_rate")
        for v in g["measures"].values():
            if (month, rate) in Q4_EXPECTED and (v, g["n"]) == Q4_EXPECTED[(month, rate)]:
                got[(month, rate)] = True
    return len(got) == 4


CHECKS = [
    ("Q2", "Gasang Tech 예전에 지원한 적 있나?", lambda a: a.status == "answered" and a.evidence == ["app_o02"]),
    ("Q3", "열람됐는데 결과 없는 곳은?", lambda a: a.status == "answered" and set(a.evidence) == VIEWED_NO_RESULT),
    ("Q4", "예상 합격률 상위 10%가 30%보다 먼저 열람되나? 지난번에도 그랬나?", q4_ok),
]


def main() -> None:
    now = datetime.fromisoformat(DEMO_NOW)
    ledger = open_demo()
    rows = []
    for label, cfg in CONFIGS:
        keeper = Keeper(ledger, ClaudeCLIAdapter(**cfg))
        keeper.pipeline.cache = None  # measure interpretation, not the cache
        for qid, q, check in CHECKS:
            a = keeper.ask(q, now=now)
            t = a.trace["totals"]
            row = {"config": label, "q": qid, "correct": bool(check(a)), "status": a.status,
                   "spec": a.spec, **{k: t[k] for k in ("input_tokens", "output_tokens", "thinking_tokens",
                                                         "cost_usd", "total_ms", "llm_overhead_ms", "llm_model_ms")}}
            rows.append(row)
            print(f"{label:28} {qid} correct={row['correct']} out={row['output_tokens']} "
                  f"(thinking {row['thinking_tokens']}) {row['total_ms']/1000:.1f}s ${row['cost_usd']}", flush=True)
    out = Path("docs/demo")
    out.mkdir(parents=True, exist_ok=True)
    (out / "0003-interpreter-tuning.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Interpreter tuning (development questions, fictional demo data)", "",
             f"Generated {datetime.now().astimezone().isoformat()} by `examples/tune_interpreter.py`. "
             "One run per cell — indicative, not a benchmark.", "",
             "| config | Q | correct | input tok | output tok | thinking tok | cost USD | total s | model s |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['config']} | {r['q']} | {'✅' if r['correct'] else '❌'} | {r['input_tokens']} | "
                     f"{r['output_tokens']} | {r['thinking_tokens']} | {r['cost_usd']} | {r['total_ms']/1000:.1f} | "
                     f"{r['llm_model_ms']/1000:.1f} |")
    (out / "0003-interpreter-tuning.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
