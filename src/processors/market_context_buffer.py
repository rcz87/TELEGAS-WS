# Market Context Buffer - OI & Funding Rate Time-Series Storage
# Thread-safe buffer with trend analysis and context evaluation

"""
Market Context Buffer Module

Responsibilities:
- Store OI and funding rate snapshots per symbol
- Provide time-series comparison (current vs N minutes ago)
- Evaluate market context alignment with signal direction
- Memory management (rolling window)
- Thread-safe access

Assessment Logic:
- Funding alignment: Does funding rate support or oppose the signal?
- OI alignment: Is OI confirming, weak, or at squeeze risk?
- Combined: Overall FAVORABLE / NEUTRAL / UNFAVORABLE verdict
"""

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from ..utils.logger import setup_logger


@dataclass
class MarketContext:
    """Aggregated market context for a symbol at signal evaluation time"""
    symbol: str

    # Current values (from v4 OHLC candle close)
    current_oi_usd: float = 0.0
    current_funding_rate: float = 0.0

    # OI change from latest candle vs previous candle (rest_poller calculates this)
    oi_change_1h_pct: float = 0.0

    # Assessments
    funding_alignment: str = "NEUTRAL"
    oi_alignment: str = "NEUTRAL"
    combined_assessment: str = "NEUTRAL"

    timestamp: float = field(default_factory=time.time)


