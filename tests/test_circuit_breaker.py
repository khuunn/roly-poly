"""서킷 브레이커 + 킬 스위치 테스트."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

from src.commands import TelegramCommands, main_keyboard
from src.config import Config
from src.models import Direction, MarketStatus, PortfolioSnapshot, SignalType, Trade


# ── Helpers ──────────────────────────────────────────────────────────

class FakeRepository:
    def __init__(self):
        self._trades: list[Trade] = []
        self._snapshot: PortfolioSnapshot | None = None

    async def get_trades_since(self, since):
        return [t for t in self._trades if t.timestamp >= since]

    async def get_latest_snapshot(self):
        return self._snapshot

    async def get_trades(self, limit=50):
        return self._trades[:limit]

    async def get_resolved_trades(self):
        return [t for t in self._trades if t.resolved]

    async def get_snapshots(self, limit=100):
        return []

    async def save_portfolio_snapshot(self, snapshot):
        self._snapshot = snapshot

    async def get_open_trades_for_market(self, market_id):
        return []

    async def save_market(self, market):
        pass

    async def initialize(self):
        pass

    async def close(self):
        pass

    async def save_trade(self, trade):
        self._trades.append(trade)

    async def update_trade_resolution(self, trade_id, pnl):
        pass


def _make_trade(**overrides) -> Trade:
    defaults = dict(
        trade_id="t-001",
        market_id="mkt-1",
        direction=Direction.UP,
        token_id="tok-up",
        amount=10.0,
        price=0.55,
        fee=0.10,
        signal_type=SignalType.BUY_UP,
        timestamp=datetime.now(timezone.utc) - timedelta(minutes=30),
    )
    defaults.update(overrides)
    return Trade(**defaults)


def _make_commands(repo=None) -> TelegramCommands:
    repo = repo or FakeRepository()
    return TelegramCommands(repo, reply_long_fn=AsyncMock())


# ── Config 기본값 테스트 ─────────────────────────────────────────────

class TestCircuitBreakerConfig:
    def test_default_config_values(self):
        config = Config()
        assert config.max_drawdown_limit == 0.2
        assert config.max_daily_loss == 50.0


# ── Circuit Breaker 체크 테스트 ──────────────────────────────────────

class TestCircuitBreakerCheck:
    async def test_drawdown_triggers_breaker(self):
        """drawdown >= limit → pause."""
        from src.main import TradingBot

        config = Config()
        bot = TradingBot.__new__(TradingBot)
        bot.config = config
        bot.running = True
        bot._trading_paused = False
        bot._pause_reason = ""

        # Portfolio mock: drawdown이 한도 이상
        bot.portfolio = MagicMock()
        bot.portfolio.max_drawdown = 0.25  # 25% > 20% limit
        bot.repo = FakeRepository()

        reason = await bot._check_circuit_breaker()

        assert reason is not None
        assert "최대 낙폭 한도 초과" in reason
        assert "25.0%" in reason

    async def test_daily_loss_triggers_breaker(self):
        """일일 손실 >= limit → pause."""
        from src.main import TradingBot

        config = Config()
        bot = TradingBot.__new__(TradingBot)
        bot.config = config
        bot.running = True
        bot._trading_paused = False
        bot._pause_reason = ""

        # Portfolio mock: drawdown은 낮음
        bot.portfolio = MagicMock()
        bot.portfolio.max_drawdown = 0.05

        # 오늘 큰 손실 기록
        repo = FakeRepository()
        repo._trades = [
            _make_trade(pnl=-30.0, resolved=True),
            _make_trade(trade_id="t-002", pnl=-25.0, resolved=True),
        ]
        bot.repo = repo

        reason = await bot._check_circuit_breaker()

        assert reason is not None
        assert "일일 손실 한도 초과" in reason

    async def test_no_trigger_under_limits(self):
        """한도 미만 → 정상 거래 (None 반환)."""
        from src.main import TradingBot

        config = Config()
        bot = TradingBot.__new__(TradingBot)
        bot.config = config
        bot.running = True
        bot._trading_paused = False
        bot._pause_reason = ""

        bot.portfolio = MagicMock()
        bot.portfolio.max_drawdown = 0.05  # 5% < 20%

        repo = FakeRepository()
        repo._trades = [
            _make_trade(pnl=-5.0, resolved=True),
        ]
        bot.repo = repo

        reason = await bot._check_circuit_breaker()

        assert reason is None


# ── Pause / Resume 테스트 ────────────────────────────────────────────

class TestPauseResume:
    async def test_pause_skips_evaluation(self):
        """정지 중 _evaluate_market 미호출."""
        from src.main import TradingBot

        config = Config()
        bot = TradingBot.__new__(TradingBot)
        bot.config = config
        bot.running = True
        bot._trading_paused = True
        bot._pause_reason = "테스트 정지"

        bot.scanner = AsyncMock()
        bot.scanner.scan_once = AsyncMock()
        market = MagicMock()
        market.status = MarketStatus.ACTIVE
        bot.scanner.markets = {"m1": market}

        bot.repo = FakeRepository()
        bot.portfolio = MagicMock()
        bot.portfolio.max_drawdown = 0.0
        bot.portfolio.save_snapshot = AsyncMock()
        bot.price_feed = MagicMock()
        bot.price_feed.price_history = [100.0, 101.0, 102.0]
        bot.notifier = AsyncMock()
        bot._check_resolutions = AsyncMock()
        bot._evaluate_market = AsyncMock()

        await bot._tick()

        bot._evaluate_market.assert_not_awaited()

    async def test_pause_still_resolves(self):
        """정지 중에도 resolution 처리."""
        from src.main import TradingBot

        config = Config()
        bot = TradingBot.__new__(TradingBot)
        bot.config = config
        bot.running = True
        bot._trading_paused = True
        bot._pause_reason = "테스트 정지"

        bot.scanner = AsyncMock()
        bot.scanner.scan_once = AsyncMock()
        market = MagicMock()
        market.status = MarketStatus.RESOLVED
        bot.scanner.markets = {"m1": market}

        bot.repo = FakeRepository()
        bot.portfolio = MagicMock()
        bot.portfolio.max_drawdown = 0.0
        bot.portfolio.save_snapshot = AsyncMock()
        bot.price_feed = MagicMock()
        bot.notifier = AsyncMock()
        bot._check_resolutions = AsyncMock()
        bot._evaluate_market = AsyncMock()

        await bot._tick()

        bot._check_resolutions.assert_awaited_once()

    async def test_resume_resumes_trading(self):
        """resume 후 거래 재개."""
        from src.main import TradingBot

        config = Config()
        bot = TradingBot.__new__(TradingBot)
        bot.config = config
        bot._trading_paused = True
        bot._pause_reason = "테스트 정지"

        assert bot.is_paused is True

        bot.resume_trading()

        assert bot.is_paused is False
        assert bot.pause_reason == ""

    async def test_stop_command(self):
        """/stop → is_paused == True."""
        cmds = _make_commands()
        mock_bot = MagicMock()
        mock_bot.is_paused = False
        mock_bot.pause_trading = MagicMock()
        cmds.set_trading_bot(mock_bot)

        message = AsyncMock()
        await cmds._do_stop(message)

        mock_bot.pause_trading.assert_called_once_with("수동 중지 (/stop)")
        message.reply_html.assert_awaited_once()
        text = message.reply_html.call_args[0][0]
        assert "거래 일시정지" in text

    async def test_resume_command(self):
        """/resume → is_paused == False."""
        cmds = _make_commands()
        mock_bot = MagicMock()
        mock_bot.is_paused = True
        mock_bot.resume_trading = MagicMock()
        cmds.set_trading_bot(mock_bot)

        message = AsyncMock()
        await cmds._do_resume(message)

        mock_bot.resume_trading.assert_called_once()
        message.reply_html.assert_awaited_once()
        text = message.reply_html.call_args[0][0]
        assert "거래 재개" in text

    async def test_stop_when_already_paused(self):
        """중복 정지 시 안내 메시지."""
        cmds = _make_commands()
        mock_bot = MagicMock()
        mock_bot.is_paused = True
        mock_bot.pause_reason = "서킷 브레이커"
        cmds.set_trading_bot(mock_bot)

        message = AsyncMock()
        await cmds._do_stop(message)

        text = message.reply_text.call_args[0][0]
        assert "이미 정지 상태" in text

    async def test_resume_when_not_paused(self):
        """이미 활성화 상태에서 resume 시 안내 메시지."""
        cmds = _make_commands()
        mock_bot = MagicMock()
        mock_bot.is_paused = False
        cmds.set_trading_bot(mock_bot)

        message = AsyncMock()
        await cmds._do_resume(message)

        text = message.reply_text.call_args[0][0]
        assert "활성화" in text

    async def test_stop_no_bot(self):
        """봇 미준비 시 안내."""
        cmds = _make_commands()
        # _trading_bot이 None (기본값)
        message = AsyncMock()
        await cmds._do_stop(message)

        text = message.reply_text.call_args[0][0]
        assert "준비되지 않았습니다" in text

    async def test_main_keyboard_has_stop_resume(self):
        """키보드에 정지/재개 버튼 포함."""
        kb = main_keyboard()
        callback_data = {btn.callback_data for row in kb.inline_keyboard for btn in row}
        assert "stop" in callback_data
        assert "resume" in callback_data

    async def test_status_shows_pause_state(self):
        """상태 조회 시 정지 상태 표시."""
        repo = FakeRepository()
        repo._snapshot = PortfolioSnapshot(
            balance=950.0,
            total_trades=10,
            wins=5,
            losses=5,
            total_pnl=-50.0,
            max_drawdown=0.05,
            timestamp=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        cmds = _make_commands(repo)

        mock_bot = MagicMock()
        mock_bot.config.trading_mode.value = "paper"
        mock_bot.config.initial_capital = 1000.0
        mock_bot.is_paused = True
        mock_bot.pause_reason = "수동 중지 (/stop)"
        cmds.set_trading_bot(mock_bot)

        message = AsyncMock()
        await cmds.send_status(message)

        text = message.reply_html.call_args[0][0]
        assert "거래 일시정지 중" in text
        assert "수동 중지" in text
