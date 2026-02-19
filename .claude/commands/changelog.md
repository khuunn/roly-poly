---
name: changelog
description: git log 기반 CHANGELOG.md 업데이트
user_invocable: true
---

CHANGELOG.md의 `[Unreleased]` 섹션을 git log 기반으로 업데이트하세요.

## 절차

1. 현재 CHANGELOG.md를 읽어서 이미 기록된 내용을 파악
2. `git log --oneline`으로 최근 커밋 확인
3. CHANGELOG에 없는 새 커밋을 식별
4. Keep a Changelog 포맷(Added/Changed/Fixed/Removed)으로 분류
5. **사용자 관점**으로 작성 — 코드 레벨 변경이 아닌 기능/동작 변화 기술

## 규칙

- 커밋 메시지를 그대로 복사하지 않는다
- 내부 리팩토링은 사용자에게 영향이 있을 때만 기록한다
- 관련 이슈/PR이 있으면 링크를 포함한다
- 변경 내용을 사용자에게 보여주고 승인 후 CHANGELOG.md를 수정한다