class MarketContextBuffer:
    """
    Rolling buffer for OI and funding rate snapshots.

    Stores up to max_snapshots per symbol (default 72 = 6 hours at 5-min intervals).
    Provides evaluate_context() to assess market alignment with signal direction.
    """

    def __init__(self, max_snapshots: int = 72):
        """
        Initialize market context buffer.

        Args:
            max_snapshots: Max snapshots per symbol (72 = 6h at 5min intervals)
        """
        self.max_snapshots = max_snapshots
        self._oi_buffers: Dict[str, deque] = {}
        self._funding_buffers: Dict[str, deque] = {}
        self._lock = threading.Lock()
        self.logger = setup_logger("MarketContextBuffer", "INFO")

    def add_oi_snapshot(self, snapshot):
        """Add OI snapshot (from rest_poller.OISnapshot)."""
        with self._lock:
            if snapshot.symbol not in self._oi_buffers:
                self._oi_buffers[snapshot.symbol] = deque(maxlen=self.max_snapshots)
            self._oi_buffers[snapshot.symbol].append(snapshot)

    def add_funding_snapshot(self, snapshot):
        """Add funding snapshot (from rest_poller.FundingSnapshot)."""
        with self._lock:
            if snapshot.symbol not in self._funding_buffers:
                self._funding_buffers[snapshot.symbol] = deque(maxlen=self.max_snapshots)
            self._funding_buffers[snapshot.symbol].append(snapshot)

    def get_latest_oi(self, symbol: str):
        """Get most recent OI snapshot for symbol."""
        with self._lock:
            buf = self._oi_buffers.get(symbol, deque())
            return buf[-1] if buf else None

    def get_latest_funding(self, symbol: str):
        """Get most recent funding snapshot for symbol."""
        with self._lock:
            buf = self._funding_buffers.get(symbol, deque())
            return buf[-1] if buf else None

    def get_oi_at_time(self, symbol: str, seconds_ago: int):
        """Get OI snapshot closest to seconds_ago from now."""
        target_time = time.time() - seconds_ago
        with self._lock:
            buf = list(self._oi_buffers.get(symbol, deque()))
        if not buf:
            return None
        closest = min(buf, key=lambda s: abs(s.timestamp - target_time))
        # Only return if within reasonable freshness (2x poll interval)
        if abs(closest.timestamp - target_time) < 600:
            return closest
        return None

    def get_oi_change_pct(self, symbol: str, seconds_ago: int = 300) -> Optional[float]:
        """
        Calculate OI change percentage between now and seconds_ago.

        Returns None if insufficient data.
        """
        latest = self.get_latest_oi(symbol)
        previous = self.get_oi_at_time(symbol, seconds_ago)
        if not latest or not previous or previous.current_oi_usd == 0:
            return None
        return (latest.current_oi_usd - previous.current_oi_usd) / previous.current_oi_usd * 100

    def evaluate_context(self, symbol: str, signal_direction: str) -> Optional[MarketContext]:
        """
        Evaluate full market context for a symbol + signal direction.

        Args:
            symbol: Base symbol (e.g. "BTC")
            signal_direction: "LONG" or "SHORT"

        Returns:
            MarketContext with assessments, or None if no data available
        """
        latest_oi = self.get_latest_oi(symbol)
        latest_funding = self.get_latest_funding(symbol)

        if not latest_oi and not latest_funding:
            return None  # No data yet

        # Current values from v4 OHLC candle close (with defaults for missing data)
        current_oi = latest_oi.current_oi_usd if latest_oi else 0
        current_funding = latest_funding.current_rate if latest_funding else 0

        # OI change: use pre-calculated change from rest_poller (current vs previous candle)
        oi_change_1h = latest_oi.oi_change_pct if latest_oi else 0

        # Assessments
        funding_alignment = self._assess_funding_alignment(
            current_funding, signal_direction
        )
        oi_alignment = self._assess_oi_alignment(oi_change_1h)
        combined = self._assess_combined(funding_alignment, oi_alignment)

        return MarketContext(
            symbol=symbol,
            current_oi_usd=current_oi,
            current_funding_rate=current_funding,
            oi_change_1h_pct=oi_change_1h,
            funding_alignment=funding_alignment,
            oi_alignment=oi_alignment,
            combined_assessment=combined,
        )

    def _assess_funding_alignment(self, funding_rate: float, direction: str) -> str:
        """
        Assess whether funding rate aligns with signal direction.

        Logic:
        - LONG + positive funding (>0.05%) = longs crowded = CAUTION
        - LONG + negative funding = shorts crowded = FAVORABLE
        - SHORT + negative funding (<-0.05%) = shorts crowded = CAUTION
        - SHORT + positive funding = longs crowded = FAVORABLE
        - Near-zero (|rate| < 0.01%) = NEUTRAL
        """
        if abs(funding_rate) < 0.0001:  # < 0.01%
            return "NEUTRAL"

        if direction == "LONG":
            if funding_rate > 0.0005:   # > 0.05% strongly positive
                return "CAUTION"
            elif funding_rate > 0:
                return "NEUTRAL"
            else:
                return "FAVORABLE"      # Negative = shorts paying
        elif direction == "SHORT":
            if funding_rate < -0.0005:  # < -0.05% strongly negative
                return "CAUTION"
            elif funding_rate < 0:
                return "NEUTRAL"
            else:
                return "FAVORABLE"      # Positive = longs paying

        return "NEUTRAL"

    def _assess_oi_alignment(self, change_1h: float) -> str:
        """
        Assess OI changes.

        Logic:
        - Rising OI > +5% in 1h = SQUEEZE_RISK (extreme buildup)
        - Rising OI > +2% in 1h = CONFIRMATION (positions building)
        - Falling OI < -1% in 1h = WEAK (positions closing)
        - Otherwise = NEUTRAL
        """
        if change_1h > 5.0:
            return "SQUEEZE_RISK"
        elif change_1h > 2.0:
            return "CONFIRMATION"
        elif change_1h < -1.0:
            return "WEAK"
        return "NEUTRAL"

    def _assess_combined(self, funding_alignment: str, oi_alignment: str) -> str:
        """
        Combine funding and OI assessments into overall verdict.

        - CAUTION funding -> UNFAVORABLE
        - FAVORABLE funding + CONFIRMATION/NEUTRAL OI -> FAVORABLE
        - SQUEEZE_RISK overrides to NEUTRAL at best
        - Everything else -> NEUTRAL
        """
        if funding_alignment == "CAUTION":
            return "UNFAVORABLE"

        if oi_alignment == "SQUEEZE_RISK":
            return "NEUTRAL"

        if funding_alignment == "FAVORABLE":
            if oi_alignment in ("CONFIRMATION", "NEUTRAL"):
                return "FAVORABLE"
            return "NEUTRAL"  # WEAK OI downgrades favorable funding

        # funding_alignment == "NEUTRAL"
        return "NEUTRAL"

    def get_stats(self) -> dict:
        """Get buffer statistics."""
        with self._lock:
            oi_symbols = len(self._oi_buffers)
            funding_symbols = len(self._funding_buffers)
            total_oi = sum(len(b) for b in self._oi_buffers.values())
            total_funding = sum(len(b) for b in self._funding_buffers.values())
        return {
            "oi_symbols_tracked": oi_symbols,
            "funding_symbols_tracked": funding_symbols,
            "total_oi_snapshots": total_oi,
            "total_funding_snapshots": total_funding,
        }
