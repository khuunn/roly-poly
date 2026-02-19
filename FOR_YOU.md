# Polymarket 5-Min BTC Paper Trading Bot — FOR YOU

## What This Is

A paper trading bot that watches Polymarket's 5-minute BTC Up/Down markets and simulates trades. Think of it as a flight simulator for your trading strategy — all the instruments are real, but the money isn't.

The real magic: flipping one config switch (`TRADING_MODE=live`) takes you from simulation to real trading. Same code, same strategies, same pipeline. No rewrite needed.

## Architecture — The Three Swappable Layers

The whole system is built around three ABCs (Abstract Base Classes) — like electrical outlets that accept different plugs:

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│  Strategy    │     │   Engine     │     │  Repository  │
│  (signal)    │────>│  (execute)   │────>│  (persist)   │
└─────────────┘     └──────────────┘     └─────────────┘
 Directional         PaperEngine          SQLiteRepo
 OrderbookImbalance  LiveEngine*          PostgresRepo*
 EnsembleStrategy    (* = future)         (* = future)
 Arbitrage
```

**Strategy** decides WHAT to trade. **Engine** decides HOW to trade. **Repository** decides WHERE to store it.

Each layer only talks to the ABC above it — swap the implementation underneath and nothing else notices.

### 전략 레이어 진화: 단일 → 앙상블

처음에는 DirectionalStrategy(EMA 모멘텀) 하나만 있었다. 지금은 두 전략이 만장일치로 투표하는 앙상블 구조:

```
EnsembleStrategy ─┬─ DirectionalStrategy (EMA 3/8 모멘텀)
                  │
                  └─ OrderbookImbalanceStrategy (bid/ask 볼륨 비율)
