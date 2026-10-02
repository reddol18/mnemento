"""The compared systems. All use the same CLI adapter and model (ADR-0008, review conditions).

- B0  full context: every memory file in the prompt, no tools
- B1  Claude Code memory: MEMORY.md (first 200 lines) in the prompt, Read/Grep/Glob over the
      memory folder, the model searches and answers
- M   Mnemento pipeline, template answer (default)
- Mn  Mnemento pipeline + LLM narration (cost/time only; graded fields are identical to M)
"""

from __future__ import annotations

import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from mnemento.keeper import Keeper
from mnemento.keeper.llm import ClaudeCLIAdapter, LLMError

from .grade import ANSWER_SCHEMA
from .questions import MAX_LISTED_IDS, Question
from .render import memory_index_head

ANSWER_RULES = """Answer the user's question about their job applications using ONLY the records provided.
Return the answer object: `number` (the main number, or null), `ids` (record ids such as app_00012
that support the answer, at most 50), `groups` (only when the format asks for groups), `flags`
(small_sample / incomplete_period as described in the format), `text` (short answer in the
question's language). Today is {today} ({tz}). Records marked as invalid/retracted do not count
as applications."""


def _usage_row(u) -> dict[str, Any]:
    return {k: v for k, v in asdict(u).items() if k != "stage"}


def run_b0(q: Question, llm: ClaudeCLIAdapter, context: str, today: str, tz: str) -> dict[str, Any]:
    system = ANSWER_RULES.format(today=today, tz=tz) + "\n\nAll memory files:\n\n" + context
    prompt = f"Question: {q.text}\nFormat: {q.format}"
    t0 = time.perf_counter()
    try:
        r = llm.complete_json(system=system, prompt=prompt, schema=ANSWER_SCHEMA, stage="answer")
    except LLMError as exc:
        return {"answer": None, "error": str(exc), "wall_ms": (time.perf_counter() - t0) * 1000, "llm": []}
    return {"answer": r.data, "llm": [_usage_row(r.usage)], "wall_ms": r.usage.wall_ms}


def run_b1(q: Question, llm: ClaudeCLIAdapter, memdir: Path, today: str, tz: str) -> dict[str, Any]:
    system = (ANSWER_RULES.format(today=today, tz=tz)
              + "\n\nYour memory is a folder of markdown files in the current directory. MEMORY.md is the "
                "index; it is loaded below (only its first lines fit). Use Grep/Glob/Read to find the "
                "records you need before answering.\n\n"
              + memory_index_head(memdir))
    prompt = f"Question: {q.text}\nFormat: {q.format}"
    t0 = time.perf_counter()
    try:
        r = llm.complete_json(system=system, prompt=prompt, schema=ANSWER_SCHEMA, stage="answer")
    except LLMError as exc:
        return {"answer": None, "error": str(exc), "wall_ms": (time.perf_counter() - t0) * 1000, "llm": []}
    return {"answer": r.data, "llm": [_usage_row(r.usage)], "wall_ms": r.usage.wall_ms}


def run_m(q: Question, keeper: Keeper, now, narrate: bool = False) -> dict[str, Any]:
    ans = keeper.ask(q.text, now=now, narrate=narrate)
    return {"answer": to_answer(ans, q), "status": ans.status, "spec": ans.spec, "sql": ans.sql,
            "llm": ans.trace["llm"],
            "wall_ms": ans.trace["totals"]["total_ms"], "trace": ans.trace, "text": ans.text}


def to_answer(ans, q: Question) -> dict[str, Any]:
    """Mechanical formatting of a KeeperAnswer into the shared answer object (no LLM)."""
    out: dict[str, Any] = {"number": None, "ids": [], "groups": [],
                           "flags": {"small_sample": False, "incomplete_period": False}, "text": ans.text}
    if ans.status != "answered" or not ans.result:
        return out
    res = ans.result
    out["ids"] = list(ans.evidence[:MAX_LISTED_IDS]) if res.get("mode") != "aggregate" else []
    warnings = " ".join(ans.warnings)
    out["flags"] = {"small_sample": "Small sample" in warnings, "incomplete_period": "incomplete period" in warnings}
    if "entity" in res:
        out["number"], out["ids"] = 1, [res["entity"]["id"]]
        return out
    mode = res.get("mode")
    if mode in ("count", "list"):
        out["number"] = res["total"]
        rows = res.get("rows") or []
        if rows and "entity_id" in rows[0]:  # events: report the records they belong to
            out["ids"] = list(dict.fromkeys(r["entity_id"] for r in rows))[:MAX_LISTED_IDS]
        return out
    groups = res.get("groups") or []
    measures = (ans.spec or {}).get("measures") or []
    count_if = [m["name"] for m in measures if m.get("agg") == "count_if"]
    share = "share" in q.format
    for g in groups:
        keys = g["group"]
        period = next((v for k, v in keys.items() if k.endswith("_month")), None)
        cats = [v for k, v in keys.items() if not k.endswith(("_month", "_day", "_week", "_year"))]
        if count_if:
            v = g["measures"].get(count_if[0])
            value = (v / g["n"] if g["n"] else None) if share and v is not None else v
        elif measures:
            value = g["measures"].get(measures[0]["name"])
        else:
            value = g["n"]
        out["groups"].append({"period": period, "category": cats[0] if cats else None, "n": g["n"], "value": value})
    # a single number for averages: n-weighted over groups
    if measures and not count_if:
        pairs = [(g["n"], g["measures"].get(measures[0]["name"])) for g in groups]
        pairs = [(n, v) for n, v in pairs if v is not None and n]
        if pairs:
            out["number"] = round(sum(n * v for n, v in pairs) / sum(n for n, _ in pairs), 4)
    elif not groups or len(groups) == 1 and not keys:
        out["number"] = res["total"]
    return out
