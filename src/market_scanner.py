from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

import httpx

from src.config import Config
from src.models import Market, MarketStatus, ResolutionOutcome

logger = logging.getLogger(__name__)


def _parse_outcome_prices(raw_prices) -> list | None:
    """outcomePrices 필드를 파싱하여 리스트로 반환."""
    if not raw_prices:
        return None
    try:
        prices = json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices
        if isinstance(prices, list) and len(prices) >= 2:
            return prices
    except (ValueError, TypeError):
        pass
    return None

GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
_SLUG_PREFIX = "btc-updown-5m-"
_INTERVAL_SECONDS = 300  # 5 minutes
_LOOKBACK_SLOTS = 3  # check current + 2 recent slots
_MAX_RETRIES = 3
_RETRY_BACKOFF = 2.0


class MarketScanner:
    """Discovers 5-min BTC Up/Down markets via timestamp-based event slugs."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._client = httpx.AsyncClient(timeout=15)
        self._markets: dict[str, Market] = {}
        self._running = False
        self._task: asyncio.Task[None] | None = None

    @property
    def markets(self) -> dict[str, Market]:
        return dict(self._markets)

    @property
    def active_markets(self) -> list[Market]:
        return [m for m in self._markets.values() if m.status == MarketStatus.ACTIVE]

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("MarketScanner started (interval=%ds)", self._config.market_scan_interval)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self._client.aclose()
        logger.info("MarketScanner stopped")

    async def scan_once(self) -> list[Market]:
        """Scan current and recent 5-min slots. Returns newly discovered or updated markets."""
        now = int(time.time())
        current_slot = now - (now % _INTERVAL_SECONDS)

        updated: list[Market] = []
        # Check next upcoming slot + current + recent slots
        slots = [current_slot + _INTERVAL_SECONDS]  # next (upcoming, open for trading)
        slots += [current_slot - i * _INTERVAL_SECONDS for i in range(_LOOKBACK_SLOTS)]

        for ts in slots:
            slug = f"{_SLUG_PREFIX}{ts}"
            event = await self._fetch_event(slug)
            if event is None:
                continue

            market = self._parse_event(event)
            if market is None:
                continue

            existing = self._markets.get(market.market_id)
            self._markets[market.market_id] = market

            if existing is None:
                logger.info("New market: %s (%s)", market.question, market.market_id)
                updated.append(market)
            elif existing.status != market.status:
                logger.info(
                    "Market %s: %s -> %s",
                    market.slug, existing.status.value, market.status.value,
                )
                updated.append(market)

        return updated

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await self.scan_once()
            except httpx.HTTPError as exc:
                logger.warning("Market scan HTTP error: %s", exc)
            except Exception:
                logger.exception("Unexpected error during market scan")
            await asyncio.sleep(self._config.market_scan_interval)

    async def _fetch_event(self, slug: str) -> dict | None:
        """Fetch a single event by its exact slug."""

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = await self._client.get(
                    GAMMA_EVENTS_URL, params={"slug": slug}
                )
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, list) and data:
                    return data[0]
                return None
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500:
                    return None
                logger.warning(
                    "Event fetch %s attempt %d/%d failed (HTTP %d)",
                    slug, attempt, _MAX_RETRIES, exc.response.status_code,
                )
            except httpx.HTTPError as exc:
                logger.warning(
                    "Event fetch %s attempt %d/%d failed: %s",
                    slug, attempt, _MAX_RETRIES, exc,
                )

            if attempt < _MAX_RETRIES:
                await asyncio.sleep(_RETRY_BACKOFF ** attempt)

        logger.error("Failed to fetch event %s after %d attempts", slug, _MAX_RETRIES)
        return None

    def _parse_event(self, event: dict) -> Market | None:
        """Parse a Gamma event dict into a Market, using the nested market object."""
        try:
            markets = event.get("markets", [])
            if not markets:
                return None

            raw = markets[0]  # 5m events always have exactly one market
            market_id = str(raw["id"])
            slug = raw.get("slug", event.get("slug", ""))
            question = raw.get("question", event.get("title", slug))

            status = self._determine_status(raw)
            resolution = self._parse_resolution(raw)

            up_token, down_token = self._parse_token_ids(raw)
            if not up_token or not down_token:
                return None

            end_time = self._parse_end_time(raw)

            outcome_prices = raw.get("outcomePrices", "")
            up_price, down_price = self._parse_outcome_prices(outcome_prices)

            return Market(
                market_id=market_id,
                slug=slug,
                question=question,
                status=status,
                up_token_id=up_token,
                down_token_id=down_token,
                end_time=end_time,
                up_price=up_price,
                down_price=down_price,
                resolution=resolution,
            )
        except (KeyError, ValueError) as exc:
            logger.debug("Failed to parse event: %s", exc)
            return None

    def _determine_status(self, raw: dict) -> MarketStatus:
        # outcomePrices of ["1","0"] or ["0","1"] means resolved
        outcome_prices = raw.get("outcomePrices", "")
        if outcome_prices:
            prices = _parse_outcome_prices(outcome_prices)
            if prices:
                vals = [float(p) for p in prices]
                if any(v >= 0.99 for v in vals):
                    return MarketStatus.RESOLVED

        if raw.get("closed", False):
            return MarketStatus.RESOLVED
        if raw.get("active", False) and raw.get("acceptingOrders", False):
            return MarketStatus.ACTIVE
        if raw.get("active", False):
            return MarketStatus.ACTIVE
        return MarketStatus.PENDING

    def _parse_resolution(self, raw: dict) -> ResolutionOutcome | None:
        outcome_prices = raw.get("outcomePrices", "")
        if not outcome_prices:
            return None
        try:
            prices = _parse_outcome_prices(outcome_prices)
            if prices:
                up_val = float(prices[0])
                down_val = float(prices[1])
                if up_val >= 0.99:
                    return ResolutionOutcome.UP
                elif down_val >= 0.99:
                    return ResolutionOutcome.DOWN
            return None
        except (ValueError, TypeError):
            return None

    def _parse_token_ids(self, raw: dict) -> tuple[str, str]:
        clob_ids = raw.get("clobTokenIds", "")
        if isinstance(clob_ids, str):
            try:
                clob_ids = json.loads(clob_ids)
            except (json.JSONDecodeError, ValueError):
                return ("", "")
        if isinstance(clob_ids, list) and len(clob_ids) >= 2:
            return (str(clob_ids[0]), str(clob_ids[1]))
        return ("", "")

    def _parse_end_time(self, raw: dict) -> datetime:
        for key in ("endDate", "end_date_iso"):
            end_str = raw.get(key, "")
            if end_str:
                try:
                    return datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                except ValueError:
                    pass
        return datetime.now(timezone.utc)

    def _parse_outcome_prices(self, outcome_prices: str | list) -> tuple[float, float]:
        if not outcome_prices:
            return (0.5, 0.5)
        prices = _parse_outcome_prices(outcome_prices)
        if prices:
            try:
                return (float(prices[0]), float(prices[1]))
            except (ValueError, TypeError):
                pass
        return (0.5, 0.5)
