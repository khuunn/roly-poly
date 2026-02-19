"""Tests for data layer: MarketScanner, OrderBookReader, PriceFeed."""

from __future__ import annotations

import json
from collections import deque

import pytest

from src.config import Config
from src.market_scanner import MarketScanner
from src.models import MarketStatus, ResolutionOutcome
from src.orderbook import OrderBookReader
from src.price_feed import PriceFeed


@pytest.fixture()
def config() -> Config:
    return Config()


@pytest.fixture()
def scanner(config: Config) -> MarketScanner:
    return MarketScanner(config)


@pytest.fixture()
def reader(config: Config) -> OrderBookReader:
    return OrderBookReader(config)


@pytest.fixture()
def feed(config: Config) -> PriceFeed:
    return PriceFeed(config)


# ---------------------------------------------------------------------------
# MarketScanner._parse_event
# ---------------------------------------------------------------------------

class TestParseEvent:
    def test_valid_event(self, scanner: MarketScanner) -> None:
        event = {
            "slug": "btc-updown-5m-1234567890",
            "title": "Bitcoin Up or Down - Test",
            "markets": [{
                "id": 12345,
                "slug": "btc-updown-5m-1234567890",
                "question": "Bitcoin Up or Down - Test",
                "active": True,
                "closed": False,
                "acceptingOrders": True,
                "clobTokenIds": json.dumps(["token-up-abc", "token-down-xyz"]),
                "outcomePrices": json.dumps([0.65, 0.35]),
                "endDate": "2025-01-20T12:30:00Z",
            }],
        }
        market = scanner._parse_event(event)

        assert market is not None
        assert market.market_id == "12345"
        assert market.slug == "btc-updown-5m-1234567890"
        assert market.question == "Bitcoin Up or Down - Test"
        assert market.status == MarketStatus.ACTIVE
        assert market.up_token_id == "token-up-abc"
        assert market.down_token_id == "token-down-xyz"
        assert market.up_price == pytest.approx(0.65)
        assert market.down_price == pytest.approx(0.35)
        assert market.resolution is None

    def test_missing_markets_returns_none(self, scanner: MarketScanner) -> None:
        event = {"slug": "btc-updown-5m-123", "markets": []}
        assert scanner._parse_event(event) is None

    def test_missing_clob_token_ids_returns_none(self, scanner: MarketScanner) -> None:
        event = {
            "markets": [{
                "id": 99,
                "slug": "btc-5m-test",
                "active": True,
                "closed": False,
                "outcomePrices": json.dumps([0.5, 0.5]),
            }],
        }
        assert scanner._parse_event(event) is None

    def test_empty_clob_token_ids_returns_none(self, scanner: MarketScanner) -> None:
        event = {
            "markets": [{
                "id": 100,
                "slug": "btc-5m-test",
                "active": True,
                "closed": False,
                "clobTokenIds": "[]",
                "outcomePrices": json.dumps([0.5, 0.5]),
            }],
        }
        assert scanner._parse_event(event) is None


# ---------------------------------------------------------------------------
# MarketScanner._determine_status
# ---------------------------------------------------------------------------

class TestDetermineStatus:
    def test_active_market(self, scanner: MarketScanner) -> None:
        raw = {"active": True, "closed": False, "outcomePrices": json.dumps([0.6, 0.4])}
        assert scanner._determine_status(raw) == MarketStatus.ACTIVE

    def test_resolved_by_outcome_prices(self, scanner: MarketScanner) -> None:
        raw = {"active": True, "closed": False, "outcomePrices": json.dumps([1.0, 0.0])}
        assert scanner._determine_status(raw) == MarketStatus.RESOLVED

    def test_resolved_by_closed_flag(self, scanner: MarketScanner) -> None:
        raw = {"active": True, "closed": True, "outcomePrices": json.dumps([0.5, 0.5])}
        assert scanner._determine_status(raw) == MarketStatus.RESOLVED

    def test_pending_market(self, scanner: MarketScanner) -> None:
        raw = {"active": False, "closed": False}
        assert scanner._determine_status(raw) == MarketStatus.PENDING


