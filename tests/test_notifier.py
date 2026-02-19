"""Tests for TelegramNotifier — disabled mode, rate limiting, message formatting."""

from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock

from src.config import Config
from src.models import (
    Direction,
    PortfolioSnapshot,
    Resolution,
    ResolutionOutcome,
    SignalType,
    Trade,
)
from src.notifier import TelegramNotifier


class FakeRepository:
    """Minimal fake for notifier tests."""

    def __init__(self):
        self._trades: list[Trade] = []
        self._snapshot: PortfolioSnapshot | None = None
        self._snapshots: list[PortfolioSnapshot] = []

    async def get_trades(self, limit=50):
        return self._trades[:limit]

    async def get_latest_snapshot(self):
        return self._snapshot

    async def get_trades_since(self, since):
        return [t for t in self._trades if t.timestamp >= since]

    async def get_resolved_trades(self):
        return [t for t in self._trades if t.resolved]

    async def get_snapshots(self, limit=100):
        return self._snapshots[:limit]


def _make_config(**overrides) -> Config:
    defaults = dict(
        telegram_bot_token=overrides.get("token", ""),
        telegram_chat_id=overrides.get("chat_id", ""),
    )
    return Config(**{k: v for k, v in defaults.items()})


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
        timestamp=datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return Trade(**defaults)


def _make_snapshot(**overrides) -> PortfolioSnapshot:
    defaults = dict(
        balance=985.50,
        total_trades=20,
        wins=13,
        losses=7,
        total_pnl=-14.50,
        max_drawdown=0.032,
        timestamp=datetime.now(timezone.utc) - timedelta(hours=2, minutes=30),
    )
    defaults.update(overrides)
    return PortfolioSnapshot(**defaults)


class TestDisabledMode:
    async def test_not_enabled_without_token(self):
        cfg = _make_config(token="", chat_id="123")
        notifier = TelegramNotifier(cfg, FakeRepository())
        assert notifier._enabled is False

    async def test_not_enabled_without_chat_id(self):
        cfg = _make_config(token="abc:123", chat_id="")
        notifier = TelegramNotifier(cfg, FakeRepository())
        assert notifier._enabled is False

    async def test_send_is_noop_when_disabled(self):
        notifier = TelegramNotifier(_make_config(), FakeRepository())
        await notifier._send("test message")

    async def test_notify_trade_noop_when_disabled(self):
        notifier = TelegramNotifier(_make_config(), FakeRepository())
        await notifier.notify_trade(_make_trade())

    async def test_notify_error_noop_when_disabled(self):
        notifier = TelegramNotifier(_make_config(), FakeRepository())
        await notifier.notify_error("something broke")

    async def test_start_noop_when_disabled(self):
        notifier = TelegramNotifier(_make_config(), FakeRepository())
        await notifier.start()
        assert notifier._app is None


class TestRateLimiting:
    async def test_under_limit_passes_immediately(self):
        notifier = TelegramNotifier(_make_config(), FakeRepository())
        now = time.monotonic()
        for i in range(5):
            notifier._send_timestamps.append(now - 10 + i)
        await notifier._wait_for_rate_limit()

    async def test_at_limit_waits(self):
        notifier = TelegramNotifier(_make_config(), FakeRepository())
        now = time.monotonic()
        for i in range(20):
            notifier._send_timestamps.append(now - 5)
        notifier._send_timestamps[0] = now - 59.5
        start = time.monotonic()
        await notifier._wait_for_rate_limit()
        elapsed = time.monotonic() - start
        assert elapsed < 2.0


