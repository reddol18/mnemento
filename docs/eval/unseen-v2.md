# 평가셋 v2 — 미노출 문항 (지휘부 작성)

- 작성: 지휘부 · 2026-10-03 · **v1 구현 코드와 M의 동작을 보지 않고** 스키마(`schemas/application.json`)와 생성기(`bench/generate.py`)의 정답 데이터 구조만 보고 작성함
- 문항 문구는 **그대로** 쓴다(맞춤법·띄어쓰기 포함). 고치지 말 것
- 구현자는 `bench/questions.py`의 `build_unseen_v2`에 정답 계산만 넣는다. 아래 정의를 따르고, 해석이 둘로 갈리는 지점은 적힌 대로 `alternatives`로 둘 다 인정한다
- **측정 전에 이 문항으로 M(또는 어떤 비교군)을 돌려 보지 않는다.** 정답 계산은 생성기의 정답 데이터로만 검증하고, 참조 QuerySpec 교차 검증은 손으로 쓴 QuerySpec으로만 한다
- 공통: `live` = 철회되지 않은 기록, `today` = `ds.now.date()`(v2는 2026-12-04, 금요일), `prev` = 지난달 1일

| id | 문항 (그대로) | format 힌트 | grader | 정답 정의 |
|---|---|---|---|---|
| V1 | 지난달에 그룹바이로 낸 지원 중에 회사가 한 번이라도 열어본 건 몇 개야? | number = count; ids = the applications counted | number | live, platform=groupby, applied 월 = prev 월, `viewed_at is not None` (이후 합격·불합격 포함) |
| V2 | {회사명}에 지원한 거 전부 몇 건이야? | number = how many applications; ids = those applications | ids | 회사 선택: 별칭이 있고, 그 회사 지원 중 `company_label`(별칭으로 적힌 기록)이 1건 이상 **그리고** 별칭 없이 적힌 기록도 1건 이상인 회사 중 지원 수 최다(동률이면 id 오름차순). {회사명} = 그 회사의 `normalized`(예: 가나소프트, "(주)"·"주식회사" 없음). 정답 = 그 회사의 live 지원 전부(별칭으로 적힌 기록 포함). 조건을 만족하는 회사가 없으면 이 문항은 그 규모에서 제외하고 report에 표기 |
| V3 | 두 번 이상 지원했는데 단 한 번도 열람된 적 없는 회사는 어디야? | number = how many companies; ids = those companies' ids (co_...) | ids | live 지원이 2건 이상이고, 그 지원 전부 `viewed_at is None`인 회사(철회·서치펌 패스 포함, 열람만 안 되면 됨) |
| V4 | 최근에 열람된 순서대로 다섯 건만 보여줘 | number = 5 (or fewer); ids = those applications | ids | live 중 `viewed_at` 있는 것을 `viewed_at` 내림차순 상위 5건(현재 상태 무관 — 열람 후 불합격도 포함). 5번째 경계에서 시각이 같으면 ids 채점 대신 number만 채점 |
| V5 | 플랫폼별로 불합격 통보까지 받은 비율 좀 비교해줘 | groups = one per platform with category=platform, n = applications, value = share rejected (0-1); flags: small_sample if any group has n<5 | rate_groups | saramin·wanted·groupby·jobkorea 각각 n = live 지원 수, value = status=rejected 비율 |
| V6 | 11월 마지막 주에 지원한 것 중에 아직 진행 중인 건 몇 개야? | number = count; ids = the applications counted | number | live, status ∈ {applied, viewed}, applied_date ∈ [2026-11-23, 2026-11-29](월~일). alternatives: [2026-11-24, 2026-11-30](말일 기준 7일), [2026-11-30, 2026-11-30]은 인정하지 않음 |
| V7 | 내가 직접 지원 취소한 건 몇 건이고 다 무슨 이유였어? | number = how many; ids = those applications; text = reasons | ids | live, status=withdrawn |
| V8 | 잡코리아로 서류 합격한 적 있어? | number = how many; ids = those applications | ids | live, platform=jobkorea, status=passed. 0건이면 number=0·ids=[]이 정답("없다"고 답해야 함) |
| V9 | 지난주 월요일부터 어제까지 열람된 지원 몇 건이야? | number = count; ids = the applications counted | number | live, `viewed_at` 날짜(Asia/Seoul) ∈ [이번 주 월요일 − 7일, today − 1일]. v2 기준일이면 [2026-11-23, 2026-12-03] |

## 채점 메모
- 날짜 계산은 정답 코드에서 `today`로부터 계산한다(V6의 11월 날짜만 고정값). 기준일이 바뀌면 V6은 다시 정의해야 하므로, 기준일이 2026-12-04가 아니면 V6을 제외한다
- V2·V3·V8은 "해당 없음"이 가능한 문항이다. 그 경우 "없다"는 답과 number=0이 정답이며, clarify로 끝나면 오답이다
- V2는 v2 생성기의 별칭 묶기(같은 회사를 다른 이름으로 적은 기록)를 확인하는 문항이다. 등록명 기록만 세면 오답