# ---------------------------------------------------------------------------
# MarketScanner._parse_resolution
# ---------------------------------------------------------------------------

class TestParseResolution:
    def test_non_resolved_returns_none(self, scanner: MarketScanner) -> None:
        raw = {"outcomePrices": json.dumps([0.6, 0.4])}
        assert scanner._parse_resolution(raw) is None

    def test_up_resolution(self, scanner: MarketScanner) -> None:
        raw = {"outcomePrices": json.dumps([1.0, 0.0])}
        assert scanner._parse_resolution(raw) == ResolutionOutcome.UP

    def test_down_resolution(self, scanner: MarketScanner) -> None:
        raw = {"outcomePrices": json.dumps([0.0, 1.0])}
        assert scanner._parse_resolution(raw) == ResolutionOutcome.DOWN

    def test_equal_prices_returns_none(self, scanner: MarketScanner) -> None:
        raw = {"outcomePrices": json.dumps([0.5, 0.5])}
        assert scanner._parse_resolution(raw) is None

    def test_missing_outcome_prices_returns_none(self, scanner: MarketScanner) -> None:
        raw = {"outcomePrices": ""}
        assert scanner._parse_resolution(raw) is None


# ---------------------------------------------------------------------------
# MarketScanner._parse_token_ids
# ---------------------------------------------------------------------------

class TestParseTokenIds:
    def test_json_string_format(self, scanner: MarketScanner) -> None:
        raw = {"clobTokenIds": json.dumps(["aaa", "bbb"])}
        assert scanner._parse_token_ids(raw) == ("aaa", "bbb")

    def test_list_format(self, scanner: MarketScanner) -> None:
        raw = {"clobTokenIds": ["up-tok", "down-tok"]}
        assert scanner._parse_token_ids(raw) == ("up-tok", "down-tok")

    def test_invalid_json_returns_empty(self, scanner: MarketScanner) -> None:
        raw = {"clobTokenIds": "not-json"}
        assert scanner._parse_token_ids(raw) == ("", "")

    def test_missing_key_returns_empty(self, scanner: MarketScanner) -> None:
        raw = {}
        assert scanner._parse_token_ids(raw) == ("", "")

    def test_single_element_list_returns_empty(self, scanner: MarketScanner) -> None:
        raw = {"clobTokenIds": json.dumps(["only-one"])}
        assert scanner._parse_token_ids(raw) == ("", "")


# ---------------------------------------------------------------------------
# MarketScanner._parse_outcome_prices
# ---------------------------------------------------------------------------

class TestParseOutcomePrices:
    def test_json_string(self, scanner: MarketScanner) -> None:
        result = scanner._parse_outcome_prices(json.dumps([0.7, 0.3]))
        assert result == pytest.approx((0.7, 0.3))

    def test_list_input(self, scanner: MarketScanner) -> None:
        result = scanner._parse_outcome_prices([0.4, 0.6])
        assert result == pytest.approx((0.4, 0.6))

    def test_empty_string_defaults(self, scanner: MarketScanner) -> None:
        assert scanner._parse_outcome_prices("") == (0.5, 0.5)

    def test_empty_list_defaults(self, scanner: MarketScanner) -> None:
        assert scanner._parse_outcome_prices([]) == (0.5, 0.5)

    def test_invalid_json_defaults(self, scanner: MarketScanner) -> None:
        assert scanner._parse_outcome_prices("bad") == (0.5, 0.5)


# ---------------------------------------------------------------------------
# OrderBookReader._parse
# ---------------------------------------------------------------------------