```

**EnsembleStrategy**는 만장일치(unanimous vote)로 결정한다. `min_votes=2` — 두 전략 모두 같은 방향을 가리켜야 시그널이 나간다. 불일치 시 SKIP. 운영 데이터 기반으로 두 전략의 합의 빈도가 높아 만장일치에도 충분한 거래 기회가 확보된다.

각 전략의 confidence를 가중 평균하고, 전략마다 이름을 붙여(`source` 필드) 어떤 전략이 시그널을 만들었는지 Telegram 알림에서 추적할 수 있다.

## File Map

| File | Role | Key Insight |
|------|------|-------------|
| `src/config.py` | All settings from `.env` | Frozen dataclass — immutable at runtime |
| `src/models.py` | Data types (Trade, Market, Signal...) | Plain dataclasses, no DB coupling |
| `src/market_scanner.py` | Finds active 5m BTC markets | Polls Gamma API, regex-matches slugs |
| `src/price_feed.py` | BTC price stream | Binance WS with auto-reconnect + exponential backoff |
| `src/orderbook.py` | Reads CLOB orderbooks | Parallel fetch for Up+Down books |
| `src/commands.py` | Telegram 명령어 + InlineKeyboard UI | /status, /history, /pnl, /chart, /stop, /resume |
| `src/notifier.py` | Telegram alerts + rate limiting | startup/daily summary, 20msg/min limit |
| `src/portfolio.py` | P&L tracking + circuit breaker | 드로다운/일일손실 한도 초과 시 거래 중단 |
| `src/main.py` | Orchestrator | asyncio main loop, entry price filter, health file |
| **Strategy** | | |
| `src/strategy/directional.py` | Momentum + EMA crossover | Fast(3) vs Slow(8) EMA, generates BUY_UP/DOWN |
| `src/strategy/orderbook_imbalance.py` | Bid/Ask 볼륨 비율 | threshold 이상 불균형 → 방향 시그널 |
| `src/strategy/ensemble.py` | 만장일치 투표 집계 | min_votes=2, 가중 평균 confidence |
| `src/strategy/arbitrage.py` | Up+Down < $1 detection | Net profit must exceed 2% fees to trigger |
| **Engine** | | |
| `src/engine/paper.py` | Simulated execution | 0.5% slippage + 1% fee, dynamic position sizing |
| `src/engine/live.py` | Real execution (stub) | Raises NotImplementedError for now |
| **Repository** | | |
| `src/repository/sqlite.py` | SQLite persistence | aiosqlite, auto-creates tables |

## Data Flow (One Tick)

1. `Path("data/health").write_text(...)` — Docker healthcheck용 타임스탬프 갱신
2. `MarketScanner.scan_once()` — discovers active markets from Gamma API
3. `_check_resolutions()` — settles any resolved markets, updates P&L
4. **Circuit breaker 체크** — 드로다운/일일손실 한도 초과 시 `_paused=True` → 거래 중단
5. `PriceFeed.price_history` — grabs recent BTC candle closes
6. For each active market:
   - **Entry price filter** — `max_entry_price=0.70` 초과 시 스킵 (확률 편향 방지)
   - Fetch Up + Down orderbooks in parallel
   - Run **EnsembleStrategy** → 내부적으로 2개 전략 병렬 실행 → 만장일치 투표
   - If confidence > threshold → **Dynamic sizing** (잔고 % × confidence 스케일링)
   - Engine executes → Trade recorded → Telegram notified

## 새로 추가된 핵심 기능들

### 1. Circuit Breaker — 자동 안전장치

돈을 잃는 속도보다 빠르게 봇을 멈추는 게 중요하다. 두 가지 차단 조건:

- **MAX_DRAWDOWN_LIMIT** (기본 20%): 최고 잔액 대비 현재 잔액이 20% 이상 하락하면 거래 중단
- **MAX_DAILY_LOSS** (기본 $50): 하루 누적 손실이 $50 넘으면 거래 중단

Portfolio가 매 거래/해소 시 자동 체크한다. Telegram으로 `/stop` 수동 중단, `/resume` 재개도 가능 — kill switch와 circuit breaker가 이중 방어.

### 2. Dynamic Position Sizing

처음에는 `BET_SIZE` 고정값이었다. 지금은 두 모드:

- `SIZING_MODE=fixed`: 기존처럼 고정 금액
- `SIZING_MODE=percent`: `POSITION_SIZE_PCT=0.02` → 잔고의 2% × confidence 스케일링

confidence가 0.8이면 100%의 2%, confidence가 0.5면 62.5%의 2%. 확신이 높을수록 더 베팅하는 구조. 단, `MIN_BET_SIZE` 이하로는 안 내려간다 — 수수료 대비 의미 없는 주문 방지.

### 3. Entry Price Filter

`MAX_ENTRY_PRICE=0.70` — 한 쪽 토큰이 $0.70 이상이면 스킵. 확률이 70%를 넘으면 리스크 대비 리턴이 낮아진다. $0.95에 사서 $1.00에 청산되면 5% 수익인데, $0.30에 사면 233% 수익. 같은 맞추기 확률에서 수익률이 달라진다.

간단하지만 효과적인 필터 — 이게 없으면 확실해 보이는 마켓에 몰빵하고 5%씩 까먹는 구조가 된다.

### 4. 배포 개선 — .env.example 기반

예전 방식: VPS에 `.env` 파일을 수동 관리. 새 환경변수가 추가되면 SSH로 접속해서 직접 편집.

새 방식:
```
git push → GitHub Actions
    ↓
VPS: cp .env.example .env
    ↓
sed로 시크릿 주입 (GitHub Secrets에서)
    ↓
