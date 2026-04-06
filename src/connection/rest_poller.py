# CoinGlass REST API Poller - Open Interest & Funding Rate
# Periodic polling for market context data

"""
REST Poller Module

Responsibilities:
- Poll CoinGlass REST API v4 for OI and funding rate data
- Manage polling intervals with jitter to avoid rate limits
- Parse OHLC candlestick responses
- Deliver data via callbacks to buffer/storage layer

CoinGlass REST API v4:
- Base URL: https://open-api-v4.coinglass.com
- Auth: CG-API-KEY header
- GET /api/futures/open-interest/aggregated-history?symbol=BTC&interval=1h&limit=2
- GET /api/futures/funding-rate/oi-weight-history?symbol=BTC&interval=1h&limit=2

Response format (OHLC candles):
{
  "code": "0",
  "msg": "success",
  "data": [
    {"time": 1636588800000, "open": "57158.76", "high": "57158.76", "low": "54806.62", "close": "54806.62"},
    ...
  ]
}
Note: timestamps are in milliseconds, values are strings.
"""

import asyncio
import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import aiohttp

from ..utils.logger import setup_logger


class RateLimiter:
    """
    Token-bucket rate limiter for CoinGlass API.

    Tracks calls in a sliding window and enforces max calls/min.
    When a 429 is received, enters a cooldown period.
    """

    def __init__(self, max_per_minute: int = 90, cooldown_seconds: float = 30.0):
        if max_per_minute <= 0:
            raise ValueError(f"max_per_minute must be > 0, got {max_per_minute}")
        self.max_per_minute = max_per_minute
        self.cooldown_seconds = cooldown_seconds
        self._timestamps: deque = deque()
        self._cooldown_until: float = 0.0
        self._logger = setup_logger("RateLimiter", "INFO")

    async def acquire(self):
        """Wait until a request slot is available."""
        while True:
            now = time.monotonic()

            # Respect 429 cooldown
            if now < self._cooldown_until:
                wait = self._cooldown_until - now
                self._logger.warning(f"Rate limit cooldown: waiting {wait:.1f}s")
                await asyncio.sleep(wait)
                now = time.monotonic()

            # Clean old timestamps (older than 60s)
            while self._timestamps and self._timestamps[0] < now - 60:
                self._timestamps.popleft()

            if len(self._timestamps) < self.max_per_minute:
                self._timestamps.append(now)
                return

            # Wait until oldest entry expires
            wait = 60.0 - (now - self._timestamps[0]) + 0.1
            self._logger.debug(f"Rate limiter: {len(self._timestamps)}/{self.max_per_minute} calls/min, waiting {wait:.1f}s")
            await asyncio.sleep(wait)

    def on_429(self):
        """Called when a 429 response is received. Enters cooldown."""
        self._cooldown_until = time.monotonic() + self.cooldown_seconds
        self._logger.warning(f"429 received! Cooling down for {self.cooldown_seconds}s")

    @property
    def current_usage(self) -> int:
        now = time.monotonic()
        while self._timestamps and self._timestamps[0] < now - 60:
            self._timestamps.popleft()
        return len(self._timestamps)


@dataclass
class OISnapshot:
    """Open Interest data snapshot for a symbol (from OHLC candles)"""
    symbol: str                  # Base symbol, e.g. "BTC"
    current_oi_usd: float        # Most recent OI close value (USD)
    previous_oi_usd: float       # Previous candle OI close (for change calc)
    oi_high_usd: float           # Highest OI in current candle
    oi_low_usd: float            # Lowest OI in current candle
    oi_change_pct: float         # Change % between previous and current close
    timestamp: float = field(default_factory=time.time)


@dataclass
class FundingSnapshot:
    """Funding Rate data snapshot for a symbol (from OI-weighted OHLC)"""
    symbol: str                  # Base symbol, e.g. "BTC"
    current_rate: float          # Most recent funding rate close
    previous_rate: float         # Previous candle funding rate close
    rate_high: float             # Highest rate in current candle
    rate_low: float              # Lowest rate in current candle
    timestamp: float = field(default_factory=time.time)


