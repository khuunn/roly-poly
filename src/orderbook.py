from __future__ import annotations

import logging

import httpx

from src.config import Config
from src.models import OrderBook, OrderBookLevel

logger = logging.getLogger(__name__)

CLOB_BOOK_URL = "https://clob.polymarket.com/book"
MAX_RETRIES = 3


class OrderBookReader:
    """Fetches and parses orderbook data from the Polymarket CLOB API."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._client = httpx.AsyncClient(timeout=10)

    async def close(self) -> None:
        await self._client.aclose()

    async def get_orderbook(self, token_id: str) -> OrderBook:
        """Fetch the orderbook for a given token. Retries on transient failures."""
        last_exc: Exception | None = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = await self._client.get(CLOB_BOOK_URL, params={"token_id": token_id})
                resp.raise_for_status()
                data = resp.json()
                return self._parse(token_id, data)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500:
                    raise
                last_exc = exc
                logger.warning(
                    "Orderbook fetch attempt %d/%d failed (HTTP %d)",
                    attempt,
                    MAX_RETRIES,
                    exc.response.status_code,
                )
            except httpx.HTTPError as exc:
                last_exc = exc
                logger.warning(
                    "Orderbook fetch attempt %d/%d failed: %s", attempt, MAX_RETRIES, exc
                )

        raise RuntimeError(
            f"Failed to fetch orderbook for {token_id} after {MAX_RETRIES} attempts"
        ) from last_exc

    async def get_both_books(
        self, up_token_id: str, down_token_id: str
    ) -> tuple[OrderBook, OrderBook]:
        """Fetch orderbooks for both the Up and Down tokens."""
        import asyncio

        up_book, down_book = await asyncio.gather(
            self.get_orderbook(up_token_id),
            self.get_orderbook(down_token_id),
        )
        return up_book, down_book

    def _parse(self, token_id: str, data: dict) -> OrderBook:
        bids = self._parse_levels(data.get("bids", []))
        asks = self._parse_levels(data.get("asks", []))

        # Sort bids descending by price, asks ascending by price
        bids.sort(key=lambda lvl: lvl.price, reverse=True)
        asks.sort(key=lambda lvl: lvl.price)

        return OrderBook(token_id=token_id, bids=bids, asks=asks)

    def _parse_levels(self, raw_levels: list[dict]) -> list[OrderBookLevel]:
        levels: list[OrderBookLevel] = []
        for entry in raw_levels:
            try:
                price = float(entry.get("price", 0))
                size = float(entry.get("size", 0))
                levels.append(OrderBookLevel(price=price, size=size))
            except (ValueError, TypeError) as exc:
                logger.debug("Skipping malformed orderbook level: %s — %s", entry, exc)
        return levels
