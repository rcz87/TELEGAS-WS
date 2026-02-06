# Signal Tracker - Auto-track signal outcomes from WebSocket price data
# Measures actual WIN/LOSS by comparing price at signal time vs price after interval

"""
Signal Tracker Module

Responsibilities:
- Record signal entry price at generation time
- Check price after configurable interval (default 15 min)
- Auto-label WIN/LOSS/NEUTRAL based on direction + price movement
- Feed results back to ConfidenceScorer
- Track cumulative statistics per signal type
"""

import asyncio
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from collections import defaultdict

from ..utils.logger import setup_logger


@dataclass
class TrackedSignal:
    """A signal being tracked for outcome"""
    symbol: str
    signal_type: str
    direction: str  # LONG or SHORT
    entry_price: float
    stop_loss: float
    target_price: float
    confidence: float
    timestamp: float  # unix time when signal was created
    check_after: float  # unix time when to check outcome
    outcome: Optional[str] = None  # WIN, LOSS, NEUTRAL, or None (pending)
    exit_price: Optional[float] = None
    _db_id: Optional[int] = None  # Database row ID for outcome updates


class SignalTracker:
    """
    Tracks signal outcomes using price data from WebSocket buffer.

    How it works:
    1. When a signal is generated, record entry price and levels
    2. Background task checks price after configurable interval
    3. Compare current price to entry/target/SL
    4. Label outcome and feed back to ConfidenceScorer
    """

    def __init__(self, buffer_manager, confidence_scorer=None, check_interval_seconds: int = 900,
                 on_outcome_callback=None):
        """
        Initialize signal tracker

        Args:
            buffer_manager: BufferManager instance (to get current prices)
            confidence_scorer: ConfidenceScorer instance (to feed results)
            check_interval_seconds: How long to wait before checking outcome (default 900 = 15 min)
            on_outcome_callback: async callback(tracked_signal, pnl_pct) called when outcome is determined
        """
        self.buffer_manager = buffer_manager
        self.confidence_scorer = confidence_scorer
        self.check_interval = check_interval_seconds
        self._on_outcome = on_outcome_callback
        self.logger = setup_logger("SignalTracker", "INFO")

        # Pending signals waiting to be checked
        self._pending: List[TrackedSignal] = []

        # Completed signal outcomes
        self._completed: List[TrackedSignal] = []

        # Stats per signal type
        self._stats: Dict[str, Dict] = defaultdict(lambda: {
            'total': 0, 'wins': 0, 'losses': 0, 'neutral': 0
        })

    def track_signal(self, signal: Any, entry_price: float, stop_loss: float, target_price: float) -> 'TrackedSignal':
        """
        Start tracking a signal for outcome measurement.

        Args:
            signal: TradingSignal object
            entry_price: Price at signal generation
            stop_loss: Stop loss level
            target_price: Target price level

        Returns:
            TrackedSignal reference (can be used to set _db_id)
        """
        now = time.time()
        tracked = TrackedSignal(
            symbol=signal.symbol,
            signal_type=signal.signal_type,
            direction=signal.direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            target_price=target_price,
            confidence=signal.confidence,
            timestamp=now,
            check_after=now + self.check_interval
        )
        self._pending.append(tracked)
        self.logger.info(
            f"Tracking {signal.symbol} {signal.signal_type} {signal.direction} "
            f"entry=${entry_price:,.0f} SL=${stop_loss:,.0f} TP=${target_price:,.0f}"
        )
        return tracked

    def get_current_price(self, symbol: str) -> Optional[float]:
        """
        Get most recent price for symbol from buffer.

        Uses the latest trade in the buffer as current price.
        """
        trades = self.buffer_manager.get_trades(symbol, time_window=60)
        if not trades:
            # Fallback: try liquidation prices
            liqs = self.buffer_manager.get_liquidations(symbol, time_window=60)
            if liqs:
                return float(liqs[-1].get("price", 0))
            return None

        # Most recent trade price
        return float(trades[-1].get("price", 0))

    async def check_outcomes(self):
        """
        Check pending signals for outcomes. Called periodically.

        For each pending signal past its check_after time:
        1. Get current price from buffer
        2. Compare to entry/target/SL
        3. Label outcome
        4. Feed to confidence scorer
        """
        now = time.time()
        still_pending = []

        for tracked in self._pending:
            if now < tracked.check_after:
                still_pending.append(tracked)
                continue

            # Time to check this signal
            current_price = self.get_current_price(tracked.symbol)

            if current_price is None or current_price <= 0:
                # No price data available, extend check window by 5 min
                if now - tracked.timestamp < self.check_interval * 3:
                    tracked.check_after = now + 300
                    still_pending.append(tracked)
                else:
                    # Too old, mark as neutral
                    tracked.outcome = "NEUTRAL"
                    tracked.exit_price = 0
                    self._record_outcome(tracked)
                continue

            tracked.exit_price = current_price
            outcome = self._evaluate_outcome(tracked, current_price)
            tracked.outcome = outcome
            self._record_outcome(tracked)

        self._pending = still_pending

    def _evaluate_outcome(self, tracked: TrackedSignal, current_price: float) -> str:
        """
        Evaluate signal outcome based on price movement.

        LONG: WIN if >= target, LOSS if <= SL, PARTIAL_WIN if >= 50% to target
        SHORT: WIN if <= target, LOSS if >= SL, PARTIAL_WIN if >= 50% to target
        Fallback (between entry and 50% mark): NEUTRAL
        """
        entry = tracked.entry_price
        target = tracked.target_price
        sl = tracked.stop_loss

        if tracked.direction == "LONG":
            if current_price >= target:
                return "WIN"
            elif current_price <= sl:
                return "LOSS"
            else:
                # Fallback: require >= 50% of distance to target for partial win
                halfway = entry + (target - entry) * 0.5
                if current_price >= halfway:
                    return "WIN"
                elif current_price < entry:
                    return "LOSS"
                return "NEUTRAL"
        else:  # SHORT
            if current_price <= target:
                return "WIN"
            elif current_price >= sl:
                return "LOSS"
            else:
                halfway = entry - (entry - target) * 0.5
                if current_price <= halfway:
                    return "WIN"
                elif current_price > entry:
                    return "LOSS"
                return "NEUTRAL"

    def _record_outcome(self, tracked: TrackedSignal):
        """Record outcome and feed to confidence scorer + database."""
        self._completed.append(tracked)

        # Keep only last 500 completed signals
        if len(self._completed) > 500:
            self._completed = self._completed[-500:]

        stats = self._stats[tracked.signal_type]
        stats['total'] += 1
        if tracked.outcome == "WIN":
            stats['wins'] += 1
        elif tracked.outcome == "LOSS":
            stats['losses'] += 1
        else:
            stats['neutral'] += 1

        # Calculate P&L percentage
        pnl_pct = 0
        if tracked.entry_price > 0 and tracked.exit_price:
            if tracked.direction == "LONG":
                pnl_pct = (tracked.exit_price - tracked.entry_price) / tracked.entry_price * 100
            else:
                pnl_pct = (tracked.entry_price - tracked.exit_price) / tracked.entry_price * 100

        self.logger.info(
            f"{'WIN' if tracked.outcome == 'WIN' else 'LOSS' if tracked.outcome == 'LOSS' else 'NEUTRAL'} "
            f"{tracked.symbol} {tracked.signal_type} {tracked.direction} "
            f"entry=${tracked.entry_price:,.0f} exit=${tracked.exit_price:,.0f} "
            f"P&L={pnl_pct:+.2f}%"
        )

        # Feed back to confidence scorer
        if self.confidence_scorer and tracked.outcome in ("WIN", "LOSS"):
            self.confidence_scorer.record_result(
                tracked.signal_type,
                was_successful=(tracked.outcome == "WIN")
            )

        # Notify database callback
        if self._on_outcome and tracked._db_id:
            try:
                import asyncio
                asyncio.create_task(
                    self._on_outcome(tracked, pnl_pct)
                )
            except Exception:
                pass

    def get_track_record(self, signal_type: str) -> dict:
        """
        Get track record for a signal type.

        Returns dict with wins, losses, total, win_rate - ready for message_formatter.
        """
        stats = self._stats.get(signal_type, {'total': 0, 'wins': 0, 'losses': 0, 'neutral': 0})
        decided = stats['wins'] + stats['losses']
        win_rate = (stats['wins'] / decided * 100) if decided > 0 else 0

        return {
            'total': stats['total'],
            'wins': stats['wins'],
            'losses': stats['losses'],
            'neutral': stats['neutral'],
            'win_rate': win_rate
        }

    def get_stats(self) -> dict:
        """Get comprehensive tracker statistics."""
        total_decided = sum(s['wins'] + s['losses'] for s in self._stats.values())
        total_wins = sum(s['wins'] for s in self._stats.values())

        # Calculate average P&L from completed signals
        pnl_list = []
        for sig in self._completed:
            if sig.entry_price > 0 and sig.exit_price and sig.outcome in ("WIN", "LOSS"):
                if sig.direction == "LONG":
                    pnl = (sig.exit_price - sig.entry_price) / sig.entry_price * 100
                else:
                    pnl = (sig.entry_price - sig.exit_price) / sig.entry_price * 100
                pnl_list.append(pnl)

        avg_pnl = sum(pnl_list) / len(pnl_list) if pnl_list else 0
        avg_win = sum(p for p in pnl_list if p > 0) / max(sum(1 for p in pnl_list if p > 0), 1)
        avg_loss = sum(p for p in pnl_list if p < 0) / max(sum(1 for p in pnl_list if p < 0), 1)

        return {
            'pending': len(self._pending),
            'completed': len(self._completed),
            'overall_win_rate': (total_wins / total_decided * 100) if total_decided > 0 else 0,
            'avg_pnl_pct': avg_pnl,
            'avg_win_pct': avg_win,
            'avg_loss_pct': avg_loss,
            'per_type': {
                stype: self.get_track_record(stype)
                for stype in self._stats
            }
        }
