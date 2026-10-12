# 평가셋 v6 — 미노출 문항 (지휘부 작성, ADR-0029 조립식 측정값 검증)

- 작성: 지휘부 · 2026-10-12. 커밋 0aa11aa의 코드와 M의 동작은 보지 않았고, 요청 메시지에 적힌 동작 설명과 v5 형식만 보고 작성함
- 규칙은 v5와 같다. 문항 문구는 **그대로** 쓰고, 해석이 갈리는 지점은 `alternatives`로 인정한다. 측정 전에 어떤 비교군도 돌리지 않는다
- 공통: `live` = 철회(retracted)되지 않은 기록. status=withdrawn은 live에 포함. `today` = 2027-02-01(월), Asia/Seoul
- 시각: `t_applied` = 지원 생성 이벤트, `t_viewed` = viewed로 바뀐 첫 이벤트, `t_result` = passed 또는 rejected로 바뀐 첫 이벤트
- 단위 원칙(v5와 같음): 질문이 정한 단위가 아니면 환산상 맞아도 오답
- 이벤트 시각이 없어 빠진 기록에 대한 경고는 채점하지 않는다(있으면 좋음)

| id | 문항 (그대로) | format 힌트 | grader | 정답 정의 |
|---|---|---|---|---|
| Z1 | 합격이든 불합격이든 서류 결과가 나오기까지 걸린 기간의 중앙값은 며칠이야? | number = median days (float) | number (허용오차 ±0.1일) | live, 결과 이벤트 있음. median((t_result − t_applied) ÷ 24h). 평균으로 답하면 오답 |
| Z2 | 열람되기까지 가장 오래 걸린 시간은 몇 시간이었어? | number = hours (float) | number (허용오차 ±0.5시간) | live, viewed 이벤트 있음. max(t_viewed − t_applied) 시간. 기록 목록으로 답하고 값을 안 내면 오답 |
| Z3 | 열람되기까지 가장 오래 걸린 지원은 어느 회사 거야? | number = 1; ids = that application | ids | Z2의 max를 낸 기록 1건(동률이면 동률 전부 인정). 값(시간)만 답하고 기록을 안 내면 오답 |
| Z4 | 원티드로 낸 지원은 서류 결과까지 평균 몇 주 걸렸어? | number = mean weeks (float) | number (허용오차 ±0.05주) | live, platform = wanted, 결과 이벤트 있음. mean(t_result − t_applied) ÷ 168h. 일·시간 단위 답은 오답. 0건이면 unsupported가 아니라 "해당 없음" + number=null |
| Z5 | 열람될 때까지 기다린 시간을 플랫폼별로 다 합치면 각각 며칠이야? | groups = one per platform with category=platform, n = viewed applications, value = total days (float) | value_groups (허용오차 ±0.5일) | 플랫폼별 live·viewed 이벤트 있음 집합에서 sum(t_viewed − t_applied) ÷ 24h. 평균을 낸 답은 오답. 열람 0건 플랫폼은 그룹 생략 또는 n=0·value=0 둘 다 인정 |
| Z6 | 합격이나 불합격 상태가 아닌 지원 중에 지원한 지 열흘 넘은 건 몇 건이야? | number = count; ids = the applications counted | number | live, 현재 status ∉ {passed, rejected}(= applied·viewed·withdrawn, NOT IN 목록), today − applied_date > 10일 (applied_date ≤ 2027-01-21). alternatives: ≥ 10일(≤ 2027-01-22). withdrawn을 뺀 답은 경고로 그 읽기를 밝혔을 때만 alternative로 인정 |
| Z7 | 한 번이라도 열람되거나 합격한 적 있는 사람인 지원은 몇 건이야? | number = count; ids = the applications counted | number | live, platform = saramin, 이력상 viewed 또는 passed 중 하나에 도달(reached [viewed, passed]). 현재 status가 rejected·withdrawn이어도 도달했으면 포함. 현재 status로만 센 답은 오답 |
| Z8 | 지원과 다음 지원 사이 간격은 평균 며칠이었어? | status = unsupported | unsupported | 서로 다른 기록 사이의 간격(연속 지원 간격)이라 같은 기록의 두 시점이 아니다 → 표현 불가. 숫자를 낸 답(특히 같은 기록의 간격으로 바꿔 낸 답)은 오답 |
| Z9 | 회사 한 곳당 평균 몇 번 지원했어? | status = unsupported | unsupported | 회사별 건수를 센 뒤 그 평균을 내는 집계 안의 집계 → 표현 불가(ADR-0029 ⑥ 범위 밖). 전체 지원 수 ÷ 회사 수를 직접 계산해 낸 답도 이 세트에서는 오답(표현 범위 검증이 목적) |

