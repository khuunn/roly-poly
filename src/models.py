from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class Direction(Enum):
    UP = "Up"
    DOWN = "Down"


class SignalType(Enum):
    BUY_UP = "BUY_UP"
    BUY_DOWN = "BUY_DOWN"
    ARBITRAGE_BUY = "ARBITRAGE_BUY"
    SKIP = "SKIP"


class MarketStatus(Enum):
    PENDING = "pending"
    ACTIVE = "active"
    RESOLVED = "resolved"


class ResolutionOutcome(Enum):
    UP = "Up"
    DOWN = "Down"
    UNKNOWN = "unknown"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Market:
    market_id: str
    slug: str
    question: str
    status: MarketStatus
    up_token_id: str
    down_token_id: str
    end_time: datetime
    up_price: float = 0.5
    down_price: float = 0.5
    resolution: ResolutionOutcome | None = None


@dataclass
class OrderBookLevel:
    price: float
    size: float


@dataclass
class OrderBook:
    token_id: str
    bids: list[OrderBookLevel] = field(default_factory=list)
    asks: list[OrderBookLevel] = field(default_factory=list)

    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None

    @property
    def spread(self) -> float | None:
        if self.best_bid is not None and self.best_ask is not None:
            return self.best_ask - self.best_bid
        return None


@dataclass
class Signal:
    signal_type: SignalType
    direction: Direction | None = None
    confidence: float = 0.0
    reason: str = ""
    timestamp: datetime = field(default_factory=_utcnow)
    arb_down_ask: float | None = None  # down-side ask price (arbitrage only)


@dataclass
class Trade:
    trade_id: str
    market_id: str
    direction: Direction
    token_id: str
    amount: float
    price: float
    fee: float
    timestamp: datetime = field(default_factory=_utcnow)
    signal_type: SignalType = SignalType.BUY_UP
    pnl: float | None = None
    resolved: bool = False
    alt_price: float | None = None  # second side fill price (arbitrage only)
    reason: str = ""  # strategy reason (e.g. ensemble vote details)


@dataclass
class Resolution:
    market_id: str
    outcome: ResolutionOutcome
    timestamp: datetime = field(default_factory=_utcnow)


@dataclass
class PortfolioSnapshot:
    balance: float
    total_trades: int
    wins: int
    losses: int
    total_pnl: float
    max_drawdown: float
    timestamp: datetime = field(default_factory=_utcnow)

    @property
    def win_rate(self) -> float:
        total = self.wins + self.losses
        return self.wins / total if total > 0 else 0.0

    @property
    def profit_factor(self) -> float:
        if self.losses == 0:
            return float("inf") if self.wins > 0 else 0.0
        return self.wins / self.losses
