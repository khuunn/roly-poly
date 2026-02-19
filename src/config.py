from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class TradingMode(Enum):
    PAPER = "paper"
    LIVE = "live"


class DatabaseType(Enum):
    SQLITE = "sqlite"
    POSTGRES = "postgres"


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    return float(raw) if raw else default


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    return int(raw) if raw else default


@dataclass(frozen=True)
class Config:
    trading_mode: TradingMode = field(
        default_factory=lambda: TradingMode(_env("TRADING_MODE", "paper"))
    )
    db_type: DatabaseType = field(
        default_factory=lambda: DatabaseType(_env("DATABASE_TYPE", "sqlite"))
    )
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL"))

    # Telegram
    telegram_bot_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str = field(default_factory=lambda: _env("TELEGRAM_CHAT_ID"))

    # Live trading
    private_key: str = field(default_factory=lambda: _env("PRIVATE_KEY"))
    funder_address: str = field(default_factory=lambda: _env("FUNDER_ADDRESS"))

    # Strategy
    initial_capital: float = field(
        default_factory=lambda: _env_float("INITIAL_CAPITAL", 1000.0)
    )
    bet_size: float = field(default_factory=lambda: _env_float("BET_SIZE", 10.0))
    max_bet_size: float = field(
        default_factory=lambda: _env_float("MAX_BET_SIZE", 5.0)
    )
    confidence_threshold: float = field(
        default_factory=lambda: _env_float("CONFIDENCE_THRESHOLD", 0.6)
    )

    # Entry price guard
    max_entry_price: float = field(
        default_factory=lambda: _env_float("MAX_ENTRY_PRICE", 0.70)
    )

    # Position sizing
    sizing_mode: str = field(
        default_factory=lambda: _env("SIZING_MODE", "fixed")
    )
    position_size_pct: float = field(
        default_factory=lambda: _env_float("POSITION_SIZE_PCT", 0.02)
    )
    min_bet_size: float = field(
        default_factory=lambda: _env_float("MIN_BET_SIZE", 1.0)
    )

    # Market scanning
    market_scan_interval: int = field(
        default_factory=lambda: _env_int("MARKET_SCAN_INTERVAL", 30)
    )
    price_history_minutes: int = field(
        default_factory=lambda: _env_int("PRICE_HISTORY_MINUTES", 30)
    )

    # Risk management
    max_drawdown_limit: float = field(
        default_factory=lambda: _env_float("MAX_DRAWDOWN_LIMIT", 0.2)
    )
    max_daily_loss: float = field(
        default_factory=lambda: _env_float("MAX_DAILY_LOSS", 50.0)
    )

    # Ensemble
    imbalance_threshold: float = field(
        default_factory=lambda: _env_float("IMBALANCE_THRESHOLD", 1.5)
    )
    ensemble_min_votes: int = field(
        default_factory=lambda: _env_int("ENSEMBLE_MIN_VOTES", 2)
    )

    # Paths
    data_dir: Path = field(default_factory=lambda: Path("data"))

    def __post_init__(self) -> None:
        if self.bet_size > self.max_bet_size:
            object.__setattr__(self, "bet_size", self.max_bet_size)

    @property
    def is_paper(self) -> bool:
        return self.trading_mode == TradingMode.PAPER

    @property
    def sqlite_path(self) -> Path:
        return self.data_dir / "trading.db"
