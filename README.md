# Polymarket Trading Bot

Polymarket 5분 BTC Up/Down 마켓을 대상으로 한 paper trading bot. 앙상블 전략으로 시그널을 생성하고, 텔레그램으로 실시간 알림 및 명령어를 지원한다.

## 주요 기능

- **앙상블 전략**: Directional(모멘텀/EMA) + Orderbook Imbalance 만장일치 투표
- **아비트라지 감지**: Up+Down 양측 매수 시 수수료 차감 후 순이익 발생 시 자동 진입
- **리스크 관리**: 서킷 브레이커 (드로다운/일일 손실 한도) + 매입가 필터 + 동적 포지션 사이징
- **텔레그램 봇**: 실시간 거래 알림, 포트폴리오 조회, PnL 차트, 킬 스위치
- **Paper/Live 아키텍처**: ABC 기반 Engine/Repository/Strategy 추상화 (현재 Paper 모드만 구현, Live는 미구현)

## 기술 스택

| 구분 | 기술 |
|------|------|
| 언어 | Python 3.11+ |
| 비동기 | asyncio, httpx, websockets (Binance BTC/USDT) |
| DB | aiosqlite (SQLite) |
| 알림 | python-telegram-bot |
| 패키지 | uv, hatchling |
| 배포 | Docker, GitHub Actions → Hetzner VPS |

## 시작하기

### 1. 환경변수 설정

```bash
cp .env.example .env
# .env 파일을 편집하여 필수 값 입력
```

### 2. 실행

**로컬 실행 (uv)**
```bash
uv sync
uv run python -m src.main
```

**Docker 실행**
```bash
docker compose up -d
```

## 환경변수

| 변수 | 설명 | 기본값 |
|------|------|--------|
| `TRADING_MODE` | `paper` 또는 `live` | `paper` |
| `TELEGRAM_BOT_TOKEN` | 텔레그램 봇 토큰 | - |
| `TELEGRAM_CHAT_ID` | 알림 받을 채팅 ID | - |
| `DATABASE_TYPE` | `sqlite` 또는 `postgres` | `sqlite` |
| `DATABASE_URL` | DB 연결 URL (postgres 사용 시) | - |
| `INITIAL_CAPITAL` | 시작 자본금 | `1000.0` |
| `BET_SIZE` | 1회 베팅 금액 (`MAX_BET_SIZE`로 캡됨) | `10.0` |
| `MAX_BET_SIZE` | 최대 베팅 금액 | `5.0` |
| `CONFIDENCE_THRESHOLD` | 최소 신뢰도 | `0.6` |
| `MARKET_SCAN_INTERVAL` | 마켓 스캔 주기 (초) | `30` |
| `PRICE_HISTORY_MINUTES` | 가격 히스토리 보관 기간 (분) | `30` |
| `ENSEMBLE_MIN_VOTES` | 앙상블 최소 투표 수 | `2` |
| `IMBALANCE_THRESHOLD` | 오더북 불균형 임계값 | `1.5` |
| `PRIVATE_KEY` | 지갑 개인키 (live 모드 전용, 미구현) | - |
| `FUNDER_ADDRESS` | 자금 출처 주소 (live 모드 전용, 미구현) | - |

## 텔레그램 명령어

| 명령어 | 설명 |
|--------|------|
| `/status` | 포트폴리오 현황 (모드, 잔액, W/L, 마지막 스냅샷) |
| `/history [n]` | 최근 n건의 거래 내역 (기본 5건) |
| `/pnl` | 기간별 수익률 리포트 |
| `/chart` | 잔액 추이 차트 |
| `/topup <금액>` | Paper balance 충전 |
| `/stop` | 거래 일시정지 (킬 스위치) |
| `/resume` | 거래 재개 |

## 테스트

```bash
uv run pytest          # 전체 테스트 (178건)
uv run ruff check src/ # 린트
```
