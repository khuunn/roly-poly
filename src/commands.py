"""Telegram 명령어 핸들러 + InlineKeyboard UI + 차트 생성."""

from __future__ import annotations

import io
import logging
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from src.models import Trade
from src.repository.base import Repository

if TYPE_CHECKING:
    from src.main import TradingBot

logger = logging.getLogger(__name__)

_KST = timezone(timedelta(hours=9))


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data)


# ── Keyboard 레이아웃 ──────────────────────────────────────────────

def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_btn("📊 현황", "status"), _btn("📈 수익률", "pnl")],
        [_btn("📋 거래내역", "history"), _btn("📉 차트", "chart")],
        [_btn("🏥 헬스체크", "health"), _btn("❓ 도움말", "help")],
        [_btn("🛑 정지", "stop"), _btn("▶️ 재개", "resume")],
    ])


def status_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_btn("📈 수익률", "pnl"), _btn("📋 거래내역", "history"), _btn("📉 차트", "chart")],
    ])


def trade_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_btn("📊 현황", "status"), _btn("📋 거래내역", "history")],
    ])


def pnl_period_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            _btn("오늘", "pnl_today"),
            _btn("7일", "pnl_7d"),
            _btn("30일", "pnl_30d"),
            _btn("전체", "pnl_all"),
        ],
    ])


# ── 공유 표시 로직 (Command + Callback 양쪽에서 재사용) ─────────────