@dataclass
class CVDSnapshot:
    """Cumulative Volume Delta snapshot (spot or futures)"""
    symbol: str           # "BTC"
    market: str           # "spot" or "futures"
    cvd_values: list      # Last N CVD values (floats)
    cvd_latest: float     # Most recent value
    cvd_change: float     # Net change over window: cvd_values[-1] - cvd_values[0]
    cvd_slope: float      # Linear regression slope (normalized)
    cvd_direction: str    # "RISING" / "FALLING" / "FLAT"
    taker_buy_vol: float = 0.0   # Sum of taker buy volume (last candle)
    taker_sell_vol: float = 0.0  # Sum of taker sell volume (last candle)
    timestamp: float = field(default_factory=time.time)


@dataclass
class WhaleAlert:
    """Hyperliquid whale position alert"""
    symbol: str           # "TAO"
    user: str
    position_size: float  # +long / -short
    position_value_usd: float
    entry_price: float
    liq_price: float
    position_action: int  # 1=open, 2=close
    create_time: int
    direction: str        # "LONG" / "SHORT"
    timestamp: float = field(default_factory=time.time)


@dataclass
class OrderbookDelta:
    """Aggregated orderbook bid/ask imbalance snapshot"""
    symbol: str             # "BTC"
    total_bid_vol: float    # Total bid volume (USD)
    total_ask_vol: float    # Total ask volume (USD)
    delta: float            # bid - ask (positive = buyers dominant)
    dominant_side: str      # "BIDS" / "ASKS" / "BALANCED"
    timestamp: float = field(default_factory=time.time)


@dataclass
class FundingPerExchange:
    """Per-exchange funding rate snapshot"""
    symbol: str
    rates: Dict[str, float]  # {"Binance": -0.00006, "Bybit": -0.00038}
    oi_weighted_avg: float
    timestamp: float = field(default_factory=time.time)


@dataclass
class PriceSnapshot:
    """Current price, 24h change, and volume"""
    symbol: str
    price: float
    price_24h_ago: float
    change_24h_pct: float
    volume_24h: float
    high_24h: float
    low_24h: float
    timestamp: float = field(default_factory=time.time)


