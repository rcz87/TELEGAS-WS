# Buffer Manager - Time-Series Data Buffers
# Production-ready buffer manager with rolling windows

"""
Buffer Manager Module

Responsibilities:
- Maintain rolling buffers for liquidations
- Maintain rolling buffers for trades
- Time-based filtering
- Memory management
- Automatic cleanup
"""

import threading
from collections import deque
from copy import deepcopy
from typing import Dict, List, Optional
from datetime import datetime, timedelta
import time

from ..utils.logger import setup_logger

class BufferManager:
    """
    Production-ready rolling buffer manager
    
    Features:
    - Per-symbol buffers
    - Time-based filtering
    - Automatic size limiting
    - Memory-efficient deque
    - Statistics tracking
    """
    
    def __init__(self, max_liquidations: int = 1000, max_trades: int = 500):
        """
        Initialize buffer manager
        
        Args:
            max_liquidations: Max liquidations per symbol
            max_trades: Max trades per symbol
        """
        self.max_liquidations = max_liquidations
        self.max_trades = max_trades
        
        # Buffers per symbol
        self.liquidation_buffers: Dict[str, deque] = {}
        self.trade_buffers: Dict[str, deque] = {}
        
        # Statistics
        self._total_liquidations = 0
        self._total_trades = 0
        self._symbols_tracked = set()
        
        # CRITICAL FIX Bug #10: Track dropped messages
        self._dropped_liquidations = 0
        self._dropped_trades = 0

        # Rolling baseline: track hourly volume per symbol for context
        # Key: symbol, Value: list of (timestamp, volume) tuples per hour
        self._hourly_liq_volume: Dict[str, deque] = {}
        self._hourly_trade_volume: Dict[str, deque] = {}
        self._last_hourly_update: float = 0

        # Thread safety
        self._lock = threading.Lock()

        # Logger
        self.logger = setup_logger("BufferManager", "INFO")
        
    def add_liquidation(self, symbol: str, event: dict):
        """
        Add liquidation event to buffer (thread-safe)

        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
            event: Liquidation event data
        """
        try:
            # Copy to avoid mutating caller's dict
            event_copy = dict(event)
            if "timestamp" not in event_copy:
                event_copy["timestamp"] = int(time.time() * 1000)

            with self._lock:
                if symbol not in self.liquidation_buffers:
                    self.liquidation_buffers[symbol] = deque(maxlen=self.max_liquidations)
                    self._symbols_tracked.add(symbol)
                    self.logger.debug(f"Created liquidation buffer for {symbol}")

                buffer = self.liquidation_buffers[symbol]
                if len(buffer) >= self.max_liquidations:
                    self._dropped_liquidations += 1
                    if self._dropped_liquidations % 100 == 1:
                        self.logger.warning(
                            f"Buffer overflow: {self._dropped_liquidations} liquidations dropped total"
                        )

                buffer.append(event_copy)
                self._total_liquidations += 1

        except Exception as e:
            self.logger.error(f"Failed to add liquidation: {e}")
    
    def add_trade(self, symbol: str, event: dict):
        """
        Add trade event to buffer (thread-safe)

        Args:
            symbol: Trading pair (e.g., "ETHUSDT")
            event: Trade event data
        """
        try:
            event_copy = dict(event)
            if "timestamp" not in event_copy:
                event_copy["timestamp"] = int(time.time() * 1000)

            with self._lock:
                if symbol not in self.trade_buffers:
                    self.trade_buffers[symbol] = deque(maxlen=self.max_trades)
                    self._symbols_tracked.add(symbol)
                    self.logger.debug(f"Created trade buffer for {symbol}")

                buffer = self.trade_buffers[symbol]
                if len(buffer) >= self.max_trades:
                    self._dropped_trades += 1
                    if self._dropped_trades % 100 == 1:
                        self.logger.warning(
                            f"Buffer overflow: {self._dropped_trades} trades dropped total"
                        )

                buffer.append(event_copy)
                self._total_trades += 1

        except Exception as e:
            self.logger.error(f"Failed to add trade: {e}")
    
    def get_liquidations(self, symbol: str, time_window: int = 30, max_count: Optional[int] = None) -> List[dict]:
        """
        Get liquidations within time window
        
        CRITICAL FIX Bug #9: Added max_count to prevent unbounded results
        
        Args:
            symbol: Trading pair
            time_window: Time window in seconds (default 30s)
            max_count: Maximum number of events to return (default None = all)
            
        Returns:
            List of liquidation events within time window (limited by max_count)
        """
        if symbol not in self.liquidation_buffers:
            return []
        
        try:
            # Calculate cutoff time (milliseconds)
            cutoff_time = int((time.time() - time_window) * 1000)
            
            # Filter events within time window
            buffer = self.liquidation_buffers[symbol]
            recent_events = [
                event for event in buffer
                if event.get("timestamp", 0) >= cutoff_time
            ]
            
            # CRITICAL FIX: Limit results to max_count (most recent first)
            if max_count is not None and len(recent_events) > max_count:
                recent_events = recent_events[-max_count:]
            
            self.logger.debug(
                f"Retrieved {len(recent_events)} liquidations for {symbol} "
                f"in last {time_window}s"
            )
            
            return recent_events
            
        except Exception as e:
            self.logger.error(f"Failed to get liquidations: {e}")
            return []
    
    def get_trades(self, symbol: str, time_window: int = 300, max_count: Optional[int] = None) -> List[dict]:
        """
        Get trades within time window
        
        CRITICAL FIX Bug #9: Added max_count to prevent unbounded results
        
        Args:
            symbol: Trading pair
            time_window: Time window in seconds (default 300s = 5 minutes)
            max_count: Maximum number of events to return (default None = all)
            
        Returns:
            List of trade events within time window (limited by max_count)
        """
        if symbol not in self.trade_buffers:
            return []
        
        try:
            # Calculate cutoff time (milliseconds)
            cutoff_time = int((time.time() - time_window) * 1000)
            
            # Filter events within time window
            buffer = self.trade_buffers[symbol]
            recent_events = [
                event for event in buffer
                if event.get("timestamp", 0) >= cutoff_time
            ]
            
            # CRITICAL FIX: Limit results to max_count (most recent first)
            if max_count is not None and len(recent_events) > max_count:
                recent_events = recent_events[-max_count:]
            
            self.logger.debug(
                f"Retrieved {len(recent_events)} trades for {symbol} "
                f"in last {time_window}s"
            )
            
            return recent_events
            
        except Exception as e:
            self.logger.error(f"Failed to get trades: {e}")
            return []
    
    def get_all_liquidations(self, symbol: str) -> List[dict]:
        """
        Get all liquidations for symbol
        
        Args:
            symbol: Trading pair
            
        Returns:
            List of all liquidation events in buffer
        """
        if symbol not in self.liquidation_buffers:
            return []
        
        return list(self.liquidation_buffers[symbol])
    
    def get_all_trades(self, symbol: str) -> List[dict]:
        """
        Get all trades for symbol
        
        Args:
            symbol: Trading pair
            
        Returns:
            List of all trade events in buffer
        """
        if symbol not in self.trade_buffers:
            return []
        
        return list(self.trade_buffers[symbol])
    
    def cleanup_old_data(self, max_age_seconds: int = 3600):
        """
        Remove old data from buffers
        
        Args:
            max_age_seconds: Maximum age in seconds (default 1 hour)
        """
        try:
            cutoff_time = int((time.time() - max_age_seconds) * 1000)
            cleaned_count = 0
            
            # Cleanup liquidation buffers
            for symbol, buffer in self.liquidation_buffers.items():
                original_size = len(buffer)
                
                # Create new deque with only recent events
                recent_events = deque(
                    (event for event in buffer if event.get("timestamp", 0) >= cutoff_time),
                    maxlen=self.max_liquidations
                )
                
                self.liquidation_buffers[symbol] = recent_events
                cleaned_count += original_size - len(recent_events)
            
            # Cleanup trade buffers
            for symbol, buffer in self.trade_buffers.items():
                original_size = len(buffer)
                
                # Create new deque with only recent events
                recent_events = deque(
                    (event for event in buffer if event.get("timestamp", 0) >= cutoff_time),
                    maxlen=self.max_trades
                )
                
                self.trade_buffers[symbol] = recent_events
                cleaned_count += original_size - len(recent_events)
            
            if cleaned_count > 0:
                self.logger.info(
                    f"Cleaned up {cleaned_count} old events "
                    f"(older than {max_age_seconds}s)"
                )
            
        except Exception as e:
            self.logger.error(f"Cleanup failed: {e}")
    
    def clear_symbol(self, symbol: str):
        """
        Clear all data for specific symbol
        
        Args:
            symbol: Trading pair
        """
        if symbol in self.liquidation_buffers:
            self.liquidation_buffers[symbol].clear()
            self.logger.info(f"Cleared liquidation buffer for {symbol}")
        
        if symbol in self.trade_buffers:
            self.trade_buffers[symbol].clear()
            self.logger.info(f"Cleared trade buffer for {symbol}")
    
    def clear_all(self):
        """Clear all buffers"""
        self.liquidation_buffers.clear()
        self.trade_buffers.clear()
        self._symbols_tracked.clear()
        self._total_liquidations = 0
        self._total_trades = 0
        self.logger.info("Cleared all buffers")
    
    def get_buffer_size(self, symbol: str) -> Dict[str, int]:
        """
        Get buffer sizes for symbol
        
        Args:
            symbol: Trading pair
            
        Returns:
            Dictionary with liquidations and trades counts
        """
        return {
            "liquidations": len(self.liquidation_buffers.get(symbol, [])),
            "trades": len(self.trade_buffers.get(symbol, []))
        }
    
    def get_tracked_symbols(self) -> List[str]:
        """Get list of tracked symbols"""
        return sorted(list(self._symbols_tracked))
    
    def update_hourly_baseline(self):
        """
        Snapshot current hour's volume per symbol for baseline comparison.
        Called periodically (e.g. every hour) from main.py cleanup_task.
        """
        try:
            now = time.time()
            for symbol in list(self._symbols_tracked):
                liqs = self.get_liquidations(symbol, time_window=3600)
                liq_vol = sum(float(l.get("vol", 0)) for l in liqs)
                with self._lock:
                    if symbol not in self._hourly_liq_volume:
                        self._hourly_liq_volume[symbol] = deque(maxlen=24)
                    self._hourly_liq_volume[symbol].append((now, liq_vol))

                trades = self.get_trades(symbol, time_window=3600)
                trade_vol = sum(float(t.get("vol", 0)) for t in trades)
                with self._lock:
                    if symbol not in self._hourly_trade_volume:
                        self._hourly_trade_volume[symbol] = deque(maxlen=24)
                    self._hourly_trade_volume[symbol].append((now, trade_vol))

            self._last_hourly_update = now
        except Exception as e:
            self.logger.error(f"Hourly baseline update failed: {e}")

    def get_baseline(self, symbol: str) -> dict:
        """
        Get rolling baseline for a symbol.

        Returns avg hourly liquidation/trade volume and how current
        period compares (multiplier).

        Returns:
            dict with avg_liq_volume, avg_trade_volume, current vs average multiplier
        """
        result = {
            'avg_hourly_liq_volume': 0,
            'avg_hourly_trade_volume': 0,
            'current_liq_volume': 0,
            'current_trade_volume': 0,
            'liq_multiplier': 1.0,
            'trade_multiplier': 1.0,
            'hours_of_data': 0
        }

        try:
            with self._lock:
                hourly_liqs = list(self._hourly_liq_volume.get(symbol, deque()))
                hourly_trades = list(self._hourly_trade_volume.get(symbol, deque()))

            if hourly_liqs:
                avg_liq = sum(v for _, v in hourly_liqs) / len(hourly_liqs)
                result['avg_hourly_liq_volume'] = avg_liq
                result['hours_of_data'] = len(hourly_liqs)

            if hourly_trades:
                avg_trade = sum(v for _, v in hourly_trades) / len(hourly_trades)
                result['avg_hourly_trade_volume'] = avg_trade

            current_liqs = self.get_liquidations(symbol, time_window=1800)
            current_liq_vol = sum(float(l.get("vol", 0)) for l in current_liqs)
            result['current_liq_volume'] = current_liq_vol

            current_trades = self.get_trades(symbol, time_window=1800)
            current_trade_vol = sum(float(t.get("vol", 0)) for t in current_trades)
            result['current_trade_volume'] = current_trade_vol

            if result['avg_hourly_liq_volume'] > 0:
                result['liq_multiplier'] = (current_liq_vol * 2) / result['avg_hourly_liq_volume']
            if result['avg_hourly_trade_volume'] > 0:
                result['trade_multiplier'] = (current_trade_vol * 2) / result['avg_hourly_trade_volume']
        except Exception as e:
            self.logger.error(f"Baseline calculation failed for {symbol}: {e}")

        return result

    def get_stats(self) -> dict:
        """Get buffer manager statistics"""
        total_liq_in_buffers = sum(
            len(buf) for buf in self.liquidation_buffers.values()
        )
        total_trades_in_buffers = sum(
            len(buf) for buf in self.trade_buffers.values()
        )
        
        return {
            "total_liquidations_received": self._total_liquidations,
            "total_trades_received": self._total_trades,
            "liquidations_in_buffers": total_liq_in_buffers,
            "trades_in_buffers": total_trades_in_buffers,
            "symbols_tracked": len(self._symbols_tracked),
            "symbols_list": self.get_tracked_symbols(),
            "avg_liquidations_per_symbol": total_liq_in_buffers / max(len(self.liquidation_buffers), 1),
            "avg_trades_per_symbol": total_trades_in_buffers / max(len(self.trade_buffers), 1),
            # CRITICAL FIX Bug #10: Report dropped messages
            "dropped_liquidations": self._dropped_liquidations,
            "dropped_trades": self._dropped_trades,
            "drop_rate_liquidations": self._dropped_liquidations / max(self._total_liquidations, 1) * 100,
            "drop_rate_trades": self._dropped_trades / max(self._total_trades, 1) * 100
        }
    
    def get_memory_usage_estimate(self) -> dict:
        """Estimate memory usage in KB"""
        import sys
        
        total_size = 0
        
        # Estimate liquidation buffers
        for symbol, buffer in self.liquidation_buffers.items():
            for event in buffer:
                total_size += sys.getsizeof(event)
        
        # Estimate trade buffers
        for symbol, buffer in self.trade_buffers.items():
            for event in buffer:
                total_size += sys.getsizeof(event)
        
        return {
            "total_kb": total_size / 1024,
            "total_mb": total_size / (1024 * 1024)
        }
