from __future__ import annotations

import logging
import uuid

from src.config import Config
from src.engine.base import ExecutionEngine
from src.models import (
    Direction,
    Market,
    MarketStatus,
    OrderBook,
    Resolution,
    Signal,
    SignalType,
    Trade,
)

logger = logging.getLogger(__name__)

_SLIPPAGE = 0.005  # 0.5%
_TAKER_FEE_RATE = 0.01  # 1%


def _trade_id() -> str:
    return uuid.uuid4().hex[:8]


class PaperEngine(ExecutionEngine):
    """Simulated execution engine for paper trading."""

    def __init__(self, config: Config) -> None:
        self._balance = config.initial_capital
        self._bet_size = config.bet_size
        self._max_bet_size = config.max_bet_size
        self._sizing_mode = config.sizing_mode
        self._position_size_pct = config.position_size_pct
        self._min_bet_size = config.min_bet_size
        self._max_entry_price = config.max_entry_price

    async def execute_order(
        self, signal: Signal, market: Market, orderbook: OrderBook
    ) -> Trade | None:
        if signal.signal_type == SignalType.SKIP:
            return None

        if signal.signal_type == SignalType.ARBITRAGE_BUY:
            return await self._execute_arbitrage(signal, market, orderbook)

        return await self._execute_directional(signal, market, orderbook)

    async def get_balance(self) -> float:
        return self._balance

    async def check_resolution(self, market: Market) -> Resolution | None:
        if market.status != MarketStatus.RESOLVED or market.resolution is None:
            return None
        return Resolution(market_id=market.market_id, outcome=market.resolution)

    async def credit_resolution_payout(self, payout: float) -> None:
        self._balance += payout

    async def topup(self, amount: float) -> None:
        self._balance += amount
        logger.info("Paper engine topped up: +%.2f → balance=%.2f", amount, self._balance)

    async def restore_balance(self, balance: float) -> None:
        logger.info("Restored engine balance: %.2f → %.2f", self._balance, balance)
        self._balance = balance

    # ------------------------------------------------------------------

    def _calculate_bet_size(self, confidence: float) -> float:
        """동적 사이징: 잔액 비율 × confidence 스케일링."""
        if self._sizing_mode != "dynamic":
            return self._bet_size

        base = self._balance * self._position_size_pct
        scale = 0.5 + 0.5 * confidence  # confidence [0.6~1.0] → scale [0.8~1.0]
        sized = base * scale
        return max(self._min_bet_size, min(sized, self._max_bet_size))

    async def _execute_directional(
        self, signal: Signal, market: Market, orderbook: OrderBook
    ) -> Trade | None:
        ask = orderbook.best_ask
        if ask is None:
            logger.warning("No ask price available for %s", market.market_id)
            return None

        if ask > self._max_entry_price:
            logger.info(
                "Ask %.4f exceeds max entry price %.2f — skipping",
                ask, self._max_entry_price,
            )
            return None

        fill_price = ask * (1 + _SLIPPAGE)
        fill_price = min(fill_price, 1.0)  # price cannot exceed 1.0
        bet_size = self._calculate_bet_size(signal.confidence)
        fee = bet_size * _TAKER_FEE_RATE
        total_cost = bet_size + fee

        if total_cost > self._balance:
            logger.warning(
                "Insufficient balance: need %.2f, have %.2f", total_cost, self._balance
            )
            return None

        self._balance -= total_cost

        direction = Direction.UP if signal.signal_type == SignalType.BUY_UP else Direction.DOWN
        token_id = (
            market.up_token_id if direction == Direction.UP else market.down_token_id
        )

        trade = Trade(
            trade_id=_trade_id(),
            market_id=market.market_id,
            direction=direction,
            token_id=token_id,
            amount=bet_size,
            price=fill_price,
            fee=fee,
            signal_type=signal.signal_type,
            reason=signal.reason,
        )
        logger.info(
            "Paper trade %s — %s %.2f @ %.4f (fee %.2f) balance=%.2f",
            trade.trade_id, direction.value, bet_size, fill_price, fee, self._balance,
        )
        return trade

    async def _execute_arbitrage(
        self, signal: Signal, market: Market, orderbook: OrderBook
    ) -> Trade | None:
        """Execute arbitrage: buy both sides. Returns the UP-side trade."""
        up_ask = orderbook.best_ask
        if up_ask is None:
            logger.warning("No ask price for UP side — skipping arbitrage")
            return None

        if up_ask > self._max_entry_price:
            logger.info(
                "Up ask %.4f exceeds max entry price %.2f — skipping arb",
                up_ask, self._max_entry_price,
            )
            return None

        single_bet = self._calculate_bet_size(signal.confidence)
        fee_per_side = single_bet * _TAKER_FEE_RATE
        total_cost = (single_bet + fee_per_side) * 2  # both sides

        if total_cost > self._balance:
            logger.warning(
                "Insufficient balance for arb: need %.2f, have %.2f",
                total_cost, self._balance,
            )
            return None

        self._balance -= total_cost

        fill_price_up = up_ask * (1 + _SLIPPAGE)
        fill_price_up = min(fill_price_up, 1.0)

        # Down-side fill price from signal (set by ArbitrageStrategy)
        down_ask = signal.arb_down_ask or (1.0 - up_ask)
        fill_price_down = down_ask * (1 + _SLIPPAGE)
        fill_price_down = min(fill_price_down, 1.0)

        trade = Trade(
            trade_id=_trade_id(),
            market_id=market.market_id,
            direction=Direction.UP,
            token_id=market.up_token_id,
            amount=single_bet * 2,  # combined amount for both sides
            price=fill_price_up,
            fee=fee_per_side * 2,
            signal_type=SignalType.ARBITRAGE_BUY,
            alt_price=fill_price_down,
            reason=signal.reason,
        )
        logger.info(
            "Paper arb %s — bought both sides, %.2f total (fee %.2f) balance=%.2f",
            trade.trade_id, single_bet * 2, fee_per_side * 2, self._balance,
        )
        return trade
