# 평가셋 v3 — 미노출 문항 (지휘부 작성)

- 작성: 지휘부 · 2026-10-04. v1.1 코드와 M의 동작은 보지 않았고, 스키마와 생성기의 정답 데이터 구조만 보고 작성함
- 문항 문구는 **그대로** 쓴다. 해석이 둘로 갈리는 지점은 아래 적힌 대로 `alternatives`로 둘 다 인정한다
- 구현자는 `build_unseen_v3`에 정답 계산만 넣는다. **측정 전에 이 문항으로 어떤 비교군도 돌리지 않는다.** 정답 검증은 생성기의 정답 데이터와 손으로 쓴 참조 QuerySpec으로만 한다
- 참조 QuerySpec으로 **표현할 수 없는 문항**은 억지로 맞추지 말고 그대로 두고 보고한다. 측정에는 포함한다. 이런 질문을 실제로 받았을 때 시스템이 어떻게 하는지가 결과다(정직한 "표현 불가" 답이나 clarify는 오답 처리하되 따로 집계)
- 공통: `live` = 철회되지 않은 기록, `today` = `ds.now.date()`(v3는 2027-02-01, 월요일). 날짜는 Asia/Seoul 기준

| id | 문항 (그대로) | format 힌트 | grader | 정답 정의 |
|---|---|---|---|---|
| W1 | 작년 12월에 사람인이랑 원티드 합쳐서 몇 군데 넣었어? | number = count; ids = the applications counted | number | live, platform ∈ {saramin, wanted}, applied_date ∈ 2026-12 |
| W2 | 지난주에 열람됐는데 아직 결과 안 나온 지원 목록 줘 | number = how many; ids = those applications | ids | live, status = viewed, viewed_at 날짜 ∈ [2027-01-25, 2027-01-31] (이번 주 월요일 − 7일 ~ 어제) |
| W3 | 연말연시(12월 24일부터 1월 3일까지)에 지원한 것 중에 서류 합격한 거 있어? | number = how many; ids = those applications | ids | live, status = passed, applied_date ∈ [2026-12-24, 2027-01-03]. 0건이면 number=0·ids=[]이 정답 |
| W4 | 서류 합격률이 제일 높은 플랫폼이 어디야? | groups = one per platform with category=platform, n = applications, value = share passed (0-1); flags: small_sample if any group has n<5 | rate_groups | saramin·wanted·groupby·jobkorea 각각 n = live 지원 수, value = status=passed 비율 |
| W5 | 열람되기까지 사흘 넘게 걸린 지원은 몇 건이야? | number = count | number | live, viewed_at 있음, (viewed_at − applied) > 72시간. alternatives: 날짜 차이(viewed_at 날짜 − applied_date) > 3일, ≥ 3일 |
| W6 | 같은 회사에 원티드랑 사람인 둘 다로 지원한 적 있어? | number = how many companies; ids = those companies' ids (co_...) | ids | live 지원 중 platform=wanted 1건 이상 **그리고** platform=saramin 1건 이상인 회사 |
| W7 | 어제 지원한 거 몇 개야? | number = count; ids = the applications counted | number | live, applied_date = 2027-01-31 (today − 1일, 월을 넘어감) |
| W8 | 이번 달 들어서 열람된 거 있어? | number = how many; ids = those applications | ids | live, viewed_at 날짜 ≥ 2027-02-01 (이번 달 = 오늘 하루). 0건이면 number=0·ids=[]이 정답 |
| W9 | 내가 취소한 거 빼고 지금까지 지원 총 몇 건이야? | number = count | number | live, status ≠ withdrawn. alternatives 없음(취소 건까지 센 답은 오답) |

## 채점 메모
- W3·W8은 0건일 수 있다. 그 경우 "없다"는 답과 number=0이 정답이고, clarify로 끝나면 오답이다
- W5·W6은 QuerySpec v1.1로 표현할 수 없을 수도 있다. 표현할 수 없으면 정답 계산만 넣고 보고하면 된다. 그렇다고 표현 가능하게 만들려고 측정 전에 기능을 추가하지 않는다
- W1의 ids는 정답 목록이 50건 이하일 때만 채점한다(기존 규칙)
