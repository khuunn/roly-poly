"""Telegram 알림 발송 + 봇 라이프사이클 관리."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

from telegram import Bot, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from src.commands import TelegramCommands, status_keyboard, trade_keyboard
from src.config import Config
from src.models import PortfolioSnapshot, Resolution, Trade
from src.repository.base import Repository

if TYPE_CHECKING:
    from src.main import TradingBot

logger = logging.getLogger(__name__)

_MAX_MESSAGES_PER_MINUTE = 20
_KST = timezone(timedelta(hours=9))
_MAX_TG_LEN = 4096


class TelegramNotifier:
    """알림 발송 + Telegram 봇 라이프사이클."""

    def __init__(
        self,
        config: Config,
        repository: Repository,
    ) -> None:
        self._token = config.telegram_bot_token
        self._chat_id = config.telegram_chat_id
        self._repo = repository
        self._enabled = bool(self._token and self._chat_id)
        self._bot: Bot | None = None
        self._app: Application | None = None
        self._send_timestamps: deque[float] = deque()
        self._commands = TelegramCommands(repository, self._reply_long_to_message)

        if not self._enabled:
            logger.warning("Telegram not configured — notifications will be skipped")

    def set_trading_bot(self, bot: TradingBot) -> None:
        self._trading_bot = bot
        self._commands.set_trading_bot(bot)

    # ── Lifecycle ───────────────────────────────────────────────────

    async def start(self) -> None:
        if not self._enabled:
            return

        self._app = (
            Application.builder()
            .token(self._token)
            .build()
        )
        self._bot = self._app.bot

        cmds = self._commands
        self._app.add_handler(CommandHandler("status", cmds.cmd_status))
        self._app.add_handler(CommandHandler("history", cmds.cmd_history))
        self._app.add_handler(CommandHandler("pnl", cmds.cmd_pnl))
        self._app.add_handler(CommandHandler("chart", cmds.cmd_chart))
        self._app.add_handler(CommandHandler("help", cmds.cmd_help))
        self._app.add_handler(CommandHandler("health", cmds.cmd_health))
        self._app.add_handler(CommandHandler("review", cmds.cmd_review))
        self._app.add_handler(CommandHandler("fix", cmds.cmd_fix))
        self._app.add_handler(CommandHandler("topup", cmds.cmd_topup))
        self._app.add_handler(CommandHandler("stop", cmds.cmd_stop))
        self._app.add_handler(CommandHandler("resume", cmds.cmd_resume))
        self._app.add_handler(CallbackQueryHandler(cmds.handle_callback))
        # MessageHandler는 마지막에 등록 — Command보다 후순위
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, cmds.handle_message))

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram notifier started")

    async def stop(self) -> None:
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            logger.info("Telegram notifier stopped")

    # ── Rate limiting + Send ────────────────────────────────────────

    async def _wait_for_rate_limit(self) -> None:
        now = time.monotonic()
        while len(self._send_timestamps) >= _MAX_MESSAGES_PER_MINUTE:
            oldest = self._send_timestamps[0]
            elapsed = now - oldest
            if elapsed >= 60:
                self._send_timestamps.popleft()
            else:
                await asyncio.sleep(60 - elapsed)
                now = time.monotonic()

    async def _send(self, text: str, reply_markup=None) -> None:
        if not self._enabled or not self._bot:
            return
        await self._wait_for_rate_limit()
        try:
            await self._bot.send_message(
                chat_id=self._chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
            self._send_timestamps.append(time.monotonic())
        except Exception:
            logger.exception("Failed to send Telegram message")

    async def _send_plain(self, text: str) -> None:
        if not self._enabled or not self._bot:
            return
        for chunk in self._split_text(text):
            await self._wait_for_rate_limit()
            try:
                await self._bot.send_message(chat_id=self._chat_id, text=chunk)
                self._send_timestamps.append(time.monotonic())
            except Exception:
                logger.exception("Failed to send Telegram message")

    @staticmethod
    def _split_text(text: str, max_len: int = _MAX_TG_LEN) -> list[str]:
        if len(text) <= max_len:
            return [text]
        chunks: list[str] = []
        while text:
            if len(text) <= max_len:
                chunks.append(text)
                break
            split_at = text.rfind("\n", 0, max_len)
            if split_at == -1:
                split_at = max_len
            chunks.append(text[:split_at])
            text = text[split_at:].lstrip("\n")
        return chunks

    async def _reply_long(self, update: Update, text: str) -> None:
        for chunk in self._split_text(text):
            await update.message.reply_text(chunk)

    async def _reply_long_to_message(self, message, text: str) -> None:
        for chunk in self._split_text(text):
            await message.reply_text(chunk)

    # ── Notification methods ────────────────────────────────────────

    async def notify_trade(self, trade: Trade, market_question: str = "") -> None:
        direction = trade.direction.value
        cost = trade.amount + trade.fee
        odds = f"{1 / trade.price:.2f}x" if trade.price > 0 else "N/A"

        market_label = market_question or trade.market_id
        kst_time = trade.timestamp.astimezone(_KST)

        text = (
            f"🎲 <b>베팅 진입!</b>\n"
            f"🎯 {market_label}\n"
            f"({kst_time:%H:%M} KST)\n"
            f"\n"
        )

        if trade.reason and "|" in trade.reason:
            text += self._format_ensemble_reason(trade.reason)
        else:
            text += f"📊 전략: {trade.signal_type.value}\n"

        text += (
            f"\n"
            f"💰 베팅: ${cost:.2f} ({odds})\n"
            f"📌 방향: {direction} @ ${trade.price:.4f}"
        )
        await self._send(text, reply_markup=trade_keyboard())

    @staticmethod
    def _format_ensemble_reason(reason: str) -> str:
        parts = [p.strip() for p in reason.split("|")]
        if not parts:
            return ""

        summary = parts[0]
        lines = [f"📊 합의: {summary}\n"]

        for part in parts[1:]:
            if "SKIP" in part:
                lines.append(f"❌ {part}\n")
            else:
                lines.append(f"✅ {part}\n")

        return "".join(lines)

    async def notify_resolution(
        self, trade: Trade, resolution: Resolution, market_question: str = "",
    ) -> None:
        pnl = trade.pnl or 0.0
        cost = trade.amount + trade.fee
        payout = pnl + cost

        market_label = market_question or trade.market_id

        if pnl > 0:
            header = "✅ <b>적중!</b>"
        else:
            header = "❌ <b>빗나감</b>"

        text = (
            f"{header}\n"
            f"📋 {market_label}\n"
            f"\n"
            f"💰 비용: ${cost:.2f}\n"
            f"💵 수익: ${payout:.2f}\n"
            f"📈 순손익: ${pnl:+.2f}"
        )
        await self._send(text, reply_markup=trade_keyboard())

    async def notify_daily_summary(self, snapshot: PortfolioSnapshot) -> None:
        snapshots = await self._repo.get_snapshots(limit=50)
        prev_balance = None
        now_utc = datetime.now(timezone.utc)
        for s in snapshots:
            if (now_utc - s.timestamp).total_seconds() >= 86400:
                prev_balance = s.balance
                break

        text = "📊 <b>일일 리포트</b>\n\n"
        text += f"💰 잔액: <code>${snapshot.balance:.2f}</code>\n"

        if prev_balance is not None and prev_balance > 0:
            diff = snapshot.balance - prev_balance
            diff_pct = diff / prev_balance
            text += f"📈 전일 대비: <code>${diff:+.2f} ({diff_pct:+.1%})</code>\n"

        text += (
            f"📊 총 거래: <code>{snapshot.total_trades}건</code> "
            f"({snapshot.wins}W / {snapshot.losses}L)\n"
            f"🎯 승률: <code>{snapshot.win_rate:.1%}</code>\n"
            f"📉 총 PnL: <code>${snapshot.total_pnl:+.2f}</code>\n"
            f"📉 최대 낙폭: <code>{snapshot.max_drawdown:.1%}</code>"
        )
        await self._send(text, reply_markup=status_keyboard())

    async def notify_startup(self, config: Config, balance: float | None = None) -> None:
        bal = balance if balance is not None else config.initial_capital
        text = (
            "🟢 <b>봇 시작</b>\n"
            f"모드: {config.trading_mode.value}\n"
            f"잔액: ${bal:.2f}\n"
            f"베팅: ${config.bet_size:.2f}"
        )
        await self._send(text)

    async def notify_error(self, error: str) -> None:
        text = f"🚨 <b>오류</b>\n<pre>{error}</pre>"
        await self._send(text)

    async def notify_circuit_breaker(self, reason: str) -> None:
        text = (
            "🚨 <b>서킷 브레이커 발동!</b>\n\n"
            f"📋 사유: {reason}\n"
            "🛑 신규 거래가 자동 중단되었습니다.\n\n"
            "재개: /resume"
        )
        await self._send(text)