class CoinGlassRestPoller:
    """
    Periodically polls CoinGlass REST API v4 for OI and funding rate data.

    Uses aggregated-history endpoints that return OHLC candlestick data.
    We fetch the latest 2 candles (1h interval) to calculate current value
    and recent change.

    Features:
    - Configurable polling interval (default 5 minutes)
    - Batch polling for multiple symbols
    - Rate limit awareness (jitter between requests)
    - Callback-based data delivery
    - Graceful shutdown via shutdown_event
    """

    BASE_URL = "https://open-api-v4.coinglass.com"

    def __init__(
        self,
        api_key: str,
        symbols: List[str],
        poll_interval: int = 300,
        request_delay: float = 0.5,
        on_oi_data: Optional[Callable] = None,
        on_funding_data: Optional[Callable] = None,
        on_spot_cvd_data: Optional[Callable] = None,
        on_futures_cvd_data: Optional[Callable] = None,
        on_whale_data: Optional[Callable] = None,
        on_orderbook_data: Optional[Callable] = None,
        on_funding_per_exchange_data: Optional[Callable] = None,
        on_price_data: Optional[Callable] = None,
        rate_limit_per_minute: int = 90,
    ):
        """
        Initialize REST poller.

        Args:
            api_key: CoinGlass API key
            symbols: Base symbols to poll, e.g. ["BTC", "ETH"]
            poll_interval: Seconds between poll cycles (default 300 = 5 min)
            request_delay: Seconds between per-symbol requests
            on_oi_data: Async callback receiving OISnapshot
            on_funding_data: Async callback receiving FundingSnapshot
            on_spot_cvd_data: Async callback receiving CVDSnapshot (spot)
            on_futures_cvd_data: Async callback receiving CVDSnapshot (futures)
            on_whale_data: Async callback receiving WhaleAlert
            on_orderbook_data: Async callback receiving OrderbookDelta
            on_funding_per_exchange_data: Async callback receiving FundingPerExchange
            on_price_data: Async callback receiving PriceSnapshot
            rate_limit_per_minute: Max API calls per minute (default 90)
        """
        self.api_key = api_key
        self.symbols = list(symbols)
        self.poll_interval = poll_interval
        self.request_delay = request_delay
        self.fast_symbols: List[str] = []
        self.fast_poll_interval: int = 60
        self._on_fast_poll_done: Optional[Callable] = None
        self.on_oi_data = on_oi_data
        self.on_funding_data = on_funding_data
        self.on_spot_cvd_data = on_spot_cvd_data
        self.on_futures_cvd_data = on_futures_cvd_data
        self.on_whale_data = on_whale_data
        self.on_orderbook_data = on_orderbook_data
        self.on_funding_per_exchange_data = on_funding_per_exchange_data
        self.on_price_data = on_price_data
        self.logger = setup_logger("RestPoller", "INFO")
        self._session: Optional[aiohttp.ClientSession] = None
        self._rate_limiter = RateLimiter(
            max_per_minute=rate_limit_per_minute,
            cooldown_seconds=30.0,
        )
        self._stats = {
            "polls_completed": 0,
            "oi_fetches": 0,
            "funding_fetches": 0,
            "spot_cvd_fetches": 0,
            "futures_cvd_fetches": 0,
            "whale_fetches": 0,
            "orderbook_fetches": 0,
            "funding_per_exchange_fetches": 0,
            "price_fetches": 0,
            "errors": 0,
            "rate_limited": 0,
            "last_poll_time": 0,
        }

    # Coins that CoinGlass lists with a "1000" prefix for futures pairs
    _PAIR_PREFIX_MAP = {
        "PEPE": "1000PEPE",
        "BONK": "1000BONK",
        "FLOKI": "1000FLOKI",
        "SHIB": "1000SHIB",
        "LUNC": "1000LUNC",
        "SATS": "1000SATS",
        "RATS": "1000RATS",
        "CAT": "1000CAT",
        "CHEEMS": "1000CHEEMS",
        "MOGUSDT": "1000MOG",
        "WHY": "1000WHY",
        "X": "1000X",
        "APU": "1000APU",
    }

    def _to_pair(self, symbol: str) -> str:
        """Convert base symbol to CoinGlass pair name (e.g. PEPE → 1000PEPEUSDT)."""
        mapped = self._PAIR_PREFIX_MAP.get(symbol, symbol)
        return f"{mapped}USDT"

    async def _rate_limited_get(self, url: str, params: dict = None) -> Optional[dict]:
        """
        Make a rate-limited GET request with 429 retry.

        Returns parsed JSON dict on success, None on failure.
        """
        max_retries = 3
        for attempt in range(max_retries):
            await self._rate_limiter.acquire()
            try:
                async with self._session.get(url, params=params) as resp:
                    if resp.status == 429:
                        self._stats["rate_limited"] += 1
                        self._rate_limiter.on_429()
                        if attempt < max_retries - 1:
                            backoff = (2 ** attempt) * 5  # 5s, 10s, 20s
                            self.logger.warning(
                                f"429 Too Many Requests, retry {attempt + 1}/{max_retries} "
                                f"in {backoff}s"
                            )
                            await asyncio.sleep(backoff)
                            continue
                        self.logger.error("429 exhausted retries")
                        return None
                    if resp.status != 200:
                        # Extract endpoint path for readable logging
                        path = url.replace(self.BASE_URL, "")
                        self.logger.warning(
                            f"HTTP {resp.status} for {path}"
                            f" (params={params})"
                        )
                        self._stats["errors"] += 1
                        return None
                    return await resp.json()
            except Exception as e:
                self.logger.error(f"Request error: {e}")
                return None
        return None

    async def start(self, shutdown_event: asyncio.Event):
        """Main polling loop. Runs until shutdown_event is set."""
        self.logger.info(
            f"REST Poller started: {len(self.symbols)} symbols, "
            f"interval={self.poll_interval}s, "
            f"rate_limit={self._rate_limiter.max_per_minute}/min"
        )
        self._session = aiohttp.ClientSession(
            headers={"CG-API-KEY": self.api_key, "Accept": "application/json"},
            timeout=aiohttp.ClientTimeout(total=30),
        )

        try:
            while not shutdown_event.is_set():
                try:
                    await self._poll_all()
                    self._stats["polls_completed"] += 1
                    self._stats["last_poll_time"] = time.time()
                except Exception as e:
                    self._stats["errors"] += 1
                    self.logger.error(f"Polling cycle error: {e}")

                # Wait for next cycle (interruptible by shutdown)
                jitter = random.uniform(0, 10)
                try:
                    await asyncio.wait_for(
                        shutdown_event.wait(),
                        timeout=self.poll_interval + jitter,
                    )
                    break  # shutdown_event was set
                except asyncio.TimeoutError:
                    pass  # Normal: time to poll again
        finally:
            if self._session and not self._session.closed:
                await self._session.close()
            self.logger.info("REST Poller stopped")

    async def start_fast_poll(self, shutdown_event: asyncio.Event):
        """Fast polling loop for priority coins. Runs alongside main poll loop."""
        if not self.fast_symbols:
            return

        self.logger.info(
            f"Fast Poller started: {self.fast_symbols} every {self.fast_poll_interval}s"
        )

        # Wait for main session to be ready
        for _ in range(30):
            if self._session and not self._session.closed:
                break
            await asyncio.sleep(1)

        while not shutdown_event.is_set():
            try:
                await self._poll_symbols(self.fast_symbols)
                self.logger.debug(f"Fast poll done: {len(self.fast_symbols)} symbols")

                # Notify caller (main.py) to write state immediately
                if self._on_fast_poll_done:
                    try:
                        await self._on_fast_poll_done()
                    except Exception as e:
                        self.logger.error(f"Fast poll callback error: {e}")

            except Exception as e:
                self.logger.error(f"Fast poll error: {e}")

            try:
                await asyncio.wait_for(
                    shutdown_event.wait(),
                    timeout=self.fast_poll_interval,
                )
                break
            except asyncio.TimeoutError:
                pass

        self.logger.info("Fast Poller stopped")

    async def _poll_symbols(self, symbols: List[str]):
        """Poll all data types for a list of symbols."""
        sem = asyncio.Semaphore(3)

        # Global endpoints (whale, funding per exchange) only in main poll
        all_funding_data = None

        async def _fetch_symbol(symbol: str):
            async with sem:
                oi = await self._fetch_oi(symbol)
                if oi and self.on_oi_data:
                    await self.on_oi_data(oi)
                await asyncio.sleep(self.request_delay / 2)

                funding = await self._fetch_funding(symbol)
                if funding and self.on_funding_data:
                    await self.on_funding_data(funding)
                await asyncio.sleep(self.request_delay / 2)

                spot_cvd = await self._fetch_spot_cvd(symbol)
                if spot_cvd and self.on_spot_cvd_data:
                    await self.on_spot_cvd_data(spot_cvd)
                await asyncio.sleep(self.request_delay / 2)

                futures_cvd = await self._fetch_futures_cvd(symbol)
                if futures_cvd and self.on_futures_cvd_data:
                    await self.on_futures_cvd_data(futures_cvd)
                await asyncio.sleep(self.request_delay / 2)

                ob = await self._fetch_orderbook_delta(symbol)
                if ob and self.on_orderbook_data:
                    await self.on_orderbook_data(ob)
                await asyncio.sleep(self.request_delay / 2)

                price = await self._fetch_price(symbol)
                if price and self.on_price_data:
                    await self.on_price_data(price)

        tasks = [asyncio.create_task(_fetch_symbol(s)) for s in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                self.logger.error(f"Fast poll error for {symbols[i]}: {result}")

    async def _poll_all(self):
        """Poll OI, funding, CVD, whale, orderbook, and price for all symbols.

        Uses concurrency-limited batching via asyncio.Semaphore to fetch
        multiple symbols in parallel while respecting API rate limits.
        """
        # Concurrency limit: max simultaneous requests to CoinGlass
        # Keep at 3 to avoid "Too Many Requests" with 80+ symbols
        sem = asyncio.Semaphore(3)

        # ── Phase 1: Global endpoints (not per-symbol) ──────────────
        whale_alerts = await self._fetch_whale_alerts()
        if whale_alerts and self.on_whale_data:
            for alert in whale_alerts:
                await self.on_whale_data(alert)

        all_funding_data = await self._fetch_all_funding_per_exchange()

        # ── Phase 2: Per-symbol fetches (concurrent, rate-limited) ──
        async def _fetch_symbol(symbol: str):
            """Fetch all data types for one symbol, rate-limited by semaphore."""
            async with sem:
                oi = await self._fetch_oi(symbol)
                if oi and self.on_oi_data:
                    await self.on_oi_data(oi)

                await asyncio.sleep(self.request_delay / 2)

                funding = await self._fetch_funding(symbol)
                if funding and self.on_funding_data:
                    await self.on_funding_data(funding)

                await asyncio.sleep(self.request_delay / 2)

                spot_cvd = await self._fetch_spot_cvd(symbol)
                if spot_cvd and self.on_spot_cvd_data:
                    await self.on_spot_cvd_data(spot_cvd)

                await asyncio.sleep(self.request_delay / 2)

                futures_cvd = await self._fetch_futures_cvd(symbol)
                if futures_cvd and self.on_futures_cvd_data:
                    await self.on_futures_cvd_data(futures_cvd)

                await asyncio.sleep(self.request_delay / 2)

                ob = await self._fetch_orderbook_delta(symbol)
                if ob and self.on_orderbook_data:
                    await self.on_orderbook_data(ob)

                # Funding per exchange: from bulk cache (no API call)
                fpe = self._extract_funding_per_exchange(symbol, all_funding_data)
                if fpe and self.on_funding_per_exchange_data:
                    await self.on_funding_per_exchange_data(fpe)

                await asyncio.sleep(self.request_delay / 2)

                price = await self._fetch_price(symbol)
                if price and self.on_price_data:
                    await self.on_price_data(price)

        # Skip fast_symbols in main poll — they're handled by fast poller
        fast_set = set(self.fast_symbols) if self.fast_symbols else set()
        poll_symbols = [s for s in self.symbols if s not in fast_set]

        # Launch all symbols concurrently (semaphore limits to 3 at a time)
        tasks = [asyncio.create_task(_fetch_symbol(s)) for s in poll_symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Log any unexpected exceptions (individual fetch errors already handled)
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                self.logger.error(f"Symbol batch error for {self.symbols[i]}: {result}")

    async def _fetch_oi(self, symbol: str) -> Optional[OISnapshot]:
        """
        Fetch aggregated Open Interest history for a symbol.

        Uses 1h interval, last 2 candles to get current OI and change.
        Response: {"data": [{"time": ms, "open": "...", "high": "...", "low": "...", "close": "..."}, ...]}
        """
        try:
            url = f"{self.BASE_URL}/api/futures/open-interest/aggregated-history"
            params = {"symbol": symbol, "interval": "1h", "limit": 2}
            data = await self._rate_limited_get(url, params)
            if data is None:
                return None

            if str(data.get("code", "")) != "0":
                self.logger.warning(
                    f"OI API error for {symbol}: {data.get('msg', 'unknown')}"
                )
                return None

            candles = data.get("data", [])
            if not candles:
                return None

            # Latest candle is current, previous candle for change calculation
            latest = candles[-1]
            previous = candles[-2] if len(candles) >= 2 else latest

            current_oi = float(latest.get("close", 0))
            previous_oi = float(previous.get("close", 0))
            oi_high = float(latest.get("high", 0))
            oi_low = float(latest.get("low", 0))

            # Calculate change percentage
            change_pct = 0.0
            if previous_oi > 0:
                change_pct = (current_oi - previous_oi) / previous_oi * 100

            self._stats["oi_fetches"] += 1
            return OISnapshot(
                symbol=symbol,
                current_oi_usd=current_oi,
                previous_oi_usd=previous_oi,
                oi_high_usd=oi_high,
                oi_low_usd=oi_low,
                oi_change_pct=change_pct,
            )
        except Exception as e:
            self._stats["errors"] += 1
            self.logger.error(f"OI fetch error for {symbol}: {e}")
            return None

    async def _fetch_funding(self, symbol: str) -> Optional[FundingSnapshot]:
        """
        Fetch OI-weighted funding rate history for a symbol.

        Uses 1h interval, last 2 candles to get current rate and trend.
        Response: {"data": [{"time": ms, "open": "...", "high": "...", "low": "...", "close": "..."}, ...]}
        """
        try:
            url = f"{self.BASE_URL}/api/futures/funding-rate/oi-weight-history"
            params = {"symbol": symbol, "interval": "1h", "limit": 2}
            data = await self._rate_limited_get(url, params)
            if data is None:
                return None

            if str(data.get("code", "")) != "0":
                self.logger.warning(
                    f"Funding API error for {symbol}: {data.get('msg', 'unknown')}"
                )
                return None

            candles = data.get("data", [])
            if not candles:
                return None

            latest = candles[-1]
            previous = candles[-2] if len(candles) >= 2 else latest

            current_rate = float(latest.get("close", 0))
            previous_rate = float(previous.get("close", 0))
            rate_high = float(latest.get("high", 0))
            rate_low = float(latest.get("low", 0))

            self._stats["funding_fetches"] += 1
            return FundingSnapshot(
                symbol=symbol,
                current_rate=current_rate,
                previous_rate=previous_rate,
                rate_high=rate_high,
                rate_low=rate_low,
            )
        except Exception as e:
            self._stats["errors"] += 1
            self.logger.error(f"Funding fetch error for {symbol}: {e}")
            return None

    @staticmethod
    def _calculate_cvd_slope(values: List[float]) -> tuple:
        """
        Linear regression slope on CVD values (last 10 points).

        Returns:
            (slope_normalized, direction) where direction is RISING/FALLING/FLAT.
            Slope is normalized by mean absolute value to make threshold scale-independent.
        """
        n = len(values)
        if n < 2:
            return (0.0, "FLAT")

        # Simple linear regression: slope = Σ((x-x̄)(y-ȳ)) / Σ((x-x̄)²)
        x_mean = (n - 1) / 2.0
        y_mean = sum(values) / n

        numerator = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
        denominator = sum((i - x_mean) ** 2 for i in range(n))

        if denominator == 0:
            return (0.0, "FLAT")

        slope = numerator / denominator

        # Normalize slope by mean absolute value for scale-independent threshold
        mean_abs = sum(abs(v) for v in values) / n if n > 0 else 1.0
        normalized = slope / mean_abs if mean_abs > 0 else 0.0

        if normalized > 0.01:
            direction = "RISING"
        elif normalized < -0.01:
            direction = "FALLING"
        else:
            direction = "FLAT"

        return (normalized, direction)

    async def _fetch_spot_cvd(self, symbol: str) -> Optional[CVDSnapshot]:
        """
        Fetch spot aggregated CVD (across exchanges).

        Endpoint: GET /api/spot/aggregated-cvd/history
        Params: exchange_list, symbol (base coin e.g. BTC), interval, limit
        Response: list of {agg_taker_buy_vol, agg_taker_sell_vol, cum_vol_delta, time}
        """
        try:
            url = f"{self.BASE_URL}/api/spot/aggregated-cvd/history"
            params = {
                "exchange_list": "Binance",
                "symbol": symbol,
                "interval": "5m",
                "limit": 12,
            }
            data = await self._rate_limited_get(url, params)
            if data is None:
                return None

            if str(data.get("code", "")) != "0":
                self.logger.warning(f"SpotCVD API error for {symbol}: {data.get('msg', 'unknown')}")
                return None

            candles = data.get("data", [])
            if not candles:
                return None

            # API provides cum_vol_delta directly
            cvd_values = [float(c.get("cum_vol_delta", 0)) for c in candles]

            # Taker volumes (sum last 3 candles = ~15min window)
            recent = candles[-3:] if len(candles) >= 3 else candles
            tbv = sum(float(c.get("agg_taker_buy_vol", 0)) for c in recent)
            tsv = sum(float(c.get("agg_taker_sell_vol", 0)) for c in recent)

            # Slope on last 10 values
            slope_data = cvd_values[-10:]
            slope, direction = self._calculate_cvd_slope(slope_data)

            # Net change over window
            cvd_change = (cvd_values[-1] - cvd_values[0]) if len(cvd_values) >= 2 else 0.0

            self._stats["spot_cvd_fetches"] += 1
            return CVDSnapshot(
                symbol=symbol,
                market="spot",
                cvd_values=cvd_values,
                cvd_latest=cvd_values[-1] if cvd_values else 0.0,
                cvd_change=cvd_change,
                cvd_slope=slope,
                cvd_direction=direction,
                taker_buy_vol=tbv,
                taker_sell_vol=tsv,
            )
        except Exception as e:
            self._stats["errors"] += 1
            self.logger.error(f"SpotCVD fetch error for {symbol}: {e}")
            return None

    async def _fetch_futures_cvd(self, symbol: str) -> Optional[CVDSnapshot]:
        """
        Fetch futures aggregated CVD (across exchanges).

        Endpoint: GET /api/futures/aggregated-cvd/history
        Params: exchange_list, symbol (base coin e.g. BTC), interval, limit
        Response: list of {agg_taker_buy_vol, agg_taker_sell_vol, cum_vol_delta, time}
        """
        try:
            url = f"{self.BASE_URL}/api/futures/aggregated-cvd/history"
            params = {
                "exchange_list": "Binance",
                "symbol": symbol,
                "interval": "5m",
                "limit": 12,
            }
            data = await self._rate_limited_get(url, params)
            if data is None:
                return None

            if str(data.get("code", "")) != "0":
                self.logger.warning(f"FuturesCVD API error for {symbol}: {data.get('msg', 'unknown')}")
                return None

            candles = data.get("data", [])
            if not candles:
                return None

            # API provides cum_vol_delta directly
            cvd_values = [float(c.get("cum_vol_delta", 0)) for c in candles]

            # Taker volumes (sum last 3 candles = ~15min window)
            recent = candles[-3:] if len(candles) >= 3 else candles
            tbv = sum(float(c.get("agg_taker_buy_vol", 0)) for c in recent)
            tsv = sum(float(c.get("agg_taker_sell_vol", 0)) for c in recent)

            slope_data = cvd_values[-10:]
            slope, direction = self._calculate_cvd_slope(slope_data)

            # Net change over window
            cvd_change = (cvd_values[-1] - cvd_values[0]) if len(cvd_values) >= 2 else 0.0

            self._stats["futures_cvd_fetches"] += 1
            return CVDSnapshot(
                symbol=symbol,
                market="futures",
                cvd_values=cvd_values,
                cvd_latest=cvd_values[-1] if cvd_values else 0.0,
                cvd_change=cvd_change,
                cvd_slope=slope,
                cvd_direction=direction,
                taker_buy_vol=tbv,
                taker_sell_vol=tsv,
            )
        except Exception as e:
            self._stats["errors"] += 1
            self.logger.error(f"FuturesCVD fetch error for {symbol}: {e}")
            return None

    async def _fetch_whale_alerts(self) -> List[WhaleAlert]:
        """
        Fetch Hyperliquid whale position alerts.

        Endpoint: GET /api/hyperliquid/whale-alert
        Filters: position_action == 1 (open positions only).
        """
        try:
            url = f"{self.BASE_URL}/api/hyperliquid/whale-alert"
            data = await self._rate_limited_get(url)
            if data is None:
                return []

            if str(data.get("code", "")) != "0":
                self.logger.warning(f"Whale API error: {data.get('msg', 'unknown')}")
                return []

            raw_alerts = data.get("data", [])
            if not raw_alerts:
                return []

            alerts = []
            for a in raw_alerts:
                # Only open positions (snake_case from API)
                if a.get("position_action") != 1:
                    continue

                pos_size = float(a.get("position_size", 0))
                pos_value = abs(float(a.get("position_value_usd", 0)))
                direction = "LONG" if pos_size > 0 else "SHORT"

                symbol = str(a.get("symbol", "")).upper()

                alerts.append(WhaleAlert(
                    symbol=symbol,
                    user=str(a.get("user", "")),
                    position_size=pos_size,
                    position_value_usd=pos_value,
                    entry_price=float(a.get("entry_price", 0)),
                    liq_price=float(a.get("liq_price", 0)),
                    position_action=1,
                    create_time=int(a.get("create_time", 0)),
                    direction=direction,
                ))

            self._stats["whale_fetches"] += 1
            return alerts

        except Exception as e:
            self._stats["errors"] += 1
            self.logger.error(f"Whale alert fetch error: {e}")
            return []

    async def _fetch_orderbook_delta(self, symbol: str) -> Optional[OrderbookDelta]:
        """
        Fetch aggregated orderbook bid/ask imbalance.

        Endpoint: GET /api/futures/orderbook/aggregated-ask-bids-history
        Params: exchange_list, symbol (base coin e.g. BTC), interval, limit, range
        Response: list of {aggregated_bids_usd, aggregated_asks_usd, time}
        """
        try:
            url = f"{self.BASE_URL}/api/futures/orderbook/aggregated-ask-bids-history"
            params = {
                "exchange_list": "Binance",
                "symbol": symbol,
                "interval": "5m",
                "limit": 1,
                "range": "1",
            }
            data = await self._rate_limited_get(url, params)
            if data is None:
                return None

            if str(data.get("code", "")) != "0":
                self.logger.warning(f"Orderbook API error for {symbol}: {data.get('msg', 'unknown')}")
                return None

            candles = data.get("data", [])
            if not candles:
                return None

            latest = candles[-1]
            bid_vol = float(latest.get("aggregated_bids_usd", 0))
            ask_vol = float(latest.get("aggregated_asks_usd", 0))
            delta = bid_vol - ask_vol

            if bid_vol > ask_vol * 1.1:
                dominant = "BIDS"
            elif ask_vol > bid_vol * 1.1:
                dominant = "ASKS"
            else:
                dominant = "BALANCED"

            self._stats["orderbook_fetches"] += 1
            return OrderbookDelta(
                symbol=symbol,
                total_bid_vol=bid_vol,
                total_ask_vol=ask_vol,
                delta=delta,
                dominant_side=dominant,
            )
        except Exception as e:
            self._stats["errors"] += 1
            self.logger.error(f"Orderbook fetch error for {symbol}: {e}")
            return None

    async def _fetch_all_funding_per_exchange(self) -> list:
        """
        Fetch per-exchange funding rates for ALL symbols (single API call).

        Endpoint: GET /api/futures/funding-rate/exchange-list
        No params — returns all symbols.
        Returns raw data list for use with _extract_funding_per_exchange().
        """
        try:
            url = f"{self.BASE_URL}/api/futures/funding-rate/exchange-list"
            data = await self._rate_limited_get(url)
            if data is None:
                return []

            if str(data.get("code", "")) != "0":
                self.logger.warning(f"FundingPerEx API error: {data.get('msg', 'unknown')}")
                return []

            self._stats["funding_per_exchange_fetches"] += 1
            return data.get("data", [])

        except Exception as e:
            self._stats["errors"] += 1
            self.logger.error(f"FundingPerEx bulk fetch error: {e}")
            return []

    def _extract_funding_per_exchange(self, symbol: str, all_data: list) -> Optional[FundingPerExchange]:
        """Extract per-exchange funding rates for a specific symbol from bulk data."""
        if not all_data:
            return None

        # Find matching symbol
        symbol_data = None
        for item in all_data:
            if item.get("symbol", "").upper() == symbol.upper():
                symbol_data = item
                break

        if not symbol_data:
            return None

        # Parse stablecoin margin list (USDT pairs)
        rates = {}
        for entry in symbol_data.get("stablecoin_margin_list", []):
            ex_name = entry.get("exchange", "")
            rate = float(entry.get("funding_rate", 0))
            # Sanity check: FR should be between -1% and +1% per interval
            # Values like 1.0 or -1.0 are clearly wrong (100%) — skip them
            if ex_name and rate != 0 and abs(rate) < 0.01:
                rates[ex_name] = rate

        if not rates:
            return None

        oi_weighted_avg = sum(rates.values()) / len(rates) if rates else 0.0

        return FundingPerExchange(
            symbol=symbol,
            rates=rates,
            oi_weighted_avg=oi_weighted_avg,
        )

    async def _fetch_price(self, symbol: str) -> Optional[PriceSnapshot]:
        """
        Fetch current price + 24h OHLC.

        Endpoint: GET /api/futures/price/history
        Params: exchange (required), symbol as pair (BTCUSDT), interval, limit
        Response: list of {time, open, high, low, close, volume_usd}
        """
        try:
            url = f"{self.BASE_URL}/api/futures/price/history"
            params = {
                "symbol": self._to_pair(symbol),
                "exchange": "Binance",
                "interval": "1d",
                "limit": 2,
            }
            data = await self._rate_limited_get(url, params)
            if data is None:
                return None

            if str(data.get("code", "")) != "0":
                self.logger.warning(f"Price API error for {symbol}: {data.get('msg', 'unknown')}")
                return None

            candles = data.get("data", [])
            if not candles:
                return None

            latest = candles[-1]
            previous = candles[-2] if len(candles) >= 2 else latest

            price = float(latest.get("close", 0))
            price_24h_ago = float(previous.get("close", 0))
            change_pct = ((price - price_24h_ago) / price_24h_ago * 100) if price_24h_ago > 0 else 0.0
            volume = float(latest.get("volume_usd", 0))
            high = float(latest.get("high", 0))
            low = float(latest.get("low", 0))

            self._stats["price_fetches"] += 1
            return PriceSnapshot(
                symbol=symbol,
                price=price,
                price_24h_ago=price_24h_ago,
                change_24h_pct=change_pct,
                volume_24h=volume,
                high_24h=high,
                low_24h=low,
            )
        except Exception as e:
            self._stats["errors"] += 1
            self.logger.error(f"Price fetch error for {symbol}: {e}")
            return None

    def update_symbols(self, symbols: List[str]):
        """Add newly discovered symbols to poll list."""
        new_symbols = [s for s in symbols if s not in self.symbols]
        if new_symbols:
            self.symbols.extend(new_symbols)
            self.logger.info(
                f"Added {len(new_symbols)} symbols to REST poller: {new_symbols}"
            )

    def get_stats(self) -> dict:
        """Get poller statistics."""
        stats = dict(self._stats)
        stats["rate_limit_usage"] = f"{self._rate_limiter.current_usage}/{self._rate_limiter.max_per_minute}/min"
        return stats
