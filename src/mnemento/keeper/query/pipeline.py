"""Question pipeline: interpret -> define query -> answer (PLAN 5-1 A), with measurement."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ...ledger import ENTITY_ID_RE, Ledger
from ...timeutil import now as tz_now
from ..identity import IdentityResolver
from ..llm import LLMAdapter, LLMError
from ..trace import Trace
from .answer import (NARRATE_SCHEMA, NARRATE_SYSTEM, execute, narration_payload, ref_labels, render_text,
                     warnings_for)
from ..drafts import effective_schema
from .cache import PlanCache
from .compile import compile_spec
from .series_query import compile_series, execute_series, series_warnings
from .log import QueryLog
from .interpret import (Clarification, InterpretError, dump_spec, interpret, observed_values,
                        select_schemas)
from .rules import parse_simple
from .spec import EVENT_SCHEMA, Filter, QuerySpec, validate_spec


@dataclass
class KeeperAnswer:
    status: str  # "answered" | "clarify" | "error"
    question: str
    text: str
    spec: dict[str, Any] | None = None
    sql: str | None = None
    params: list[Any] = field(default_factory=list)
    result: dict[str, Any] | None = None
    evidence: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    resolved: dict[str, Any] = field(default_factory=dict)
    options: list[str] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


_FROM_ENV = object()


class QueryPipeline:
    def __init__(self, ledger: Ledger, llm: LLMAdapter | None = None, use_cache: bool = True,
                 query_log: QueryLog | None | object = _FROM_ENV):
        self.ledger = ledger
        self.llm = llm
        self.cache: PlanCache | None = PlanCache(ledger.storage) if use_cache else None
        # ADR-0015: every answer is logged locally unless MNEMENTO_QUERY_LOG=off (or query_log=None)
        self.query_log: QueryLog | None = (QueryLog.from_env(ledger.storage) if query_log is _FROM_ENV
                                           else query_log)  # type: ignore[assignment]

    def _schemas(self):
        """Registered schemas plus drafts (fields/values stored but not organized yet) — ADR-0014: what is
        stored can be asked about right away."""
        return {n: effective_schema(self.ledger, n) for n in self.ledger.schemas.names()}

    def ask(self, question: str, *, spec: QuerySpec | dict | None = None, now: datetime | None = None,
            narrate: bool = False, hint: str | None = None, caller: str | None = None) -> KeeperAnswer:
        """`hint`: how the asker wants the answer shaped (passed to the interpreter only; plans
        interpreted with a hint are not cached, since the same words may come with another hint).
        `caller`: who asked (agent or client name) — kept in the query log only."""
        now = now or tz_now(self.ledger.tz)
        try:
            ans = self._ask(question, spec=spec, now=now, narrate=narrate, hint=hint)
        except Exception as exc:
            self._log(KeeperAnswer("error", question, f"{type(exc).__name__}: {exc}",
                                   spec=dump_spec(spec) if isinstance(spec, QuerySpec) else spec), caller, now)
            raise
        self._log(ans, caller, now)
        return ans

    def _log(self, ans: KeeperAnswer, caller: str | None, now: datetime) -> None:
        if self.query_log is None:
            return
        try:  # straight from the table: building schema objects would cost more than the log itself
            versions = {r["name"]: r["v"] for r in self.ledger.storage.fetch_all(
                "SELECT name, MAX(version) AS v FROM schemas GROUP BY name")}
        except Exception:  # noqa: BLE001
            versions = {}
        self.query_log.write(ans, caller=caller, now=now, schema_versions=versions)

    def _ask(self, question: str, *, spec: QuerySpec | dict | None, now: datetime, narrate: bool,
             hint: str | None) -> KeeperAnswer:
        trace = Trace()
        schemas = self._schemas()

        # ---- ① interpretation -------------------------------------------------------------
        with trace.stage("interpret"):
            if spec is not None:
                trace.path = "structured"
                spec = spec if isinstance(spec, QuerySpec) else QuerySpec.model_validate(spec)
                errs = validate_spec(spec, schemas)
                if errs:
                    return self._done(KeeperAnswer("error", question, "Invalid QuerySpec: " + "; ".join(errs),
                                                   spec=dump_spec(spec)), trace)
            else:
                q = question.strip()
                if ENTITY_ID_RE.match(q) and self.ledger.get_entity(q):
                    trace.path = "fast"
                    ent = self.ledger.get_entity(q)
                    return self._done(KeeperAnswer(
                        "answered", question, f"{ent.id} ({ent.type}): {ent.doc}",
                        result={"entity": ent.as_json(), "retracted": ent.retracted},
                        evidence=[ent.id]), trace)
                spec = parse_simple(question, schemas, now, resolve_name=self._certain_name)
                if spec is not None:
                    trace.path = "fast"
                elif hint is None and self.cache is not None and (cached := self.cache.lookup(question, schemas, now)):
                    trace.path = "cache"
                    spec = cached
                elif self.llm is None:
                    return self._done(KeeperAnswer(
                        "error", question,
                        "This question needs the LLM interpreter, but no LLM is configured. "
                        "Pass a structured QuerySpec instead."), trace)
                else:
                    trace.path = "llm"
                    try:
                        observed = observed_values(self.ledger.storage.fetch_all,
                                                   select_schemas(question, schemas))
                        out = interpret(question, llm=self.llm, schemas=schemas, observed=observed,
                                        now=now, tz=self.ledger.tz, trace=trace, hint=hint)
                    except InterpretError as exc:
                        return self._done(KeeperAnswer(
                            "clarify", question,
                            "I could not express this question with the recorded fields: "
                            + "; ".join(exc.errors), options=[]), trace)
                    except LLMError as exc:
                        return self._done(KeeperAnswer("error", question, f"LLM call failed: {exc}"), trace)
                    if isinstance(out, Clarification):
                        return self._done(KeeperAnswer("clarify", question, out.question,
                                                       options=out.options), trace)
                    spec = out
                    if self.cache is not None and hint is None:
                        self.cache.store(question, spec, schemas, now)

        # ---- ② query definition -----------------------------------------------------------
        with trace.stage("resolve"):
            spec, resolved_names, clar = self._resolve_names(spec)
            if clar is not None:
                return self._done(KeeperAnswer("clarify", question, clar.question, spec=dump_spec(spec),
                                               options=clar.options, resolved={"names": resolved_names}),
                                  trace)
        schema = EVENT_SCHEMA if spec.source == "events" else schemas[spec.entity_type]
        if spec.source == "series":
            return self._done(self._series(question, spec, schema, now, trace, narrate), trace)
        with trace.stage("compile"):
            compiled = compile_spec(spec, schema, now)
        with trace.stage("execute"):
            result = execute(compiled, self.ledger.storage.fetch_all)

        # ---- ③ answer ---------------------------------------------------------------------
        with trace.stage("answer"):
            notes = warnings_for(spec, schema, result, now, compiled.resolved_dates,
                                 self.ledger.storage.fetch_all)
            for name, info in resolved_names.items():
                if info["matches"]:
                    notes.append(f"'{name}' matched {info['matches']} by {info['rule']}.")
                else:
                    notes.append(f"'{name}' matches no recorded entity (exact name, alias or identifier).")
            labels = ref_labels(schema, result, self.ledger.storage.get_entity) if spec.source == "entities" else {}
            default_fields = [f for f, fd in schema.fields.items() if fd.ref] + (
                ["status"] if "status" in schema.fields else [])
            text = render_text(spec, result, notes, labels, default_fields)
        ans = KeeperAnswer(
            "answered", question, text, spec=dump_spec(spec), sql=compiled.sql, params=compiled.params,
            result={"mode": result.mode, "total": result.total,
                    "groups": result.groups if result.mode != "list" else [],
                    "rows": result.rows, "labels": labels},
            evidence=result.evidence, warnings=notes,
            resolved={"dates": compiled.resolved_dates, "names": resolved_names, "now": now.isoformat()},
        )
        if narrate:
            with trace.stage("narrate"):
                prose, usage = self.narrate(ans)
                if usage is not None:
                    trace.add_llm(usage)
                if prose:
                    ans.text = prose + "\n\n" + ans.text
        return self._done(ans, trace)

    def _series(self, question: str, spec: QuerySpec, schema, now: datetime, trace: Trace,
                narrate: bool) -> KeeperAnswer:
        """ADR-0016: series questions (measurements over time) — same stages, series SQL and warnings."""
        with trace.stage("compile"):
            compiled = compile_series(spec, schema, now)
        with trace.stage("execute"):
            result = execute_series(compiled, self.ledger.storage.fetch_all)
        with trace.stage("answer"):
            notes = series_warnings(spec, schema, compiled, result, now, self.ledger.storage.fetch_all)
            text = render_text(spec, result, notes, {}, compiled.columns)
        ans = KeeperAnswer(
            "answered", question, text, spec=dump_spec(spec), sql=compiled.sql, params=compiled.params,
            result={"mode": result.mode, "total": result.total,
                    "groups": result.groups if result.mode != "list" else [], "rows": result.rows, "labels": {}},
            evidence=result.evidence, warnings=notes,
            resolved={"dates": compiled.resolved_dates, "names": {}, "now": now.isoformat()})
        if narrate:
            with trace.stage("narrate"):
                prose, usage = self.narrate(ans)
                if usage is not None:
                    trace.add_llm(usage)
                if prose:
                    ans.text = prose + "\n\n" + ans.text
        return ans

    def narrate(self, ans: KeeperAnswer) -> tuple[str | None, Any]:
        """Optional prose answer written by the LLM from the aggregates of an existing answer.
        Returns (prose or None, LLMUsage or None). Used by ask(narrate=True) and by the benchmark,
        which adds narration on top of an already interpreted answer."""
        if self.llm is None or ans.status != "answered" or not ans.result or "entity" in ans.result:
            return None, None
        payload = narration_payload(ans.question, (ans.spec or {}).get("interpretation", ""), ans.result,
                                    ans.warnings, (ans.spec or {}).get("list_fields", []))
        try:
            r = self.llm.complete_json(system=NARRATE_SYSTEM, prompt=_json(payload), schema=NARRATE_SCHEMA,
                                       stage="narrate")
        except LLMError as exc:
            ans.warnings.append(f"narration failed: {exc}")
            return None, None
        return r.data["answer"], r.usage

    def _certain_name(self, ref_type: str, text: str) -> bool:
        if ref_type not in self.ledger.schemas.names() or len(text) < 2:
            return False
        res = IdentityResolver(self.ledger, ref_type).resolve(text)
        return res.status == "match" and res.rule != "identifier"

    def _resolve_names(self, spec: QuerySpec):
        """Replace name_is filters with id filters via identity resolution (ADR-0006)."""
        schema = self.ledger.schemas.get(spec.entity_type)
        new_filters, resolved = [], {}
        for f in spec.filters:
            if f.op != "name_is":
                new_filters.append(f)
                continue
            ref = schema.fields[f.field].ref
            res = IdentityResolver(self.ledger, ref).resolve(str(f.value))
            # several records sharing one external identifier: all of them are meant (same company)
            if res.status == "match" or (res.status == "ambiguous" and res.rule is not None
                                         and (res.rule == "business_number" or res.rule.startswith("identifier:"))):
                resolved[str(f.value)] = {"matches": res.matches, "rule": res.rule}
                new_filters.append(Filter(field=f.field, op="in", value=res.matches))
            elif res.status == "ambiguous":
                return spec, resolved, Clarification(
                    f"'{f.value}' matches several {ref} records. Which one?", res.matches)
            elif res.status == "candidates":
                return spec, resolved, Clarification(
                    f"No {ref} is recorded exactly as '{f.value}'. Did you mean one of these?",
                    [f"{c['id']} ({c['name']})" for c in res.candidates[:5]] + ["none of these"])
            else:
                resolved[str(f.value)] = {"matches": [], "rule": None}
                new_filters.append(Filter(field=f.field, op="in", value=["__no_match__"]))
        return spec.model_copy(update={"filters": new_filters}), resolved, None

    @staticmethod
    def _done(ans: KeeperAnswer, trace: Trace) -> KeeperAnswer:
        ans.trace = trace.finish().to_dict()
        return ans


def _json(v: Any) -> str:
    import json

    return json.dumps(v, ensure_ascii=False, default=str)
