# 작업 0001 — v0 1단계: 저장소 코어 + 스키마 사전

- 지시: 지휘부 · 2026-10-02
- 근거: PLAN 4장(데이터 모델), ADR 0001·0003·0004·0005·0007

## 만들 것

1. **프로젝트 골격** — `pyproject.toml`(Python 3.11+), `src/mnemento/`, `tests/`, MIT `LICENSE`, git 초기화(첫 커밋은 지휘부 확인 후)
2. **저장소 계층** (`storage/`)
   - 인터페이스(추상 클래스)와 SQLite 구현. WAL 모드.
   - 테이블: `schemas`(이름·버전·JSON Schema·필드 설명·허용값·생성 시각), `events`(append-only), `entities`(현재 상태 JSON + `schema_version`)
   - 스키마 사전에서 "인덱스 대상"으로 표시한 필드는 생성 컬럼 + 인덱스로 꺼낸다
3. **스키마 사전** (`schema/`)
   - 스키마 등록·조회·버전 올리기. JSON Schema로 문서 검증.
   - 필드 정의에 `description`, `enum`(허용값), `format: date | date-time`, `indexed: bool`
4. **기록 API** (Python 함수 수준, MCP는 다음 작업)
   - `record_event(entity_id, kind, payload, at, by, evidence)` → events 추가 + entities 재계산
   - 지원할 kind: `created`, `updated`, `status_changed`, `corrected`, `retracted`
   - 현재 상태는 events에서 언제든 재생성 가능해야 함 (`rebuild_entity` 테스트)
5. **도메인 스키마 3종** (`schemas/*.json`, 가상 예시 데이터 포함)
   - `company`(정규화 이름, 별칭, 사업자번호 선택)
   - `posting`(플랫폼, 공고번호, 제목, 근무지, 마감일)
   - `application`(company_id, posting_id, platform, status: applied|viewed|passed|rejected|withdrawn, applied_at, viewed_at, expected_rate, reason)

## 하지 않을 것 (다음 작업)
- MCP 서버, 자연어 질의, 동일 엔티티 판정(ADR-0006), 실데이터 이관

## 완료 기준
- pytest 전부 통과. 최소 포함:
  - 스키마 검증 실패 시 저장 거부
  - status_changed 후 현재 상태 반영 + 이력 보존
  - corrected / retracted 반영 후 집계 결과가 바뀌는지
  - events만으로 entities 재생성 시 동일
  - 시각 필드에 오프셋 없는 값이 들어오면 거부(ADR-0004)
- 지휘부에 보고: 파일 구조, 테스트 수/통과 수, 설계와 달라진 점