docker compose up -d --build
```

설정 변경은 `.env.example`을 코드에 커밋하면 끝. 시크릿만 GitHub Secrets에서 관리. 어떤 환경변수가 어떤 값으로 설정되는지 `.env.example` 하나만 보면 된다.

### 5. Telegram 명령어 확장 (commands.py)

`notifier.py`에 있던 명령어 처리가 `commands.py`로 분리됐다. InlineKeyboard UI도 추가:

| 명령 | 기능 |
|------|------|
| `/status` | 현재 잔액, 승률, 활성 포지션 |
| `/history N` | 최근 N건 거래 내역 |
| `/pnl` | 기간별 수익률 리포트 |
| `/stop` | 수동 거래 중단 (kill switch) |
| `/resume` | 거래 재개 |
| `/topup [금액]` | 잔액 충전 |
| 차트 버튼 | PnL 추이 차트 (matplotlib) |

`_KST = timezone(timedelta(hours=9))` — 모든 시간 표시가 KST.

## Technology Choices & Why

- **Python + asyncio**: Everything is I/O-bound (WebSocket, HTTP, DB). asyncio lets us run price feed, scanner, and trading loop concurrently in one process. No need for threading complexity.
- **httpx over requests**: Async-native HTTP client. Zero adapters needed.
- **websockets**: Lightweight, works well with asyncio. Binance's WS API is the fastest BTC price source.
- **aiosqlite**: SQLite but async. Perfect for single-process bot — no need for a DB server on a $5 VPS.
- **python-telegram-bot v21+**: Fully async Telegram API. Built-in command handlers.
- **matplotlib (Agg backend)**: Telegram으로 PnL 차트 이미지 전송. GUI 없는 서버 환경이라 Agg 백엔드 사용.
- **Frozen Config dataclass**: No accidental mutation after startup. If you need to change config, restart the bot.

## Bugs That Bit Us (And How We Fixed Them)

초기 구현 후 코드 리뷰에서 실제 돈을 잃었을 법한 버그 5개를 잡았다. Paper trading의 존재 이유가 여기 있다.

### 1. 아비트라지 PnL이 항상 마이너스 (치명적)

**증상**: 아비트라지 전략이 이론상 수익인데 PnL이 항상 음수.

**원인**: 원래 코드가 `payout = amount / 2`로 고정 — 가격에 관계없이 투자금의 절반만 돌려받는 셈이었다. 실제로는 `shares = half / price`로 주식 수를 계산하고, 승리 측 주식이 $1로 해소되는 구조.

**수정 전**: `payout = half` → 항상 손실
**수정 후**: `up_shares = half / up_price`, `payout = winning_shares * $1.0` → 정상 수익 계산

이건 마치 환전소에서 환율을 무시하고 항상 "반만 돌려주는" 것과 같았다.

### 2. 잔액 복원 이중 계산

**증상**: 마켓 해소 후 잔액이 이상하게 부풀거나 줄어듦.

**원인**: Engine이 주문 시 `balance -= (amount + fee)`를 이미 차감한 상태인데, Portfolio가 해소 시 `balance += amount + pnl`로 복원하면서 수수료를 이중 처리. `pnl = payout - amount - fee`이므로 올바른 복원은 `balance += payout` = `balance += pnl + amount + fee`.

가게에서 이미 계산한 거스름돈을 다시 계산하는 격이었다.

### 3. `closed=false` 필터가 해소 감지를 차단

**증상**: 봇 재시작 후 이미 종료된 마켓의 해소 결과를 영원히 받지 못함.

**원인**: Gamma API 호출에 `closed=false` 파라미터를 넣으면 닫힌 마켓이 응답에서 빠진다. 봇이 꺼졌다 켜지면 열린 거래의 마켓이 이미 닫혀있을 수 있는데, 이 필터가 그 마켓을 아예 못 찾게 만들었다.

문 잠근 가게를 "영업중인 가게만 보여줘"로 검색하면 당연히 못 찾는다.

### 4. `if trade.alt_price` — Python의 falsy 함정

**증상**: `alt_price=0.0`인 거래에서 실제 가격 대신 추정값(1-up_price)이 사용됨.

**원인**: Python에서 `0.0`은 falsy. `if trade.alt_price:`는 값이 0일 때 False로 평가된다. 올바른 체크는 `if trade.alt_price is not None:`.

Python 입문자가 가장 많이 밟는 지뢰 중 하나. 0이 유효한 값인 금융 데이터에서는 특히 위험.

### 5. Zero Price Division

**증상**: 가격이 0인 거래에서 `ZeroDivisionError` 크래시.

**수정**: `shares = amount / price` 앞에 `if trade.price <= 0: return 0.0` 가드 추가. 아비트라지에서도 양쪽 가격에 동일 가드 적용.

---

## Lessons Learned

### The Gamma API Slug Problem
Polymarket의 slug 포맷은 문서화되어 있지 않다. `"5m"`과 `"btc"`를 순서 무관하게 regex 매칭한다. 실제 형식이 달라질 수 있으므로 (`btc-updown-5m-*`) 정확한 패턴을 하드코딩하면 안 된다.

### Market Resolution Timing
마켓이 "closed"이지만 아직 "resolved"가 아닐 수 있다. `resolved`와 `closed` 플래그를 모두 확인해야 한다. `outcomePrices`는 `resolved=true` 이후에만 신뢰할 수 있다.

### Token ID Parsing
`clobTokenIds`가 JSON 문자열일 수도 있고 실제 리스트일 수도 있다. 항상 둘 다 처리해야 한다. 첫 번째 토큰 = Up, 두 번째 = Down.

### Paper Engine PnL의 핵심 공식
- **방향성**: `shares = amount / price`, 승리 시 `payout = shares * $1.00`, 패배 시 $0
- **아비트라지**: 양쪽에 절반씩 투자, 한쪽이 반드시 이긴다. `payout = winning_shares * $1.00`
- **공통**: `PnL = payout - amount - fee`

### Telegram Rate Limits
Telegram은 봇당 ~30 msg/sec이지만 채팅별 한도는 더 낮다. 20/min으로 제한. deque 기반 rate limiter가 token bucket보다 단순하고 이 볼륨에서는 충분하다.

### alt_price가 필요한 이유
아비트라지에서 Up과 Down의 체결가가 다르다. 처음에는 `1 - up_price`로 추정했는데, 오더북 스프레드 때문에 실제 down 체결가와 차이가 난다. `Signal.arb_down_ask` → `Trade.alt_price`로 실제 가격을 전달하는 구조로 수정.

### .env.example을 Single Source of Truth로
VPS의 `.env`를 직접 관리하면 "어떤 환경변수가 있는지" 추적이 안 된다. `.env.example`을 코드에 커밋하고, 배포 시 `cp .env.example .env` → `sed`로 시크릿 주입하면 설정 변경이 코드 리뷰를 거친다.

## Quick Start

```bash
cp .env.example .env
# Edit .env: add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
uv run python -m src.main
```

Or with Docker:
```bash
docker compose up -d
```

## Test Coverage — 178 Tests, ~1.5s

12개 테스트 파일로 핵심 로직을 커버한다. 외부 API 호출 없이 모두 로컬에서 돈다.

| 파일 | 범위 |
|------|------|
| `test_data_layer.py` | 스캐너 파싱, 오더북 파싱, 가격 피드 kline 처리 |
| `test_engine_portfolio.py` | Paper 엔진 체결 + 포트폴리오 PnL + 잔액 흐름 + 드로다운 |
| `test_notifier.py` | 비활성 모드, rate limiting, 메시지 포맷, 에러 처리 |
| `test_sqlite_repository.py` | DB 왕복, enum 직렬화, upsert, alt_price |
| `test_edge_cases.py` | zero price 가드, UNKNOWN 해소, 잔액 경계, 지표 엣지케이스 |
| `test_strategy.py` | 방향성 + 아비트라지 시그널 생성 |
| `test_strategy_ensemble.py` | 앙상블 투표, 가중 confidence, SKIP 조건 |
| `test_strategy_orderbook_imbalance.py` | 오더북 불균형 감지, threshold 경계값 |
| `test_circuit_breaker.py` | 드로다운/일일손실 한도, 차단 후 거래 불가 |
| `test_position_sizing.py` | fixed/percent 모드, confidence 스케일링, min_bet 하한 |
| `test_entry_price_filter.py` | max_entry_price 필터, 경계값 테스트 |
| `test_commands.py` | Telegram InlineKeyboard, /stop, /resume |

```bash
uv run pytest tests/ -q  # 178 passed in ~1.5s
```

### 테스트 설계 원칙
- **FakeRepository**: 모든 테스트에서 실제 DB 대신 인메모리 fake 사용. 빠르고 격리됨.
- **경계값 테스트**: 잔액이 정확히 비용과 같을 때, 1센트 부족할 때, 연속 거래로 소진될 때.
- **Zero/None 가드**: 가격 0, alt_price가 None vs 0.0, UNKNOWN 해소 등 실전에서 터지는 케이스.

## Monitoring & Health — 봇이 살아있는지 어떻게 아는가

VPS에서 24/7 돌리려면 "조용히 죽는" 상황을 잡아야 한다. 네 가지 레이어로 커버한다.

### 1. 시작 알림 (즉시 확인)
봇이 `start()` 하면 Telegram으로 시작 메시지가 온다. 모드, 자본, 베팅 사이즈가 함께 표시되니까, 서버에 SSH 안 해도 "방금 배포한 봇이 제대로 떴는지" 바로 확인 가능.

### 2. 일일 요약 (매일 자정 KST)
`_daily_summary_loop()`가 별도 asyncio task로 돈다. 매일 15:00 UTC (자정 KST)에 스냅샷 저장 + 일일 요약 발송.

### 3. Docker Healthcheck (자동 복구)
매 `_tick()` 시작에 `data/health` 파일에 타임스탬프를 쓴다. Dockerfile의 `HEALTHCHECK`가 60초마다 이 파일이 120초 이내에 갱신됐는지 확인. 3회 연속 실패하면 Docker가 컨테이너를 unhealthy로 마킹.

### 4. Circuit Breaker (자동 중단)
드로다운 20% 또는 일일 손실 $50 초과 시 거래를 자동 중단하고 Telegram으로 알린다. 봇이 살아있지만 돈을 잃고 있을 때의 방어선.

## Potential Pitfalls

### 봇 재시작 시 상태 복원
`Portfolio.restore()`가 마지막 스냅샷에서 복원하지만, 스냅샷 이후~크래시 사이의 거래는 유실될 수 있다.

### Binance WebSocket 끊김
WebSocket은 24시간 뒤 강제 종료된다. 자동 재연결이 있지만, 재연결 동안 가격 히스토리에 갭이 생긴다.

### Gamma API 응답 형식 변경
Polymarket API는 공식 문서가 부실하고 응답 구조가 예고 없이 바뀔 수 있다. 방어적 파싱이 필수.

## VPS 배포 — $4.59/mo로 24/7 운영

### 인프라 구성

- **Hetzner Cloud CAX11** (Helsinki): 2 Ampere vCPU, 4GB RAM, 40GB SSD, $3.99 + IPv4 $0.60 = **$4.59/mo**
- ARM 아키텍처 — x86 대비 가성비 최고. Python 봇은 아키텍처 무관하게 잘 돌아간다.

### CI/CD 파이프라인

```
git push origin main
    ↓
