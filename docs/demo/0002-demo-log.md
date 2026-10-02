# Demo log — task 0002 (question pipeline)

- Model: `haiku` via `claude-cli` adapter · dataset: mnemento.demo (fictional) · "now" fixed at `2026-10-03T12:00:00+09:00`
- Generated: 2026-10-03T00:53:22.603800+09:00 by `examples/demo_queries.py`
- Time columns: **CLI overhead** = process start + CLI bookkeeping, **model** = API time reported by the CLI, **code** = everything else (DB queries, compile, answer).

| # | path | LLM calls | input tok | output tok | cost (USD) | total ms | CLI overhead ms | model ms | code ms |
|---|---|---|---|---|---|---|---|---|---|
| Q1 | fast | 0 | 0 | 0 | 0 | 14 | 0 | 0 | 14.1 |
| Q2 | llm | 1 | 3834 | 685 | 0.007259 | 8135 | 1950 | 6153 | 31.6 |
| Q3 | llm | 1 | 3833 | 1168 | 0.009673 | 12831 | 1896 | 10916 | 18.7 |
| Q4 | llm | 2 | 5567 | 3654 | 0.023837 | 36152 | 3951 | 32179 | 22.0 |

## Q1. 10/2 사람인 지원 몇 곳?

**Status:** answered

**QuerySpec**

```json
{
  "entity_type": "application",
  "mode": "count",
  "filters": [
    {
      "field": "platform",
      "op": "eq",
      "value": "saramin"
    },
    {
      "field": "applied_at",
      "op": "eq",
      "value": "2026-10-02"
    }
  ],
  "interpretation": "application count where platform=saramin, applied_at=2026-10-02"
}
```

**SQL** (parameters bound separately)

```sql
SELECT COUNT(*) AS _n, json_group_array(id) AS _ids FROM entities WHERE type = ? AND retracted = 0 AND f_platform = ? AND f_applied_at = ?
```

params: `["application", "saramin", "2026-10-02"]`

**Answer**

```
Interpretation: application count where platform=saramin, applied_at=2026-10-02
Answer: 13
Evidence: app_o02, app_o03, app_o04, app_o05, app_o06, app_o07, app_o08, app_o09, app_o10, app_o11, app_o12, app_o13, app_o14
```

**Evidence (13):** app_o02, app_o03, app_o04, app_o05, app_o06, app_o07, app_o08, app_o09, app_o10, app_o11, app_o12, app_o13, app_o14

**Stages (ms):** interpret 1.2, resolve 3.3, compile 0.0, execute 0.2, answer 0.0


## Q2. Gasang Tech 예전에 지원한 적 있나?

**Status:** answered

**QuerySpec**

```json
{
  "entity_type": "application",
  "mode": "count",
  "filters": [
    {
      "field": "company_id",
      "op": "in",
      "value": [
        "co_gasangtech"
      ]
    }
  ],
  "interpretation": "How many applications to Gasang Tech has the user submitted?"
}
```

**SQL** (parameters bound separately)

```sql
SELECT COUNT(*) AS _n, json_group_array(id) AS _ids FROM entities WHERE type = ? AND retracted = 0 AND f_company_id IN (?)
```

params: `["application", "co_gasangtech"]`

**Answer**

```
Interpretation: How many applications to Gasang Tech has the user submitted?
Answer: 1
Evidence: app_o02
Note: 'Gasang Tech' matched ['co_gasangtech'] by alias.
```

**Evidence (1):** app_o02

**Stages (ms):** interpret 8114.4, resolve 11.7, compile 0.0, execute 0.1, answer 0.1


## Q3. 열람됐는데 결과 없는 곳은?

**Status:** answered

**QuerySpec**

```json
{
  "entity_type": "application",
  "mode": "list",
  "filters": [
    {
      "field": "status",
      "op": "eq",
      "value": "viewed"
    }
  ],
  "list_fields": [
    "company_id",
    "posting_id",
    "viewed_at",
    "status"
  ],
  "interpretation": "회사가 열람했지만 아직 최종 결과(합격/불합격)가 나지 않은 지원서들"
}
```

**SQL** (parameters bound separately)

```sql
SELECT id, doc, COUNT(*) OVER () AS _total FROM entities WHERE type = ? AND retracted = 0 AND f_status = ? ORDER BY id LIMIT ?
```

params: `["application", "viewed", 100]`

**Answer**

```
Interpretation: 회사가 열람했지만 아직 최종 결과(합격/불합격)가 나지 않은 지원서들
Answer: 7 record(s)
- app_o01 (company_id=co_samplelabs, viewed_at=2026-10-02, status=viewed)
- app_o02 (company_id=co_gasangtech, viewed_at=2026-10-02, status=viewed)
- app_o03 (company_id=co_gasangmedia, viewed_at=2026-10-03, status=viewed)
- app_s02 (company_id=co_v02, viewed_at=2026-09-12, status=viewed)
- app_s07 (company_id=co_v07, viewed_at=2026-09-11, status=viewed)
- app_s09 (company_id=co_v09, viewed_at=2026-09-16, status=viewed)
- app_s10 (company_id=co_v10, viewed_at=2026-09-19, status=viewed)
Note: 3 of these records have applied_at within the last 3 days (since 2026-10-01); their outcome may not be known yet.
```

