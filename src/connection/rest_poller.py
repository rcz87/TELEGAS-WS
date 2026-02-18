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
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import aiohttp

from ..utils.logger import setup_logger


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
        """
        self.api_key = api_key
        self.symbols = list(symbols)
        self.poll_interval = poll_interval
        self.request_delay = request_delay
        self.on_oi_data = on_oi_data
        self.on_funding_data = on_funding_data
        self.logger = setup_logger("RestPoller", "INFO")
        self._session: Optional[aiohttp.ClientSession] = None
        self._stats = {
            "polls_completed": 0,
            "oi_fetches": 0,
            "funding_fetches": 0,
            "errors": 0,
            "last_poll_time": 0,
        }

    async def start(self, shutdown_event: asyncio.Event):
        """Main polling loop. Runs until shutdown_event is set."""
        self.logger.info(
            f"REST Poller started: {len(self.symbols)} symbols, "
            f"interval={self.poll_interval}s"
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

    async def _poll_all(self):
        """Poll OI and funding for all symbols."""
        for symbol in self.symbols:
            # Fetch OI
            oi = await self._fetch_oi(symbol)
            if oi and self.on_oi_data:
                await self.on_oi_data(oi)

            await asyncio.sleep(self.request_delay / 2)

            # Fetch Funding
            funding = await self._fetch_funding(symbol)
            if funding and self.on_funding_data:
                await self.on_funding_data(funding)

            # Delay between symbols to respect rate limits
            await asyncio.sleep(self.request_delay)

    async def _fetch_oi(self, symbol: str) -> Optional[OISnapshot]:
        """
        Fetch aggregated Open Interest history for a symbol.

        Uses 1h interval, last 2 candles to get current OI and change.
        Response: {"data": [{"time": ms, "open": "...", "high": "...", "low": "...", "close": "..."}, ...]}
        """
        try:
            url = f"{self.BASE_URL}/api/futures/open-interest/aggregated-history"
            params = {"symbol": symbol, "interval": "1h", "limit": 2}
            async with self._session.get(url, params=params) as resp:
                if resp.status != 200:
                    self.logger.warning(
                        f"OI fetch failed for {symbol}: HTTP {resp.status}"
                    )
                    return None
                data = await resp.json()

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
            async with self._session.get(url, params=params) as resp:
                if resp.status != 200:
                    self.logger.warning(
                        f"Funding fetch failed for {symbol}: HTTP {resp.status}"
                    )
                    return None
                data = await resp.json()

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
        return dict(self._stats)
