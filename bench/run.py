"""Benchmark CLI (task 0003, ADR-0008).

    uv run python -m bench.run prepare  --scale 100            # generate data (no LLM)
    uv run python -m bench.run estimate --scales 100,1000,10000 --reps 5
    uv run python -m bench.run run      --scale 100 --systems B0,B1,M,Mn --reps 5 --model haiku
    uv run python -m bench.run calibrate --model haiku           # fixed CLI overhead per config

Runs one scale at a time (subscription usage; review condition). Results are appended to
bench/results/<run>/results.jsonl, so an interrupted run can be resumed with the same --run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from mnemento import Ledger
from mnemento.keeper import Keeper, KeeperAnswer
from mnemento.keeper.llm import ClaudeCLIAdapter
from mnemento.keeper.query.pipeline import QueryPipeline

from . import questions as qmod
from .generate import REFERENCE_DATES, TZ, generate
from .grade import ANSWER_SCHEMA, grade
from .render import SCHEMA_DIR, full_context, to_ledger, to_memory_dir
from .runners import run_b0, run_b1, run_m

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "bench" / "data"
RESULTS = ROOT / "bench" / "results"
DEFAULT_SEED = 20261120
CONTEXT_LIMIT_TOKENS = 200_000
CHARS_PER_TOKEN = 1.6  # Korean-heavy markdown; replaced by `calibrate` measurements when available

def _frozen_files() -> list[str]:
    """Everything that shapes the systems' behaviour, hashed into every run's metadata: the whole
    package, the schemas and the harness except reporting/plotting/guarding (which do not change answers)."""
    files = sorted(str(p.relative_to(ROOT)).replace("\\", "/") for p in (ROOT / "src" / "mnemento").rglob("*.py"))
    files += sorted(str(p.relative_to(ROOT)).replace("\\", "/") for p in (ROOT / "schemas").glob("*.json"))
    files += [f"bench/{n}" for n in ("generate.py", "render.py", "questions.py", "grade.py", "runners.py", "run.py")]
    return files


FROZEN_FILES = _frozen_files()


def data_dir(scale: int, seed: int, eval_set: str = "v0") -> Path:
    return DATA / (f"s{seed}-n{scale}" if eval_set == "v0" else f"{eval_set}-s{seed}-n{scale}")


def frozen_hash() -> str:
    h = hashlib.sha256()
    for f in FROZEN_FILES:
        h.update(f.encode())
        h.update((ROOT / f).read_bytes().replace(b"\r\n", b"\n"))  # same hash for any checkout
    return h.hexdigest()[:16]


def git_head() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True)
        return out.stdout.strip() + ("+dirty" if dirty.stdout.strip() else "") if out.returncode == 0 else None
    except OSError:
        return None


# ---- prepare --------------------------------------------------------------------------------

def prepare(scale: int, seed: int, eval_set: str = "v0") -> Path:
    d = data_dir(scale, seed, eval_set)
    if (d / "done").exists():
        qs = qmod.build(generate(scale, seed, version=eval_set))  # questions may be added after the data was built
        (d / "questions.json").write_text(json.dumps([_plain(q.__dict__) for q in qs], ensure_ascii=False,
                                                     indent=1, default=_jsonable), encoding="utf-8")
        return d
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    t0 = time.perf_counter()
    ds = generate(scale, seed, version=eval_set)
    led = to_ledger(ds, d / "mnemento.db")
    led.close()
    to_memory_dir(ds, d / "memory")
    (d / "context.md").write_text(full_context(ds), encoding="utf-8")
    qs = qmod.build(ds)
    (d / "questions.json").write_text(json.dumps([_plain(q.__dict__) for q in qs], ensure_ascii=False, indent=1,
                                                 default=_jsonable), encoding="utf-8")
    stats = {"scale": scale, "seed": seed, "applications": len(ds.apps), "live": len(ds.live),
             "companies": len(ds.companies), "context_chars": len((d / "context.md").read_text(encoding="utf-8")),
             "memory_files": len(list((d / "memory").glob("*.md"))), "seconds": round(time.perf_counter() - t0, 1)}
    (d / "stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    (d / "done").write_text("ok")
    return d


def _plain(v):
    if isinstance(v, dict):
        return {("|".join(map(str, k)) if isinstance(k, tuple) else k): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return v


def _jsonable(v):
    if isinstance(v, tuple):
        return list(v)
    return str(v)


def load_questions(scale: int, seed: int, eval_set: str = "v0") -> list[qmod.Question]:
    ds = generate(scale, seed, version=eval_set)  # deterministic; recompute so tuple keys survive
    return qmod.build(ds)


# ---- estimate -------------------------------------------------------------------------------

def estimate(scales: list[int], reps: int, seed: int, systems: list[str], calib: dict | None) -> dict:
    cpt = (calib or {}).get("chars_per_token", CHARS_PER_TOKEN)
    overhead = (calib or {}).get("overhead_input_tokens", {"plain": 1300, "tools": 4000})
    n_q = None
    rows = []
    for scale in scales:
        d = prepare(scale, seed)
        stats = json.loads((d / "stats.json").read_text())
        qs = load_questions(scale, seed)
        n_q = len(qs)
        ctx_tokens = int(stats["context_chars"] / cpt)
        for system in systems:
            calls = reps * n_q
            if system == "B0":
                per_in = ctx_tokens + overhead["plain"] + 300
                feasible = per_in < CONTEXT_LIMIT_TOKENS
                per_out = 1500
            elif system == "B1":
                # assumption until piloted: ~8 turns, each re-reading prior turns (mostly cache reads)
                turns = 8 if scale <= 1000 else 12
                per_in = overhead["tools"] * turns + 2500 * turns * (turns + 1) // 2
                feasible, per_out = True, 600 * turns
            elif system in ("M", "Mn"):
                # LLM only for questions the fast path/cache cannot take; cold cache each repetition
                llm_q = sum(1 for q in qs if q.id not in ("D1",))
                calls = reps * llm_q * (2 if system == "Mn" else 1)
                per_in, per_out, feasible = 3900 if system == "M" else 3000, 1500, True
            rows.append({"scale": scale, "system": system, "cli_calls": calls if feasible else 0,
                         "input_tokens_per_call": per_in, "output_tokens_per_call": per_out,
                         "input_tokens_total": per_in * calls if feasible else 0,
                         "output_tokens_total": per_out * calls if feasible else 0,
                         "feasible": feasible, "context_tokens": ctx_tokens})
    return {"questions": n_q, "reps": reps, "rows": rows, "chars_per_token": cpt, "overhead": overhead}


# ---- run ------------------------------------------------------------------------------------

class BudgetExceeded(RuntimeError):
    """The list-price cost spent by this invocation passed --max-cost; stopped cleanly."""


class TooManyFailures(RuntimeError):
    """Several CLI calls failed in a row (subscription limit, network...). Resume later with the same --run."""


NOT_MEASURABLE = "context limit exceeded (not measurable)"
MAX_CONSECUTIVE_FAILURES = 3
CLI_TIMEOUT_S = 600

PLANS = {
    # Reduced measurement (decided 2026-10-03): 12 of 20 questions picked mechanically (first four ids of
    # each set), 3 repetitions, narration (Mn) only at 100 records. Same run layout, so the remaining
    # questions/repetitions can be added later to the same run.
    "v0-reduced": {
        "description": "Reduced measurement: 12/20 questions (D1-D4, U1-U4, H1-H4), 3 repetitions, Mn at 100 only",
        "reps": 3,
        "questions": ["D1", "D2", "D3", "D4", "U1", "U2", "U3", "U4", "H1", "H2", "H3", "H4"],
        "steps": [(100, ["B0", "B1", "M", "Mn"]), (1000, ["B1", "M"]), (10000, ["B1", "M"])],
        "eval_set": "v0", "hint": False,
    },
    # task 0004 ④: after the v1 changes, M alone on the v0 data and the v0 condition (question only),
    # to confirm that questions M already answered (D1-D3, U1, U3, H1) still pass
    "v1-regression": {
        "description": "v1 regression: M only, v0 evaluation set and condition, 12 questions, 1 repetition",
        "reps": 1,
        "questions": ["D1", "D2", "D3", "D4", "U1", "U2", "U3", "U4", "H1", "H2", "H3", "H4"],
        "steps": [(100, ["M"]), (1000, ["M"])],
        "eval_set": "v0", "hint": False,
    },
    # task 0005: after the v1.1 fixes, M-haiku on the v2 data (every v0/v2 question is dev now)
    "v1.1-regression": {
        "description": "v1.1 regression: M-haiku, eval set v2 data (all questions dev now), same format hint, "
                       "100 records, 1 repetition",
        "reps": 1,
        "questions": None,
        "steps": [(100, ["M:haiku"])],
        "eval_set": "v2", "hint": True, "seed": 20261204,
    },
    # evaluation set v3 (task 0005): dev 28 (v0 + v2) + unseen 9 (W1-W9); 100 records first, then 1,000-record M,
    # then 1,000-record B1-opus last (the largest and most variable usage)
    "v3": {
        "description": "Eval set v3: dev 28 + unseen 9 questions, same format hint, 3 repetitions; "
                       "B1-opus, M-opus, M-haiku at 100 and 1,000 records",
        "reps": 3,
        "questions": None,
        "steps": [(100, ["M:haiku", "M:opus", "B1:opus"]), (1000, ["M:haiku", "M:opus"]), (1000, ["B1:opus"])],
        "eval_set": "v3", "hint": True, "seed": 20270201,
    },
    # evaluation set v2 (task 0004 ③): own seed and date, dev 20 + unseen 9 questions, the same format
    # hint for every system; steps may carry their own repetition count
    # step 1 of v2 (decided 2026-10-03): 100 records, both systems on both models (2x2)
    "v2-100-2x2": {
        "description": "Eval set v2 at 100 records: M and B1 each on haiku and opus (2x2), dev 20 + unseen 9, "
                       "same format hint, 3 repetitions",
        "reps": 3,
        "questions": None,
        "steps": [(100, ["M:haiku", "M:opus", "B1:haiku", "B1:opus"])],
        "eval_set": "v2", "hint": True, "seed": 20261204,
    },
    "v2": {
        "description": "Eval set v2: dev 20 + unseen 9 questions, same format hint for every system, "
                       "3 repetitions (B1 at 10,000: 1)",
        "reps": 3,
        "questions": None,  # all questions of the evaluation set
        "steps": [(100, ["B0", "B1", "M"]), (1000, ["B1", "M"]), (10000, ["M"]), (10000, ["B1"], 1)],
        "eval_set": "v2", "hint": True, "seed": 20261204,
    },
}


def _open_run(run_id: str, model: str, thinking: int | None, seed: int, plan: str | None,
              eval_set: str = "v0", hint: bool = False) -> Path:
    out_dir = RESULTS / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    meta_path = out_dir / "meta.json"
    meta = {"run": run_id, "model": model, "thinking_tokens": thinking, "seed": seed,
            "frozen_hash": frozen_hash(), "git": git_head(), "started": datetime.now().astimezone().isoformat(),
            "adapter": "claude-cli", "plan": plan, "plan_description": PLANS.get(plan or "", {}).get("description"),
            "eval_set": eval_set,
            "hint": "same format hint for every system" if hint else "B0/B1 format hint, M question only (v0 condition)"}
    if meta_path.exists():
        old = json.loads(meta_path.read_text())
        if old["frozen_hash"] != meta["frozen_hash"]:
            sys.exit(f"frozen files changed since this run started ({old['frozen_hash']} -> {meta['frozen_hash']}); "
                     "start a new --run")
        if (old.get("eval_set", "v0"), old.get("hint", meta["hint"])) != (eval_set, meta["hint"]):
            sys.exit(f"run {run_id} was started with eval_set={old.get('eval_set', 'v0')} hint={old.get('hint')}")
        if (old["model"], old.get("thinking_tokens")) != (model, thinking):
            sys.exit(f"run {run_id} was started with model={old['model']} thinking={old.get('thinking_tokens')}")
    else:
        meta_path.write_text(json.dumps(meta, indent=1))
    return out_dir


def _load_rows(results_path: Path) -> dict[tuple, dict]:
    """Latest row per (scale, system, question, repetition)."""
    rows: dict[tuple, dict] = {}
    if results_path.exists():
        for line in results_path.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            rows[(r["scale"], r["system"], r["q"], r["rep"])] = r
    return rows


def _finished(row: dict | None) -> bool:
    # failed calls (timeouts, limits) are retried on resume; "not measurable" is a final result
    return row is not None and (not row.get("error") or row.get("error") == NOT_MEASURABLE)


def run(scale: int, seed: int, systems: list[str], reps: int, model: str, run_id: str,
        thinking: int | None, sets: list[str], only: list[str] | None = None, plan: str | None = None,
        eval_set: str = "v0", hint: bool = False, max_cost: float | None = None) -> Path:
    d = prepare(scale, seed, eval_set)
    out_dir = _open_run(run_id, model, thinking, seed, plan, eval_set, hint)
    now = REFERENCE_DATES[eval_set]
    spent = 0.0
    results_path = out_dir / "results.jsonl"
    rows = _load_rows(results_path)
    qs = [q for q in load_questions(scale, seed, eval_set) if q.set in sets and (not only or q.id in only)]
    context = (d / "context.md").read_text(encoding="utf-8")
    stats = json.loads((d / "stats.json").read_text())
    today, tz = now.date().isoformat(), str(TZ)
    kw_default = {"model": model, "max_thinking_tokens": thinking, "timeout_s": CLI_TIMEOUT_S}
    kw = kw_default
    failures = 0

    def write(q, system, rep, res):
        nonlocal failures, spent
        ok, why = grade(q, res.get("answer"))
        row = {"scale": scale, "system": system, "q": q.id, "set": q.set, "rep": rep, "correct": ok,
               "why": why, **res}
        with results_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        rows[(scale, system, q.id, rep)] = row
        tok = sum(u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                  + u.get("cache_creation_input_tokens", 0) for u in res.get("llm", []))
        print(f"n={scale} {system:2} {q.id} rep{rep}: {'OK ' if ok else 'BAD'} {why[:46]:46} "
              f"{res.get('wall_ms', 0) / 1000:6.1f}s in={tok}", flush=True)
        failures = failures + 1 if res.get("error") and res.get("error") != NOT_MEASURABLE else 0
        new_calls = res.get("llm", [])[-1:] if system == "Mn" else res.get("llm", [])  # Mn repeats M's calls
        if system == "Mn" and not res.get("narration"):
            new_calls = []
        spent += sum((u.get("cost_usd") or 0) for u in new_calls)
        if max_cost is not None and spent > max_cost:
            raise BudgetExceeded(f"spent ${spent:.2f} > --max-cost ${max_cost:.2f}; stopped after "
                                 f"n={scale} {system} {q.id} rep{rep}. Resume with --run {run_id}.")
        if failures >= MAX_CONSECUTIVE_FAILURES:
            raise TooManyFailures(f"{failures} CLI calls failed in a row (last: {res.get('error')}). "
                                  f"Resume later with --run {run_id}.")

    for spec_ in systems:
        # "B1:opus" -> system B1 on model opus, recorded as "B1-opus"; a bare name uses --model
        base, _, sys_model = spec_.partition(":")
        system = f"{base}-{sys_model}" if sys_model else base
        kw = {**kw_default, "model": sys_model or model}
        if base == "Mn":
            continue  # derived from M below
        for rep in range(reps):
            todo = [q for q in qs if not _finished(rows.get((scale, system, q.id, rep)))]
            if not todo:
                continue
            keeper = None
            if base == "M":
                # fresh copy of the DB per repetition: a cold plan cache, like a first-time question
                db = out_dir / f"tmp_{scale}_{system}.db"
                for suffix in ("", "-wal", "-shm"):
                    Path(f"{db}{suffix}").unlink(missing_ok=True)
                shutil.copy(d / "mnemento.db", db)
                ledger = Ledger.open(db)
                ledger.schemas.load_dir(SCHEMA_DIR)  # the current schema dictionary (additive versions)
                keeper = Keeper(ledger, ClaudeCLIAdapter(**kw))
            try:
                for q in todo:
                    if base == "B0" and stats["context_chars"] / CHARS_PER_TOKEN > CONTEXT_LIMIT_TOKENS:
                        res = {"answer": None, "error": NOT_MEASURABLE, "llm": [], "wall_ms": 0}
                    elif base == "B0":
                        res = run_b0(q, ClaudeCLIAdapter(**kw), context, today, tz)
                    elif base == "B1":
                        res = run_b1(q, ClaudeCLIAdapter(**kw, tools=["Read", "Grep", "Glob"],
                                                         workdir=str(d / "memory")), d / "memory", today, tz)
                    else:
                        res = run_m(q, keeper, now, hint=hint)
                    write(q, system, rep, res)
            finally:
                if keeper:
                    keeper.ledger.close()

    if "Mn" in systems:  # (Mn always derives from the plain "M" system)
        # Mn = the very same M answer + one narration call (reuses M's interpretation)
        pipeline = QueryPipeline(Ledger.open(":memory:"), ClaudeCLIAdapter(**kw), use_cache=False)
        try:
            for rep in range(reps):
                for q in qs:
                    m_row = rows.get((scale, "M", q.id, rep))
                    if _finished(rows.get((scale, "Mn", q.id, rep))) or not _finished(m_row):
                        continue
                    write(q, "Mn", rep, narrate_m_row(m_row, pipeline))
        finally:
            pipeline.ledger.close()
    _record_models(out_dir, rows)
    return results_path


def _record_models(out_dir: Path, rows: dict) -> None:
    """meta.resolved_models: the model ids the CLI actually used, per system (e.g. opus -> claude-opus-5-5)."""
    seen: dict[str, set] = {}
    for r in rows.values():
        for u in r.get("llm", []):
            seen.setdefault(r["system"], set()).add(u.get("model"))
    meta_path = out_dir / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta["resolved_models"] = {k: sorted(m for m in v if m) for k, v in sorted(seen.items())}
    meta_path.write_text(json.dumps(meta, indent=1))


def narrate_m_row(m_row: dict, pipeline: QueryPipeline) -> dict:
    """Mn row: M's graded answer and cost, plus one narration call on M's stored answer."""
    ka = m_row.get("keeper_answer")
    res = {k: m_row.get(k) for k in ("answer", "status", "spec", "sql", "text")}
    res["llm"] = list(m_row.get("llm", []))
    res["wall_ms"] = m_row.get("wall_ms", 0)
    res["narration"] = None
    if ka:
        t0 = time.perf_counter()
        prose, usage = pipeline.narrate(KeeperAnswer(**ka))
        res["wall_ms"] += (time.perf_counter() - t0) * 1000
        if usage is not None:
            res["llm"].append(asdict(usage))
        res["narration"] = prose
    return res


def run_plan(plan: str, run_id: str, model: str, thinking: int | None, seed: int,
             max_cost: float | None = None) -> None:
    p = PLANS[plan]
    seed = p.get("seed", seed)
    spent_before = _run_cost(run_id)  # the cap covers the whole run, across resumed sessions
    for step in p["steps"]:
        scale, systems = step[0], step[1]
        reps = step[2] if len(step) > 2 else p["reps"]
        print(f"== {plan}: n={scale} systems={','.join(systems)} reps={reps}", flush=True)
        budget = None if max_cost is None else max_cost - spent_before
        run(scale, seed, systems, reps, model, run_id, thinking, ["dev", "unseen"], p["questions"], plan,
            p.get("eval_set", "v0"), p.get("hint", False), budget)
        spent_before = _run_cost(run_id)
    print(f"== {plan} complete. Report: uv run python -m bench.report --run {run_id}", flush=True)


def _run_cost(run_id: str) -> float:
    """List-price cost recorded in a run so far (Mn rows count only their narration call)."""
    rows = _load_rows(RESULTS / run_id / "results.jsonl")
    total = 0.0
    for r in rows.values():
        calls = r.get("llm", [])[-1:] if r["system"] == "Mn" and r.get("narration") else (
            [] if r["system"] == "Mn" else r.get("llm", []))
        total += sum((u.get("cost_usd") or 0) for u in calls)
    return total


def calibrate(model: str, out: Path) -> dict:
    """Fixed overhead of one CLI call per configuration, and chars/token on our Korean markdown."""
    sample = (prepare(100, DEFAULT_SEED) / "context.md").read_text(encoding="utf-8")[:20000]
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    plain = ClaudeCLIAdapter(model=model).complete_json(system="Reply ok.", prompt="ok", schema=schema, stage="cal")
    with_text = ClaudeCLIAdapter(model=model).complete_json(system="Reply ok.\n\n" + sample, prompt="ok",
                                                            schema=schema, stage="cal")
    tools = ClaudeCLIAdapter(model=model, tools=["Read", "Grep", "Glob"]).complete_json(
        system="Reply ok without using tools.", prompt="ok", schema=schema, stage="cal")
    def total_in(u) -> int:
        return u.input_tokens + u.cache_read_input_tokens + u.cache_creation_input_tokens

    res = {
        "model": model,
        "overhead_input_tokens": {"plain": total_in(plain.usage), "tools": total_in(tools.usage)},
        "overhead_ms": {"plain": plain.usage.overhead_ms, "tools": tools.usage.overhead_ms},
        "chars_per_token": round(len(sample) / max(1, total_in(with_text.usage) - total_in(plain.usage)), 3),
        "raw": [plain.usage.__dict__, with_text.usage.__dict__, tools.usage.__dict__],
    }
    out.write_text(json.dumps(res, indent=1), encoding="utf-8")
    return res


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="bench.run")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare"); p.add_argument("--scale", type=int, required=True)  # noqa: E702
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    e = sub.add_parser("estimate"); e.add_argument("--scales", default="100,1000,10000")  # noqa: E702
    e.add_argument("--reps", type=int, default=5); e.add_argument("--seed", type=int, default=DEFAULT_SEED)  # noqa: E702
    e.add_argument("--systems", default="B0,B1,M,Mn")
    r = sub.add_parser("run"); r.add_argument("--scale", type=int, required=True)  # noqa: E702
    r.add_argument("--systems", default="B0,B1,M,Mn"); r.add_argument("--reps", type=int, default=5)  # noqa: E702
    r.add_argument("--model", default="haiku"); r.add_argument("--seed", type=int, default=DEFAULT_SEED)  # noqa: E702
    r.add_argument("--run", default=None); r.add_argument("--thinking", type=int, default=None)  # noqa: E702
    r.add_argument("--sets", default="dev,unseen")
    r.add_argument("--questions", default="", help="comma-separated question ids (pilot runs)")
    c = sub.add_parser("calibrate"); c.add_argument("--model", default="haiku")  # noqa: E702
    pl = sub.add_parser("plan", help="run a predefined plan, all scales in order, resumable")
    pl.add_argument("--plan", default="v0-reduced", choices=sorted(PLANS))
    pl.add_argument("--run", required=True)
    pl.add_argument("--model", default="haiku")
    pl.add_argument("--thinking", type=int, default=None)
    pl.add_argument("--seed", type=int, default=DEFAULT_SEED)
    pl.add_argument("--max-cost", type=float, default=None, help="stop when list-price spend passes this (USD)")
    r.add_argument("--eval-set", default="v0", choices=sorted(REFERENCE_DATES))
    r.add_argument("--hint", action="store_true", help="give M the same format hint as B0/B1")
    r.add_argument("--max-cost", type=float, default=None)
    args = ap.parse_args(argv)
    if args.cmd == "prepare":
        d = prepare(args.scale, args.seed)
        print(d, (d / "stats.json").read_text())
    elif args.cmd == "estimate":
        cal_path = RESULTS / "calibration.json"
        calib = json.loads(cal_path.read_text()) if cal_path.exists() else None
        est = estimate([int(s) for s in args.scales.split(",")], args.reps, args.seed, args.systems.split(","), calib)
        print(json.dumps(est, indent=1))
    elif args.cmd == "run":
        run_id = args.run or datetime.now().strftime("%Y%m%d-%H%M%S")
        try:
            run(args.scale, args.seed, args.systems.split(","), args.reps, args.model, run_id, args.thinking,
                args.sets.split(","), [q for q in args.questions.split(",") if q], None, args.eval_set,
                args.hint, args.max_cost)
        except (TooManyFailures, BudgetExceeded) as exc:
            sys.exit(str(exc))
    elif args.cmd == "plan":
        try:
            run_plan(args.plan, args.run, args.model, args.thinking, args.seed, args.max_cost)
        except (TooManyFailures, BudgetExceeded) as exc:
            sys.exit(str(exc))
    elif args.cmd == "calibrate":
        RESULTS.mkdir(parents=True, exist_ok=True)
        print(json.dumps(calibrate(args.model, RESULTS / "calibration.json"), indent=1))


if __name__ == "__main__":
    main()