class TestOrderBookParse:
    def test_parse_bids_and_asks(self, reader: OrderBookReader) -> None:
        data = {
            "bids": [
                {"price": "0.50", "size": "100"},
                {"price": "0.55", "size": "200"},
            ],
            "asks": [
                {"price": "0.60", "size": "50"},
                {"price": "0.58", "size": "150"},
            ],
        }
        book = reader._parse("tok-1", data)

        assert book.token_id == "tok-1"
        assert len(book.bids) == 2
        assert len(book.asks) == 2

    def test_bids_sorted_descending(self, reader: OrderBookReader) -> None:
        data = {
            "bids": [
                {"price": "0.40", "size": "10"},
                {"price": "0.60", "size": "20"},
                {"price": "0.50", "size": "15"},
            ],
            "asks": [],
        }
        book = reader._parse("tok-2", data)
        bid_prices = [lvl.price for lvl in book.bids]
        assert bid_prices == [0.60, 0.50, 0.40]

    def test_asks_sorted_ascending(self, reader: OrderBookReader) -> None:
        data = {
            "bids": [],
            "asks": [
                {"price": "0.70", "size": "30"},
                {"price": "0.58", "size": "10"},
                {"price": "0.65", "size": "20"},
            ],
        }
        book = reader._parse("tok-3", data)
        ask_prices = [lvl.price for lvl in book.asks]
        assert ask_prices == [0.58, 0.65, 0.70]

    def test_best_bid_best_ask_spread(self, reader: OrderBookReader) -> None:
        data = {
            "bids": [
                {"price": "0.45", "size": "100"},
                {"price": "0.50", "size": "200"},
            ],
            "asks": [
                {"price": "0.55", "size": "50"},
                {"price": "0.60", "size": "150"},
            ],
        }
        book = reader._parse("tok-4", data)

        assert book.best_bid == pytest.approx(0.50)
        assert book.best_ask == pytest.approx(0.55)
        assert book.spread == pytest.approx(0.05)

    def test_empty_orderbook(self, reader: OrderBookReader) -> None:
        data = {"bids": [], "asks": []}
        book = reader._parse("tok-5", data)

        assert book.bids == []
        assert book.asks == []
        assert book.best_bid is None
        assert book.best_ask is None
        assert book.spread is None


# ---------------------------------------------------------------------------
# PriceFeed._handle_kline
# ---------------------------------------------------------------------------

class TestPriceFeedHandleKline:
    def test_closed_candle_appends_to_history(self, feed: PriceFeed) -> None:
        msg = {"k": {"c": "67500.25", "x": True}}
        feed._handle_kline(msg)

        assert feed.latest_price == pytest.approx(67500.25)
        assert feed.price_history == [pytest.approx(67500.25)]

    def test_open_candle_updates_latest_not_history(self, feed: PriceFeed) -> None:
        msg = {"k": {"c": "68000.00", "x": False}}
        feed._handle_kline(msg)

        assert feed.latest_price == pytest.approx(68000.0)
        assert feed.price_history == []

    def test_sequence_of_open_then_close(self, feed: PriceFeed) -> None:
        feed._handle_kline({"k": {"c": "67000.00", "x": False}})
        feed._handle_kline({"k": {"c": "67100.00", "x": False}})
        feed._handle_kline({"k": {"c": "67200.00", "x": True}})

        assert feed.latest_price == pytest.approx(67200.0)
        assert len(feed.price_history) == 1
        assert feed.price_history[0] == pytest.approx(67200.0)

    def test_deque_maxlen_respected(self) -> None:
        cfg = Config()
        small_feed = PriceFeed(cfg)
        small_feed._history = deque(maxlen=3)

        for i in range(5):
            small_feed._handle_kline({"k": {"c": str(100 + i), "x": True}})

        assert len(small_feed.price_history) == 3
        expected = [pytest.approx(102), pytest.approx(103), pytest.approx(104)]
        assert small_feed.price_history == expected

    def test_price_history_returns_list_copy(self, feed: PriceFeed) -> None:
        feed._handle_kline({"k": {"c": "50000", "x": True}})
        history = feed.price_history
        history.append(99999)
        assert len(feed.price_history) == 1

    def test_no_kline_key_is_noop(self, feed: PriceFeed) -> None:
        feed._handle_kline({"e": "trade", "p": "67000"})
        assert feed.latest_price is None
        assert feed.price_history == []
