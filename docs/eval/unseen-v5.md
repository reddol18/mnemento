# 평가셋 v5 — 미노출 문항 (지휘부 작성, #16 시간/일 단위)

- 작성: 지휘부 · 2026-10-12. 커밋 320214d의 코드와 M의 동작은 보지 않았고, 요청 메시지에 적힌 데이터 사실과 v4 형식만 보고 작성함
- 규칙은 v4와 같다. 문항 문구는 **그대로** 쓰고, 해석이 갈리는 지점은 `alternatives`로 인정한다. 측정 전에 어떤 비교군도 돌리지 않는다. 표현할 수 없는 문항은 보고한다
- 공통: `live` = 철회(retracted)되지 않은 기록. status=withdrawn(사용자 취소)은 live에 포함된다. `today` = 2027-02-01(월), Asia/Seoul
- 시각 표기: `t_applied` = 지원 생성 이벤트 시각, `t_viewed` = viewed로 바뀐 이벤트 시각, `t_result` = passed 또는 rejected로 바뀐 **첫** 이벤트 시각, `t_end` = passed·rejected·withdrawn 중 첫 이벤트 시각
- 날짜 대안: `applied_date`(applied_at), `viewed_at`(날짜 필드). 결과·종료는 날짜 필드가 없으므로 날짜 대안은 이벤트 시각의 날짜(Asia/Seoul)를 쓴다
- **단위 채점 원칙:** 질문이 정한 단위가 아닌 값(예: "몇 시간"에 일수로 답함)은 숫자가 환산상 맞아도 오답이다. 이번 문항들의 핵심 검증 대상이다

| id | 문항 (그대로) | format 힌트 | grader | 정답 정의 |
|---|---|---|---|---|
| Y1 | 지원하고 열람되기까지 평균 몇 시간 걸렸어? | number = mean hours (float) | number (허용오차 ±1시간) | live, viewed 이벤트 있음. mean(t_viewed − t_applied) 시간. alternatives: mean(viewed_at − applied_date) 일 × 24 (허용오차 ±1시간). 일 단위 답은 오답 |
| Y2 | 플랫폼별로 지원부터 결과 나오기까지 평균 몇 주 걸렸어? | groups = one per platform with category=platform, n = applications with a result, value = mean weeks (float) | value_groups (허용오차 ±0.05주) | 결과 = passed 또는 rejected. 플랫폼별 live·결과 이벤트 있음 집합에서 mean(t_result − t_applied) ÷ 168시간. alternatives: mean(결과 이벤트 날짜 − applied_date) 일 ÷ 7. 결과 0건인 플랫폼은 그룹 생략 또는 n=0·value=null 둘 다 인정. 일·시간 단위 답은 오답 |
| Y3 | 지원하고 48시간 안에 합격이든 불합격이든 결과가 나온 건 몇 건이야? | number = count; ids = the applications counted | number | live, 결과 이벤트 있음, (t_result − t_applied) ≤ 48시간. alternatives: (결과 이벤트 날짜 − applied_date) ≤ 2일. 현재 status가 아니라 결과 이벤트 도달 여부로 센다(이후 다른 상태로 바뀐 경우가 있어도 포함). 0건이면 number=0·ids=[]이 정답 |
| Y4 | 열람될 때까지 기다린 시간을 전부 합치면 며칠이야? | number = total days (float) | number (허용오차 ±0.5일) | live, viewed 이벤트 있음. sum(t_viewed − t_applied) ÷ 24시간. alternatives: sum(viewed_at − applied_date) 일. 평균을 낸 답, 시간 단위 답은 오답 |
| Y5 | 열람되고 나서 결과가 나오기까지는 평균 며칠 걸렸어? | number = mean days (float) | number (허용오차 ±0.1일) | 시작점이 지원이 아니라 열람이다. live, viewed 이벤트와 결과 이벤트가 둘 다 있고 t_result ≥ t_viewed. mean(t_result − t_viewed) ÷ 24. alternatives: mean(결과 이벤트 날짜 − viewed_at) 일 (허용오차 ±0.1일). 지원 시점부터 잰 답은 오답 |
| Y6 | 지원한 지 3주가 넘었는데 아직 끝나지 않은 지원은 몇 건이야? | number = count; ids = the applications counted | number | 끝남 = passed·rejected·withdrawn. live, 현재 status ∈ {applied, viewed}, today − applied_date > 21일 (applied_date ≤ 2027-01-10). alternatives: ≥ 21일 (applied_date ≤ 2027-01-11), 또는 (today 00:00 KST − t_applied) > 504시간. withdrawn을 "끝나지 않음"에 넣은 답은 오답 |

## 채점 메모
- 검증 대상 정리: 단위 명시 평균(Y1 시간·Y2 주·Y5 일), 합계(Y4), 조건(Y3 시간·Y6 주). 시작·끝이 이벤트 시각인 것(Y1·Y3·Y5 주 정의)과 날짜 필드인 것(Y6 주 정의, 각 alternatives)을 섞었다
- 여러 상태를 한 번에 가리키는 말: "결과"(Y2·Y3·Y5 = passed|rejected), "끝나지 않은"(Y6 = applied|viewed, 즉 passed|rejected|withdrawn의 여집합)
- Y5에서 t_result < t_viewed인 기록(열람 이벤트 없이 결과가 먼저 난 경우 등)이 생성기에 있으면 제외한다. 이런 기록이 있는지 알려 주면 정의를 확정하겠다
- Y3·Y6은 0건일 수 있다. 그 경우 "없다"와 number=0이 정답이고 clarify로 끝나면 오답
- ids는 정답 목록이 50건 이하일 때만 채점한다(기존 규칙)

## 측정 전 정리 (구현 세션, 2026-10-12)
- Y5: 생성기에 결과가 열람보다 먼저인 기록은 없다(결과 있는 11건 모두 열람 뒤). 제외 규칙은 0건에 적용.
- Y3 정답은 0건(대안도 0건).
- 현재 기능으로 표현할 수 없는 것: Y4(경과 시간의 합계 측정값 없음), Y2의 주 단위(unit은 hours/days만). 측정 전에 기능을 더하지 않는다.
- 세트 이름 unseen5, 채점은 작성자 원칙대로 단위 환산을 인정하지 않는다.
