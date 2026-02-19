from __future__ import annotations

import logging

from src.config import Config
from src.models import (
    Direction,
    PortfolioSnapshot,
    Resolution,
    ResolutionOutcome,
    SignalType,
    Trade,
)
from src.repository.base import Repository

logger = logging.getLogger(__name__)


class Portfolio:
    """Tracks balance, trades, wins/losses, and cumulative PnL."""

    def __init__(self, config: Config, repository: Repository) -> None:
        self._repository = repository
        self._balance = config.initial_capital
        self._initial_capital = config.initial_capital
        self._total_trades = 0
        self._wins = 0
        self._losses = 0
        self._total_pnl = 0.0
        self._peak_balance = config.initial_capital
        self._max_drawdown = 0.0
        self._open_trades: dict[str, Trade] = {}

    @property
    def balance(self) -> float:
        return self._balance

    @property
    def total_trades(self) -> int:
        return self._total_trades

    @property
    def win_rate(self) -> float:
        total = self._wins + self._losses
        return self._wins / total if total > 0 else 0.0

    @property
    def profit_factor(self) -> float:
        if self._losses == 0:
            return float("inf") if self._wins > 0 else 0.0
        return self._wins / self._losses

    @property
    def max_drawdown(self) -> float:
        return self._max_drawdown

    async def restore(self) -> None:
        """Restore state from trades (ground truth) + snapshot (max_drawdown).

        스냅샷 잔액 대신 실제 거래 내역으로 재계산하여 수동 수정 등
        edge case에서도 항상 정확한 상태를 보장한다.
        """
        # 1. 전체 거래 내역으로 잔액·통계 재계산
        all_trades = await self._repository.get_trades(limit=10000)
        open_trades = await self._repository.get_all_open_trades()
        open_ids = {t.trade_id for t in open_trades}

        resolved = [t for t in all_trades if t.resolved and t.pnl is not None]
        open_list = [t for t in all_trades if t.trade_id in open_ids]

        wins   = sum(1 for t in resolved if t.pnl > 0)
        losses = sum(1 for t in resolved if t.pnl <= 0)
        total_pnl = sum(t.pnl for t in resolved)

        # balance = 초기자본 + 확정된 pnl - 미결 포지션 비용
        open_cost = sum(t.amount + t.fee for t in open_list)
        balance = self._initial_capital + total_pnl - open_cost

        self._balance = balance
        self._total_trades = len(resolved)
        self._wins = wins
        self._losses = losses
        self._total_pnl = total_pnl
        self._peak_balance = max(balance, self._initial_capital)

        # 2. max_drawdown은 히스토리 재구성이 복잡 — 스냅샷 값 사용
        snapshot = await self._repository.get_latest_snapshot()
        self._max_drawdown = snapshot.max_drawdown if snapshot else 0.0

        if not all_trades:
            logger.info("No snapshot found — starting fresh")
            return

        logger.info(
            "Restored portfolio — balance=%.2f trades=%d wins=%d losses=%d pnl=%.2f",
            self._balance, self._total_trades, self._wins, self._losses, self._total_pnl,
        )

    async def record_trade(self, trade: Trade) -> None:
        """Record a new trade and persist it."""
        self._total_trades += 1
        self._open_trades[trade.trade_id] = trade
        self._balance -= (trade.amount + trade.fee)
        # Track drawdown at cost-deduction time
        if self._peak_balance > 0:
            dd = (self._peak_balance - self._balance) / self._peak_balance
            if dd > self._max_drawdown:
                self._max_drawdown = dd
        await self._repository.save_trade(trade)
        logger.info(
            "Recorded trade %s — %s %s amount=%.2f price=%.4f",
            trade.trade_id, trade.signal_type.value, trade.direction.value,
            trade.amount, trade.price,
        )

    async def handle_resolution(self, trade: Trade, resolution: Resolution) -> None:
        """Process a market resolution for an open trade."""
        pnl = self._calculate_pnl(trade, resolution)
        trade.pnl = pnl
        trade.resolved = True

        if pnl > 0:
            self._wins += 1
        else:
            self._losses += 1

        self._total_pnl += pnl
        # Engine already deducted (amount + fee). Add back the payout.
        # pnl = payout - amount - fee  →  payout = pnl + amount + fee
        payout = pnl + trade.amount + trade.fee
        self._balance += payout

        # Update drawdown tracking
        if self._balance > self._peak_balance:
            self._peak_balance = self._balance
        drawdown = (self._peak_balance - self._balance) / self._peak_balance
        if drawdown > self._max_drawdown:
            self._max_drawdown = drawdown

        self._open_trades.pop(trade.trade_id, None)
        await self._repository.update_trade_resolution(trade.trade_id, pnl)

        logger.info(
            "Resolved trade %s — pnl=%.2f balance=%.2f w/l=%d/%d",
            trade.trade_id, pnl, self._balance, self._wins, self._losses,
        )

    async def topup(self, amount: float) -> float:
        """Paper balance 충전. 새 잔액 리턴."""
        self._balance += amount
        if self._balance > self._peak_balance:
            self._peak_balance = self._balance
        await self.save_snapshot()
        return self._balance

    async def save_snapshot(self) -> None:
        """Persist current portfolio state."""
        snapshot = PortfolioSnapshot(
            balance=self._balance,
            total_trades=self._total_trades,
            wins=self._wins,
            losses=self._losses,
            total_pnl=self._total_pnl,
            max_drawdown=self._max_drawdown,
        )
        await self._repository.save_portfolio_snapshot(snapshot)
        logger.debug("Portfolio snapshot saved — balance=%.2f", self._balance)

    def _calculate_pnl(self, trade: Trade, resolution: Resolution) -> float:
        """Calculate profit/loss for a resolved trade.

        Balance flow (PaperEngine deducts cost at execution, portfolio returns payout here):
          - Engine: balance -= (amount + fee)
          - Portfolio: balance += payout   (via handle_resolution)
          - Net PnL = payout - amount - fee
        """
        if trade.signal_type == SignalType.ARBITRAGE_BUY:
            # Arbitrage: bought both Up & Down for trade.amount total (half each side).
            # One side resolves to $1/share, the other to $0.
            # Winning side's shares * $1 = payout.
            half = trade.amount / 2
            up_price = trade.price
            down_price = (
                trade.alt_price if trade.alt_price is not None
                else max(1.0 - up_price, 0.01)
            )

            up_shares = half / up_price if up_price > 0 else 0.0
            down_shares = half / down_price if down_price > 0 else 0.0

            if resolution.outcome == ResolutionOutcome.UP:
                payout = up_shares * 1.0
            elif resolution.outcome == ResolutionOutcome.DOWN:
                payout = down_shares * 1.0
            else:
                payout = half  # unknown — conservative estimate

            return payout - trade.amount - trade.fee

        # Directional trade
        won = (
            (trade.direction == Direction.UP and resolution.outcome == ResolutionOutcome.UP)
            or (trade.direction == Direction.DOWN and resolution.outcome == ResolutionOutcome.DOWN)
        )

        if won:
            # Payout is $1 per share; shares = amount / price
            if trade.price <= 0:
                return 0.0
            shares = trade.amount / trade.price
            payout = shares * 1.0
            return payout - trade.amount - trade.fee
        else:
            # Total loss: lose the amount and the fee
            return -(trade.amount + trade.fee)