class TestMessageFormatting:
    async def test_notify_trade_contains_fields(self):
        cfg = _make_config(token="fake:token", chat_id="123")
        notifier = TelegramNotifier(cfg, FakeRepository())
        notifier._enabled = True
        notifier._bot = AsyncMock()

        await notifier.notify_trade(_make_trade(), "Bitcoin Up or Down - Feb 16, 3:00AM-3:05AM ET")

        text = notifier._bot.send_message.call_args.kwargs["text"]
        assert "베팅 진입" in text
        assert "Bitcoin Up or Down" in text
        assert "Up" in text
        assert "0.5500" in text
        assert "KST" in text

    async def test_notify_trade_has_inline_keyboard(self):
        cfg = _make_config(token="fake:token", chat_id="123")
        notifier = TelegramNotifier(cfg, FakeRepository())
        notifier._enabled = True
        notifier._bot = AsyncMock()

        await notifier.notify_trade(_make_trade())

        reply_markup = notifier._bot.send_message.call_args.kwargs["reply_markup"]
        assert reply_markup is not None
        callback_data = {btn.callback_data for row in reply_markup.inline_keyboard for btn in row}
        assert "status" in callback_data
        assert "history" in callback_data

    async def test_notify_resolution_win(self):
        cfg = _make_config(token="fake:token", chat_id="123")
        notifier = TelegramNotifier(cfg, FakeRepository())
        notifier._enabled = True
        notifier._bot = AsyncMock()

        trade = _make_trade(pnl=5.50)
        resolution = Resolution(market_id="mkt-1", outcome=ResolutionOutcome.UP)
        await notifier.notify_resolution(trade, resolution, "Bitcoin Up or Down - Test")

        text = notifier._bot.send_message.call_args.kwargs["text"]
        assert "적중" in text
        assert "+5.50" in text
        assert "비용" in text
        assert "수익" in text
        assert "순손익" in text

    async def test_notify_resolution_loss(self):
        cfg = _make_config(token="fake:token", chat_id="123")
        notifier = TelegramNotifier(cfg, FakeRepository())
        notifier._enabled = True
        notifier._bot = AsyncMock()

        trade = _make_trade(pnl=-10.10)
        resolution = Resolution(market_id="mkt-1", outcome=ResolutionOutcome.DOWN)
        await notifier.notify_resolution(trade, resolution)

        text = notifier._bot.send_message.call_args.kwargs["text"]
        assert "빗나감" in text
        assert "-10.10" in text

    async def test_notify_resolution_has_inline_keyboard(self):
        cfg = _make_config(token="fake:token", chat_id="123")
        notifier = TelegramNotifier(cfg, FakeRepository())
        notifier._enabled = True
        notifier._bot = AsyncMock()

        trade = _make_trade(pnl=5.50)
        resolution = Resolution(market_id="mkt-1", outcome=ResolutionOutcome.UP)
        await notifier.notify_resolution(trade, resolution)

        assert notifier._bot.send_message.call_args.kwargs["reply_markup"] is not None

    async def test_notify_error_contains_message(self):
        cfg = _make_config(token="fake:token", chat_id="123")
        notifier = TelegramNotifier(cfg, FakeRepository())
        notifier._enabled = True
        notifier._bot = AsyncMock()

        await notifier.notify_error("WebSocket disconnected")

        text = notifier._bot.send_message.call_args.kwargs["text"]
        assert "오류" in text
        assert "WebSocket disconnected" in text

    async def test_notify_startup_contains_fields(self):
        cfg = _make_config(token="fake:token", chat_id="123")
        notifier = TelegramNotifier(cfg, FakeRepository())
        notifier._enabled = True
        notifier._bot = AsyncMock()

        await notifier.notify_startup(cfg)

        text = notifier._bot.send_message.call_args.kwargs["text"]
        assert "봇 시작" in text
        assert cfg.trading_mode.value in text
        assert f"${cfg.initial_capital:.2f}" in text
        assert f"${cfg.bet_size:.2f}" in text

    async def test_send_failure_does_not_raise(self):
        cfg = _make_config(token="fake:token", chat_id="123")
        notifier = TelegramNotifier(cfg, FakeRepository())
        notifier._enabled = True
        notifier._bot = AsyncMock()
        notifier._bot.send_message.side_effect = Exception("Telegram API error")

        await notifier._send("test")


class TestDailySummary:
    async def test_daily_summary_korean(self):
        cfg = _make_config(token="fake:token", chat_id="123")
        repo = FakeRepository()
        repo._snapshots = [
            _make_snapshot(
                balance=970.0,
                timestamp=datetime.now(timezone.utc) - timedelta(hours=25),
            ),
        ]
        notifier = TelegramNotifier(cfg, repo)
        notifier._enabled = True
        notifier._bot = AsyncMock()

        await notifier.notify_daily_summary(_make_snapshot(balance=985.50))

        text = notifier._bot.send_message.call_args.kwargs["text"]
        assert "일일 리포트" in text
        assert "잔액" in text
        assert "전일 대비" in text
        assert "승률" in text

    async def test_daily_summary_no_previous(self):
        cfg = _make_config(token="fake:token", chat_id="123")
        notifier = TelegramNotifier(cfg, FakeRepository())
        notifier._enabled = True
        notifier._bot = AsyncMock()

        await notifier.notify_daily_summary(_make_snapshot())

        text = notifier._bot.send_message.call_args.kwargs["text"]
        assert "일일 리포트" in text
        assert "전일 대비" not in text
