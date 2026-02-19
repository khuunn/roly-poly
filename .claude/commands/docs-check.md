---
name: docs-check
description: 문서 4종의 코드 대비 정합성 검증
user_invocable: true
---

README.md, CHANGELOG.md, ARCHITECTURE.md, CLAUDE.md를 실제 코드와 교차 검증하세요.

## 검증 항목

### 1. README.md

**환경변수 정합성**
- `src/config.py`의 모든 환경변수가 README 환경변수 표에 있는지
- 기본값이 코드와 일치하는지
- `.env.example`과 README 표가 일치하는지

**텔레그램 명령어 정합성**
- `src/notifier.py`의 `CommandHandler` 등록과 README 명령어 표가 일치하는지

**기술 스택 정합성**
- `pyproject.toml`의 dependencies와 README 기술 스택 표가 일치하는지

**실행 커맨드 검증**
- 시작하기 섹션의 커맨드가 실제로 동작 가능한지

**테스트 수**
- `uv run pytest --collect-only -q`의 실제 테스트 수와 README 기재 수 비교

### 2. CHANGELOG.md

**커밋 커버리지**
- `git log --oneline`에서 사용자 영향이 있는 커밋 중 CHANGELOG에 누락된 것이 있는지
- 내부 리팩토링/린트 커밋은 무시
- `feat`, `fix` 접두사 커밋에 집중

### 3. ARCHITECTURE.md

**모듈 존재 검증**
- 기재된 모든 모듈/클래스가 실제 코드에 존재하는지
- `src/` 디렉토리 구조와 문서의 디렉토리 트리가 일치하는지

**ABC 구현체 검증**
- Strategy/Engine/Repository ABC의 구현체 목록이 실제와 일치하는지
- 미구현 표기가 정확한지 (NotImplementedError 여부 확인)

**외부 서비스 URL 검증**
- 다이어그램의 외부 서비스가 코드의 실제 URL/엔드포인트와 일치하는지

**설계 결정 현행화**
- "현재 상태" 관련 기술이 실제와 일치하는지

### 4. CLAUDE.md

**커맨드 검증**
- 기재된 빌드/테스트/린트 커맨드가 실제로 동작하는지

**컨벤션 검증**
- frozen Config 주장이 코드와 일치하는지
- ABC 패턴 주장이 코드와 일치하는지

**디렉토리 구조 검증**
- 문서의 디렉토리 트리와 실제 파일 시스템 비교

**문서 간 중복/충돌**
- CLAUDE.md와 글로벌 CLAUDE.md 규칙 간 중복이 없는지
- CLAUDE.md와 ARCHITECTURE.md 간 불일치가 없는지

## 출력 형식

| 문서 | 항목 | 결과 | 비고 |
|------|------|------|------|
| README.md | 환경변수 | PASS/FAIL | 상세 내용 |
| README.md | 명령어 | PASS/FAIL | 상세 내용 |
| ... | ... | ... | ... |

FAIL 항목이 있으면:
1. 구체적인 불일치 내용을 나열하세요
2. 수정 방안을 제시하세요
3. 사용자 동의 후 직접 수정하세요
