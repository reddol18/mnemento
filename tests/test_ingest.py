"""Issue #3 / ADR-0017, ADR-0025: mixed-text ingest on fictional notes. The LLM is a deterministic fake that reads the
prompt like the real one would; the code under test does the splitting, evidence checks, merging, conflicts,
preview/apply/revert and sibling warnings."""

import json
import re
from datetime import datetime

import pytest

from mnemento import Ledger
from mnemento.keeper import Keeper
from mnemento.keeper.ingest import Ingestor, IngestError, split, value_from_quote
from mnemento.keeper.llm import LLMResult, LLMUsage

NOW = datetime.fromisoformat("2026-10-01T09:00:00+09:00")

NOTES = """---
name: 투자 메모 (가상)
---
# 매매 기록
- 2026-07-30 가상바이오 1회차 3주 12,340원 매수 — 수출 반등이 근거
- 2026-08-20 가상바이오 2회차 3주 11,800원 매수

# 규칙
매수는 세 번에 나눠서 한다. 추격 매수 금지.

# 주간 정리
- 2026-07-30 가상바이오 1회차 3주 12,340원 매수
- 2026-08-20 가상바이오 2회차 3주 11,900원 매수

# 독서
| 날짜 | 책 | 별점 |
|---|---|---|
| 2026-09-01 | 가상의 책 | 4 |
"""

SCHEMAS = [
    {"name": "security", "version": 1, "description": "A stock (fictional).", "keywords": ["종목"],
     "fields": {"code": {"type": "string", "description": "Code.", "identifier": True, "indexed": True},
                "name": {"type": "string", "description": "Name.", "indexed": True}}},
    {"name": "trade", "version": 1, "description": "A buy or sell.", "keywords": ["매수", "매도", "체결"],
     "default_date_field": "traded_at",
     "relations": [{"type": "decision", "note": "a trade and the decision behind it may be one event"}],
     "fields": {"security_id": {"type": "string", "ref": "security", "description": "The stock.", "required": True,
                                "indexed": True},
                "traded_at": {"type": "string", "format": "date", "description": "Trade day.", "required": True},
                "side": {"type": "string", "enum": ["buy", "sell"], "labels": {"buy": ["매수"], "sell": ["매도"]},
                         "description": "buy or sell"},
                "units": {"type": "integer", "description": "Shares."},
                "price": {"type": "number", "description": "Price per share, KRW."}}},
    {"name": "decision", "version": 1, "description": "Why I traded or did not.", "default_date_field": "decided_at",
     "fields": {"security_id": {"type": "string", "ref": "security", "description": "The stock.", "indexed": True},
                "decided_at": {"type": "string", "format": "date", "description": "Day."},
                "text": {"type": "string", "description": "The reason, as written."}}},
]


