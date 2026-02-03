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
    
    def __init__(self, learning_rate: float = 0.1):
        """
        Initialize confidence scorer
        
        Args:
            learning_rate: How fast to adjust based on results (0.0-1.0)
        """
        self.learning_rate = learning_rate
        self.logger = setup_logger("ConfidenceScorer", "INFO")
        
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
        
    def adjust_confidence(self, base_confidence: float, signal_type: str, metadata: dict = None) -> float:
        """
        Adjust base confidence score based on historical performance
        
        Adjustment factors:
        1. Historical win rate for this signal type
        2. Recent trend (last 10 signals)
        3. Signal quality metrics from metadata
        
        Args:
            base_confidence: Initial confidence from analyzers
            signal_type: Type of signal (STOP_HUNT, etc.)
            metadata: Additional signal metadata
            
        Returns:
            Adjusted confidence score (50-99%)
        """
        try:
            adjusted = base_confidence
            
            # Factor 1: Historical win rate adjustment
            win_rate = self.win_rates.get(signal_type, 0.5)
            
            if win_rate > 0.7:  # Good track record
                adjusted += 5
                self.logger.debug(f"{signal_type}: +5% (strong track record: {win_rate:.1%})")
            elif win_rate > 0.6:
                adjusted += 3
            elif win_rate < 0.4:  # Poor track record
                adjusted -= 5
                self.logger.debug(f"{signal_type}: -5% (weak track record: {win_rate:.1%})")
            elif win_rate < 0.5:
                adjusted -= 3
            
            # Factor 2: Recent trend
            recent_trend = self.get_recent_trend(signal_type, window=10)
            if recent_trend > 0.75:  # Hot streak
                adjusted += 3
                self.logger.debug(f"{signal_type}: +3% (hot streak)")
            elif recent_trend < 0.25:  # Cold streak
                adjusted -= 3
                self.logger.debug(f"{signal_type}: -3% (cold streak)")
            
            # Factor 3: Quality metrics from metadata
            if metadata:
                quality_boost = self.calculate_quality_boost(metadata)
                adjusted += quality_boost
                if quality_boost != 0:
                    self.logger.debug(f"Quality adjustment: {quality_boost:+.1f}%")
            
            self._scores_calculated += 1
            
            # Ensure bounds
            return max(50.0, min(adjusted, 99.0))
            
        except Exception as e:
            self.logger.error(f"Confidence adjustment failed: {e}")
            return base_confidence
    
    def calculate_quality_boost(self, metadata: dict) -> float:
        """
        Calculate quality-based boost from metadata
        
        Args:
            metadata: Signal metadata dictionary
            
        Returns:
            Quality boost value (-5 to +5)
        """
        boost = 0.0
        
        # Check stop hunt metadata
        if 'stop_hunt' in metadata:
            sh = metadata['stop_hunt']
            
            # High absorption is positive
            if sh.get('absorption_volume', 0) > 500_000:
                boost += 2
            
            # Very one-sided is positive
            if sh.get('directional_pct', 0) > 0.85:
                boost += 2
        
        # Check order flow metadata
        if 'order_flow' in metadata:
            of = metadata['order_flow']
            
            # Strong buy/sell ratio
            buy_ratio = of.get('buy_ratio', 0.5)
            if buy_ratio > 0.75 or buy_ratio < 0.25:
                boost += 1.5
            
            # Many large orders
            large_count = of.get('large_buys', 0) + of.get('large_sells', 0)
            if large_count > 15:
                boost += 1.5
        
        # Check event metadata
        if 'events' in metadata and len(metadata['events']) >= 2:
            boost += 1  # Multiple confirming events
        
        return min(boost, 5.0)  # Cap at +5
    
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
