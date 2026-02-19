# Changelog

[Keep a Changelog](https://keepachangelog.com) 포맷을 따릅니다.

## [Unreleased]

### Added
- 서킷 브레이커 + 킬 스위치 (최대 낙폭/일일 손실 한도 초과 시 자동 거래 중단, `/stop`, `/resume`)
- 동적 position sizing (잔액 비율 + confidence 스케일링)
- 매입가 필터 (ask > max_entry_price 시 진입 차단)
- `/topup` 커맨드로 paper balance 충전 기능
- `/status` 명령어에 거래 모드, W/L, 마지막 스냅샷 시간 표시
- 앙상블 전략 (Directional + Orderbook Imbalance 만장일치 투표)
- 아비트라지 전략 (Up+Down 양측 매수 수익 감지)
- 텔레그램 봇 시작 알림 및 일일 요약 스케줄러
- GitHub Actions CI/CD 파이프라인 + VPS 자동 배포
- Docker healthcheck (파일 기반 heartbeat)

### Changed
- 텔레그램 알림을 한국어 포맷으로 업그레이드 (거래/리졸루션 메시지)
- 알림에서 신뢰도 표시를 전략 라벨로 변경 (앙상블 투표 상세 표시)
- 봇 시작 알림에 복원된 실제 잔액 표시 (초기 자본금 대신)

### Removed
- LLM 기능 전체 제거 — AgentService, LLMEventStrategy, NewsFeed, TokenManager, 자동 헬스체크/코드리뷰
- claude-agent-sdk 의존성 제거 (Docker 이미지 경량화)
- CryptoPanic 뉴스 피드 연동 제거

### Fixed
- OrderbookImbalance bid_vol=0일 때 division by zero 수정
- 스냅샷 저장 타이밍 버그 수정
- PnL 계산 버그 수정 (아비트라지 payout 공식 오류)
- Portfolio/Engine 잔액 동기화 버그 수정
- PaperEngine 재시작 시 잔액 복원 누락 수정
- 마켓당 중복 거래 방지 로직 추가
- events API + timestamp slug 기반 마켓 탐색으로 전환 (기존 방식 실패 대응)
- Dockerfile 빌드 오류 수정 + .dockerignore 추가
- Docker botuser UID를 1000으로 고정하여 볼륨 권한 문제 해결
