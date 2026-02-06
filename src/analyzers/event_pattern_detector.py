# Event Pattern Detector - Market Event Detection
# Production-ready event pattern detection

"""
Event Pattern Detector Module

Responsibilities:
- Detect liquidation cascades
- Detect whale accumulation windows  
- Detect volume spikes
- Detect price anomalies
- Emit EventSignal

Algorithm:
1. Monitor multiple event types simultaneously
2. Detect patterns in real-time
3. Calculate confidence for each pattern
4. Return signals for detected events
"""

from typing import List, Optional
from dataclasses import dataclass
from datetime import datetime, timezone

from ..utils.logger import setup_logger

@dataclass
class EventSignal:
    """Event signal data structure"""
    event_type: str
    symbol: str
    description: str
    confidence: float
    timestamp: str
    data: dict

class EventPatternDetector:
    """
    Production-ready event pattern detector
    
    Detects various market events and patterns that
    may indicate important market moments.
    
    Features:
    - Liquidation cascade detection
    - Whale accumulation windows
    - Volume spike detection
    - Multiple pattern types
    """
    
    def __init__(self, buffer_manager, monitoring_config: dict = None,
                 large_order_threshold: float = 10_000):
        """
        Initialize event pattern detector

        Args:
            buffer_manager: BufferManager instance
            monitoring_config: Dynamic monitoring config with per-tier thresholds
            large_order_threshold: Min volume for whale order detection (default $10K)
        """
        self.buffer_manager = buffer_manager
        self.large_order_threshold = large_order_threshold
        self.logger = setup_logger("EventPatternDetector", "INFO")
        self._detections = 0

        # Tiered thresholds for dynamic all-coin monitoring
        monitoring = monitoring_config or {}
        self._tier1_symbols = set(monitoring.get('tier1_symbols', ['BTCUSDT', 'ETHUSDT']))
        self._tier2_symbols = set(monitoring.get('tier2_symbols', []))
        self._tier1_cascade = monitoring.get('tier1_cascade', 2_000_000)
        self._tier2_cascade = monitoring.get('tier2_cascade', 200_000)
        self._tier3_cascade = monitoring.get('tier3_cascade', 50_000)

    def get_threshold_for_symbol(self, symbol: str) -> float:
        """Get dynamic cascade threshold based on coin tier."""
        if symbol in self._tier1_symbols:
            return self._tier1_cascade
        elif symbol in self._tier2_symbols:
            return self._tier2_cascade
        else:
            return self._tier3_cascade

    async def detect_liquidation_cascade(self, symbol: str, threshold: float = None) -> Optional[EventSignal]:
        """
        Detect liquidation cascade event
        
        Criteria:
        - $2M+ liquidations in 30 seconds
        - Rapid liquidation sequence
        
        Args:
            symbol: Trading pair
            threshold: Minimum volume for cascade (default $2M)
            
        Returns:
            EventSignal if detected, None otherwise
        """
        try:
            # Use dynamic per-coin threshold if not explicitly passed
            if threshold is None:
                threshold = self.get_threshold_for_symbol(symbol)

            liquidations = self.buffer_manager.get_liquidations(symbol, time_window=30)

            if not liquidations:
                return None

            total_volume = sum(float(liq.get("vol", 0)) for liq in liquidations)

            if total_volume < threshold:
                return None

            # Calculate confidence based on volume ratio to threshold
            volume_ratio = total_volume / max(threshold, 1)
            if volume_ratio > 5.0:
                confidence = 95.0
            elif volume_ratio > 2.5:
                confidence = 85.0
            elif volume_ratio > 1.5:
                confidence = 75.0
            else:
                confidence = 65.0
            
            description = (
                f"Liquidation cascade detected: ${total_volume/1_000_000:.1f}M "
                f"in {len(liquidations)} events over 30 seconds"
            )
            
            signal = EventSignal(
                event_type="LIQUIDATION_CASCADE",
                symbol=symbol,
                description=description,
                confidence=confidence,
                timestamp=datetime.now(timezone.utc).isoformat(),
                data={
                    "total_volume": total_volume,
                    "liquidation_count": len(liquidations),
                    "time_window": 30
                }
            )
            
            self._detections += 1
            self.logger.info(f"⚡ {description}")
            
            return signal
            
        except Exception as e:
            self.logger.error(f"Cascade detection failed: {e}")
            return None
    
    def _get_large_order_threshold(self, symbol: str) -> float:
        """Get tier-aware large order threshold for whale detection."""
        if symbol in self._tier1_symbols:
            return self.large_order_threshold  # $10K for BTC/ETH
        elif symbol in self._tier2_symbols:
            return self.large_order_threshold * 0.5  # $5K for mid-caps
        else:
            return self.large_order_threshold * 0.2  # $2K for small coins

    async def detect_whale_accumulation_window(self, symbol: str, min_large_orders: int = 5) -> Optional[EventSignal]:
        """
        Detect whale accumulation OR distribution window.

        Accumulation: >= 60% large orders are buys (LONG bias)
        Distribution: >= 60% large orders are sells (SHORT bias)

        Uses tier-aware large order threshold for fair detection across coin sizes.

        Args:
            symbol: Trading pair
            min_large_orders: Minimum large orders to detect (default 5)

        Returns:
            EventSignal if detected, None otherwise
        """
        try:
            trades = self.buffer_manager.get_trades(symbol, time_window=300)

            if not trades or len(trades) < 20:
                return None

            threshold = self._get_large_order_threshold(symbol)
            large_buys = 0
            large_sells = 0

            for trade in trades:
                vol = float(trade.get("vol", 0))
                side = int(trade.get("side", 0))

                if vol >= threshold:
                    if side == 2:  # Buy
                        large_buys += 1
                    elif side == 1:  # Sell
                        large_sells += 1

            total_large = large_buys + large_sells

            if total_large < min_large_orders:
                return None

            buy_ratio = large_buys / total_large

            # Detect accumulation (majority buys)
            if buy_ratio >= 0.6:
                dominant_ratio = buy_ratio
                event_type = "WHALE_ACCUMULATION"
                description = (
                    f"Whale accumulation window: {large_buys} large buy orders "
                    f"vs {large_sells} sells in 5 minutes"
                )
            # Detect distribution (majority sells)
            elif buy_ratio <= 0.4:
                dominant_ratio = 1.0 - buy_ratio  # sell ratio
                event_type = "WHALE_DISTRIBUTION"
                description = (
                    f"Whale distribution window: {large_sells} large sell orders "
                    f"vs {large_buys} buys in 5 minutes"
                )
            else:
                return None  # No clear direction

            confidence = 50 + (dominant_ratio * 40)  # 50-90% range

            signal = EventSignal(
                event_type=event_type,
                symbol=symbol,
                description=description,
                confidence=confidence,
                timestamp=datetime.now(timezone.utc).isoformat(),
                data={
                    "large_buys": large_buys,
                    "large_sells": large_sells,
                    "buy_ratio": buy_ratio,
                    "time_window": 300
                }
            )

            self._detections += 1
            self.logger.info(f"🐋 {description}")

            return signal

        except Exception as e:
            self.logger.error(f"Whale window detection failed: {e}")
            return None
    
    async def detect_volume_spike(self, symbol: str, spike_multiplier: float = 3.0) -> Optional[EventSignal]:
        """
        Detect volume spike
        
        Criteria:
        - Recent volume (1min) is spike_multiplier times higher than average (5min)
        
        Args:
            symbol: Trading pair
            spike_multiplier: Volume spike threshold (default 3x)
            
        Returns:
            EventSignal if detected, None otherwise
        """
        try:
            import time as _time

            # Get recent trades (1 minute) - the potential spike window
            recent_trades = self.buffer_manager.get_trades(symbol, time_window=60)

            # Get historical trades (5 minutes) for baseline
            historical_trades = self.buffer_manager.get_trades(symbol, time_window=300)

            if not recent_trades or not historical_trades:
                return None

            recent_volume = sum(float(t.get("vol", 0)) for t in recent_trades)

            # Exclude the recent 1-min window from historical to avoid self-dilution
            now_ms = int(_time.time() * 1000)
            cutoff_ms = now_ms - 60_000
            baseline_trades = [t for t in historical_trades if t.get("timestamp", 0) < cutoff_ms]

            if not baseline_trades:
                return None

            baseline_volume = sum(float(t.get("vol", 0)) for t in baseline_trades)

            # Calculate actual time span of baseline data
            now_ts = _time.time()
            oldest_ts = min(
                (float(t.get("timestamp", now_ts * 1000)) / 1000 for t in baseline_trades),
                default=now_ts
            )
            baseline_minutes = max((now_ts - 60 - oldest_ts) / 60, 1.0)
            avg_volume_per_minute = baseline_volume / baseline_minutes

            if avg_volume_per_minute == 0:
                return None

            # Check for spike (no self-dilution now)
            spike_ratio = recent_volume / avg_volume_per_minute
            
            if spike_ratio < spike_multiplier:
                return None
            
            confidence = min(50 + (spike_ratio * 10), 99)
            
            description = (
                f"Volume spike: {spike_ratio:.1f}x normal volume "
                f"(${recent_volume/1000:.0f}K in 1 min vs ${avg_volume_per_minute/1000:.0f}K avg)"
            )
            
            signal = EventSignal(
                event_type="VOLUME_SPIKE",
                symbol=symbol,
                description=description,
                confidence=confidence,
                timestamp=datetime.now(timezone.utc).isoformat(),
                data={
                    "recent_volume": recent_volume,
                    "avg_volume": avg_volume_per_minute,
                    "spike_ratio": spike_ratio
                }
            )
            
            self._detections += 1
            self.logger.info(f"📈 {description}")
            
            return signal
            
        except Exception as e:
            self.logger.error(f"Volume spike detection failed: {e}")
            return None
    
    async def analyze(self, symbol: str) -> List[EventSignal]:
        """
        Run all event detectors
        
        Args:
            symbol: Trading pair
            
        Returns:
            List of detected event signals
        """
        signals = []
        
        try:
            # Detect liquidation cascade
            cascade = await self.detect_liquidation_cascade(symbol)
            if cascade:
                signals.append(cascade)
            
            # Detect whale accumulation
            accumulation = await self.detect_whale_accumulation_window(symbol)
            if accumulation:
                signals.append(accumulation)
            
            # Detect volume spike
            volume_spike = await self.detect_volume_spike(symbol)
            if volume_spike:
                signals.append(volume_spike)
            
        except Exception as e:
            self.logger.error(f"Event analysis failed for {symbol}: {e}")
        
        return signals
    
    def get_stats(self) -> dict:
        """Get detector statistics"""
        return {
            "total_detections": self._detections
        }
