"""Demo: answer docs/eval Q1-Q4-style questions on the fictional dataset and write a log.

    uv run python examples/demo_queries.py [--model haiku] [--out docs/demo]

Uses the Claude Code CLI as the LLM (your local login; no API key). Q1 is answered by the
rule-based fast path without any LLM call.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from mnemento.demo import DEMO_NOW, open_demo
from mnemento.keeper import ClaudeCLIAdapter, Keeper

QUESTIONS = [
    ("Q1", "10/2 사람인 지원 몇 곳?", False),
    ("Q2", "Gasang Tech 예전에 지원한 적 있나?", False),
    ("Q3", "열람됐는데 결과 없는 곳은?", False),
    ("Q4", "예상 합격률 상위 10%가 30%보다 먼저 열람되나? 지난번에도 그랬나?", True),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--out", default="docs/demo")
    args = ap.parse_args()

    ledger = open_demo()
    keeper = Keeper(ledger, ClaudeCLIAdapter(model=args.model))
    now = datetime.fromisoformat(DEMO_NOW)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    records = []
    for qid, question, narrate in QUESTIONS:
        ans = keeper.ask(question, now=now, narrate=narrate)
        records.append({"id": qid, **ans.to_dict()})
        t = ans.trace["totals"]
        print(f"{qid} [{ans.status}, path={ans.trace['path']}] llm_calls={t['llm_calls']} "
              f"in={t['input_tokens']} out={t['output_tokens']} total={t['total_ms']:.0f}ms "
              f"(cli overhead {t['llm_overhead_ms']:.0f} / model {t['llm_model_ms']:.0f} / code {t['code_ms']:.1f})",
              flush=True)

    meta = {"model": args.model, "adapter": "claude-cli", "now": DEMO_NOW,
            "dataset": "mnemento.demo (fictional)", "generated_at": datetime.now().astimezone().isoformat()}
    (out_dir / "0002-demo-log.json").write_text(
        json.dumps({"meta": meta, "answers": records}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out_dir / "0002-demo-log.md").write_text(render_md(meta, records), encoding="utf-8")
    ledger.close()
    return 0


def render_md(meta: dict, records: list[dict]) -> str:
    lines = [
        "# Demo log — task 0002 (question pipeline)",
        "",
        f"- Model: `{meta['model']}` via `{meta['adapter']}` adapter · dataset: {meta['dataset']} · "
        f"\"now\" fixed at `{meta['now']}`",
        f"- Generated: {meta['generated_at']} by `examples/demo_queries.py`",
        "- Time columns: **CLI overhead** = process start + CLI bookkeeping, **model** = API time reported "
        "by the CLI, **code** = everything else (DB queries, compile, answer).",
        "",
        "| # | path | LLM calls | input tok | output tok | cost (USD) | total ms | CLI overhead ms | model ms | code ms |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in records:
        t = r["trace"]["totals"]
        lines.append(f"| {r['id']} | {r['trace']['path']} | {t['llm_calls']} | {t['input_tokens']} | "
                     f"{t['output_tokens']} | {t['cost_usd']} | {t['total_ms']:.0f} | {t['llm_overhead_ms']:.0f} | "
                     f"{t['llm_model_ms']:.0f} | {t['code_ms']:.1f} |")
    for r in records:
        lines += ["", f"## {r['id']}. {r['question']}", "", f"**Status:** {r['status']}", ""]
        if r.get("spec"):
            lines += ["**QuerySpec**", "", "```json", json.dumps(r["spec"], ensure_ascii=False, indent=2), "```", ""]
        if r.get("sql"):
            lines += ["**SQL** (parameters bound separately)", "", "```sql", r["sql"], "```", "",
                      f"params: `{json.dumps(r['params'], ensure_ascii=False)}`", ""]
        lines += ["**Answer**", "", "```", r["text"], "```", ""]
        if r.get("options"):
            lines += ["**Options:** " + "; ".join(r["options"]), ""]
        lines += [f"**Evidence ({len(r['evidence'])}):** " + (", ".join(r["evidence"][:30]) or "—"), ""]
        lines += ["**Stages (ms):** " + ", ".join(f"{k} {v:.1f}" for k, v in r["trace"]["stages_ms"].items()), ""]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
