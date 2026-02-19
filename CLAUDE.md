# Polymarket Trading

Polymarket 5분 BTC Up/Down 마켓 paper trading bot — 앙상블 전략 + 텔레그램 제어

## 기술 스택

- Python 3.11+ / asyncio / httpx / websockets / aiosqlite
- python-telegram-bot / matplotlib
- Docker / GitHub Actions / Hetzner VPS

## 커맨드

```bash
uv run python -m src.main     # 봇 실행
uv run pytest                  # 테스트 (178건)
uv run ruff check src/         # 린트
docker compose up -d --build   # Docker 실행
```

## 코드 컨벤션

- 주석, 로그 메시지, 텔레그램 알림은 한국어로 작성한다
- `Config`는 `frozen=True` dataclass — 런타임에 변경하지 않는다
- 새로운 Engine/Repository/Strategy는 반드시 ABC를 상속한다
- 모든 I/O 작업은 async로 구현한다 (sync 호출 금지)

## 디렉토리 구조

```
src/
├── main.py          # TradingBot 오케스트레이터
├── config.py        # frozen Config (환경변수 바인딩)
├── models.py        # 도메인 모델
├── market_scanner.py, price_feed.py, orderbook.py  # 외부 데이터
├── portfolio.py     # 잔액/PnL 관리
├── notifier.py      # 텔레그램 알림
├── commands.py      # 텔레그램 명령어 + InlineKeyboard UI
├── engine/          # ExecutionEngine ABC → Paper/Live
├── repository/      # Repository ABC → SQLite
└── strategy/        # Strategy ABC → Directional/Orderbook/Ensemble/Arbitrage
```

## 주의사항

- Gamma API의 slug 기반 마켓 검색은 비공식 — API 변경 시 scanner 수정 필요
- WebSocket 가격 피드는 끊길 수 있음 — 지수 백오프 재연결이 있지만 재연결 중 가격 데이터 갭 발생 가능
- Engine과 Portfolio의 이중 잔액 — `topup`, `restore_balance` 시 양쪽 동기화 필수

## Development Workflow

### Phase 1: Plan
- 구현 요청을 받으면 plan mode로 진입하여 계획을 수립한다
- 계획 수립 시 다음을 자체 검증한다:
  - 요구사항 대비 과도한 설계가 아닌지 (YAGNI 원칙)
  - 기존 코드와 통합하거나 재사용할 수 있는 부분은 없는지
  - 변경 범위가 최소한인지
- 사용자 승인 후 다음 Phase로 진행한다

### Phase 2: Implement
- 승인된 계획에 따라 구현을 진행한다
- 구현 중 계획과 달라지는 부분이 있으면 사용자에게 알린다

### Phase 3: Review (구현 완료 후 자동 수행)
- 구현이 끝나면 다음 관점으로 자체 리뷰를 수행하고 결과를 보고한다:
  1. 목적 부합: 요구사항을 정확히 충족하는지
  2. 보안/버그: 크리티컬 이슈, 보안 취약점, 잠재 버그
  3. 사이드 이펙트: 기존 기능에 영향이 없는지
  4. 코드 구조: 과도하게 큰 함수/파일이 있으면 분리
  5. 재사용: 기존 코드와 중복되거나 통합 가능한 부분
  6. 정리: 구현 과정에서 불필요해진 코드 제거
  7. 품질: 전체 코드 품질 최종 평가
- 문제 발견 시 직접 수정하고, 수정 내용을 보고한다
- 문제가 없으면 항목별로 OK 표시와 함께 전체 변경 사항을 요약한다