## 채점 메모
- 검증 대상 정리: median(Z1), max 값 vs 기록 구분(Z2·Z3 한 쌍), weeks(Z4), sum + 그룹(Z5), NOT IN + 오늘까지 경과(Z6), reached 목록(Z7), 표현 불가 2종(Z8 다른 기록 간 간격, Z9 중첩 집계)
- Z8은 생성기 데이터만으로 답이 정의되므로(정보 없음이 아님) clarify가 아니라 unsupported가 정답이다. Z9도 마찬가지
- Z9는 조건이 엄격하다. 사람이라면 "총 지원 ÷ 회사 수"로 답해도 된다고 볼 수 있다. 이 세트는 "범위 밖을 정직하게 말하는가"를 보는 용도라 그렇게 정했다. 사용자가 다르게 보면 alternatives로 숫자 답을 인정해도 된다(그 경우 정답 = live 지원 수 ÷ live 지원이 있는 회사 수)
- Z4는 원티드 결과 건수가 0이면 문항을 교체해야 한다. 생성기로 먼저 확인해 달라
- ids는 정답 목록이 50건 이하일 때만 채점한다(기존 규칙)

## 측정 전 정리 (구현 세션, 2026-10-12)
- 세트 이름 unseen6, 정답은 생성기(seed 20270201, n=100)에서 계산한다(`bench/questions.py`의 `build_unseen_v6`).
- Z4: 원티드 지원 중 결과가 난 건은 4건이라 문항을 그대로 둔다(작은 표본).
- Z3: 최댓값 동률은 없다(1건).
- Z6: 주 정의 53건, ≥10일 54건. withdrawn을 뺀 50건 / 51건은 답 본문(해석·경고)에 withdrawn·철회·취소를 밝혔을 때만 인정한다(채점기 `stated_alternatives`).
- Z8·Z9: 채점기 `unsupported`. status unsupported만 정답으로 친다.
- Z9를 숫자 답(live 지원 수 ÷ 회사 수)도 인정할지는 측정 승인 때 사용자에게 묻는다.

## 측정 결과 (커밋 3c90c8a, 2026-10-12) — 세 모델 모두 8/9, 문항 소진
- 결과: bench/results/unseen6-3models/report.md

| 모델 | 정확도 | 문항당 비용 | 평균 / p95 지연 |
|---|---|---|---|
| Haiku 4.5 (고정) | 8/9 | $0.024 | 30.0 / 70.9초 |
| Sonnet 5.5 | 8/9 | $0.017 | 5.7 / 10.4초 |
| Opus 5.5 | 8/9 | $0.030 | 6.1 / 7.7초 |

- 세 모델 모두 정답: Z1 중앙값, Z2·Z3(값과 기록을 구분), Z4 주 단위, Z6 NOT IN, Z7 reached 목록, Z8·Z9 "계산할 수 없음"(엄격 채점, 사용자 결정).
- Z5는 세 모델 모두 오답이다. 원인은 둘이다.
  - 벤치 변환기: 그룹 값으로 첫 측정값(건수)을 가져갔다. "value = total"이면 합계 측정값을 쓰도록 고쳤다(이후 측정부터 적용, 이번 기록은 그대로 둔다).
  - 모델: 세 모델 모두 이벤트 시각 대신 날짜 필드로 합계를 냈다(사람인 29일, 정답 27.17일). 변환기가 맞았어도 정답 정의(이벤트 시각)와 허용오차 ±0.5일에 맞지 않는다. 프롬프트의 "이벤트로 두 시점을 알 수 있으면 이벤트를 쓴다"가 합계에서는 지켜지지 않았다.
