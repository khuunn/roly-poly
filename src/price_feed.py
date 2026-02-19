from __future__ import annotations

import asyncio
import json
import logging
from collections import deque

import websockets

from src.config import Config

logger = logging.getLogger(__name__)

BINANCE_WS_URL = "wss://stream.binance.com:9443/ws/btcusdt@kline_1m"

# Reconnect backoff parameters
_INITIAL_BACKOFF = 1.0
_MAX_BACKOFF = 60.0
_BACKOFF_FACTOR = 2.0


class PriceFeed:
    """Streams real-time BTC/USDT price from Binance via WebSocket.

    Maintains a rolling window of 1-minute candle close prices
    and exposes the latest price for strategy consumption.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._history: deque[float] = deque(maxlen=config.price_history_minutes)
        self._latest: float | None = None
        self._running = False
        self._task: asyncio.Task[None] | None = None

    @property
    def latest_price(self) -> float | None:
        return self._latest

    @property
    def price_history(self) -> list[float]:
        return list(self._history)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._connect_loop())
        logger.info("PriceFeed started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("PriceFeed stopped")

    async def _connect_loop(self) -> None:
        backoff = _INITIAL_BACKOFF

        while self._running:
            try:
                async with websockets.connect(BINANCE_WS_URL) as ws:
                    logger.info("Binance WebSocket connected")
                    backoff = _INITIAL_BACKOFF
                    await self._read_messages(ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not self._running:
                    break
                logger.warning(
                    "WebSocket disconnected: %s — reconnecting in %.1fs", exc, backoff
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * _BACKOFF_FACTOR, _MAX_BACKOFF)

    async def _read_messages(self, ws: websockets.ClientConnection) -> None:
        async for raw in ws:
            if not self._running:
                break
            try:
                msg = json.loads(raw)
                self._handle_kline(msg)
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                logger.debug("Malformed Binance message: %s", exc)

    def _handle_kline(self, msg: dict) -> None:
        kline = msg.get("k")
        if kline is None:
            return

        close_price = float(kline["c"])
        self._latest = close_price

        # Only record the close price when the candle is finalized
        is_closed = kline.get("x", False)
        if is_closed:
            self._history.append(close_price)
            logger.debug(
                "Candle closed: BTC/USDT %.2f (history len=%d)", close_price, len(self._history)
            )
