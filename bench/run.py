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
from .generate import BENCH_NOW, TZ, generate
from .grade import ANSWER_SCHEMA, grade
from .render import full_context, to_ledger, to_memory_dir
from .runners import run_b0, run_b1, run_m

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "bench" / "data"
RESULTS = ROOT / "bench" / "results"
DEFAULT_SEED = 20261120
CONTEXT_LIMIT_TOKENS = 200_000
CHARS_PER_TOKEN = 1.6  # Korean-heavy markdown; replaced by `calibrate` measurements when available

FROZEN_FILES = [  # everything that shapes the systems' behaviour; hashed into every run's metadata
    "src/mnemento/keeper/query/interpret.py", "src/mnemento/keeper/query/rules.py",
    "src/mnemento/keeper/query/spec.py", "src/mnemento/keeper/query/compile.py",
    "src/mnemento/keeper/query/answer.py", "src/mnemento/keeper/query/pipeline.py",
    "src/mnemento/keeper/query/cache.py", "src/mnemento/keeper/llm.py", "schemas/application.json",
    "schemas/company.json", "schemas/posting.json", "bench/runners.py", "bench/questions.py", "bench/grade.py",
    "bench/generate.py", "bench/render.py",
]


def data_dir(scale: int, seed: int) -> Path:
    return DATA / f"s{seed}-n{scale}"


def frozen_hash() -> str:
    h = hashlib.sha256()
    for f in FROZEN_FILES:
        h.update(f.encode())
        h.update((ROOT / f).read_bytes())
    return h.hexdigest()[:16]


def git_head() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True)
        return out.stdout.strip() + ("+dirty" if dirty.stdout.strip() else "") if out.returncode == 0 else None
    except OSError:
        return None


# ---- prepare --------------------------------------------------------------------------------

def prepare(scale: int, seed: int) -> Path:
    d = data_dir(scale, seed)
    if (d / "done").exists():
        qs = qmod.build(generate(scale, seed))  # questions may be added after the data was built
        (d / "questions.json").write_text(json.dumps([_plain(q.__dict__) for q in qs], ensure_ascii=False,
                                                     indent=1, default=_jsonable), encoding="utf-8")
        return d
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    t0 = time.perf_counter()
    ds = generate(scale, seed)
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


def load_questions(scale: int, seed: int) -> list[qmod.Question]:
    ds = generate(scale, seed)  # deterministic; recompute so tuple keys survive
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
    },
}


def _open_run(run_id: str, model: str, thinking: int | None, seed: int, plan: str | None) -> Path:
    out_dir = RESULTS / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    meta_path = out_dir / "meta.json"
    meta = {"run": run_id, "model": model, "thinking_tokens": thinking, "seed": seed,
            "frozen_hash": frozen_hash(), "git": git_head(), "started": datetime.now().astimezone().isoformat(),
            "adapter": "claude-cli", "plan": plan, "plan_description": PLANS.get(plan or "", {}).get("description")}
    if meta_path.exists():
        old = json.loads(meta_path.read_text())
        if old["frozen_hash"] != meta["frozen_hash"]:
            sys.exit(f"frozen files changed since this run started ({old['frozen_hash']} -> {meta['frozen_hash']}); "
                     "start a new --run")
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
        thinking: int | None, sets: list[str], only: list[str] | None = None, plan: str | None = None) -> Path:
    d = prepare(scale, seed)
    out_dir = _open_run(run_id, model, thinking, seed, plan)
    results_path = out_dir / "results.jsonl"
    rows = _load_rows(results_path)
    qs = [q for q in load_questions(scale, seed) if q.set in sets and (not only or q.id in only)]
    context = (d / "context.md").read_text(encoding="utf-8")
    stats = json.loads((d / "stats.json").read_text())
    today, tz = BENCH_NOW.date().isoformat(), str(TZ)
    kw = {"model": model, "max_thinking_tokens": thinking, "timeout_s": CLI_TIMEOUT_S}
    failures = 0

    def write(q, system, rep, res):
        nonlocal failures
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
        if failures >= MAX_CONSECUTIVE_FAILURES:
            raise TooManyFailures(f"{failures} CLI calls failed in a row (last: {res.get('error')}). "
                                  f"Resume later with --run {run_id}.")

    for system in systems:
        if system == "Mn":
            continue  # derived from M below
        for rep in range(reps):
            todo = [q for q in qs if not _finished(rows.get((scale, system, q.id, rep)))]
            if not todo:
                continue
            keeper = None
            if system == "M":
                # fresh copy of the DB per repetition: a cold plan cache, like a first-time question
                db = out_dir / f"tmp_{scale}_M.db"
                for suffix in ("", "-wal", "-shm"):
                    Path(f"{db}{suffix}").unlink(missing_ok=True)
                shutil.copy(d / "mnemento.db", db)
                keeper = Keeper(Ledger.open(db), ClaudeCLIAdapter(**kw))
            try:
                for q in todo:
                    if system == "B0" and stats["context_chars"] / CHARS_PER_TOKEN > CONTEXT_LIMIT_TOKENS:
                        res = {"answer": None, "error": NOT_MEASURABLE, "llm": [], "wall_ms": 0}
                    elif system == "B0":
                        res = run_b0(q, ClaudeCLIAdapter(**kw), context, today, tz)
                    elif system == "B1":
                        res = run_b1(q, ClaudeCLIAdapter(**kw, tools=["Read", "Grep", "Glob"],
                                                         workdir=str(d / "memory")), d / "memory", today, tz)
                    else:
                        res = run_m(q, keeper, BENCH_NOW)
                    write(q, system, rep, res)
            finally:
                if keeper:
                    keeper.ledger.close()

    if "Mn" in systems:
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
    return results_path


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


def run_plan(plan: str, run_id: str, model: str, thinking: int | None, seed: int) -> None:
    p = PLANS[plan]
    for scale, systems in p["steps"]:
        print(f"== {plan}: n={scale} systems={','.join(systems)} reps={p['reps']}", flush=True)
        run(scale, seed, systems, p["reps"], model, run_id, thinking, ["dev", "unseen"], p["questions"], plan)
    print(f"== {plan} complete. Report: uv run python -m bench.report --run {run_id}", flush=True)


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
                args.sets.split(","), [q for q in args.questions.split(",") if q])
        except TooManyFailures as exc:
            sys.exit(str(exc))
    elif args.cmd == "plan":
        try:
            run_plan(args.plan, args.run, args.model, args.thinking, args.seed)
        except TooManyFailures as exc:
            sys.exit(str(exc))
    elif args.cmd == "calibrate":
        RESULTS.mkdir(parents=True, exist_ok=True)
        print(json.dumps(calibrate(args.model, RESULTS / "calibration.json"), indent=1))


if __name__ == "__main__":
    main()
