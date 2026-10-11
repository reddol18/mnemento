# ADR-0027: 조건은 그 조건이 붙은 답에만 — 측정값의 경과 시간, 모호한 말의 범위

- 상태: **승인** (이슈 https://github.com/reddol18/mnemento/issues/10, https://github.com/reddol18/mnemento/issues/11, 사용자 지시로 처리 2026-10-12)
- 보완: [ADR-0018](0018-elapsed-filter-and-vague-defaults.md)

## 맥락
"열람까지 평균 몇 시간 걸렸나? 빠르게 열람된 곳의 결과는?"(v3 dev D5)은 두 부분으로 된 질문이다. 평균은 열람된 지원 전체로 내고, "빠르게"는 두 번째 부분(결과)에만 붙어야 한다. 회귀 측정에서 두 가지 실패가 반복됐다.
- haiku: "빠르게 = 1일 이내"를 질문 전체의 `elapsed`로 걸어, 평균까지 빠르게 열람된 곳만으로 냈다.
- opus: 측정값마다 붙는 기간 조건을 표현할 수 없다고 밝히고, 결과를 전체 기준으로 냈다. `where`(필드 조건)는 측정값에 붙일 수 있었지만 `elapsed`는 붙일 수 없었다.

## 결정
1. **`Measure.elapsed`**: 측정값에도 `elapsed`(ADR-0018과 같은 모양)를 붙인다. SQL은 그 측정값의 `CASE WHEN` 안에만 들어간다(`where`와 같다). `count_if`는 `where` 또는 `elapsed`가 있으면 된다. series 측정값에는 쓰지 않는다.
2. **해석기 규칙**
   - 모호한 말의 기본값은 **그 말이 나온 부분에만** 적용한다. 전체 대상은 그대로 두고, 그 부분의 측정값(`where`·`elapsed`)에 넣는다. 질문 전체의 filters나 elapsed에는 넣지 않는다.
   - 답의 한 부분에만 해당하는 시간 조건은 그 측정값의 `elapsed`에 둔다.
3. 측정값의 `elapsed`가 쓰는 날짜 필드도 "N건은 이 필드가 없음" 경고 대상이다.

## 결과
- **모델 주의**: CLI 별칭 `haiku`가 Claude Haiku 5.5로 바뀌었다. 이전 측정은 모두 Haiku 4.5다.
- Haiku 5.5로 D5를 3회 돌리면 3/3 정답이다(bench/results/issue10-d5-check).
- Haiku 4.5로 고정해 돌린 D5 3회와 회귀 1회(bench/results/issue10-d5-check-h45, low-issues-regression-h45)
  - 범위는 4/4 모두 맞았다. 평균은 전체로 내고, "빠르게"는 결과 측정값의 `elapsed`에만 붙였다. 이 ADR이 고치려던 실패는 사라졌다.
  - 그래도 답은 틀렸다. "평균 몇 시간"을 이벤트 시각의 시간 차이가 아니라 날짜 차이의 일수(약 1.52일)로 냈기 때문이다. 단위 선택은 이 ADR과 별개의 문제다.
  - 회귀 결과: Haiku 4.5 26/28(D5 단위, D7 기존), Haiku 5.5 28/28, Opus 28/28.
- 해석기 규칙이 바뀌었으므로 entity 회귀로 확인한다. 개선 효과의 공식 수치는 새 미노출 문항으로 잰다(https://github.com/reddol18/mnemento/issues/13).