**Evidence (7):** app_o01, app_o02, app_o03, app_s02, app_s07, app_s09, app_s10

**Stages (ms):** interpret 12819.3, resolve 3.4, compile 0.0, execute 0.3, answer 0.1


## Q4. 예상 합격률 상위 10%가 30%보다 먼저 열람되나? 지난번에도 그랬나?

**Status:** answered

**QuerySpec**

```json
{
  "entity_type": "application",
  "mode": "aggregate",
  "filters": [
    {
      "field": "expected_rate",
      "op": "in",
      "value": [
        "top10",
        "top30"
      ]
    }
  ],
  "group_by": [
    {
      "field": "applied_at",
      "bucket": "month"
    },
    {
      "field": "expected_rate"
    }
  ],
  "measures": [
    {
      "name": "avg_days_to_view",
      "agg": "avg_days_between",
      "field": "applied_at",
      "field_end": "viewed_at"
    },
    {
      "name": "viewed_count",
      "agg": "count_if",
      "where": [
        {
          "field": "viewed_at",
          "op": "exists"
        }
      ]
    }
  ],
  "interpretation": "상위 10% 기대 합격률과 상위 30% 기대 합격률 지원이 월별로 얼마나 빨리 열람되는지 비교 (평균 열람까지의 일수로 측정, 지난 기간들과의 패턴 일관성 확인)"
}
```

**SQL** (parameters bound separately)

```sql
SELECT substr(f_applied_at, 1, 7) AS g0, json_extract(doc, '$.expected_rate') AS g1, AVG(julianday(f_viewed_at) - julianday(f_applied_at)) AS m0, SUM(CASE WHEN f_viewed_at IS NOT NULL THEN 1 ELSE 0 END) AS m1, COUNT(*) AS _n, json_group_array(id) AS _ids FROM entities WHERE type = ? AND retracted = 0 AND json_extract(doc, '$.expected_rate') IN (?, ?) GROUP BY g0, g1 ORDER BY g0, g1
```

params: `["application", "top10", "top30"]`

**Answer**

```
지난 9월에는 상위 30%가 평균 1.25일로 상위 10%의 1.33일보다 오히려 약간 먼저 열람되었으며, 현재 10월은 상위 10% 0.67일에 비해 상위 30%의 열람 데이터가 없어 비교 불가합니다. 단, 10월 상위 10%는 작은 샘플(n=4)이고, 10월은 미완성 기간이며, 최근 3일 내 신청된 13개 기록의 결과가 아직 불명확하여 결론의 신뢰도가 낮습니다.

Interpretation: 상위 10% 기대 합격률과 상위 30% 기대 합격률 지원이 월별로 얼마나 빨리 열람되는지 비교 (평균 열람까지의 일수로 측정, 지난 기간들과의 패턴 일관성 확인)
Answer: 4 group(s), 25 record(s)
- applied_at_month=2026-09 / expected_rate=top10: n=6, avg_days_to_view=1.33, viewed_count=3/6 (50%)  [evidence: app_s01, app_s02, app_s03, app_s04, app_s05, app_s06]
- applied_at_month=2026-09 / expected_rate=top30: n=6, avg_days_to_view=1.25, viewed_count=4/6 (67%)  [evidence: app_s07, app_s08, app_s09, app_s10, app_s11, app_s12]
- applied_at_month=2026-10 / expected_rate=top10: n=4, avg_days_to_view=0.67, viewed_count=3/4 (75%)  [evidence: app_o01, app_o02, app_o03, app_o04]
- applied_at_month=2026-10 / expected_rate=top30: n=9, avg_days_to_view=None, viewed_count=0/9 (0%)  [evidence: app_o05, app_o06, app_o07, app_o08, app_o09, app_o10, app_o11, app_o12, app_o13]
Note: Small sample (n<5) in: applied_at_month=2026-10 / expected_rate=top10 (n=4). Treat differences as anecdotal.
Note: applied_at_month=2026-10 is still in progress (incomplete period).
Note: 13 of these records have applied_at within the last 3 days (since 2026-10-01); their outcome may not be known yet.
```

**Evidence (25):** app_s01, app_s02, app_s03, app_s04, app_s05, app_s06, app_s07, app_s08, app_s09, app_s10, app_s11, app_s12, app_o01, app_o02, app_o03, app_o04, app_o05, app_o06, app_o07, app_o08, app_o09, app_o10, app_o11, app_o12, app_o13

**Stages (ms):** interpret 22988.2, resolve 3.7, compile 0.1, execute 0.6, answer 0.2, narrate 13150.8

