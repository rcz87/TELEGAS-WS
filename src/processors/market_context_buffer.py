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
from typing import Dict, List, Optional

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

    # CVD data
    spot_cvd_direction: str = "UNKNOWN"
    spot_cvd_slope: float = 0.0
    spot_cvd_latest: float = 0.0
    spot_cvd_change: float = 0.0
    futures_cvd_direction: str = "UNKNOWN"
    futures_cvd_slope: float = 0.0
    futures_cvd_latest: float = 0.0
    futures_cvd_change: float = 0.0
    cvd_alignment: str = "NEUTRAL"

    # Whale data
    whale_conflicting: bool = False
    whale_largest_value_usd: float = 0.0
    whale_largest_direction: str = ""
    whale_alignment: str = "NEUTRAL"

    # Orderbook data
    orderbook_bid_vol: float = 0.0
    orderbook_ask_vol: float = 0.0
    orderbook_dominant: str = "UNKNOWN"

    # Per-exchange funding rates
    funding_per_exchange: dict = field(default_factory=dict)

    # Price data
    current_price: float = 0.0
    price_change_24h_pct: float = 0.0
    volume_24h: float = 0.0

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
        self._spot_cvd_buffers: Dict[str, deque] = {}
        self._futures_cvd_buffers: Dict[str, deque] = {}
        self._whale_positions: Dict[str, list] = {}  # symbol -> list of WhaleAlert
        self._orderbook_buffers: Dict[str, deque] = {}
        self._funding_per_exchange: Dict[str, object] = {}  # symbol -> latest FundingPerExchange
        self._price_buffers: Dict[str, deque] = {}
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

    def add_spot_cvd_snapshot(self, snapshot):
        """Add spot CVD snapshot (from rest_poller.CVDSnapshot)."""
        with self._lock:
            if snapshot.symbol not in self._spot_cvd_buffers:
                self._spot_cvd_buffers[snapshot.symbol] = deque(maxlen=self.max_snapshots)
            self._spot_cvd_buffers[snapshot.symbol].append(snapshot)

    def add_futures_cvd_snapshot(self, snapshot):
        """Add futures CVD snapshot (from rest_poller.CVDSnapshot)."""
        with self._lock:
            if snapshot.symbol not in self._futures_cvd_buffers:
                self._futures_cvd_buffers[snapshot.symbol] = deque(maxlen=self.max_snapshots)
            self._futures_cvd_buffers[snapshot.symbol].append(snapshot)

    def get_latest_spot_cvd(self, symbol: str):
        """Get most recent spot CVD snapshot for symbol."""
        with self._lock:
            buf = self._spot_cvd_buffers.get(symbol, deque())
            return buf[-1] if buf else None

    def get_latest_futures_cvd(self, symbol: str):
        """Get most recent futures CVD snapshot for symbol."""
        with self._lock:
            buf = self._futures_cvd_buffers.get(symbol, deque())
            return buf[-1] if buf else None

    def update_whale_positions(self, alerts: list):
        """
        Update whale positions from a list of WhaleAlert objects.

        Replaces the full whale position list per symbol (fresh each poll cycle).
        """
        with self._lock:
            # Group by symbol
            by_symbol: Dict[str, list] = {}
            for alert in alerts:
                by_symbol.setdefault(alert.symbol, []).append(alert)
            # Replace stored positions per symbol
            for symbol, positions in by_symbol.items():
                self._whale_positions[symbol] = positions

    def get_whale_positions(self, symbol: str, min_value_usd: float = 0) -> list:
        """Get whale positions for a symbol, optionally filtered by min value."""
        with self._lock:
            positions = self._whale_positions.get(symbol, [])
            if min_value_usd > 0:
                return [p for p in positions if p.position_value_usd >= min_value_usd]
            return list(positions)

    def add_orderbook_snapshot(self, snapshot):
        """Add orderbook delta snapshot."""
        with self._lock:
            if snapshot.symbol not in self._orderbook_buffers:
                self._orderbook_buffers[snapshot.symbol] = deque(maxlen=self.max_snapshots)
            self._orderbook_buffers[snapshot.symbol].append(snapshot)

    def get_latest_orderbook(self, symbol: str):
        """Get most recent orderbook delta for symbol."""
        with self._lock:
            buf = self._orderbook_buffers.get(symbol, deque())
            return buf[-1] if buf else None

    def update_funding_per_exchange(self, snapshot):
        """Store latest per-exchange funding rates (replaces previous)."""
        with self._lock:
            self._funding_per_exchange[snapshot.symbol] = snapshot

    def get_funding_per_exchange(self, symbol: str):
        """Get per-exchange funding rates."""
        with self._lock:
            return self._funding_per_exchange.get(symbol)

    def add_price_snapshot(self, snapshot):
        """Add price snapshot."""
        with self._lock:
            if snapshot.symbol not in self._price_buffers:
                self._price_buffers[snapshot.symbol] = deque(maxlen=self.max_snapshots)
            self._price_buffers[snapshot.symbol].append(snapshot)

    def get_latest_price(self, symbol: str):
        """Get most recent price snapshot for symbol."""
        with self._lock:
            buf = self._price_buffers.get(symbol, deque())
            return buf[-1] if buf else None

    def get_price_history(self, symbol: str, limit: int = 5) -> list:
        """Get last N price snapshots for symbol."""
        with self._lock:
            buf = list(self._price_buffers.get(symbol, deque()))
            return buf[-limit:] if buf else []

    def get_spot_cvd_history(self, symbol: str, n: int = 6) -> list:
        """Get last N spot CVD snapshots for symbol."""
        with self._lock:
            buf = list(self._spot_cvd_buffers.get(symbol, deque()))
            return buf[-n:] if buf else []

    def get_futures_cvd_history(self, symbol: str, n: int = 6) -> list:
        """Get last N futures CVD snapshots for symbol."""
        with self._lock:
            buf = list(self._futures_cvd_buffers.get(symbol, deque()))
            return buf[-n:] if buf else []

    def get_oi_history(self, symbol: str, n: int = 6) -> list:
        """Get last N OI snapshots for symbol."""
        with self._lock:
            buf = list(self._oi_buffers.get(symbol, deque()))
            return buf[-n:] if buf else []

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

        # CVD data
        spot_cvd = self.get_latest_spot_cvd(symbol)
        futures_cvd = self.get_latest_futures_cvd(symbol)

        spot_cvd_direction = spot_cvd.cvd_direction if spot_cvd else "UNKNOWN"
        spot_cvd_slope = spot_cvd.cvd_slope if spot_cvd else 0.0
        spot_cvd_latest = spot_cvd.cvd_latest if spot_cvd else 0.0
        spot_cvd_change = spot_cvd.cvd_change if spot_cvd else 0.0
        futures_cvd_direction = futures_cvd.cvd_direction if futures_cvd else "UNKNOWN"
        futures_cvd_slope = futures_cvd.cvd_slope if futures_cvd else 0.0
        futures_cvd_latest = futures_cvd.cvd_latest if futures_cvd else 0.0
        futures_cvd_change = futures_cvd.cvd_change if futures_cvd else 0.0

        # Whale data
        whale_positions = self.get_whale_positions(symbol, min_value_usd=1_000_000)
        whale_conflicting = False
        whale_largest_value = 0.0
        whale_largest_dir = ""
        for wp in whale_positions:
            if wp.direction != signal_direction:
                whale_conflicting = True
                if wp.position_value_usd > whale_largest_value:
                    whale_largest_value = wp.position_value_usd
                    whale_largest_dir = wp.direction

        # Orderbook data
        orderbook = self.get_latest_orderbook(symbol)
        ob_bid_vol = orderbook.total_bid_vol if orderbook else 0.0
        ob_ask_vol = orderbook.total_ask_vol if orderbook else 0.0
        ob_dominant = orderbook.dominant_side if orderbook else "UNKNOWN"

        # Per-exchange funding
        fpe = self.get_funding_per_exchange(symbol)
        fpe_rates = fpe.rates if fpe else {}

        # Price data
        price_snap = self.get_latest_price(symbol)
        current_price = price_snap.price if price_snap else 0.0
        price_change_24h = price_snap.change_24h_pct if price_snap else 0.0
        volume_24h = price_snap.volume_24h if price_snap else 0.0

        # Assessments
        funding_alignment = self._assess_funding_alignment(
            current_funding, signal_direction
        )
        oi_alignment = self._assess_oi_alignment(oi_change_1h)
        cvd_alignment = self._assess_cvd_alignment(
            spot_cvd_direction, futures_cvd_direction, signal_direction
        )
        whale_alignment = self._assess_whale_alignment(
            whale_conflicting, whale_largest_value
        )
        combined = self._assess_combined(
            funding_alignment, oi_alignment,
            cvd_alignment=cvd_alignment, whale_alignment=whale_alignment,
        )

        return MarketContext(
            symbol=symbol,
            current_oi_usd=current_oi,
            current_funding_rate=current_funding,
            oi_change_1h_pct=oi_change_1h,
            funding_alignment=funding_alignment,
            oi_alignment=oi_alignment,
            combined_assessment=combined,
            spot_cvd_direction=spot_cvd_direction,
            spot_cvd_slope=spot_cvd_slope,
            spot_cvd_latest=spot_cvd_latest,
            spot_cvd_change=spot_cvd_change,
            futures_cvd_direction=futures_cvd_direction,
            futures_cvd_slope=futures_cvd_slope,
            futures_cvd_latest=futures_cvd_latest,
            futures_cvd_change=futures_cvd_change,
            cvd_alignment=cvd_alignment,
            whale_conflicting=whale_conflicting,
            whale_largest_value_usd=whale_largest_value,
            whale_largest_direction=whale_largest_dir,
            whale_alignment=whale_alignment,
            orderbook_bid_vol=ob_bid_vol,
            orderbook_ask_vol=ob_ask_vol,
            orderbook_dominant=ob_dominant,
            funding_per_exchange=fpe_rates,
            current_price=current_price,
            price_change_24h_pct=price_change_24h,
            volume_24h=volume_24h,
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

    def _assess_cvd_alignment(
        self, spot_dir: str, futures_dir: str, signal_direction: str
    ) -> str:
        """
        Assess CVD alignment with signal direction.

        - LONG + SpotCVD FALLING → VETO
        - SHORT + SpotCVD RISING → VETO
        - Both CVDs aligned with direction → CONFIRMS
        - SpotCVD confirms, futures diverges → PARTIAL
        - No data (UNKNOWN) → NEUTRAL
        """
        if spot_dir == "UNKNOWN":
            return "NEUTRAL"

        # Determine if spot CVD opposes signal
        spot_opposes = (
            (signal_direction == "LONG" and spot_dir == "FALLING") or
            (signal_direction == "SHORT" and spot_dir == "RISING")
        )
        if spot_opposes:
            return "VETO"

        # Spot confirms: check if direction matches signal
        spot_confirms = (
            (signal_direction == "LONG" and spot_dir == "RISING") or
            (signal_direction == "SHORT" and spot_dir == "FALLING")
        )

        if spot_confirms:
            # Check futures alignment
            futures_confirms = (
                (signal_direction == "LONG" and futures_dir == "RISING") or
                (signal_direction == "SHORT" and futures_dir == "FALLING")
            )
            if futures_confirms or futures_dir == "UNKNOWN":
                return "CONFIRMS"
            return "PARTIAL"

        # spot_dir == "FLAT"
        return "NEUTRAL"

    def _assess_whale_alignment(
        self, conflicting: bool, largest_value: float
    ) -> str:
        """
        Assess whale position alignment.

        - No conflict → NEUTRAL
        - Whale ≥$5M conflicting → VETO
        - Whale ≥$1M conflicting → CAUTION
        """
        if not conflicting:
            return "NEUTRAL"
        if largest_value >= 5_000_000:
            return "VETO"
        if largest_value >= 1_000_000:
            return "CAUTION"
        return "NEUTRAL"

    def _assess_combined(
        self, funding_alignment: str, oi_alignment: str,
        cvd_alignment: str = "NEUTRAL", whale_alignment: str = "NEUTRAL",
    ) -> str:
        """
        Combine funding, OI, CVD, and whale assessments into overall verdict.

        Priority:
        1. CVD VETO → UNFAVORABLE
        2. Whale VETO → UNFAVORABLE
        3. CAUTION funding → UNFAVORABLE
        4. CVD CONFIRMS can promote to FAVORABLE
        5. FAVORABLE funding + CONFIRMATION/NEUTRAL OI → FAVORABLE
        6. SQUEEZE_RISK overrides to NEUTRAL at best
        7. Everything else → NEUTRAL
        """
        # CVD VETO is highest priority
        if cvd_alignment == "VETO":
            return "UNFAVORABLE"

        # Whale VETO
        if whale_alignment == "VETO":
            return "UNFAVORABLE"

        if funding_alignment == "CAUTION":
            return "UNFAVORABLE"

        if oi_alignment == "SQUEEZE_RISK":
            return "NEUTRAL"

        if funding_alignment == "FAVORABLE":
            if oi_alignment in ("CONFIRMATION", "NEUTRAL"):
                return "FAVORABLE"
            return "NEUTRAL"  # WEAK OI downgrades favorable funding

        # CVD CONFIRMS can promote neutral context to FAVORABLE
        if cvd_alignment == "CONFIRMS" and oi_alignment != "WEAK":
            return "FAVORABLE"

        # funding_alignment == "NEUTRAL"
        return "NEUTRAL"

    def get_stats(self) -> dict:
        """Get buffer statistics."""
        with self._lock:
            oi_symbols = len(self._oi_buffers)
            funding_symbols = len(self._funding_buffers)
            total_oi = sum(len(b) for b in self._oi_buffers.values())
            total_funding = sum(len(b) for b in self._funding_buffers.values())
            cvd_symbols = len(self._spot_cvd_buffers)
            total_spot_cvd = sum(len(b) for b in self._spot_cvd_buffers.values())
            total_futures_cvd = sum(len(b) for b in self._futures_cvd_buffers.values())
            whale_symbols = len(self._whale_positions)
            total_whales = sum(len(v) for v in self._whale_positions.values())
            total_orderbook = sum(len(b) for b in self._orderbook_buffers.values())
            total_price = sum(len(b) for b in self._price_buffers.values())
            fpe_symbols = len(self._funding_per_exchange)
        return {
            "oi_symbols_tracked": oi_symbols,
            "funding_symbols_tracked": funding_symbols,
            "total_oi_snapshots": total_oi,
            "total_funding_snapshots": total_funding,
            "cvd_symbols_tracked": cvd_symbols,
            "total_spot_cvd_snapshots": total_spot_cvd,
            "total_futures_cvd_snapshots": total_futures_cvd,
            "whale_symbols_tracked": whale_symbols,
            "total_whale_positions": total_whales,
            "total_orderbook_snapshots": total_orderbook,
            "total_price_snapshots": total_price,
            "funding_per_exchange_symbols": fpe_symbols,
        }
