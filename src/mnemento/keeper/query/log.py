"""Query log (ADR-0015): every question -> interpretation -> SQL -> result, kept in the local database
so answers can be checked afterwards ("why did the same question give another number?").

Writing a log row never blocks an answer: failures are swallowed and counted in `write_errors`.
Retention: at most `max_rows` rows or `max_bytes` (approximate, summed row sizes), whichever comes
first. When a limit is passed the oldest rows are dropped down to 90% of it; rows that are errors or
part of a diverging group are dropped last, up to `keep_ratio` of `max_rows`.

Environment: MNEMENTO_QUERY_LOG = off | on | <max rows>.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from ...storage.base import Storage
from ...timeutil import format_instant
from .cache import extract_slots

MAX_ROWS = 10_000
MAX_BYTES = 50 * 1024 * 1024
KEEP_RATIO = 0.2
MAX_EVIDENCE = 200
CHECK_EVERY = 50  # inserts between retention checks

VIEWS = ("recent", "warnings", "diverging", "errors", "path", "empty")


class QueryLog:
    def __init__(self, storage: Storage, *, max_rows: int = MAX_ROWS, max_bytes: int = MAX_BYTES,
                 keep_ratio: float = KEEP_RATIO, check_every: int = CHECK_EVERY):
        self.storage = storage
        self.max_rows = max_rows
        self.max_bytes = max_bytes
        self.keep_ratio = keep_ratio
        self.check_every = check_every
        self._since_check = check_every  # check on the first write after start
        self.write_errors = 0

    @classmethod
    def from_env(cls, storage: Storage) -> "QueryLog | None":
        v = os.environ.get("MNEMENTO_QUERY_LOG", "on").strip().lower()
        if v in ("off", "0", "false", "no"):
            return None
        if v.isdigit():
            return cls(storage, max_rows=int(v))
        return cls(storage)

    # ---- write ----------------------------------------------------------------------------

    def write(self, ans: Any, *, caller: str | None, now: datetime,
              schema_versions: dict[str, int]) -> int | None:
        """Log one answer (a KeeperAnswer). Returns the row id, or None when logging failed."""
        try:
            row = self._row(ans, caller=caller, now=now, schema_versions=schema_versions)
            row_id = self.storage.insert_query_log(row)
            if row["sql"] and self._diverges(row["question_norm"]):
                self.storage.mark_query_log_diverging(row["question_norm"])
            self._since_check += 1
            if self._since_check >= self.check_every:
                self._since_check = 0
                self.enforce_limits()
            return row_id
        except Exception:  # noqa: BLE001 — the log must never break an answer
            self.write_errors += 1
            return None

    @staticmethod
    def _row(ans: Any, *, caller: str | None, now: datetime, schema_versions: dict[str, int]) -> dict[str, Any]:
        trace = ans.trace or {}
        path = trace.get("path") or "none"
        if ans.result and "entity" in ans.result:
            path = "entity"
        spec = ans.spec or None
        evidence = list(ans.evidence or [])
        row = {
            "asked_at": format_instant(now),
            "caller": caller or "unknown",
            "question": ans.question,
            "question_norm": extract_slots(ans.question, now)[0],
            "path": path,
            "status": ans.status,
            "interpretation": (spec or {}).get("interpretation"),
            "spec": _dumps(spec) if spec else None,
            "sql": ans.sql,
            "params": _dumps(ans.params) if ans.sql else None,
            "result_total": (ans.result or {}).get("total") if ans.result and "entity" not in ans.result
            else (1 if ans.result else None),
            "evidence": _dumps(evidence[:MAX_EVIDENCE]),
            "warnings": _dumps(list(ans.warnings or [])),
            "stages_ms": _dumps(trace.get("stages_ms", {})),
            "llm": _dumps({"calls": [_llm_brief(u) for u in trace.get("llm", [])],
                           "totals": {k: (trace.get("totals") or {}).get(k) for k in
                                      ("input_tokens", "output_tokens", "cost_usd", "total_ms")}}),
            "error": ans.text if ans.status == "error" else None,
            "schema_versions": _dumps(schema_versions),
        }
        row["size"] = sum(len(v) for v in row.values() if isinstance(v, str))
        return row

    def _diverges(self, question_norm: str) -> bool:
        rows = self.storage.fetch_all(
            "SELECT COUNT(DISTINCT sql) AS n FROM query_log WHERE question_norm = ? AND sql IS NOT NULL",
            [question_norm])
        return rows[0]["n"] > 1

    # ---- retention ------------------------------------------------------------------------

    def enforce_limits(self) -> int:
        """Drop old rows when a limit is passed. Returns how many rows were deleted."""
        stat = self.storage.fetch_all("SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS b FROM query_log")[0]
        if stat["n"] <= self.max_rows and stat["b"] <= self.max_bytes:
            return 0
        rows = self.storage.fetch_all(
            "SELECT id, size, (status = 'error' OR diverging = 1) AS keep FROM query_log ORDER BY id DESC")
        target_rows, target_bytes = int(self.max_rows * 0.9), int(self.max_bytes * 0.9)
        keep_budget = int(self.max_rows * self.keep_ratio)
        # newest first: keep priority rows (within budget) first, then the newest ordinary rows
        kept, n, b = set(), 0, 0
        for r in rows:
            if r["keep"] and keep_budget > 0 and n < target_rows and b + r["size"] <= target_bytes:
                kept.add(r["id"]); keep_budget -= 1; n += 1; b += r["size"]
        for r in rows:
            if r["id"] in kept or r["keep"]:
                continue
            if n >= target_rows or b + r["size"] > target_bytes:
                break
            kept.add(r["id"]); n += 1; b += r["size"]
        drop = [r["id"] for r in rows if r["id"] not in kept]
        return self.storage.delete_query_log(drop) if drop else 0

    def purge(self, before: str | None = None) -> int:
        """Delete rows asked before `before` (ISO 8601), or every row when None."""
        if before is None:
            ids = [r["id"] for r in self.storage.fetch_all("SELECT id FROM query_log")]
        else:
            ids = [r["id"] for r in self.storage.fetch_all("SELECT id FROM query_log WHERE asked_at < ?", [before])]
        return self.storage.delete_query_log(ids) if ids else 0

    # ---- read -----------------------------------------------------------------------------

    def find(self, view: str = "recent", *, n: int = 20, since: str | None = None,
             path: str | None = None) -> dict[str, Any]:
        if view not in VIEWS:
            raise ValueError(f"view must be one of {', '.join(VIEWS)}")
        n = max(1, min(int(n), 500))
        if view == "diverging":
            return {"view": view, "groups": self._diverging(n, since)}
        where, params = [], []
        if since:
            where.append("asked_at >= ?"); params.append(since)
        if view == "warnings":
            where.append("warnings <> '[]'")
        elif view == "errors":
            where.append("status = 'error'")
        elif view == "empty":  # answered with nothing found: a wrong reading looks exactly like this (issue #5)
            where.append("status = 'answered' AND result_total = 0")
        elif view == "path" and not path:
            raise ValueError("view 'path' needs path (fast | cache | llm | structured | entity | none)")
        if path and view in ("path", "empty"):
            where.append("path = ?"); params.append(path)
        sql = "SELECT * FROM query_log" + (" WHERE " + " AND ".join(where) if where else "") + \
              " ORDER BY id DESC LIMIT ?"
        entries = [_entry(r) for r in self.storage.fetch_all(sql, [*params, n])]
        out: dict[str, Any] = {"view": view, "entries": entries}
        if view == "path" or view == "recent":
            out["by_path"] = {r["path"]: r["n"] for r in self.storage.fetch_all(
                "SELECT path, COUNT(*) AS n FROM query_log" + (" WHERE asked_at >= ?" if since else "") +
                " GROUP BY path ORDER BY n DESC", [since] if since else [])}
        return out

    def _diverging(self, n: int, since: str | None) -> list[dict[str, Any]]:
        cond, params = ("AND asked_at >= ?", [since]) if since else ("", [])
        norms = self.storage.fetch_all(
            f"SELECT question_norm, MAX(id) AS last FROM query_log WHERE diverging = 1 {cond} "
            "GROUP BY question_norm ORDER BY last DESC LIMIT ?", [*params, n])
        groups = []
        for g in norms:
            variants = self.storage.fetch_all(
                f"SELECT sql, COUNT(*) AS times, MAX(asked_at) AS last_asked, MAX(question) AS example, "
                f"json_group_array(result_total) AS totals, json_group_array(id) AS ids, "
                f"MAX(interpretation) AS interpretation, json_group_array(DISTINCT path) AS paths "
                f"FROM query_log WHERE question_norm = ? AND sql IS NOT NULL {cond} "
                "GROUP BY sql ORDER BY last_asked DESC", [g["question_norm"], *params])
            groups.append({"question_norm": g["question_norm"], "variants": [
                {**v, "totals": json.loads(v["totals"]), "ids": json.loads(v["ids"]),
                 "paths": json.loads(v["paths"])} for v in variants]})
        return groups


def _llm_brief(u: dict[str, Any]) -> dict[str, Any]:
    return {k: u.get(k) for k in ("stage", "model", "input_tokens", "output_tokens", "cost_usd", "wall_ms")}


def _entry(r: dict[str, Any]) -> dict[str, Any]:
    out = dict(r)
    for k in ("spec", "params", "evidence", "warnings", "stages_ms", "llm", "schema_versions"):
        if out.get(k) is not None:
            out[k] = json.loads(out[k])
    out["diverging"] = bool(out["diverging"])
    out.pop("size", None)
    return out


def _dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=str, separators=(",", ":"))