class FakeLLM:
    """Answers by reading the chunks in the prompt — deterministic stand-in for the real model."""
    model = "fake"

    def __init__(self, lie: bool = False):
        self.calls = []
        self.lie = lie  # put a price that is not in the quote into one trade

    def complete_json(self, *, system, prompt, schema, stage):
        self.calls.append(stage)
        chunks = [json.loads(l) for l in prompt.split("\n") if l.startswith("{")]
        if stage == "classify":
            out = []
            for c in chunks:
                t = c["text"]
                if "매수" in t and "원" in t:
                    types = ["trade"] + (["decision"] if "근거" in t else [])
                    out.append({"id": c["id"], "kind": "data", "types": types, "reason": "a trade"})
                elif "가상의 책" in t:
                    out.append({"id": c["id"], "kind": "data", "types": ["reading_note"], "reason": "a book read"})
                else:
                    out.append({"id": c["id"], "kind": "not_data", "types": [], "reason": "rule or metadata"})
            data = {"chunks": out}
        elif stage == "draft_schema":
            data = {"description": "Books I finished (fictional).", "kind": "entity", "keywords": ["독서", "책"],
                    "series_key": [], "time_field": "", "measures": [],
                    "fields": [{"name": "title", "type": "string", "format": "", "description": "Book title.",
                                "enum": [], "labels": [], "ref": ""},
                               {"name": "read_on", "type": "string", "format": "date", "description": "Day finished.",
                                "enum": [], "labels": [], "ref": ""},
                               {"name": "rating", "type": "integer", "format": "", "description": "Stars 1-5.",
                                "enum": [], "labels": [], "ref": ""}]}
        else:
            recs = []
            for c in chunks:
                t = c["text"]
                if "trade" in c["types"]:
                    d = re.search(r"\d{4}-\d{2}-\d{2}", t).group(0)
                    units = re.search(r"(\d+)주", t)
                    price = re.search(r"([\d,]+)원", t)
                    pv = 99999 if self.lie and "2회차" in t and "11,800" in t else float(price.group(1).replace(",", ""))
                    recs.append({"chunk": c["id"], "type": "trade", "values": [
                        {"field": "security_id", "value": "가상바이오", "quote": "가상바이오"},
                        {"field": "traded_at", "value": d, "quote": d},
                        {"field": "side", "value": "buy", "quote": "매수"},
                        {"field": "units", "value": int(units.group(1)), "quote": units.group(0)},
                        {"field": "price", "value": pv, "quote": price.group(0)}]})
                if "decision" in c["types"]:
                    d = re.search(r"\d{4}-\d{2}-\d{2}", t).group(0)
                    recs.append({"chunk": c["id"], "type": "decision", "values": [
                        {"field": "security_id", "value": "가상바이오", "quote": "가상바이오"},
                        {"field": "decided_at", "value": d, "quote": d},
                        {"field": "text", "value": "수출 반등이 근거", "quote": "수출 반등이 근거"}]})
                if "reading_note" in c["types"]:
                    recs.append({"chunk": c["id"], "type": "reading_note", "values": [
                        {"field": "title", "value": "가상의 책", "quote": "가상의 책"},
                        {"field": "read_on", "value": "2026-09-01", "quote": "2026-09-01"},
                        {"field": "rating", "value": 4, "quote": "| 4 |"}]})
            data = {"records": recs}
        return LLMResult(data, LLMUsage(stage=stage, model="fake", input_tokens=len(prompt) // 4, output_tokens=10,
                                        cost_usd=0.0, wall_ms=0.0, model_ms=0.0, overhead_ms=0.0))


@pytest.fixture
def led(tmp_path):
    ledger = Ledger.open(tmp_path / "i.db")
    for s in SCHEMAS:
        ledger.schemas.register(s)
    ledger.record_event("sec_900001", "created", {"code": "900001", "name": "가상바이오"},
                        "2026-07-01T09:00:00+09:00", "t", None, entity_type="security")
    yield ledger
    ledger.close()


def test_split_keeps_lines_headings_and_table_headers():
    chunks = split(NOTES, "memo.md")
    assert chunks[0].start == 1 and chunks[0].end == 3  # front matter
    trade = next(c for c in chunks if "1회차" in c.text)
    assert (trade.start, trade.end) == (5, 5) and trade.context == "매매 기록"
    row = next(c for c in chunks if "가상의 책" in c.text)
    assert "table header: | 날짜 | 책 | 별점 |" in row.context
    rule = next(c for c in chunks if "추격" in c.text)
    assert rule.context == "규칙"
    assert len({c.span_id for c in chunks}) == len(chunks)


@pytest.mark.parametrize("value,quote,ok", [
    (12340.0, "12,340원", True), (12340.0, "1.2만원", False), (12000.0, "1.2만원", True), (3, "3주", True),
    ("2026-07-30", "2026-07-30", True), ("2026-07-30", "7월 30일", True), ("2026-07-31", "7/30", False),
    ("가상바이오", "가상바이오(900001)", True), ("샘플전자", "가상바이오", False),
])
def test_values_must_come_from_their_quote(value, quote, ok):
    from mnemento.schema.definition import FieldDef

    fd = FieldDef("x", "string", "d", format="date") if str(value).startswith("2026") else None
    assert (value_from_quote(value, quote, fd) is None) == ok


