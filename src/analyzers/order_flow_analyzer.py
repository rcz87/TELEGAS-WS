# Order Flow Analyzer - Buy/Sell Pressure Analysis
# Production-ready order flow analysis for whale detection

"""
Order Flow Analyzer Module

Responsibilities:
- Calculate buy vs sell volume ratio
- Count large orders (whale activity)
- Detect accumulation/distribution
- Calculate confidence score
- Emit OrderFlowSignal

Algorithm:
1. Get trades in 5-minute windows
2. Separate buy vs sell volume
3. Count large orders (>$10K)
4. Detect accumulation (buy ratio >65%) or distribution (buy ratio <35%)
5. Calculate confidence based on ratio clarity and whale activity
"""

from typing import Optional, Tuple, List
from dataclasses import dataclass
from datetime import datetime

from ..utils.logger import setup_logger

@dataclass
class OrderFlowSignal:
    """Order flow signal data structure"""
    symbol: str
    time_window: int
    buy_volume: float
    sell_volume: float
    buy_ratio: float
    large_buys: int
    large_sells: int
    signal_type: str  # ACCUMULATION or DISTRIBUTION
    confidence: float
    timestamp: str
    total_trades: int
    net_delta: float

class OrderFlowAnalyzer:
    """
    Production-ready order flow analyzer
    
    Detects whale accumulation and distribution patterns
    by analyzing buy/sell pressure and large order flow.
    
    Features:
    - Buy/sell volume analysis
    - Large order detection (whales)
    - Accumulation/distribution signals
    - Confidence scoring
    """
    
    def __init__(
        self,
        buffer_manager,
        large_order_threshold: float = 10000,
        accumulation_ratio: float = 0.65,
        distribution_ratio: float = 0.35,
        monitoring_config: dict = None
    ):
        """
        Initialize order flow analyzer

        Args:
            buffer_manager: BufferManager instance
            large_order_threshold: Min volume for large order (default $10K)
            accumulation_ratio: Buy ratio threshold for accumulation (default 0.65)
            distribution_ratio: Buy ratio threshold for distribution (default 0.35)
            monitoring_config: Dynamic monitoring config with per-tier thresholds
        """
        self.buffer_manager = buffer_manager
        self.large_order_threshold = large_order_threshold
        self.accumulation_ratio = accumulation_ratio
        self.distribution_ratio = distribution_ratio

        # Tiered volume thresholds for fair confidence scoring across coin sizes
        monitoring = monitoring_config or {}
        self._tier1_symbols = set(monitoring.get('tier1_symbols', ['BTCUSDT', 'ETHUSDT']))
        self._tier2_symbols = set(monitoring.get('tier2_symbols', []))
        self._tier1_cascade = monitoring.get('tier1_cascade', 2_000_000)
        self._tier2_cascade = monitoring.get('tier2_cascade', 200_000)
        self._tier3_cascade = monitoring.get('tier3_cascade', 50_000)

        self.logger = setup_logger("OrderFlowAnalyzer", "INFO")
        self._detections = 0

    def get_volume_threshold(self, symbol: str) -> float:
        """Get volume threshold for tier-aware confidence scoring."""
        if symbol in self._tier1_symbols:
            return self._tier1_cascade
        elif symbol in self._tier2_symbols:
            return self._tier2_cascade
        else:
            return self._tier3_cascade
        
    async def analyze(self, symbol: str, time_window: int = 300) -> Optional[OrderFlowSignal]:
        """
        Analyze order flow for accumulation/distribution
        
        Algorithm:
        1. Get trades in last 5 minutes
        2. Calculate buy vs sell volume
        3. Count large orders (whales)
        4. Determine signal type (ACCUMULATION/DISTRIBUTION)
        5. Calculate confidence score
        6. Return signal if criteria met
        
        Args:
            symbol: Trading pair (e.g., "ETHUSDT")
            time_window: Time window in seconds (default 300 = 5 minutes)
            
        Returns:
            OrderFlowSignal if detected, None otherwise
        """
        try:
            # Step 1: Get recent trades
            trades = self.buffer_manager.get_trades(symbol, time_window=time_window)
            
            if not trades or len(trades) < 10:  # Need minimum trades
                return None
            
            # Step 2: Calculate volumes
            buy_volume, sell_volume = self.calculate_volumes(trades)
            total_volume = buy_volume + sell_volume
            
            if total_volume == 0:
                return None
            
            buy_ratio = buy_volume / total_volume
            
            # Step 3: Count large orders
            large_buys, large_sells = self.count_large_orders(trades)
            
            # Step 4: Determine signal type
            signal_type = self.determine_signal_type(buy_ratio, large_buys, large_sells)
            
            if not signal_type:
                return None  # No clear signal
            
            # Step 5: Calculate confidence (tier-aware)
            confidence = self.calculate_confidence(
                buy_ratio=buy_ratio,
                large_buys=large_buys,
                large_sells=large_sells,
                total_volume=total_volume,
                total_trades=len(trades),
                symbol=symbol
            )
            
            # Calculate net delta
            net_delta = buy_volume - sell_volume
            
            # Create signal
            signal = OrderFlowSignal(
                symbol=symbol,
                time_window=time_window,
                buy_volume=buy_volume,
                sell_volume=sell_volume,
                buy_ratio=buy_ratio,
                large_buys=large_buys,
                large_sells=large_sells,
                signal_type=signal_type,
                confidence=confidence,
                timestamp=datetime.now().isoformat(),
                total_trades=len(trades),
                net_delta=net_delta
            )
            
            self._detections += 1
            self.logger.info(
                f"📊 Order flow: {symbol} - {signal_type} - "
                f"Buy ratio: {buy_ratio*100:.1f}% - "
                f"Whales: {large_buys}B/{large_sells}S - "
                f"Confidence: {confidence:.0f}%"
            )
            
            return signal
            
        except Exception as e:
            self.logger.error(f"Analysis failed for {symbol}: {e}")
            return None
    
    def calculate_volumes(self, trades: List[dict]) -> Tuple[float, float]:
        """
        Calculate buy and sell volumes
        
        Side values:
        - 1 = Sell
        - 2 = Buy
        
        Args:
            trades: List of trade events
            
        Returns:
            (buy_volume, sell_volume) tuple in USD
        """
        buy_volume = 0.0
        sell_volume = 0.0
        
        for trade in trades:
            side = int(trade.get("side", 0))
            vol = float(trade.get("volume_usd", trade.get("vol", 0)))
            
            if side == 2:  # Buy
                buy_volume += vol
            elif side == 1:  # Sell
                sell_volume += vol
        
        return (buy_volume, sell_volume)
    
    def count_large_orders(self, trades: List[dict]) -> Tuple[int, int]:
        """
        Count large buy and sell orders (whale activity)
        
        Args:
            trades: List of trade events
            
        Returns:
            (large_buys, large_sells) count tuple
        """
        large_buys = 0
        large_sells = 0
        
        for trade in trades:
            side = int(trade.get("side", 0))
            vol = float(trade.get("volume_usd", trade.get("vol", 0)))
            
            if vol >= self.large_order_threshold:
                if side == 2:  # Large buy
                    large_buys += 1
                elif side == 1:  # Large sell
                    large_sells += 1
        
        return (large_buys, large_sells)
    
    def determine_signal_type(self, buy_ratio: float, large_buys: int, large_sells: int) -> Optional[str]:
        """
        Determine if ACCUMULATION or DISTRIBUTION
        
        Criteria:
        - ACCUMULATION: buy_ratio > 0.65 AND large_buys >= 3
        - DISTRIBUTION: buy_ratio < 0.35 AND large_sells >= 3
        
        Args:
            buy_ratio: Buy volume ratio (0.0-1.0)
            large_buys: Number of large buy orders
            large_sells: Number of large sell orders
            
        Returns:
            "ACCUMULATION", "DISTRIBUTION", or None
        """
        # Check for accumulation
        if buy_ratio >= self.accumulation_ratio and large_buys >= 3:
            return "ACCUMULATION"
        
        # Check for distribution
        if buy_ratio <= self.distribution_ratio and large_sells >= 3:
            return "DISTRIBUTION"
        
        # No clear signal
        return None
    
    def calculate_confidence(
        self,
        buy_ratio: float,
        large_buys: int,
        large_sells: int,
        total_volume: float,
        total_trades: int,
        symbol: str = ""
    ) -> float:
        """
        Calculate confidence score (0-99%) with tier-aware volume scoring.

        Factors:
        - Ratio clarity (extreme = more confident)
        - Large order count (more whales = more confident)
        - Volume ratio relative to coin's tier threshold (fair across all coins)
        - Trade count (more = more reliable)

        Args:
            buy_ratio: Buy volume ratio
            large_buys: Large buy order count
            large_sells: Large sell order count
            total_volume: Total volume
            total_trades: Total trade count
            symbol: Trading pair for tier-aware thresholds
        """
        confidence = 50.0  # Base

        # Factor 1: Ratio clarity
        if buy_ratio > 0.8 or buy_ratio < 0.2:
            confidence += 20
        elif buy_ratio > 0.75 or buy_ratio < 0.25:
            confidence += 15
        elif buy_ratio > 0.7 or buy_ratio < 0.3:
            confidence += 10
        elif buy_ratio > 0.65 or buy_ratio < 0.35:
            confidence += 5

        # Factor 2: Large order count
        dominant_large_orders = max(large_buys, large_sells)

        if dominant_large_orders >= 10:
            confidence += 20
        elif dominant_large_orders >= 7:
            confidence += 15
        elif dominant_large_orders >= 5:
            confidence += 10
        elif dominant_large_orders >= 3:
            confidence += 5

        # Factor 3: Volume relative to tier threshold (fair for all coin sizes)
        threshold = self.get_volume_threshold(symbol) if symbol else 2_000_000
        volume_ratio = total_volume / max(threshold, 1)

        if volume_ratio > 5.0:
            confidence += 15
        elif volume_ratio > 2.5:
            confidence += 10
        elif volume_ratio > 1.0:
            confidence += 5

        # Factor 4: Trade count
        if total_trades > 100:
            confidence += 5
        elif total_trades > 50:
            confidence += 3

        return min(confidence, 99.0)
    
    def get_stats(self) -> dict:
        """Get analyzer statistics"""
        return {
            "total_detections": self._detections,
            "large_order_threshold": self.large_order_threshold,
            "accumulation_ratio": self.accumulation_ratio,
            "distribution_ratio": self.distribution_ratio
        }
