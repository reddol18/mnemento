"""Render one generated dataset three ways — the same facts for every system:

- M:  a Mnemento ledger (events through the normal write API)
- B1: Claude Code style memory — `MEMORY.md` index + one markdown file per record
- B0: the same memory files concatenated into one text, put in the prompt whole
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from mnemento import Ledger

from .generate import TZ, App, Company, Dataset

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas"
BY = "bench_seed"
PLATFORM_KO = {"saramin": "사람인", "wanted": "원티드", "groupby": "그룹바이", "jobkorea": "잡코리아"}
STATUS_KO = {"applied": "지원완료", "viewed": "열람", "passed": "서류합격", "rejected": "불합격", "withdrawn": "지원취소(패스)"}
RATE_KO = {"top10": "상위 10%", "top30": "상위 30%", "top50": "상위 50%"}
MEMORY_INDEX_LINES = 200  # Claude Code loads only the first 200 lines of MEMORY.md


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ---- M: Mnemento ledger --------------------------------------------------------------------

def to_ledger(ds: Dataset, path: str | Path) -> Ledger:
    led = Ledger.open(path)
    led.schemas.load_dir(SCHEMA_DIR)
    seed_at = ds.now - timedelta(days=4000)
    for c in ds.companies:
        doc = {"name": c.name, "normalized_name": c.normalized}
        if c.aliases:
            doc["aliases"] = c.aliases
        led.record_event(c.id, "created", doc, _iso(seed_at), BY, "bench seed", entity_type="company")
    for a in sorted(ds.apps, key=lambda a: a.applied):
        doc = {"company_id": a.company_id, "platform": a.platform, "status": "applied",
               "applied_at": (a.recorded_applied_date or a.applied_date).isoformat(), "reason": a.reason}
        if a.rate:
            doc["expected_rate"] = a.rate
        created = led.record_event(a.id, "created", doc, _iso(a.applied), BY, "지원완료 메일",
                                   entity_type="application")
        if a.recorded_applied_date:
            led.record_event(a.id, "corrected",
                             {"target": created.id, "payload": {**doc, "applied_at": a.applied_date.isoformat()}},
                             _iso(a.applied + timedelta(days=1)), BY, a.correction_reason)
        if a.retracted:
            led.record_event(a.id, "retracted", {}, _iso(a.applied + timedelta(hours=3)), BY, a.retraction_reason)
            continue
        for ev in a.events:
            led.record_event(a.id, "status_changed", {"to": ev.to}, _iso(ev.at), BY, f"플랫폼 알림: {ev.to}")
            if ev.to == "viewed":
                led.record_event(a.id, "updated", {"viewed_at": ev.at.date().isoformat()}, _iso(ev.at), BY,
                                 "플랫폼 알림: viewed")
    return led


# ---- B1 / B0: memory markdown --------------------------------------------------------------

def _company_file(c: Company) -> tuple[str, str, str]:
    fname = f"company_{c.id}.md"
    alias = f" (다른 이름: {', '.join(c.aliases)})" if c.aliases else ""
    body = (f"---\nname: company-{c.id}\ndescription: 회사 {c.name}{alias}\nmetadata:\n  type: reference\n---\n\n"
            f"- 회사명: {c.name}\n- 정규화 이름: {c.normalized}\n"
            + (f"- 다른 이름(별칭): {', '.join(c.aliases)}\n" if c.aliases else "")
            + f"- 회사 id: {c.id}\n")
    hook = f"회사 {c.name}{alias}"
    return fname, body, hook


def _app_file(a: App, c: Company) -> tuple[str, str, str]:
    shown = a.company_label or c.name  # v2: some records were written with the company's other name
    fname = f"application_{a.id}.md"
    shown_date = a.recorded_applied_date or a.applied_date
    status = "무효(중복 기록, 철회됨)" if a.retracted else STATUS_KO[a.status]
    lines = [
        f"- 회사: {shown} (company_{c.id}.md)",
        f"- 플랫폼: {PLATFORM_KO[a.platform]}",
        f"- 지원 시각: {a.applied.strftime('%Y-%m-%d %H:%M')} (KST)",
        f"- 지원일: {a.applied_date.isoformat()}",
        f"- 예상 합격률: {RATE_KO.get(a.rate, '미기재')}",
        f"- 지원 이유: {a.reason}",
        f"- 현재 상태: {status}",
    ]
    if a.events and not a.retracted:
        lines.append("- 진행 기록:")
        for ev in a.events:
            lines.append(f"  - {ev.at.strftime('%Y-%m-%d %H:%M')} {STATUS_KO[ev.to]}")
    if a.recorded_applied_date:
        lines.append(f"- 정정: 처음에 지원일을 {shown_date.isoformat()}로 적었으나 {a.applied_date.isoformat()}로 정정함 "
                     f"(이유: {a.correction_reason})")
    if a.retracted:
        lines.append(f"- 철회: 이 기록은 무효 (이유: {a.retraction_reason})")
    desc = f"{a.applied_date.isoformat()} {PLATFORM_KO[a.platform]} 지원 — {shown}, {status}"
    body = (f"---\nname: application-{a.id}\ndescription: {desc}\nmetadata:\n  type: project\n---\n\n"
            + "\n".join(lines) + "\n")
    return fname, body, desc


def memory_files(ds: Dataset) -> list[tuple[str, str, str]]:
    """[(file name, content, index hook)] in the order an agent would have written them."""
    out = []
    by_id = {c.id: c for c in ds.companies}
    seen: set[str] = set()
    for a in sorted(ds.apps, key=lambda a: a.applied):
        c = by_id[a.company_id]
        if c.id not in seen:
            seen.add(c.id)
            out.append(_company_file(c))
        out.append(_app_file(a, c))
    return out


def to_memory_dir(ds: Dataset, directory: str | Path) -> Path:
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    files = memory_files(ds)
    index = ["# Memory index", ""]
    for fname, body, hook in files:
        (d / fname).write_text(body, encoding="utf-8")
        index.append(f"- [{fname[:-3]}]({fname}) — {hook}")
    (d / "MEMORY.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    return d


def memory_index_head(directory: str | Path, lines: int = MEMORY_INDEX_LINES) -> str:
    all_lines = (Path(directory) / "MEMORY.md").read_text(encoding="utf-8").splitlines()
    head = all_lines[:lines]
    if len(all_lines) > lines:
        head.append(f"... ({len(all_lines) - lines} more lines truncated)")
    return "\n".join(head)


def full_context(ds: Dataset) -> str:
    return "\n\n".join(f"### {fname}\n{body}" for fname, body, _ in memory_files(ds))