def test_preview_merges_repeats_reports_conflicts_and_keeps_notes(led):
    pv = Ingestor(led, FakeLLM(lie=True)).preview([("memo.md", NOTES)], now=NOW)
    s = pv["summary"]
    assert s["by_kind"] == {"data": 5, "not_data": 2, "ambiguous": 0}
    # 07-30: one record from two spans. 08-20: the made-up price was dropped, so only 11,900 is left and the two
    # spans merge (the dropped value is listed under rejected_values for the user to see)
    assert s["records"] == {"decision": 1, "reading_note": 1, "trade": 2}
    trade = next(r for r in pv["records"] if r["type"] == "trade" and r["doc"]["traded_at"] == "2026-07-30")
    assert len(trade["spans"]) == 2 and trade["doc"]["security_id"] == "sec_900001" and trade["doc"]["price"] == 12340
    decision = next(r for r in pv["records"] if r["type"] == "decision")
    assert decision["siblings"] == [trade["rid"]] and trade["siblings"] == [decision["rid"]]
    assert any(r["value"] == 99999 and r["why"] == "number not in the quote" for r in pv["rejected_values"])
    assert s["new_types"] == ["reading_note"] and pv["new_types"]["reading_note"]["fields"]["read_on"]["format"] == "date"
    assert {n["summary"] for n in pv["notes"]} == {"rule or metadata"} and len(pv["notes"]) == 2


def test_conflicting_repeats_are_never_picked(led):
    pv = Ingestor(led, FakeLLM()).preview([("memo.md", NOTES)], now=NOW)
    c = next(c for c in pv["conflicts"] if c["type"] == "trade")
    assert c["fields"] == {"price": ["11800.0", "11900.0"]} and len(c["spans"]) == 2
    assert not any(r["type"] == "trade" and r["doc"]["traded_at"] == "2026-08-20" for r in pv["records"])


def test_apply_writes_exactly_the_preview_and_revert_takes_it_back(led):
    ing = Ingestor(led, FakeLLM())
    pv = ing.preview([("memo.md", NOTES)], now=NOW)
    with pytest.raises(IngestError):
        ing.apply(pv["id"], approved_by="", user_answer="")
    out = ing.apply(pv["id"], approved_by="user", user_answer="미리보기대로 넣어", now=NOW)
    assert out["records"] == 3 and out["new_types"] == ["reading_note"] and out["notes_kept"] == 2
    assert led.schemas.get("reading_note").is_draft  # ADR-0021: approved as a type later
    trades = led.find("trade")
    assert len(trades) == 1 and "ingest:" in led.history(trades[0].id)[0].evidence
    with pytest.raises(IngestError, match="already applied"):
        ing.apply(pv["id"], approved_by="user", user_answer="again")
    again = ing.preview([("memo.md", NOTES)], now=NOW)  # the same notes once more: already recorded, nothing new
    assert again["summary"]["already_recorded"] >= 2 and not any(r["type"] == "trade" for r in again["records"])
    rv = ing.revert(out["batch_id"], now=NOW)
    assert rv["retracted_records"] == 3 and led.find("trade") == []


def test_correcting_a_record_asks_about_its_siblings(led):
    ing = Ingestor(led, FakeLLM())
    pv = ing.preview([("memo.md", NOTES)], now=NOW)
    ing.apply(pv["id"], approved_by="user", user_answer="ok", now=NOW)
    trade = led.find("trade")[0]
    k = Keeper(led, None)
    created = led.history(trade.id)[0]
    res = k.record({"entity_type": "trade", "kind": "corrected", "entity_id": trade.id,
                    "payload": {"target": created.id, "payload": {**trade.doc, "units": 4}}}, by="user",
                   evidence="잘못 적음")
    assert res.status == "recorded"
    sib = led.find("decision")[0].id
    assert any(sib in q and "sibling" in q for q in res.questions)
    assert k.get_entity(trade.id)["siblings"] == [sib]


def test_ingest_eval_scorer_runs_on_a_preview():
    from bench.ingest_eval import load_set, open_ledger, score

    sources, labels = load_set("dev")
    led = open_ledger()
    pv = Ingestor(led, FakeLLM()).preview(sources, now=NOW)
    s = score(pv, labels)
    assert s["values_not_in_source"] == 0 and s["classification"][1] == 13
    assert s["field_recall"][0] >= 5  # the fake model finds the 07-30 and 08-20 buys
    led.close()
