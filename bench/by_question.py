"""Per-question results (correct out of 3) for the published benchmark runs, with a one-line reason for
misses. Generated from the raw results — no number is typed by hand.

    uv run python -m bench.by_question > docs/bench/results-by-question.md

Reasons: where a miss was diagnosed (by reading the system's answer), the diagnosis is shown; otherwise the
grader's own message (e.g. "number 12 ≠ 14"). Cells for a system that was not measured say so.
"""

from __future__ import annotations

from collections import Counter

from .run import RESULTS, _load_rows, load_questions

# Diagnosed causes, (run, system, question) -> one line. Only what was actually inspected.
DIAGNOSED = {
    ("v2-100-2x2", "M", "D6"): "read '정정' (correction) as ordinary `updated` events — fixed in v1.1 (event-kind labels)",
    ("v2-100-2x2", "M", "D7"): "schema selection left out applications (question named postings only) → clarify — fixed in v1.1",
    ("v2-100-2x2", "M", "V4"): "sorted by a day-level date field, ties within a day — fixed in v1.1 (order by event time)",
    ("v2-100-2x2", "M", "V6"): "'last week of November' read as 8 days — fixed in v1.1 (week rule + month tokens)",
    ("v2-100-2x2", "M", "U6"): "benchmark formatter reported event ids instead of record ids — harness bug, fixed in v1.1",
    ("v3-haiku-opus", "M-haiku", "W5"): "not expressible in QuerySpec v1.1 (filter on time between two events): "
                                        "said so (clarify) in 3 runs, a wrong workaround count in the others",
    ("v3-haiku-opus", "M-opus", "W5"): "not expressible in QuerySpec v1.1; at 100 a day-pair grouping workaround hit an accepted reading, "
                                       "at 1,000 a per-posting grouping returned 0",
    ("v3-haiku-opus", "M", "D5"): "asked what counts as 'quickly viewed' (clarify) instead of choosing a default",
    ("v3-haiku-opus", "M", "D3"): "Mnemento answered '187 records, showing 50'; the benchmark formatter reported the 50 shown "
                                  "because the spec carried limit 50 (from the format hint) — harness bug, scored as measured",
    ("v3-haiku-opus", "M-haiku", "D7"): "read '패스' (skipped) as status passed (cleared screening) instead of withdrawn",
}

# diagnoses for one scale only, (run, system, question, records) -> one line; checked before DIAGNOSED
DIAGNOSED_AT = {
    ("v3-haiku-opus", "M-haiku", "D5", 1000):
        "1 of 3 asked what counts as 'quickly viewed' (clarify); in the other 2 the answer held the right average "
        "(35.09 h) next to count_if measures, and the benchmark converter recorded the record count (1000, 382) — "
        "harness bug found and fixed in task 0008 step 2, scored as measured",
}

V2 = ("v2-100-2x2", "v2", 20261204, [(100, s) for s in ("M-haiku", "M-opus", "B1-haiku", "B1-opus")])
V3 = ("v3-haiku-opus", "v3", 20270201,
      [(100, s) for s in ("M-haiku", "M-opus", "B1-haiku", "B1-opus")]
      + [(1000, s) for s in ("M-haiku", "M-opus", "B1-haiku", "B1-opus")])


def _reason(run: str, system: str, q: str, fails: list[dict], scale: int | None = None) -> str:
    if (run, system, q, scale) in DIAGNOSED_AT:
        return DIAGNOSED_AT[(run, system, q, scale)]
    for key in ((run, system, q), (run, system.split("-")[0], q)):
        if key in DIAGNOSED:
            return DIAGNOSED[key]
    msg = Counter(r["why"] for r in fails).most_common(1)[0][0]
    return msg.replace("!=", "≠").replace("no number", "no answer (clarify or no number)")


def table(run: str, eval_set: str, seed: int, columns: list[tuple[int, str]]) -> list[str]:
    rows = _load_rows(RESULTS / run / "results.jsonl")
    qs = load_questions(100, seed, eval_set)
    out = []
    for qset, title in (("dev", "Dev questions (seen during development)"), ("unseen", "Unseen questions")):
        group = [q for q in qs if q.set == qset]
        if not group:
            continue
        out += [f"#### {title}", "",
                "| q | question | " + " | ".join(f"{s} · {n:,}" for n, s in columns) + " |",
                "|---|---|" + "---|" * len(columns)]
        notes = []
        for q in group:
            cells = []
            for n, s in columns:
                got = [rows[(n, s, q.id, r)] for r in range(3) if (n, s, q.id, r) in rows]
                if not got:
                    cells.append("not measured")
                    continue
                ok = sum(r["correct"] for r in got)
                cells.append(f"{ok}/{len(got)}" if ok == len(got) else f"**{ok}/{len(got)}**")
                fails = [r for r in got if not r["correct"]]
                if fails:
                    notes.append(f"- {q.id} · {s} · {n:,}: {_reason(run, s, q.id, fails, n)}")
            text = q.text if len(q.text) <= 40 else q.text[:38] + "…"
            out.append(f"| {q.id} | {text} | " + " | ".join(cells) + " |")
        out += ["", "Misses:" if notes else "No misses.", *notes, ""]
    return out


def main() -> None:
    lines = ["# Benchmark results by question", "",
             "Correct answers out of 3 repetitions per question and system (bold = at least one miss), generated by "
             "`bench/by_question.py` from the raw results. Reasons: a diagnosis where the answer was inspected, "
             "otherwise the grader's message. **Dev questions were used while fixing Mnemento (v1, v1.1), so an "
             "advantage on them may be overfitting; compare systems on the unseen questions.**", ""]
    for (run, eval_set, seed, columns), heading in (
            (V3, "## v3 — opus and haiku, 100 and 1,000 records (frozen version 2c56c658bc5f10f9)"),
            (V2, "## v2 — 100 records, haiku and opus (2×2)")):
        lines += [heading, "", f"Run `{run}`, evaluation set {eval_set} (seed {seed}), same format hint for every system.", ""]
        lines += table(run, eval_set, seed, columns)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
