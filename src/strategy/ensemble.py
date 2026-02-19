from __future__ import annotations

import asyncio
import logging
from collections import Counter

from src.models import Direction, Market, OrderBook, Signal, SignalType
from src.strategy.base import Strategy

logger = logging.getLogger(__name__)


class EnsembleStrategy(Strategy):
    """Aggregates multiple sub-strategies via majority vote."""

    def __init__(self, strategies: list[Strategy], min_votes: int = 2) -> None:
        self._strategies = strategies
        self._min_votes = min_votes

    @property
    def name(self) -> str:
        return "Ensemble"

    async def evaluate(
        self,
        market: Market,
        up_book: OrderBook,
        down_book: OrderBook,
        price_history: list[float],
    ) -> Signal:
        results = await asyncio.gather(
            *[s.evaluate(market, up_book, down_book, price_history) for s in self._strategies],
            return_exceptions=True,
        )

        # Pair each strategy with its signal, handling exceptions
        votes: list[tuple[Strategy, Signal]] = []
        for strategy, result in zip(self._strategies, results):
            if isinstance(result, Exception):
                logger.warning("Strategy %s raised: %s", strategy.name, result)
                continue
            votes.append((strategy, result))

        # Separate non-SKIP signals
        active_votes = [(s, sig) for s, sig in votes if sig.signal_type != SignalType.SKIP]

        # Build reason string showing all votes
        vote_lines = []
        for s, sig in votes:
            if sig.signal_type == SignalType.SKIP:
                vote_lines.append(f"{s.name}: SKIP")
            else:
                direction_label = "UP" if sig.direction == Direction.UP else "DOWN"
                vote_lines.append(f"{s.name}: {direction_label} ({sig.confidence:.2f})")

        if len(active_votes) < self._min_votes:
            active_n, total_n = len(active_votes), len(votes)
            prefix = f"{active_n}/{total_n} active (min {self._min_votes})"
            reason = prefix + " | " + " | ".join(vote_lines)
            logger.info("Ensemble SKIP [%s] — insufficient votes: %s", market.slug, reason)
            return Signal(signal_type=SignalType.SKIP, reason=reason)

        # Count direction votes
        direction_counts: Counter[Direction] = Counter()
        for _, sig in active_votes:
            if sig.direction:
                direction_counts[sig.direction] += 1

        if not direction_counts:
            reason = "no directional votes | " + " | ".join(vote_lines)
            return Signal(signal_type=SignalType.SKIP, reason=reason)

        # Find majority direction
        top_two = direction_counts.most_common(2)
        winner_dir, winner_count = top_two[0]

        # Check for tie
        if len(top_two) > 1 and top_two[0][1] == top_two[1][1]:
            reason = f"tie {top_two[0][1]}v{top_two[1][1]} | " + " | ".join(vote_lines)
            logger.info("Ensemble SKIP [%s] — tie: %s", market.slug, reason)
            return Signal(signal_type=SignalType.SKIP, reason=reason)

        # Calculate average confidence from agreeing signals
        agreeing = [sig for _, sig in active_votes if sig.direction == winner_dir]
        avg_confidence = sum(s.confidence for s in agreeing) / len(agreeing)

        signal_type = SignalType.BUY_UP if winner_dir == Direction.UP else SignalType.BUY_DOWN
        reason = f"{winner_count}/{len(votes)} {winner_dir.value} | " + " | ".join(vote_lines)

        logger.info("Ensemble %s — %s", signal_type.value, reason)
        return Signal(
            signal_type=signal_type,
            direction=winner_dir,
            confidence=avg_confidence,
            reason=reason,
        )