GitHub Actions: test job (ruff + pytest, ~25s)
    ↓ (통과 시)
GitHub Actions: deploy job (~50s)
    ↓
SSH → VPS:
  git pull
  cp .env.example .env
  sed -i 시크릿 주입 (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
  docker compose down
  docker compose up -d --build
    ↓
봇 시작 → Telegram 시작 알림
```

### 데이터 영속화

```
VPS: ~/polymarket-trading/data/
├── health              ← Docker healthcheck 타임스탬프
└── polymarket.db       ← SQLite 데이터

Docker volume: ./data:/app/data — 컨테이너 재빌드해도 영속
```

### SSH 키 3종

| 키 | 용도 | 위치 |
|----|------|------|
| `id_ed25519_github_personal` | 로컬 → VPS SSH 접속 | `~/.ssh/` (로컬) |
| VPS deploy key | VPS → GitHub 프라이빗 레포 pull | `/home/deploy/.ssh/id_ed25519` (VPS) |
| GitHub Actions key | CI/CD → VPS SSH 배포 | GitHub Secrets `VPS_SSH_KEY` |

### Lessons Learned

- **`vps-setup.sh`에서 git clone 실패**: 프라이빗 레포는 HTTPS로 클론 안 됨. SSH deploy key 등록 후 `git@github.com:...` URL로 해결.
- **GitHub Actions secrets 저장 안 됨**: 가끔 UI에서 저장이 안 된 것처럼 보임. 다시 추가하면 해결.
- **첫 배포 시 lint 실패**: 로컬에서 안 잡혔던 unused import 18개가 CI에서 터짐. 교훈: CI 파이프라인은 일찍 만들수록 좋다.
- **`data/health` FileNotFoundError**: CI에서 `data/` 디렉토리가 없어서 `health` 파일 쓰기 실패. `health.parent.mkdir(parents=True, exist_ok=True)` 추가로 해결.

### VPS 확인 명령어

```bash
# SSH 접속
ssh -i ~/.ssh/id_ed25519_github_personal deploy@77.42.93.228

# 컨테이너 상태
docker ps

# 실시간 로그
cd ~/polymarket-trading && docker compose logs -f

# 재시작
docker compose down && docker compose up -d --build
```

## What's Next (When Going Live)

1. ~~**모니터링 인프라**~~ — ✅ 시작 알림 + 일일 요약 + Docker healthcheck
2. ~~**VPS Paper 배포**~~ — ✅ Hetzner CAX11, CI/CD, Telegram 알림 확인
3. ~~**앙상블 전략**~~ — ✅ 2-전략 만장일치 투표 (Directional + Orderbook)
4. ~~**안전장치**~~ — ✅ Circuit breaker + entry price filter + dynamic sizing
5. **24시간 모니터링** — 데이터 축적 후 봇 동작 패턴 분석
7. **전략 튜닝** — 운영 데이터 기반으로 EMA 기간, confidence 임계값, 베팅 사이즈 조정
8. **`engine/live.py` 구현** — `py-clob-client`로 Polymarket CLOB에 실제 주문 실행
9. `TRADING_MODE=live` + `PRIVATE_KEY` 설정 → 실전 전환

## Live 전환 시 잔액 동기화 설계

### 현재 Paper 모드의 한계

Paper 모드에서는 잔액이 두 곳에서 독립적으로 추적된다:

```
PaperEngine._balance  ← 주문 가능 잔액 (인메모리)
Portfolio._balance    ← PnL 추적용 잔액 (인메모리 + SQLite 스냅샷)
```

이 둘은 **봇이 유일한 자금 출입구**라는 가정 하에 동기화된다. Paper 모드에서는 맞지만, Live에서는 외부 입출금이 생기면서 깨진다.

### 설계 원칙 — 지갑 잔액이 진실의 원천

| | Paper | Live |
|---|---|---|
| 잔액의 진실 | `Engine._balance` (계산값) | **CLOB API 조회** (실제값) |
| Portfolio._balance | 진실이자 추적값 | 봇 거래의 PnL 기록용 (참고값) |
| 주문 가능 판단 | `Engine._balance >= cost` | `API잔액 >= cost` |

### 요약

Paper → Live 전환 시 잔액 관련 코드 변경은 **LiveEngine 내부에만 집중**된다. Portfolio, main loop, 전략 코드는 Engine의 ABC를 통해 간접 접근하므로 수정 불필요. 이게 처음에 ABC로 설계한 이유 — Engine만 갈아끼우면 나머지는 모른다.
