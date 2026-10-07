"""Mixed-text ingest evaluation (ADR-0017 metrics, issue #3) on fictional, labelled notes.

    python -m bench.ingest_eval check                     # the labels against the fixture (no LLM)
    python -m bench.ingest_eval run --model haiku --set dev [--run NAME]

The ledger starts with fictional securities and the trade/decision schemas; the notes in bench/ingest_sets/<set>/ are
previewed (never applied) and the preview is scored against labels.json:
- classification accuracy (data / not_data per chunk; ambiguous counts as wrong)
- field precision / recall on existing types (matched by type and lines)
- values for new types (matched by their values, field names free) and kind accuracy
- values not in the source: stored values whose quote check fails (must be 0) and dropped values (reported)
- wrong merges (one record spanning facts the labels keep apart), sibling pairs found, conflict recall
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from mnemento import Ledger
from mnemento.keeper.ingest import Ingestor

ROOT = Path(__file__).resolve().parent
SETS = ROOT / "ingest_sets"
RESULTS = ROOT / "results"
NOW = datetime.fromisoformat("2026-10-01T09:00:00+09:00")

SCHEMAS = [
    {"name": "security", "version": 1, "description": "A stock or fund the user trades or watches.",
     "keywords": ["종목"],
     "fields": {"code": {"type": "string", "description": "Exchange code.", "identifier": True, "indexed": True},
                "name": {"type": "string", "description": "Name.", "indexed": True}}},
    {"name": "trade", "version": 1, "description": "One buy or sell of a security.",
     "keywords": ["매수", "매도", "체결"], "default_date_field": "traded_at",
     "relations": [{"type": "decision", "note": "a trade and the decision behind it may be one event; count trades "
                                                "from trade"}],
     "fields": {"security_id": {"type": "string", "ref": "security", "description": "The security.", "required": True,
                                "indexed": True},
                "traded_at": {"type": "string", "format": "date", "description": "Trade day.", "required": True},
                "side": {"type": "string", "enum": ["buy", "sell"], "labels": {"buy": ["매수"], "sell": ["매도"]},
                         "description": "buy or sell"},
                "units": {"type": "integer", "description": "Shares or units."},
                "price": {"type": "number", "description": "Price per unit, KRW."}}},
    {"name": "decision", "version": 1, "description": "Why the user bought, sold or held back, as written.",
     "keywords": ["판단", "근거", "이유"], "default_date_field": "decided_at",
     "fields": {"security_id": {"type": "string", "ref": "security", "description": "The security.", "indexed": True},
                "decided_at": {"type": "string", "format": "date", "description": "Day of the decision."},
                "text": {"type": "string", "description": "The reason, in the note's words."}}},
]
SECURITIES = [("900001", "가상바이오"), ("900002", "샘플전자")]


def open_ledger() -> Ledger:
    led = Ledger.open(":memory:")
    for s in SCHEMAS:
        led.schemas.register(s)
    for code, name in SECURITIES:
        led.record_event(f"sec_{code}", "created", {"code": code, "name": name}, "2026-07-01T09:00:00+09:00",
                         "ingest_eval", None, entity_type="security")
    return led


def load_set(name: str) -> tuple[list[tuple[str, str]], dict[str, Any]]:
    d = SETS / name
    sources = [(f.name, f.read_text(encoding="utf-8")) for f in sorted(d.glob("*.md"))]
    return sources, json.loads((d / "labels.json").read_text(encoding="utf-8"))


def _num(v: Any) -> Any:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return v


def _same(got: Any, want: Any) -> bool:
    """Numbers equal; free text: one contains the other after normalizing spaces and end punctuation (a reason copied
    with a little more or less of the sentence is the same reason) — rule set before the unseen run."""
    if isinstance(want, str) and isinstance(got, str) and not want.startswith("sec_"):
        norm = lambda s: re.sub(r"[\s.。]+", " ", s).strip()  # noqa: E731
        a, b = norm(got), norm(want)
        return bool(a) and (a in b or b in a)
    return _num(got) == _num(want)


def score(pv: dict[str, Any], labels: dict[str, Any]) -> dict[str, Any]:
    span_line = {c["span_id"]: (c["source"], c["start"]) for c in pv["chunks"]}
    # classification
    exp_kind = {(f, int(line)): k for f, m in labels["chunks"].items() for line, k in m.items()}
    got_kind = {(c["source"], c["start"]): c["kind"] for c in pv["chunks"]}
    cls_ok = sum(1 for key, k in exp_kind.items() if got_kind.get(key) == k)
    # existing-type records: matched by type and the lines they came from
    exp = labels["records"]
    got = [r for r in pv["records"] if r["type"] in {s["name"] for s in SCHEMAS}]
    match: dict[int, dict] = {}
    for i, e in enumerate(exp):
        lines = {tuple(x) for x in e["lines"]}
        cand = [r for r in got if r["type"] == e["type"] and {span_line[s] for s in r["spans"]} & lines]
        if cand:
            match[i] = cand[0]
    tp = sum(1 for i, r in match.items() for k, v in exp[i]["doc"].items() if _same(r["doc"].get(k), v))
    exp_fields = sum(len(e["doc"]) for e in exp)
    got_fields = sum(len(r["doc"]) for r in got)
    # wrong merges: a record whose lines belong to two different labelled facts
    fact_of = {(e["type"], *x): i for i, e in enumerate(exp) for x in e["lines"]}  # one line may hold two types
    wrong_merges = sum(1 for r in got if len({fact_of.get((r["type"], *span_line[s])) for s in r["spans"]}
                                             - {None}) > 1)
    # siblings
    sib_found = sum(1 for a, b in labels.get("siblings", []) if a in match and b in match
                    and match[b]["rid"] in match[a].get("siblings", []))
    # conflicts
    conf_found = sum(1 for c in labels.get("conflicts", []) if any(
        g["type"] == c["type"] and c["field"] in g.get("fields", {}) for g in pv["conflicts"]))
    # new types: values (field names are the model's choice) and kind
    new_ok = new_total = kind_ok = 0
    for nt in labels.get("new_types", []):
        lines = {tuple(x) for x in nt["lines"]}
        recs = [r for r in pv["records"] if r["type"] in pv["new_types"]
                and {span_line[s] for s in r["spans"]} & lines]
        types = {r["type"] for r in recs}
        kind_ok += bool(types) and all(pv["new_types"][t].get("kind", "entity") == nt["kind"] for t in types)
        for vals in nt["values"]:
            new_total += len(vals)
            best = max((sum(1 for v in vals if _num(v) in {_num(x) for x in r["doc"].values()}) for r in recs),
                       default=0)
            new_ok += best
    # stored values must pass the evidence check again (by construction: 0)
    from mnemento.keeper.ingest import value_from_quote
    unsupported = sum(1 for r in pv["records"] for k, v in r["doc"].items()
                      if k in r.get("quotes", {}) and not isinstance(v, str) and value_from_quote(v, r["quotes"][k], None)
                      and not str(v).startswith("sec_"))
    return {
        "classification": [cls_ok, len(exp_kind)],
        "field_precision": [tp, got_fields], "field_recall": [tp, exp_fields],
        "new_type_values": [new_ok, new_total], "new_type_kind": [kind_ok, len(labels.get("new_types", []))],
        "values_not_in_source": unsupported, "dropped_values": len(pv["rejected_values"]),
        "wrong_merges": wrong_merges, "siblings": [sib_found, len(labels.get("siblings", []))],
        "conflict_recall": [conf_found, len(labels.get("conflicts", []))],
        "ambiguous": len(pv["ambiguous"]), "llm": pv.get("llm", {}),
    }


def run(model: str, which: str, run_id: str) -> None:
    from mnemento.keeper.llm import ClaudeCLIAdapter

    sources, labels = load_set(which)
    led = open_ledger()
    pv = Ingestor(led, ClaudeCLIAdapter(model=model)).preview(sources, now=NOW)
    s = score(pv, labels)
    out = RESULTS / run_id
    out.mkdir(parents=True, exist_ok=True)
    with (out / "results.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"model": model, "set": which, "score": s, "preview": pv}, ensure_ascii=False,
                            default=str) + "\n")
    print(f"M-{model} {which}: " + json.dumps({k: v for k, v in s.items() if k != "llm"}, ensure_ascii=False)
          + f" cost ${s['llm'].get('cost_usd') or 0:.3f}")
    led.close()


def check() -> int:
    """Every labelled chunk exists and every labelled record's fields are valid for its type (no LLM)."""
    bad = 0
    from mnemento.keeper.ingest import split

    for d in sorted(p for p in SETS.iterdir() if p.is_dir()):
        sources, labels = load_set(d.name)
        starts = {(n, c.start) for n, t in sources for c in split(t, n)}
        for f, m in labels["chunks"].items():
            for line in m:
                if (f, int(line)) not in starts:
                    print(f"{d.name}: no chunk starts at {f}:{line}")
                    bad += 1
        led = open_ledger()
        for r in labels["records"]:
            errs = led.schemas.get(r["type"]).validation_errors(r["doc"], lenient=True)
            if errs:
                print(f"{d.name}: {r['type']} label invalid: {errs}")
                bad += 1
        led.close()
        print(f"{d.name}: {sum(len(m) for m in labels['chunks'].values())} chunks, {len(labels['records'])} records, "
              f"{len(labels.get('new_types', []))} new types, {len(labels.get('conflicts', []))} conflicts")
    return bad


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    r = sub.add_parser("run")
    r.add_argument("--model", default="haiku")
    r.add_argument("--set", default="dev")
    r.add_argument("--run", default=None)
    a = p.parse_args(argv)
    if a.cmd == "check":
        sys.exit(1 if check() else 0)
    run(a.model, a.set, a.run or f"ingest-{a.set}")


if __name__ == "__main__":
    main()
