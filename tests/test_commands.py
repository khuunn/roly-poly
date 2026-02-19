"""Tests for TelegramCommands — InlineKeyboard, PnL, Chart, 콜백 라우팅."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.commands import (
    TelegramCommands,
    main_keyboard,
    pnl_period_keyboard,
)
from src.models import Direction, PortfolioSnapshot, SignalType, Trade


class FakeRepository:
    def __init__(self):
        self._trades: list[Trade] = []
        self._snapshot: PortfolioSnapshot | None = None
        self._snapshots: list[PortfolioSnapshot] = []
        self._resolved_trades: list[Trade] = []

    async def get_trades(self, limit=50):
        return self._trades[:limit]

    async def get_latest_snapshot(self):
        return self._snapshot

    async def get_trades_since(self, since):
        return [t for t in self._trades if t.timestamp >= since]

    async def get_resolved_trades(self):
        return self._resolved_trades or [t for t in self._trades if t.resolved]

    async def get_snapshots(self, limit=100):
        return self._snapshots[:limit]


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


def _make_commands(repo=None) -> TelegramCommands:
    repo = repo or FakeRepository()
    return TelegramCommands(repo, reply_long_fn=AsyncMock())


class TestKeyboardLayout:
    async def test_main_keyboard_has_all_buttons(self):
        kb = main_keyboard()
        callback_data = {btn.callback_data for row in kb.inline_keyboard for btn in row}
        expected = {
            "status", "pnl", "history", "chart",
            "health", "help", "stop", "resume",
        }
        assert callback_data == expected

    async def test_pnl_period_keyboard_has_4_periods(self):
        kb = pnl_period_keyboard()
        callback_data = {btn.callback_data for row in kb.inline_keyboard for btn in row}
        assert callback_data == {"pnl_today", "pnl_7d", "pnl_30d", "pnl_all"}


class TestCallbackRouting:
    async def test_callback_status(self):
        repo = FakeRepository()
        repo._snapshot = _make_snapshot()
        cmds = _make_commands(repo)

        mock_bot = MagicMock()
        mock_bot.config.trading_mode.value = "paper"
        mock_bot.config.initial_capital = 1000.0
        cmds.set_trading_bot(mock_bot)

        query = AsyncMock()
        query.data = "status"
        query.message = AsyncMock()
        update = MagicMock()
        update.callback_query = query

        await cmds.handle_callback(update, None)

        query.answer.assert_awaited_once()
        query.message.reply_html.assert_awaited_once()
        text = query.message.reply_html.call_args[0][0]
        assert "포트폴리오 현황" in text
        assert "잔액" in text

    async def test_callback_pnl_menu(self):
        cmds = _make_commands()

        query = AsyncMock()
        query.data = "pnl"
        query.message = AsyncMock()
        update = MagicMock()
        update.callback_query = query

        await cmds.handle_callback(update, None)

        query.answer.assert_awaited_once()
        call_kwargs = query.message.reply_text.call_args.kwargs
        assert "reply_markup" in call_kwargs

    async def test_callback_pnl_period(self):
        repo = FakeRepository()
        repo._trades = [
            _make_trade(pnl=5.0, resolved=True),
            _make_trade(trade_id="t-002", pnl=-3.0, resolved=True),
        ]
        cmds = _make_commands(repo)

        mock_bot = MagicMock()
        mock_bot.config.initial_capital = 1000.0
        cmds.set_trading_bot(mock_bot)

        query = AsyncMock()
        query.data = "pnl_all"
        query.message = AsyncMock()
        update = MagicMock()
        update.callback_query = query

        await cmds.handle_callback(update, None)

        text = query.message.reply_html.call_args[0][0]
        assert "수익률 리포트" in text
        assert "전체" in text

    async def test_callback_history(self):
        repo = FakeRepository()
        repo._trades = [_make_trade()]
        cmds = _make_commands(repo)

        query = AsyncMock()
        query.data = "history"
        query.message = AsyncMock()
        update = MagicMock()
        update.callback_query = query

        await cmds.handle_callback(update, None)

        text = query.message.reply_html.call_args[0][0]
        assert "최근 거래" in text

    async def test_callback_history_empty(self):
        cmds = _make_commands()

        query = AsyncMock()
        query.data = "history"
        query.message = AsyncMock()
        update = MagicMock()
        update.callback_query = query

        await cmds.handle_callback(update, None)

        text = query.message.reply_text.call_args[0][0]
        assert "거래 내역" in text

    async def test_callback_chart_insufficient_data(self):
        repo = FakeRepository()
        repo._snapshots = [_make_snapshot()]
        cmds = _make_commands(repo)

        query = AsyncMock()
        query.data = "chart"
        query.message = AsyncMock()
        update = MagicMock()
        update.callback_query = query

        await cmds.handle_callback(update, None)

        text = query.message.reply_text.call_args[0][0]
        assert "데이터 부족" in text

    async def test_callback_chart_sends_photo(self):
        repo = FakeRepository()
        repo._snapshots = [
            _make_snapshot(timestamp=datetime.now(timezone.utc) - timedelta(hours=i))
            for i in range(5)
        ]
        cmds = _make_commands(repo)

        query = AsyncMock()
        query.data = "chart"
        query.message = AsyncMock()
        update = MagicMock()
        update.callback_query = query

        await cmds.handle_callback(update, None)

        query.message.reply_photo.assert_awaited_once()
        assert "잔액 추이" in query.message.reply_photo.call_args.kwargs["caption"]

    async def test_callback_help(self):
        cmds = _make_commands()

        query = AsyncMock()
        query.data = "help"
        query.message = AsyncMock()
        update = MagicMock()
        update.callback_query = query

        await cmds.handle_callback(update, None)

        call_kwargs = query.message.reply_text.call_args.kwargs
        assert "reply_markup" in call_kwargs


class TestSendStatus:
    async def test_status_no_data(self):
        cmds = _make_commands()
        message = AsyncMock()
        await cmds.send_status(message)
        assert "데이터가 없습니다" in message.reply_text.call_args[0][0]

    async def test_status_with_data(self):
        repo = FakeRepository()
        repo._snapshot = _make_snapshot()
        cmds = _make_commands(repo)

        mock_bot = MagicMock()
        mock_bot.config.trading_mode.value = "paper"
        mock_bot.config.initial_capital = 1000.0
        cmds.set_trading_bot(mock_bot)

        message = AsyncMock()
        await cmds.send_status(message)
        text = message.reply_html.call_args[0][0]
        assert "포트폴리오 현황" in text
        assert "ROI" in text
        assert "paper" in text


class TestSendPnl:
    async def test_pnl_all_period(self):
        repo = FakeRepository()
        repo._resolved_trades = [
            _make_trade(pnl=8.18, resolved=True),
            _make_trade(trade_id="t-002", pnl=-10.10, resolved=True),
        ]
        cmds = _make_commands(repo)

        mock_bot = MagicMock()
        mock_bot.config.initial_capital = 1000.0
        cmds.set_trading_bot(mock_bot)

        message = AsyncMock()
        await cmds.send_pnl(message, "all")

        text = message.reply_html.call_args[0][0]
        assert "수익률 리포트" in text
        assert "전체" in text
        assert "2건" in text
        assert "1W" in text
        assert "1L" in text

    async def test_pnl_today_period(self):
        repo = FakeRepository()
        repo._trades = [
            _make_trade(
                pnl=5.0,
                resolved=True,
                timestamp=datetime.now(timezone.utc) - timedelta(minutes=30),
            ),
        ]
        cmds = _make_commands(repo)

        mock_bot = MagicMock()
        mock_bot.config.initial_capital = 1000.0
        cmds.set_trading_bot(mock_bot)

        message = AsyncMock()
        await cmds.send_pnl(message, "today")

        text = message.reply_html.call_args[0][0]
        assert "오늘" in text


class TestHandleMessage:
    @pytest.fixture()
    def _update(self):
        update = MagicMock()
        update.message = AsyncMock()
        update.effective_chat.id = 12345
        return update

    async def test_handle_message_noop(self, _update):
        """LLM 기능 비활성화 — 메시지 무시."""
        cmds = _make_commands()
        _update.message.text = "안녕"
        await cmds.handle_message(_update, None)
        _update.message.reply_text.assert_not_awaited()
