# Stop Hunt Detector - Detect Liquidation Cascades
# Production-ready stop hunt detection with absorption analysis

"""
Stop Hunt Detector Module

Responsibilities:
- Detect liquidation cascades ($2M+ in 30s)
- Identify direction (SHORT_HUNT or LONG_HUNT)
- Detect whale absorption
- Calculate confidence score
- Emit StopHuntSignal

Algorithm:
1. Monitor liquidations in 30s windows
2. Detect cascade when volume > $2M
3. Determine majority direction (long/short)
4. Check for absorption (large opposite orders)
5. Calculate confidence based on volume & absorption
"""

from typing import Dict, Optional, List, Tuple
from dataclasses import dataclass
from datetime import datetime, timezone
import asyncio

from ..utils.logger import setup_logger

@dataclass
class StopHuntSignal:
    """Stop hunt signal data structure"""
    symbol: str
    total_volume: float
    direction: str  # SHORT_HUNT or LONG_HUNT
    price_zone: Tuple[float, float]
    absorption_detected: bool
    absorption_volume: float
    confidence: float
    timestamp: str
    liquidation_count: int
    directional_percentage: float

class StopHuntDetector:
    """
    Production-ready stop hunt detector

    Detects liquidation cascades and whale absorption patterns
    to identify safe entry points after stop hunts.

    Features:
    - Dynamic tiered thresholds (BTC $2M, mid-caps $200K, small coins $50K)
    - Direction identification
    - Whale absorption detection
    - Confidence scoring
    - All-coin monitoring support
    """

    def __init__(self, buffer_manager, threshold: float = 2000000, absorption_threshold: float = 100000, absorption_min_order_usd: float = 5000, monitoring_config: dict = None):
        """
        Initialize stop hunt detector

        Args:
            buffer_manager: BufferManager instance
            threshold: Default liquidation volume for cascade (default $2M, used for tier1)
            absorption_threshold: Default absorption volume (default $100K, used for tier1)
            absorption_min_order_usd: Minimum single order size for absorption (default $5K)
            monitoring_config: Dynamic monitoring config with per-tier thresholds
        """
        self.buffer_manager = buffer_manager
        self.threshold = threshold
        self.absorption_threshold = absorption_threshold
        self.absorption_min_order_usd = absorption_min_order_usd

        # Tiered thresholds for dynamic all-coin monitoring
        monitoring = monitoring_config or {}
        self._tier1_symbols = set(monitoring.get('tier1_symbols', ['BTCUSDT', 'ETHUSDT']))
        self._tier2_symbols = set(monitoring.get('tier2_symbols', []))
        self._tier1_cascade = monitoring.get('tier1_cascade', threshold)
        self._tier2_cascade = monitoring.get('tier2_cascade', 200_000)
        self._tier3_cascade = monitoring.get('tier3_cascade', 50_000)
        self._tier1_absorption = monitoring.get('tier1_absorption', absorption_threshold)
        self._tier2_absorption = monitoring.get('tier2_absorption', 20_000)
        self._tier3_absorption = monitoring.get('tier3_absorption', 5_000)

        self.logger = setup_logger("StopHuntDetector", "INFO")
        self._detections = 0

    def get_threshold_for_symbol(self, symbol: str) -> tuple:
        """
        Get dynamic cascade and absorption thresholds based on coin tier.

        Tier 1 (BTC, ETH): Highest thresholds - most liquid
        Tier 2 (mid-caps): Medium thresholds
        Tier 3 (small coins): Lowest thresholds - small cascade = significant

        Args:
            symbol: Trading pair symbol

        Returns:
            (cascade_threshold, absorption_threshold) tuple
        """
        if symbol in self._tier1_symbols:
            return (self._tier1_cascade, self._tier1_absorption)
        elif symbol in self._tier2_symbols:
            return (self._tier2_cascade, self._tier2_absorption)
        else:
            return (self._tier3_cascade, self._tier3_absorption)
        
    async def analyze(self, symbol: str, cascade_window: int = 30, absorption_window: int = 30) -> Optional[StopHuntSignal]:
        """
        Analyze liquidations for stop hunt pattern
        
        Algorithm:
        1. Get liquidations in last 30 seconds
        2. Check if total volume > threshold ($2M)
        3. Determine direction (SHORT_HUNT or LONG_HUNT)
        4. Check for absorption in next 30s
        5. Calculate confidence score
        6. Return signal if confident enough
        
        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
            cascade_window: Time window for cascade detection (seconds)
            absorption_window: Time window for absorption detection (seconds)
            
        Returns:
            StopHuntSignal if detected, None otherwise
        """
        try:
            # Step 1: Get recent liquidations
            liquidations = self.buffer_manager.get_liquidations(symbol, time_window=cascade_window)
            
            if not liquidations:
                return None
            
            # Step 2: Calculate total volume with dynamic per-coin threshold
            total_volume = self.calculate_total_volume(liquidations)

            cascade_threshold, absorption_threshold = self.get_threshold_for_symbol(symbol)

            if total_volume < cascade_threshold:
                self.logger.debug(
                    f"{symbol}: Volume ${total_volume:,.0f} below threshold ${cascade_threshold:,.0f}"
                )
                return None

            # Step 3: Determine direction
            direction, directional_pct = self.determine_direction(liquidations)

            # Step 4: Get price zone
            price_zone = self.get_price_zone(liquidations)

            # Step 5: Check for absorption
            # No sleep needed - buffer is updated synchronously
            absorption_volume = await self.check_absorption(symbol, direction, absorption_window)
            absorption_detected = absorption_volume >= absorption_threshold
            
            # Step 6: Calculate confidence (relative to coin's threshold)
            confidence = self.calculate_confidence(
                total_volume=total_volume,
                absorption_volume=absorption_volume,
                directional_pct=directional_pct,
                liquidation_count=len(liquidations),
                cascade_threshold=cascade_threshold
            )
            
            # Create signal
            signal = StopHuntSignal(
                symbol=symbol,
                total_volume=total_volume,
                direction=direction,
                price_zone=price_zone,
                absorption_detected=absorption_detected,
                absorption_volume=absorption_volume,
                confidence=confidence,
                timestamp=datetime.now(timezone.utc).isoformat(),
                liquidation_count=len(liquidations),
                directional_percentage=directional_pct
            )
            
            self._detections += 1
            self.logger.info(
                f"🎯 Stop hunt detected: {symbol} - {direction} - "
                f"${total_volume:,.0f} liquidated - Confidence: {confidence:.0f}%"
            )
            
            return signal
            
        except Exception as e:
            self.logger.error(f"Analysis failed for {symbol}: {e}")
            return None
    
    def calculate_total_volume(self, liquidations: List[dict]) -> float:
        """
        Calculate total liquidation volume
        
        Args:
            liquidations: List of liquidation events
            
        Returns:
            Total volume in USD
        """
        total = 0.0
        for liq in liquidations:
            vol = liq.get("vol", 0)
            total += float(vol)
        return total
    
    def determine_direction(self, liquidations: List[dict]) -> Tuple[str, float]:
        """
        Determine if SHORT_HUNT or LONG_HUNT
        
        Side values:
        - 1 = Long liquidation (longs got stopped = SHORT_HUNT by whales)
        - 2 = Short liquidation (shorts got stopped = LONG_HUNT by whales)
        
        Args:
            liquidations: List of liquidation events
            
        Returns:
            (direction, percentage) e.g., ("SHORT_HUNT", 0.78)
        """
        long_liq_count = 0
        short_liq_count = 0
        long_liq_volume = 0.0
        short_liq_volume = 0.0
        
        for liq in liquidations:
            side = int(liq.get("side", 0))
            vol = float(liq.get("vol", 0))

            if side == 1:  # Long liquidation
                long_liq_count += 1
                long_liq_volume += vol
            elif side == 2:  # Short liquidation
                short_liq_count += 1
                short_liq_volume += vol
        
        total_volume = long_liq_volume + short_liq_volume
        
        if total_volume == 0:
            return "UNKNOWN", 0.5
        
        # If majority are long liquidations, it's a SHORT_HUNT (whales hunting longs)
        # If majority are short liquidations, it's a LONG_HUNT (whales hunting shorts)
        if long_liq_volume > short_liq_volume:
            direction = "SHORT_HUNT"
            percentage = long_liq_volume / total_volume
        else:
            direction = "LONG_HUNT"
            percentage = short_liq_volume / total_volume
        
        return direction, percentage
    
    async def check_absorption(self, symbol: str, hunt_direction: str, time_window: int = 30) -> float:
        """
        Check for whale absorption after stop hunt
        
        After SHORT_HUNT (longs liquidated), look for large BUY orders
        After LONG_HUNT (shorts liquidated), look for large SELL orders
        
        Args:
            symbol: Trading pair
            hunt_direction: "SHORT_HUNT" or "LONG_HUNT"
            time_window: Time window to check for absorption
            
        Returns:
            Total absorption volume in USD
        """
        try:
            # Get recent trades
            trades = self.buffer_manager.get_trades(symbol, time_window=time_window)
            
            if not trades:
                return 0.0
            
            absorption_volume = 0.0
            
            # After SHORT_HUNT, look for BUY orders (side=2)
            # After LONG_HUNT, look for SELL orders (side=1)
            target_side = 2 if hunt_direction == "SHORT_HUNT" else 1
            
            for trade in trades:
                side = int(trade.get("side", 0))
                vol = float(trade.get("vol", 0))

                # Only count large orders (>absorption_min_order_usd)
                if side == target_side and vol > self.absorption_min_order_usd:
                    absorption_volume += vol
            
            return absorption_volume
            
        except Exception as e:
            self.logger.error(f"Absorption check failed: {e}")
            return 0.0
    
    def get_price_zone(self, liquidations: List[dict]) -> Tuple[float, float]:
        """
        Get price zone where liquidations occurred
        
        Args:
            liquidations: List of liquidation events
            
        Returns:
            (min_price, max_price) tuple
        """
        prices = []
        for liq in liquidations:
            price = float(liq.get("price", 0))
            if price > 0:
                prices.append(price)
        
        if not prices:
            return (0.0, 0.0)
        
        return (min(prices), max(prices))
    
    def calculate_confidence(
        self,
        total_volume: float,
        absorption_volume: float,
        directional_pct: float,
        liquidation_count: int,
        cascade_threshold: float = 2_000_000
    ) -> float:
        """
        Calculate confidence score (0-99%)

        Uses volume ratios relative to threshold so small coins
        get fair scoring (e.g. $100K on a $50K-threshold coin
        scores the same as $4M on a $2M-threshold coin).

        Factors:
        - Total liquidation volume relative to threshold
        - Absorption volume relative to cascade
        - Directional clarity (one-sided = more confident)
        - Number of liquidations (more = more reliable)

        Args:
            total_volume: Total liquidation volume
            absorption_volume: Whale absorption volume
            directional_pct: Percentage in main direction (0.0-1.0)
            liquidation_count: Number of liquidation events
            cascade_threshold: The threshold used for this coin tier

        Returns:
            Confidence score (0-99%)
        """
        confidence = 50.0  # Base

        # Factor 1: Volume relative to threshold (works for any coin size)
        volume_ratio = total_volume / max(cascade_threshold, 1)
        if volume_ratio > 5.0:
            confidence += 25
        elif volume_ratio > 2.5:
            confidence += 20
        elif volume_ratio > 1.5:
            confidence += 15
        elif volume_ratio >= 1.0:
            confidence += 10

        # Factor 2: Absorption relative to total volume
        if total_volume > 0:
            absorption_ratio = absorption_volume / total_volume
            if absorption_ratio > 0.3:
                confidence += 25
            elif absorption_ratio > 0.2:
                confidence += 20
            elif absorption_ratio > 0.1:
                confidence += 15
            elif absorption_ratio > 0.05:
                confidence += 10

        # Factor 3: Directional clarity
        if directional_pct > 0.9:  # >90% one direction
            confidence += 15
        elif directional_pct > 0.8:  # >80%
            confidence += 12
        elif directional_pct > 0.7:  # >70%
            confidence += 8

        # Factor 4: Liquidation count
        if liquidation_count > 100:
            confidence += 5
        elif liquidation_count > 50:
            confidence += 3

        return min(confidence, 99.0)  # Cap at 99%
    
    def get_stats(self) -> dict:
        """Get detector statistics"""
        return {
            "total_detections": self._detections,
            "threshold": self.threshold,
            "absorption_threshold": self.absorption_threshold
        }
