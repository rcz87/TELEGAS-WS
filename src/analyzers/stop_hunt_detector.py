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
from datetime import datetime
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
    - $2M+ cascade detection
    - Direction identification
    - Whale absorption detection
    - Confidence scoring
    """
    
    def __init__(self, buffer_manager, threshold: float = 2000000, absorption_threshold: float = 100000):
        """
        Initialize stop hunt detector
        
        Args:
            buffer_manager: BufferManager instance
            threshold: Minimum liquidation volume for cascade (default $2M)
            absorption_threshold: Minimum absorption volume (default $100K)
        """
        self.buffer_manager = buffer_manager
        self.threshold = threshold
        self.absorption_threshold = absorption_threshold
        
        self.logger = setup_logger("StopHuntDetector", "INFO")
        self._detections = 0
        
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
            
            # Step 2: Calculate total volume
            total_volume = self.calculate_total_volume(liquidations)
            
            if total_volume < self.threshold:
                self.logger.debug(
                    f"{symbol}: Volume ${total_volume:,.0f} below threshold ${self.threshold:,.0f}"
                )
                return None
            
            # Step 3: Determine direction
            direction, directional_pct = self.determine_direction(liquidations)
            
            # Step 4: Get price zone
            price_zone = self.get_price_zone(liquidations)
            
            # Step 5: Check for absorption
            # Wait a bit for absorption to happen
            await asyncio.sleep(1)  # Small delay to allow buffer to update
            absorption_volume = await self.check_absorption(symbol, direction, absorption_window)
            absorption_detected = absorption_volume >= self.absorption_threshold
            
            # Step 6: Calculate confidence
            confidence = self.calculate_confidence(
                total_volume=total_volume,
                absorption_volume=absorption_volume,
                directional_pct=directional_pct,
                liquidation_count=len(liquidations)
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
                timestamp=datetime.now().isoformat(),
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
            vol = liq.get("volume_usd", liq.get("vol", 0))
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
            vol = float(liq.get("volume_usd", liq.get("vol", 0)))
            
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
                vol = float(trade.get("volume_usd", trade.get("vol", 0)))
                
                # Only count large orders (>$5K)
                if side == target_side and vol > 5000:
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
        liquidation_count: int
    ) -> float:
        """
        Calculate confidence score (0-99%)
        
        Factors:
        - Total liquidation volume (higher = more significant)
        - Absorption volume (higher = more confident)
        - Directional clarity (one-sided = more confident)
        - Number of liquidations (more = more reliable)
        
        Args:
            total_volume: Total liquidation volume
            absorption_volume: Whale absorption volume
            directional_pct: Percentage in main direction (0.0-1.0)
            liquidation_count: Number of liquidation events
            
        Returns:
            Confidence score (0-99%)
        """
        confidence = 50.0  # Base
        
        # Factor 1: Total volume
        if total_volume > 10_000_000:  # $10M+
            confidence += 25
        elif total_volume > 5_000_000:  # $5M+
            confidence += 20
        elif total_volume > 3_000_000:  # $3M+
            confidence += 15
        elif total_volume > 2_000_000:  # $2M+
            confidence += 10
        
        # Factor 2: Absorption
        if absorption_volume > 1_000_000:  # $1M+
            confidence += 25
        elif absorption_volume > 500_000:  # $500K+
            confidence += 20
        elif absorption_volume > 200_000:  # $200K+
            confidence += 15
        elif absorption_volume > 100_000:  # $100K+
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
