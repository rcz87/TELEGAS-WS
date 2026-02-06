# Confidence Scorer - Dynamic Confidence Adjustment
# Production-ready confidence scoring with historical learning

"""
Confidence Scorer Module

Responsibilities:
- Dynamic confidence adjustment based on signal quality
- Historical accuracy tracking and learning
- Multi-factor scoring enhancement
- Performance-based score adjustment

Note: Base confidence is calculated by analyzers.
This module enhances/adjusts based on historical performance.
"""

from typing import Dict, Optional
from datetime import datetime, timedelta
import json

from ..utils.logger import setup_logger

class ConfidenceScorer:
    """
    Production-ready confidence scorer
    
    Adjusts and enhances confidence scores based on:
    - Historical accuracy per signal type
    - Recent performance trends
    - Signal quality metrics
    - Pattern recognition learning
    
    Features:
    - Historical tracking
    - Dynamic adjustment
    - Performance learning
    - Statistics reporting
    """
    
    def __init__(self, learning_rate: float = 0.1, monitoring_config: dict = None):
        """
        Initialize confidence scorer

        Args:
            learning_rate: How fast to adjust based on results (0.0-1.0)
            monitoring_config: Tier config for fair threshold scaling
        """
        self.learning_rate = learning_rate
        self.logger = setup_logger("ConfidenceScorer", "INFO")

        # Tiered thresholds for fair quality scoring
        monitoring = monitoring_config or {}
        self._tier1_symbols = set(monitoring.get('tier1_symbols', ['BTCUSDT', 'ETHUSDT']))
        self._tier2_symbols = set(monitoring.get('tier2_symbols', []))
        self._tier1_absorption = monitoring.get('tier1_absorption', 100_000)
        self._tier2_absorption = monitoring.get('tier2_absorption', 20_000)
        self._tier3_absorption = monitoring.get('tier3_absorption', 5_000)
        
        # Historical accuracy tracking
        self.signal_history: Dict[str, list] = {
            "STOP_HUNT": [],
            "ACCUMULATION": [],
            "DISTRIBUTION": [],
            "EVENT": []
        }
        
        # Win rates per signal type
        self.win_rates: Dict[str, float] = {
            "STOP_HUNT": 0.5,  # Start at 50%
            "ACCUMULATION": 0.5,
            "DISTRIBUTION": 0.5,
            "EVENT": 0.5
        }
        
        self._scores_calculated = 0
        
    def adjust_confidence(self, base_confidence: float, signal_type: str,
                          metadata: dict = None, symbol: str = "") -> float:
        """
        Adjust base confidence score based on historical performance

        Args:
            base_confidence: Initial confidence from analyzers
            signal_type: Type of signal (STOP_HUNT, etc.)
            metadata: Additional signal metadata
            symbol: Trading pair for tier-aware thresholds
        """
        try:
            adjusted = base_confidence

            # Factor 1: Historical win rate adjustment
            win_rate = self.win_rates.get(signal_type, 0.5)

            if win_rate > 0.7:
                adjusted += 5
                self.logger.debug(f"{signal_type}: +5% (strong track record: {win_rate:.1%})")
            elif win_rate > 0.6:
                adjusted += 3
            elif win_rate < 0.4:
                adjusted -= 5
                self.logger.debug(f"{signal_type}: -5% (weak track record: {win_rate:.1%})")
            elif win_rate < 0.5:
                adjusted -= 3

            # Factor 2: Recent trend
            recent_trend = self.get_recent_trend(signal_type, window=10)
            if recent_trend > 0.75:
                adjusted += 3
                self.logger.debug(f"{signal_type}: +3% (hot streak)")
            elif recent_trend < 0.25:
                adjusted -= 3
                self.logger.debug(f"{signal_type}: -3% (cold streak)")

            # Factor 3: Quality metrics from metadata (tier-aware)
            if metadata:
                quality_boost = self.calculate_quality_boost(metadata, symbol)
                adjusted += quality_boost
                if quality_boost != 0:
                    self.logger.debug(f"Quality adjustment: {quality_boost:+.1f}%")
            
            self._scores_calculated += 1
            
            # Ensure bounds
            return max(50.0, min(adjusted, 99.0))
            
        except Exception as e:
            self.logger.error(f"Confidence adjustment failed: {e}")
            return base_confidence
    
    def _get_absorption_threshold(self, symbol: str) -> float:
        """Get tier-aware absorption threshold for quality scoring."""
        if symbol in self._tier1_symbols:
            return self._tier1_absorption
        elif symbol in self._tier2_symbols:
            return self._tier2_absorption
        else:
            return self._tier3_absorption

    def calculate_quality_boost(self, metadata: dict, symbol: str = "") -> float:
        """
        Calculate quality-based boost from metadata (tier-aware).

        Args:
            metadata: Signal metadata dictionary
            symbol: Trading pair for tier-aware thresholds

        Returns:
            Quality boost value (-5 to +5)
        """
        boost = 0.0

        # Check stop hunt metadata
        if 'stop_hunt' in metadata:
            sh = metadata['stop_hunt']

            # Absorption relative to tier threshold (fair for all coins)
            absorption = sh.get('absorption_volume', 0)
            abs_threshold = self._get_absorption_threshold(symbol)
            if absorption > abs_threshold * 5:
                boost += 2
            elif absorption > abs_threshold * 2:
                boost += 1

            # Very one-sided is positive
            if sh.get('directional_pct', 0) > 0.85:
                boost += 2

        # Check order flow metadata (consistent with OrderFlowAnalyzer 0.65/0.35)
        if 'order_flow' in metadata:
            of = metadata['order_flow']

            buy_ratio = of.get('buy_ratio', 0.5)
            if buy_ratio > 0.75 or buy_ratio < 0.25:
                boost += 1.5
            elif buy_ratio > 0.65 or buy_ratio < 0.35:
                boost += 0.5

            # Large orders (scale: 5+ = noteworthy, 10+ = strong)
            large_count = of.get('large_buys', 0) + of.get('large_sells', 0)
            if large_count > 10:
                boost += 1.5
            elif large_count >= 5:
                boost += 0.5

        # Check event metadata
        if 'events' in metadata and len(metadata['events']) >= 2:
            boost += 1

        return min(boost, 5.0)
    
    def get_recent_trend(self, signal_type: str, window: int = 10) -> float:
        """
        Get recent performance trend
        
        Args:
            signal_type: Type of signal
            window: Number of recent signals to check
            
        Returns:
            Win rate for recent signals (0.0-1.0)
        """
        history = self.signal_history.get(signal_type, [])
        
        if not history:
            return 0.5  # Neutral
        
        recent = history[-window:]
        if not recent:
            return 0.5
        
        wins = sum(1 for result in recent if result)
        return wins / len(recent)
    
    def record_result(self, signal_type: str, was_successful: bool):
        """
        Record signal result and update statistics
        
        This allows the system to learn from outcomes and
        adjust confidence scoring over time.
        
        Args:
            signal_type: Type of signal
            was_successful: Whether the signal was successful
        """
        try:
            # Add to history
            if signal_type not in self.signal_history:
                self.signal_history[signal_type] = []
            
            self.signal_history[signal_type].append(was_successful)
            
            # Keep only last 100 results per type
            if len(self.signal_history[signal_type]) > 100:
                self.signal_history[signal_type] = self.signal_history[signal_type][-100:]
            
            # Update win rate with learning rate
            history = self.signal_history[signal_type]
            current_win_rate = sum(1 for r in history if r) / len(history)
            
            # Exponential moving average
            old_rate = self.win_rates.get(signal_type, 0.5)
            new_rate = (old_rate * (1 - self.learning_rate)) + (current_win_rate * self.learning_rate)
            self.win_rates[signal_type] = new_rate
            
            self.logger.info(
                f"📊 {signal_type} result: {'✅' if was_successful else '❌'} - "
                f"Win rate: {new_rate:.1%} (n={len(history)})"
            )
            
        except Exception as e:
            self.logger.error(f"Failed to record result: {e}")
    
    def get_win_rate(self, signal_type: str) -> float:
        """Get current win rate for signal type"""
        return self.win_rates.get(signal_type, 0.5)
    
    def get_signal_count(self, signal_type: str) -> int:
        """Get number of recorded signals for type"""
        return len(self.signal_history.get(signal_type, []))
    
    def get_overall_stats(self) -> dict:
        """Get overall performance statistics"""
        total_signals = sum(len(h) for h in self.signal_history.values())
        total_wins = sum(sum(1 for r in h if r) for h in self.signal_history.values())
        
        return {
            "total_signals": total_signals,
            "total_wins": total_wins,
            "overall_win_rate": total_wins / max(total_signals, 1),
            "scores_calculated": self._scores_calculated,
            "per_type": {
                signal_type: {
                    "count": len(history),
                    "wins": sum(1 for r in history if r),
                    "win_rate": self.win_rates[signal_type]
                }
                for signal_type, history in self.signal_history.items()
                if history
            }
        }
    
    def reset_history(self, signal_type: Optional[str] = None):
        """
        Reset historical data
        
        Args:
            signal_type: Specific type to reset, or None for all
        """
        if signal_type:
            self.signal_history[signal_type] = []
            self.win_rates[signal_type] = 0.5
            self.logger.info(f"Reset history for {signal_type}")
        else:
            for st in self.signal_history.keys():
                self.signal_history[st] = []
                self.win_rates[st] = 0.5
            self.logger.info("Reset all history")
    
    def export_stats(self) -> dict:
        """Export statistics for persistence"""
        return {
            "timestamp": datetime.now().isoformat(),
            "win_rates": self.win_rates.copy(),
            "history_lengths": {
                st: len(h) for st, h in self.signal_history.items()
            },
            "overall_stats": self.get_overall_stats()
        }
