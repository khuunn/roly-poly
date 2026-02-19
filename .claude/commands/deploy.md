---
name: deploy
description: 배포 전 린트/테스트 체크리스트 실행
user_invocable: true
---

배포 전 다음 체크리스트를 순서대로 실행하세요.

## 체크리스트

### 1. 린트
```bash
uv run ruff check src/ tests/
```

### 2. 테스트
```bash
uv run pytest
```

### 3. Docker 빌드 확인
```bash
docker compose build
```

## 출력 형식

각 단계별로 통과/실패를 보고하세요:

| # | 항목 | 결과 | 비고 |
|---|------|------|------|
| 1 | 린트 | PASS/FAIL | 위반 사항 |
| 2 | 테스트 | PASS/FAIL | 실패 건수 |
| 3 | Docker 빌드 | PASS/FAIL | 에러 내용 |

모든 항목이 PASS이면 "배포 준비 완료"를 알려주세요.
실패 항목이 있으면 원인과 수정 방안을 제시하세요.
