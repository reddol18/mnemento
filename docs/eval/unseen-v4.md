# 평가셋 v4 — 미노출 문항 (지휘부 작성)

- 작성: 지휘부 · 2026-10-12. 커밋 1c4a737·8f4345d의 코드와 M의 동작은 보지 않았고, 요청 메시지에 적힌 데이터 사실과 v3 unseen 형식만 보고 작성함
- 대상 기능: 경과 시간 조건(elapsed) · 모호한 말의 기본값 · 측정값별 조건
- 규칙은 v3와 같다. 문항 문구는 **그대로** 쓰고, 해석이 갈리는 지점은 `alternatives`로 둘 다 인정한다. 측정 전에 어떤 비교군도 돌리지 않는다. 표현할 수 없는 문항은 억지로 맞추지 말고 보고한다
- 공통: `live` = 철회되지 않은 기록, `today` = 2027-02-01(월). 날짜는 Asia/Seoul 기준
- `applied_at` / `viewed_at_ts` = 상태 변경 이벤트의 정확한 시각(지원 생성 시각, viewed로 바뀐 시각). `열람 소요` = viewed_at_ts − applied_at
- 사전 기본값: "빠르게 열람" = 지원 후 1일 이내 열람. **1일 = 24시간을 주 정의로, 날짜 차이(viewed_at − applied_date) ≤ 1일을 alternative로 둔다** (사전이 둘 중 무엇인지 정해 두었다면 그쪽만 주 정의로 하고 나머지는 지워도 된다)

| id | 문항 (그대로) | format 힌트 | grader | 정답 정의 |
|---|---|---|---|---|
| X1 | 빠르게 열람된 지원은 몇 건이야? | number = count; ids = the applications counted | number | live, viewed_at 있음, 열람 소요 ≤ 24시간. alternatives: 날짜 차이 ≤ 1일 |
| X2 | 전체 서류 합격률이랑, 빠르게 열람된 것만 따로 본 합격률을 같이 알려줘 | groups = two groups with category=subset ("all", "fast_viewed"), n = applications, value = share passed (0-1); flags: small_sample if any group has n<5 | rate_groups | all: n = live 지원 수, value = status=passed 비율. fast_viewed: X1 집합 안에서 n·status=passed 비율. **조건은 두 번째 측정값에만 붙는다** — 전체 합격률을 빠른 열람 집합으로 계산하면 오답 |
| X3 | 지원한 지 2주 넘었는데 아직 열람도 안 된 거 몇 개야? | number = count; ids = the applications counted | number | live, status = applied, viewed_at 없음, today − applied_date > 14일 (applied_date ≤ 2027-01-17). alternatives: ≥ 14일 (applied_date ≤ 2027-01-18) |
| X4 | 플랫폼별로 열람까지 평균 며칠 걸렸어? | groups = one per platform with category=platform, n = viewed applications, value = mean days (float) | value_groups (허용오차 ±0.1일) | 플랫폼별 live·viewed_at 있음 집합에서 mean(열람 소요 시간 ÷ 24). alternatives: mean(viewed_at − applied_date, 일 단위 정수). 열람 0건인 플랫폼은 그룹 생략 또는 n=0·value=null 둘 다 인정 |
| X5 | 1월에 넣은 지원 수랑, 그중 일주일 안에 열람된 수를 플랫폼별로 보여줘 | groups = one per platform with category=platform, n = applications applied in Jan 2027, value = count viewed within 7 days | value_groups (정확 일치) | n: live, applied_date ∈ 2027-01. value: 그 n 집합 중 열람 소요 ≤ 168시간. alternatives: 날짜 차이 ≤ 7일. **"일주일 안" 조건은 value에만 붙는다** — n까지 열람 건으로 줄이면 오답 |
| X6 | 마감 사흘 남기고 몰아서 낸 지원 중에 서류 합격한 거 있어? | number = how many; ids = those applications | ids | live, status = passed, 0 ≤ (posting.deadline − applied_date) ≤ 3일. alternatives: 0 ≤ 차이 < 3일. deadline 없는 공고는 제외. 0건이면 number=0·ids=[]이 정답 |
| X7 | 오래 묵혀둔 지원 목록 줘 | number = how many; ids = those applications | ids_with_stated_reading | 사전에 없는 모호한 말. 정답 집합 = live, status ∈ {applied, viewed}(결과 없음), today − applied_date > T, T ∈ {14, 21, 30}일 중 **답이 경고로 밝힌 T**. 경고에 읽기(기준 일수)를 밝히지 않으면 집합이 맞아도 오답. status 조건 없이 경과일만으로 고른 답도 경고에 그렇게 밝혔으면 alternative로 인정 |
| X8 | 서류 결과 나오기까지 보통 며칠 걸려? | number = mean days (float) | number (허용오차 ±0.1일) | 결과 = status가 passed 또는 rejected로 바뀐 이벤트. live 중 결과 이벤트가 있는 지원에서 mean(결과 시각 − applied_at, 일). alternatives: 중앙값(“보통”), 날짜 차이 평균. **결과 이벤트에 정확한 시각이 생성기에 없으면 이 문항은 '데이터 없음'으로 분류** — 그 경우 "결과 시각 기록이 없다"는 정직한 답은 오답 처리하되 따로 집계 |

## 채점 메모
- 새 grader 두 개를 가정했다: `value_groups`(rate_groups와 같은 구조에 value가 비율이 아닌 수) · `ids_with_stated_reading`(경고에 밝힌 읽기로 정답 집합을 고른 뒤 ids 채점). 기존 grader로 대체 가능하면 바꿔도 된다
- X2·X5가 측정값별 조건(#11) 검증용, X1·X2가 사전 기본값, X7이 모델이 정하는 모호한 말(#10), X3·X4·X6·X8이 경과 시간 조건이다
- X6은 posting.deadline과의 차이(지원 ↔ 다른 엔티티 날짜)라 표현 불가일 수 있다. X8은 데이터에 결과 시각이 없을 수 있다. 둘 다 표현 불가면 정답 계산만 넣고 보고한다
- X6·X3은 0건일 수 있다. 그 경우 "없다"와 number=0이 정답이고 clarify로 끝나면 오답
- ids는 정답 목록이 50건 이하일 때만 채점한다(기존 규칙)

## 측정 전 정리 (구현 세션, 2026-10-12)
- X6은 생성기에 공고·마감일이 없어 측정 전에 뺐다.
- 채점기: value_groups, ids_with_stated_reading(가정대로), X2는 rate_pair(두 비율이 답의 측정값 비율 중에 모두 있으면 정답). X4는 시간 단위 답(÷24)도 인정.
- 세트 이름은 unseen4(dev 회귀와 섞이지 않게). 결과: bench/results/unseen4-haiku45/report.md
