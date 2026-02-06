# Signal Generator - Unified Signal Generation
# Production-ready signal generation from analyzer outputs

"""
Signal Generator Module

Responsibilities:
- Combine outputs from multiple analyzers
- Generate unified TradingSignal
- Merge confidence scores intelligently
- Add comprehensive metadata
- Signal prioritization

Algorithm:
1. Collect signals from all analyzers
2. Determine primary signal type and direction
3. Merge confidence scores (weighted average)
4. Add metadata (sources, timestamp, etc)
5. Return unified signal if confidence meets threshold
"""

from typing import Optional, List
from dataclasses import dataclass
from datetime import datetime

from ..utils.logger import setup_logger

@dataclass
class TradingSignal:
    """Unified trading signal dataclass"""
    symbol: str
    signal_type: str  # STOP_HUNT, ACCUMULATION, DISTRIBUTION, EVENT
    direction: str  # LONG, SHORT, or specific direction
    confidence: float
    sources: List[str]  # Which analyzers contributed
    timestamp: str
    metadata: dict
    priority: int  # 1=High, 2=Medium, 3=Low

class SignalGenerator:
    """
    Production-ready signal generator
    
    Combines outputs from multiple analyzers into
    unified, actionable trading signals.
    
    Features:
    - Multi-source signal combining
    - Weighted confidence merging
    - Intelligent prioritization
    - Rich metadata
    """
    
    def __init__(self, min_confidence: float = 65.0):
        """
        Initialize signal generator
        
        Args:
            min_confidence: Minimum confidence threshold (default 65%)
        """
        self.min_confidence = min_confidence
        self.logger = setup_logger("SignalGenerator", "INFO")
        self._signals_generated = 0
        
    async def generate(
        self,
        symbol: str,
        stop_hunt_signal=None,
        order_flow_signal=None,
        event_signals=None
    ) -> Optional[TradingSignal]:
        """
        Generate unified signal from analyzer outputs
        
        Priority:
        1. StopHunt + OrderFlow + Events = Highest confidence
        2. StopHunt + OrderFlow = High confidence
        3. StopHunt alone = Medium confidence
        4. OrderFlow alone = Medium confidence
        5. Events alone = Low confidence
        
        Args:
            symbol: Trading pair
            stop_hunt_signal: StopHuntSignal or None
            order_flow_signal: OrderFlowSignal or None
            event_signals: List of EventSignal or None
            
        Returns:
            TradingSignal if confidence meets threshold, None otherwise
        """
        try:
            # Collect available signals
            signals = []
            sources = []
            
            if stop_hunt_signal:
                signals.append(('stop_hunt', stop_hunt_signal))
                sources.append('StopHuntDetector')
            
            if order_flow_signal:
                signals.append(('order_flow', order_flow_signal))
                sources.append('OrderFlowAnalyzer')
            
            if event_signals:
                signals.append(('events', event_signals))
                sources.append('EventPatternDetector')
            
            if not signals:
                return None
            
            # Determine primary signal type and direction
            signal_type, direction = self.determine_signal_type_and_direction(
                stop_hunt_signal, order_flow_signal
            )
            
            # Merge confidence scores
            merged_confidence = self.merge_confidence(
                stop_hunt_signal, order_flow_signal, event_signals
            )
            
            # Check confidence threshold
            if merged_confidence < self.min_confidence:
                self.logger.debug(
                    f"{symbol}: Confidence {merged_confidence:.1f}% below threshold {self.min_confidence}%"
                )
                return None
            
            # Determine priority
            priority = self.determine_priority(
                stop_hunt_signal, order_flow_signal, event_signals, merged_confidence
            )
            
            # Build metadata
            metadata = self.build_metadata(
                stop_hunt_signal, order_flow_signal, event_signals
            )
            
            # Create unified signal
            trading_signal = TradingSignal(
                symbol=symbol,
                signal_type=signal_type,
                direction=direction,
                confidence=merged_confidence,
                sources=sources,
                timestamp=datetime.now().isoformat(),
                metadata=metadata,
                priority=priority
            )
            
            self._signals_generated += 1
            self.logger.info(
                f"🎯 Signal generated: {symbol} - {signal_type} {direction} - "
                f"Confidence: {merged_confidence:.1f}% - Priority: {priority} - "
                f"Sources: {len(sources)}"
            )
            
            return trading_signal
            
        except Exception as e:
            self.logger.error(f"Signal generation failed: {e}")
            return None
    
    def determine_signal_type_and_direction(self, stop_hunt_signal, order_flow_signal) -> tuple:
        """
        Determine primary signal type and direction
        
        Priority logic:
        - If StopHunt exists, use it as primary
        - Otherwise, use OrderFlow
        
        Returns:
            (signal_type, direction) tuple
        """
        if stop_hunt_signal:
            # Stop hunt is primary
            if stop_hunt_signal.direction == "SHORT_HUNT":
                return ("STOP_HUNT", "LONG")  # After SHORT_HUNT, go LONG
            else:  # LONG_HUNT
                return ("STOP_HUNT", "SHORT")  # After LONG_HUNT, go SHORT
        
        elif order_flow_signal:
            # Order flow is primary
            if order_flow_signal.signal_type == "ACCUMULATION":
                return ("ACCUMULATION", "LONG")
            else:  # DISTRIBUTION
                return ("DISTRIBUTION", "SHORT")
        
        else:
            return ("EVENT", "NEUTRAL")
    
    def merge_confidence(self, stop_hunt_signal, order_flow_signal, event_signals) -> float:
        """
        Merge confidence scores using weighted average
        
        Weights:
        - StopHunt: 50% (most important)
        - OrderFlow: 35%
        - Events: 15%
        
        If multiple signals align (same direction), boost confidence by 10%
        
        Args:
            stop_hunt_signal: StopHuntSignal or None
            order_flow_signal: OrderFlowSignal or None
            event_signals: List of EventSignal or None
            
        Returns:
            Merged confidence score (0-99%)
        """
        total_weight = 0
        weighted_sum = 0
        
        # Add StopHunt confidence
        if stop_hunt_signal:
            weighted_sum += stop_hunt_signal.confidence * 0.50
            total_weight += 0.50
        
        # Add OrderFlow confidence
        if order_flow_signal:
            weighted_sum += order_flow_signal.confidence * 0.35
            total_weight += 0.35
        
        # Add Events confidence (average of all events)
        if event_signals:
            avg_event_confidence = sum(e.confidence for e in event_signals) / len(event_signals)
            weighted_sum += avg_event_confidence * 0.15
            total_weight += 0.15
        
        if total_weight == 0:
            return 0.0
        
        # Calculate weighted average
        merged = weighted_sum / total_weight
        
        # Alignment bonus: if signals agree on direction, boost confidence
        if self.signals_aligned(stop_hunt_signal, order_flow_signal):
            merged = min(merged + 10, 99.0)
            self.logger.debug("Signals aligned - confidence boosted by 10%")
        
        return merged
    
    def signals_aligned(self, stop_hunt_signal, order_flow_signal) -> bool:
        """Check if stop hunt and order flow signals agree on direction"""
        if not stop_hunt_signal or not order_flow_signal:
            return False
        
        # After SHORT_HUNT (longs liquidated), expect ACCUMULATION (buying)
        # After LONG_HUNT (shorts liquidated), expect DISTRIBUTION (selling)
        if stop_hunt_signal.direction == "SHORT_HUNT" and order_flow_signal.signal_type == "ACCUMULATION":
            return True
        
        if stop_hunt_signal.direction == "LONG_HUNT" and order_flow_signal.signal_type == "DISTRIBUTION":
            return True
        
        return False
    
    def determine_priority(self, stop_hunt_signal, order_flow_signal, event_signals, confidence: float) -> int:
        """
        Determine signal priority
        
        Priority levels:
        1 = High (multiple strong signals + high confidence)
        2 = Medium (single strong signal or moderate confidence)
        3 = Low (events only or low confidence)
        
        Returns:
            Priority level (1-3)
        """
        signal_count = sum([
            1 if stop_hunt_signal else 0,
            1 if order_flow_signal else 0,
            1 if event_signals else 0
        ])
        
        # High priority: multiple signals + high confidence
        if signal_count >= 2 and confidence >= 80:
            return 1
        
        # High priority: all signals present
        if signal_count == 3:
            return 1
        
        # Medium priority: stop hunt or order flow with good confidence
        if (stop_hunt_signal or order_flow_signal) and confidence >= 70:
            return 2
        
        # Low priority: single signal or low confidence
        return 3
    
    def build_metadata(self, stop_hunt_signal, order_flow_signal, event_signals) -> dict:
        """
        Build comprehensive metadata dictionary
        
        Returns:
            Dictionary with signal details
        """
        metadata = {}
        
        if stop_hunt_signal:
            metadata['stop_hunt'] = {
                'total_volume': stop_hunt_signal.total_volume,
                'liquidation_count': stop_hunt_signal.liquidation_count,
                'price_zone': stop_hunt_signal.price_zone,
                'absorption_volume': stop_hunt_signal.absorption_volume,
                'directional_pct': stop_hunt_signal.directional_percentage
            }
        
        if order_flow_signal:
            metadata['order_flow'] = {
                'buy_ratio': order_flow_signal.buy_ratio,
                'buy_volume': order_flow_signal.buy_volume,
                'sell_volume': order_flow_signal.sell_volume,
                'large_buys': order_flow_signal.large_buys,
                'large_sells': order_flow_signal.large_sells,
                'net_delta': order_flow_signal.net_delta,
                'total_trades': order_flow_signal.total_trades
            }
        
        if event_signals:
            metadata['events'] = [
                {
                    'type': e.event_type,
                    'description': e.description,
                    'confidence': e.confidence
                }
                for e in event_signals
            ]
        
        return metadata
    
    def get_stats(self) -> dict:
        """Get generator statistics"""
        return {
            "signals_generated": self._signals_generated,
            "min_confidence": self.min_confidence
        }