class TelegramCommands:
    """명령어 핸들러 + InlineKeyboard 콜백을 관리."""

    def __init__(
        self,
        repo: Repository,
        reply_long_fn,
    ) -> None:
        self._repo = repo
        self._reply_long = reply_long_fn  # TelegramNotifier._reply_long_to_message
        self._trading_bot: TradingBot | None = None

    def set_trading_bot(self, bot: TradingBot) -> None:
        self._trading_bot = bot

    # ── Callback 라우터 ─────────────────────────────────────────────

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        data = query.data

        dispatch = {
            "status": lambda m: self.send_status(m),
            "pnl": lambda m: self.send_pnl_menu(m),
            "history": lambda m: self.send_history(m),
            "chart": lambda m: self.send_chart(m),
            "health": lambda m: self._do_health(m),
            "stop": lambda m: self._do_stop(m),
            "resume": lambda m: self._do_resume(m),
            "help": lambda m: m.reply_text("명령어를 선택하세요:", reply_markup=main_keyboard()),
        }

        handler = dispatch.get(data)
        if handler:
            await handler(query.message)
        elif data and data.startswith("pnl_"):
            period = data.split("_", 1)[1]
            await self.send_pnl(query.message, period)

    async def _do_health(self, message) -> None:
        """헬스체크 — LLM 기능 비활성화."""
        await message.reply_text("LLM 기능이 비활성화되어 있습니다.")

    # ── 표시 로직 ────────────────────────────────────────────────────

    async def send_status(self, message) -> None:
        snapshot = await self._repo.get_latest_snapshot()
        if snapshot is None:
            await message.reply_text("아직 포트폴리오 데이터가 없습니다.")
            return

        mode = self._trading_bot.config.trading_mode.value if self._trading_bot else "?"
        initial = self._trading_bot.config.initial_capital if self._trading_bot else 1000.0
        roi = (snapshot.balance - initial) / initial if initial > 0 else 0.0

        uptime = datetime.now(timezone.utc) - snapshot.timestamp
        hours, rem = divmod(int(uptime.total_seconds()), 3600)
        mins = rem // 60

        text = (
            f"📊 <b>포트폴리오 현황</b> ({mode})\n\n"
            f"💰 잔액: <code>${snapshot.balance:.2f}</code>\n"
            f"📈 수익률(ROI): <code>{roi:+.1%}</code>\n"
            f"📊 거래: <code>{snapshot.total_trades}건</code> "
            f"({snapshot.wins}W / {snapshot.losses}L)\n"
            f"🎯 승률: <code>{snapshot.win_rate:.1%}</code>\n"
            f"📉 총 PnL: <code>${snapshot.total_pnl:+.2f}</code>\n"
            f"📉 최대 낙폭: <code>{snapshot.max_drawdown:.1%}</code>\n"
            f"⏰ 마지막 업데이트: <code>{hours}h {mins}m 전</code>"
        )

        if self._trading_bot and self._trading_bot.is_paused:
            text += f"\n\n⚠️ <b>거래 일시정지 중</b>\n사유: {self._trading_bot.pause_reason}"

        await message.reply_html(text, reply_markup=status_keyboard())

    async def send_history(self, message, limit: int = 5) -> None:
        trades = await self._repo.get_trades(limit=limit)
        if not trades:
            await message.reply_text("아직 거래 내역이 없습니다.")
            return

        lines = [f"📋 <b>최근 거래</b> ({len(trades)}건)\n"]
        for i, t in enumerate(trades, 1):
            kst_time = t.timestamp.astimezone(_KST)
            if t.pnl is not None:
                pnl_str = f"${t.pnl:+.2f}"
                icon = "✅" if t.pnl > 0 else "❌"
                pnl_display = f"PnL: {pnl_str} {icon}"
            else:
                pnl_display = "⏳ 진행중"

            lines.append(
                f"{i}️⃣ {t.direction.value} | ${t.amount:.2f} @ {t.price:.4f}\n"
                f"   {pnl_display} | {kst_time:%m-%d %H:%M}"
            )
        await message.reply_html("\n".join(lines))

    async def send_pnl_menu(self, message) -> None:
        await message.reply_text(
            "📈 기간을 선택하세요:", reply_markup=pnl_period_keyboard()
        )

    async def _fetch_trades_for_period(self, period: str) -> list[Trade]:
        """기간에 해당하는 거래 조회."""
        if period == "today":
            since = datetime.now(_KST).replace(hour=0, minute=0, second=0, microsecond=0)
            return await self._repo.get_trades_since(since.astimezone(timezone.utc))
        elif period in ("7d", "30d"):
            days = 7 if period == "7d" else 30
            since_utc = datetime.now(timezone.utc) - timedelta(days=days)
            return await self._repo.get_trades_since(since_utc)
        return await self._repo.get_resolved_trades()

    async def send_pnl(self, message, period: str) -> None:
        _PERIOD_LABELS = {"today": "오늘", "7d": "7일", "30d": "30일", "all": "전체"}
        label = _PERIOD_LABELS.get(period, period)

        trades = await self._fetch_trades_for_period(period)
        resolved = [t for t in trades if t.resolved]
        pnl = sum(t.pnl for t in resolved if t.pnl is not None)
        wins = sum(1 for t in resolved if (t.pnl or 0) > 0)
        losses = len(resolved) - wins
        total = wins + losses
        win_rate = wins / total if total > 0 else 0.0

        initial = self._trading_bot.config.initial_capital if self._trading_bot else 1000.0
        roi = pnl / initial if initial > 0 else 0.0

        text = (
            f"📈 <b>수익률 리포트</b> ({label})\n\n"
            f"💰 PnL: <code>${pnl:+.2f}</code>\n"
            f"📊 거래: <code>{total}건</code> ({wins}W / {losses}L)\n"
            f"🎯 승률: <code>{win_rate:.1%}</code>\n"
            f"📈 ROI: <code>{roi:+.1%}</code>"
        )
        await message.reply_html(text)

    async def send_chart(self, message) -> None:
        snapshots = await self._repo.get_snapshots(limit=200)
        if len(snapshots) < 2:
            await message.reply_text("📉 차트 데이터 부족 (최소 2개 스냅샷 필요)")
            return

        snapshots.reverse()
        times = [s.timestamp for s in snapshots]
        balances = [s.balance for s in snapshots]

        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(times, balances, color="#2196F3", linewidth=2)
        ax.fill_between(times, balances, alpha=0.1, color="#2196F3")
        ax.set_title("Balance History", fontsize=14)
        ax.set_ylabel("$")
        ax.grid(True, alpha=0.3)
        fig.autofmt_xdate()
        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100)
        buf.seek(0)
        plt.close(fig)

        await message.reply_photo(photo=buf, caption="📉 잔액 추이 차트")

    # ── Command handlers ────────────────────────────────────────────

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "명령어를 선택하세요:", reply_markup=main_keyboard()
        )

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.send_status(update.message)

    async def cmd_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        args = context.args or []
        try:
            limit = int(args[0]) if args else 5
        except ValueError:
            limit = 5
        limit = max(1, min(limit, 50))
        await self.send_history(update.message, limit=limit)

    async def cmd_pnl(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.send_pnl_menu(update.message)

    async def cmd_chart(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.send_chart(update.message)

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """일반 텍스트 메시지 — LLM 기능 비활성화."""
        return

    async def cmd_health(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text("LLM 기능이 비활성화되어 있습니다.")

    async def cmd_review(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text("LLM 기능이 비활성화되어 있습니다.")

    async def cmd_topup(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._trading_bot:
            await update.message.reply_text("봇이 준비되지 않았습니다.")
            return
        args = context.args or []
        if not args:
            await update.message.reply_text("사용법: /topup <금액>")
            return
        try:
            amount = float(args[0])
        except ValueError:
            await update.message.reply_text("유효하지 않은 금액입니다.")
            return
        if amount <= 0:
            await update.message.reply_text("금액은 양수여야 합니다.")
            return
        new_balance = await self._trading_bot.topup(amount)
        await update.message.reply_html(
            f"<b>충전 완료</b>\n+${amount:.2f} → 잔액: ${new_balance:.2f}"
        )

    async def _do_stop(self, message) -> None:
        """정지 로직 (command + callback 공용)."""
        if not self._trading_bot:
            await message.reply_text("봇이 준비되지 않았습니다.")
            return
        if self._trading_bot.is_paused:
            await message.reply_text(
                f"이미 정지 상태입니다.\n사유: {self._trading_bot.pause_reason}"
            )
            return
        self._trading_bot.pause_trading("수동 중지 (/stop)")
        await message.reply_html(
            "🛑 <b>거래 일시정지</b>\n"
            "신규 거래가 중단됩니다.\n"
            "기존 포지션 resolution은 계속 처리됩니다.\n\n"
            "재개: /resume"
        )

    async def _do_resume(self, message) -> None:
        """재개 로직 (command + callback 공용)."""
        if not self._trading_bot:
            await message.reply_text("봇이 준비되지 않았습니다.")
            return
        if not self._trading_bot.is_paused:
            await message.reply_text("현재 거래가 활성화되어 있습니다.")
            return
        self._trading_bot.resume_trading()
        await message.reply_html("▶️ <b>거래 재개</b>\n신규 거래를 다시 실행합니다.")

    async def cmd_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._do_stop(update.message)

    async def cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._do_resume(update.message)

    async def cmd_fix(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text("LLM 기능이 비활성화되어 있습니다.")
