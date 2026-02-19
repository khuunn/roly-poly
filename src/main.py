"""Polymarket 5-Min BTC Paper Trading Bot — asyncio entrypoint."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.config import Config, DatabaseType, TradingMode
from src.engine.paper import PaperEngine
from src.market_scanner import MarketScanner
from src.models import MarketStatus, SignalType
from src.notifier import TelegramNotifier
from src.orderbook import OrderBookReader
from src.portfolio import Portfolio
from src.price_feed import PriceFeed
from src.repository.sqlite import SQLiteRepository
from src.strategy.arbitrage import ArbitrageStrategy
from src.strategy.directional import DirectionalStrategy
from src.strategy.ensemble import EnsembleStrategy
from src.strategy.orderbook_imbalance import OrderbookImbalanceStrategy

logger = logging.getLogger(__name__)


def build_engine(config: Config):
    if config.trading_mode == TradingMode.PAPER:
        return PaperEngine(config)
    from src.engine.live import LiveEngine
    return LiveEngine(config)


def build_repository(config: Config):
    if config.db_type == DatabaseType.SQLITE:
        return SQLiteRepository(config)
    raise NotImplementedError(f"Database type {config.db_type} not yet implemented")


class TradingBot:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.running = False
        self._trading_paused: bool = False
        self._pause_reason: str = ""

        self.repo = build_repository(config)
        self.engine = build_engine(config)
        self.scanner = MarketScanner(config)
        self.price_feed = PriceFeed(config)
        self.orderbook_reader = OrderBookReader(config)
        self.portfolio = Portfolio(config, self.repo)
        self.notifier = TelegramNotifier(config, self.repo)
        self._orderbook_failures: dict[str, int] = {}

        ensemble = EnsembleStrategy(
            strategies=[
                DirectionalStrategy(),
                OrderbookImbalanceStrategy(config),
            ],
            min_votes=config.ensemble_min_votes,
        )
        self.strategies = [ensemble, ArbitrageStrategy()]
        self.notifier.set_trading_bot(self)

    def pause_trading(self, reason: str) -> None:
        """신규 거래 일시정지. resolution/모니터링은 계속."""
        self._trading_paused = True
        self._pause_reason = reason

    def resume_trading(self) -> None:
        """거래 재개."""
        self._trading_paused = False
        self._pause_reason = ""

    @property
    def is_paused(self) -> bool:
        return self._trading_paused

    @property
    def pause_reason(self) -> str:
        return self._pause_reason

    async def topup(self, amount: float) -> float:
        """Paper balance 충전. Portfolio + Engine 동시 갱신."""
        new_balance = await self.portfolio.topup(amount)
        await self.engine.topup(amount)
        return new_balance

    async def start(self) -> None:
        self.running = True
        logger.info(
            "Starting bot in %s mode | capital=$%.2f | bet=$%.2f",
            self.config.trading_mode.value,
            self.config.initial_capital,
            self.config.bet_size,
        )

        await self.repo.initialize()
        await self.portfolio.restore()
        await self.engine.restore_balance(self.portfolio.balance)
        await self.notifier.start()
        await self.notifier.notify_startup(self.config, self.portfolio.balance)
        await self.price_feed.start()

        background_tasks = [
            asyncio.create_task(self._daily_summary_loop()),
        ]
        try:
            await self._main_loop()
        finally:
            for task in background_tasks:
                task.cancel()
            await self.shutdown()

    async def _daily_summary_loop(self) -> None:
        """매일 자정 KST(15:00 UTC)에 일일 요약 발송."""
        while self.running:
            now = datetime.now(timezone.utc)
            next_run = now.replace(hour=15, minute=0, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=1)
            wait_seconds = (next_run - now).total_seconds()
            await asyncio.sleep(wait_seconds)
            if not self.running:
                break
            await self.portfolio.save_snapshot()
            snapshot = await self.repo.get_latest_snapshot()
            if snapshot:
                await self.notifier.notify_daily_summary(snapshot)

    async def _main_loop(self) -> None:
        while self.running:
            try:
                await self._tick()
            except Exception:
                logger.exception("Error in main loop tick")
                try:
                    await self.notifier.notify_error("Main loop error — check logs")
                except Exception:
                    pass
            await asyncio.sleep(self.config.market_scan_interval)

    async def _tick(self) -> None:
        health = Path("data/health")
        health.parent.mkdir(parents=True, exist_ok=True)
        health.write_text(str(time.time()))

        # 1. Scan for active markets
        await self.scanner.scan_once()
        all_markets = list(self.scanner.markets.values())
        if not all_markets:
            logger.debug("No active 5m BTC markets found")
            return

        # 2. Check resolutions (항상 실행 — 정지 중에도 resolution 처리)
        await self._check_resolutions(all_markets)

        # 3. Circuit breaker check (resolution 처리 후, 신규 거래 전)
        if not self._trading_paused:
            breaker_reason = await self._check_circuit_breaker()
            if breaker_reason:
                self.pause_trading(breaker_reason)
                await self.notifier.notify_circuit_breaker(breaker_reason)
                await self.portfolio.save_snapshot()
                return

        if self._trading_paused:
            return  # 일시정지 중 — 신규 거래 건너뜀

        # 4. Get current BTC price data
        price_history = self.price_feed.price_history
        if len(price_history) < 3:
            logger.debug("Waiting for price history to build up (%d/3)", len(price_history))
            return

        # 5. Evaluate each active market
        for market in all_markets:
            if market.status != MarketStatus.ACTIVE:
                continue
            await self._evaluate_market(market, price_history)

        # 6. Tick 종료 시 스냅샷 저장 — 해소/거래 모두 반영된 최신 상태
        await self.portfolio.save_snapshot()

    async def _evaluate_market(
        self, market, price_history: list[float]
    ) -> None:
        # Skip if we already have an open trade on this market
        open_trades = await self.repo.get_open_trades_for_market(market.market_id)
        if open_trades:
            return

        # Fetch orderbooks
        try:
            up_book, down_book = await self.orderbook_reader.get_both_books(
                market.up_token_id, market.down_token_id
            )
            self._orderbook_failures.pop(market.market_id, None)  # 성공 시 카운터 리셋
        except Exception:
            failures = self._orderbook_failures.get(market.market_id, 0) + 1
            self._orderbook_failures[market.market_id] = failures
            if failures >= 3:
                logger.info(
                    "Market %s 오더북 3회 연속 실패 — 스캐너에서 제거", market.slug
                )
                self.scanner._markets.pop(market.market_id, None)
                self._orderbook_failures.pop(market.market_id, None)
            else:
                logger.warning(
                    "Failed to fetch orderbooks for %s (%d/3)", market.slug, failures
                )
            return

        # Run strategies
        for strategy in self.strategies:
            sig = await strategy.evaluate(market, up_book, down_book, price_history)

            if sig.signal_type == SignalType.SKIP:
                continue

            if sig.confidence < self.config.confidence_threshold:
                logger.info(
                    "%s [%s] 신호 약함 — confidence %.2f < threshold %.2f",
                    strategy.name, market.slug, sig.confidence, self.config.confidence_threshold,
                )
                continue

            # Determine which orderbook to use for execution
            if sig.signal_type == SignalType.BUY_UP:
                book = up_book
            elif sig.signal_type == SignalType.BUY_DOWN:
                book = down_book
            elif sig.signal_type == SignalType.ARBITRAGE_BUY:
                book = up_book  # engine handles both sides internally
            else:
                continue

            # Execute
            trade = await self.engine.execute_order(sig, market, book)
            if trade:
                await self.portfolio.record_trade(trade)
                await self.repo.save_market(market)
                await self.notifier.notify_trade(trade, market.question)
                logger.info(
                    "Executed %s on %s @ $%.4f ($%.2f)",
                    sig.signal_type.value, market.slug, trade.price, trade.amount,
                )
                break  # One trade per market per tick
            else:
                logger.warning(
                    "시그널 발생했으나 체결 실패 [%s] strategy=%s signal=%s confidence=%.2f",
                    market.slug, strategy.name, sig.signal_type.value, sig.confidence,
                )

    async def _check_circuit_breaker(self) -> str | None:
        """리스크 한도 초과 확인. 초과 시 사유 문자열 반환."""
        # 1. Max drawdown 체크
        if self.portfolio.max_drawdown >= self.config.max_drawdown_limit:
            return (
                f"최대 낙폭 한도 초과: "
                f"{self.portfolio.max_drawdown:.1%} ≥ {self.config.max_drawdown_limit:.1%}"
            )

        # 2. 일일 손실 체크
        daily_loss = await self._calculate_daily_loss()
        if daily_loss >= self.config.max_daily_loss:
            return f"일일 손실 한도 초과: ${daily_loss:.2f} ≥ ${self.config.max_daily_loss:.2f}"

        return None

    async def _calculate_daily_loss(self) -> float:
        """오늘 자정(KST) 이후 확정된 손실 합계."""
        kst = timezone(timedelta(hours=9))
        today_start = datetime.now(kst).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).astimezone(timezone.utc)
        trades = await self.repo.get_trades_since(today_start)
        return abs(sum(t.pnl for t in trades if t.resolved and t.pnl and t.pnl < 0))

    async def _check_resolutions(self, markets) -> None:
        for market in markets:
            if market.status != MarketStatus.RESOLVED:
                continue

            open_trades = await self.repo.get_open_trades_for_market(market.market_id)
            if not open_trades:
                continue

            resolution = await self.engine.check_resolution(market)
            if not resolution:
                continue

            for trade in open_trades:
                await self.portfolio.handle_resolution(trade, resolution)
                payout = (trade.pnl or 0) + trade.amount + trade.fee
                await self.engine.credit_resolution_payout(payout)
                await self.notifier.notify_resolution(trade, resolution, market.question)
                logger.info(
                    "Resolved trade %s: PnL=$%.4f", trade.trade_id, trade.pnl or 0.0
                )

            await self.repo.save_market(market)

    async def shutdown(self) -> None:
        self.running = False
        logger.info("Shutting down...")
        await self.portfolio.save_snapshot()
        await self.notifier.stop()
        await self.price_feed.stop()
        await self.orderbook_reader.close()
        await self.repo.close()
        logger.info("Shutdown complete")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


async def main() -> None:
    setup_logging()
    config = Config()
    bot = TradingBot(config)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.shutdown()))

    await bot.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
