"""Quick E2E test: resolution notification pipeline.

Sends fake 적중 + 빗나감 Telegram alerts without waiting for real market resolution.
Usage: uv run python scripts/test_resolution_e2e.py
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from src.config import Config
from src.models import (
    Direction,
    Resolution,
    ResolutionOutcome,
    SignalType,
    Trade,
)
from src.notifier import TelegramNotifier
from src.repository.sqlite import SQLiteRepository

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")


async def main() -> None:
    config = Config()
    repo = SQLiteRepository(config)
    await repo.initialize()
    notifier = TelegramNotifier(config, repo)

    # Lightweight start — just build bot, skip polling (no command handlers needed)
    if not notifier._enabled:
        print("Telegram not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
        return

    from telegram import Bot
    notifier._bot = Bot(token=notifier._token)

    market_q = "Bitcoin Up or Down - Feb 16, 3:00AM-3:05AM ET"

    # --- 1) Win scenario ---
    win_trade = Trade(
        trade_id="test-win-001",
        market_id="test-mkt-1",
        direction=Direction.UP,
        token_id="tok-up",
        amount=10.0,
        price=0.55,
        fee=0.10,
        signal_type=SignalType.BUY_UP,
        timestamp=datetime(2025, 6, 1, 18, 0, 0, tzinfo=timezone.utc),
        pnl=7.72,  # (10/0.55)*1 - 10 - 0.10
    )
    win_resolution = Resolution(market_id="test-mkt-1", outcome=ResolutionOutcome.UP)

    print("Sending WIN notification...")
    await notifier.notify_resolution(win_trade, win_resolution, market_q)
    print("WIN sent!")

    await asyncio.sleep(1)

    # --- 2) Loss scenario ---
    loss_trade = Trade(
        trade_id="test-loss-002",
        market_id="test-mkt-2",
        direction=Direction.UP,
        token_id="tok-up",
        amount=10.0,
        price=0.60,
        fee=0.10,
        signal_type=SignalType.BUY_UP,
        timestamp=datetime(2025, 6, 1, 18, 5, 0, tzinfo=timezone.utc),
        pnl=-10.10,  # lost everything
    )
    loss_resolution = Resolution(market_id="test-mkt-2", outcome=ResolutionOutcome.DOWN)

    print("Sending LOSS notification...")
    await notifier.notify_resolution(loss_trade, loss_resolution, market_q)
    print("LOSS sent!")

    await asyncio.sleep(1)

    # --- 3) Trade entry notification ---
    entry_trade = Trade(
        trade_id="test-entry-003",
        market_id="test-mkt-3",
        direction=Direction.DOWN,
        token_id="tok-down",
        amount=10.0,
        price=0.45,
        fee=0.10,
        signal_type=SignalType.BUY_DOWN,
        timestamp=datetime.now(timezone.utc),
    )

    print("Sending TRADE ENTRY notification...")
    await notifier.notify_trade(entry_trade, market_q)
    print("TRADE ENTRY sent!")

    await repo.close()
    print("\nAll 3 notifications sent. Check Telegram!")


if __name__ == "__main__":
    asyncio.run(main())
