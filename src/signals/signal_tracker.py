# Signal Tracker - Auto-track signal outcomes with rich evaluation
#
# Uses OutcomeEvaluator for MFE/MAE/excursion metrics.
# Passes setup_key to ConfidenceScorer for granular learning.

import asyncio
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from collections import defaultdict

from ..utils.logger import setup_logger
from .outcome_evaluator import evaluate_outcome, OutcomeResult


@dataclass
class TrackedSignal:
    """A signal being tracked for outcome."""
    symbol: str
    signal_type: str
    direction: str  # LONG or SHORT
    entry_price: float
    stop_loss: float
    target_price: float
    confidence: float
    timestamp: float  # unix time when signal was created
    check_after: float  # unix time when to check outcome
    setup_key: str = ""  # granular setup classification
    outcome: Optional[str] = None  # WIN, LOSS, NEUTRAL, PARTIAL, or None (pending)
    exit_price: Optional[float] = None
    outcome_result: Optional[OutcomeResult] = None  # rich evaluation metrics
    _db_id: Optional[int] = None  # Database row ID for outcome updates
    # MFE/MAE tracking: updated each check cycle while pending
    _high_since_entry: Optional[float] = None
    _low_since_entry: Optional[float] = None


class SignalTracker:
    """
    Tracks signal outcomes using price data from WebSocket buffer.

    Enhanced with:
    - OutcomeEvaluator for MFE/MAE/excursion ratio
    - setup_key passthrough to ConfidenceScorer
    - PARTIAL outcome support
    - Price high/low tracking while signal is pending
    """

    def __init__(self, buffer_manager, confidence_scorer=None, check_interval_seconds: int = 900,
                 on_outcome_callback=None):
        self.buffer_manager = buffer_manager
        self.confidence_scorer = confidence_scorer
        self.check_interval = check_interval_seconds
        self._on_outcome = on_outcome_callback
        self.logger = setup_logger("SignalTracker", "INFO")

        self._pending: List[TrackedSignal] = []
        self._completed: List[TrackedSignal] = []

        self._stats: Dict[str, Dict] = defaultdict(lambda: {
            'total': 0, 'wins': 0, 'losses': 0, 'neutral': 0, 'partial': 0
        })

    def track_signal(self, signal: Any, entry_price: float, stop_loss: float,
                     target_price: float, setup_key: str = "") -> TrackedSignal:
        """
        Start tracking a signal for outcome measurement.

        Args:
            signal: TradingSignal object
            entry_price: Price at signal generation
            stop_loss: Stop loss level
            target_price: Target price level
            setup_key: Granular setup classification key

        Returns:
            TrackedSignal reference
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
            check_after=now + self.check_interval,
            setup_key=setup_key,
            _high_since_entry=entry_price,
            _low_since_entry=entry_price,
        )
        self._pending.append(tracked)
        self.logger.info(
            f"Tracking {signal.symbol} {signal.signal_type} {signal.direction} "
            f"entry=${entry_price:,.0f} SL=${stop_loss:,.0f} TP=${target_price:,.0f} "
            f"setup={setup_key[:50]}"
        )
        return tracked

    def get_current_price(self, symbol: str) -> Optional[float]:
        """Get most recent price for symbol from buffer."""
        trades = self.buffer_manager.get_trades(symbol, time_window=60)
        if not trades:
            liqs = self.buffer_manager.get_liquidations(symbol, time_window=60)
            if liqs:
                return float(liqs[-1].get("price", 0))
            return None
        return float(trades[-1].get("price", 0))

    def _update_price_extremes(self, tracked: TrackedSignal, current_price: float):
        """Update high/low watermarks for MFE/MAE calculation."""
        if current_price <= 0:
            return
        if tracked._high_since_entry is None or current_price > tracked._high_since_entry:
            tracked._high_since_entry = current_price
        if tracked._low_since_entry is None or current_price < tracked._low_since_entry:
            tracked._low_since_entry = current_price

    async def check_outcomes(self):
        """Check pending signals for outcomes. Called periodically."""
        now = time.time()
        still_pending = []

        for tracked in self._pending:
            current_price = self.get_current_price(tracked.symbol)

            # Update price extremes for MFE/MAE even if not ready to evaluate yet
            if current_price and current_price > 0:
                self._update_price_extremes(tracked, current_price)

            if now < tracked.check_after:
                still_pending.append(tracked)
                continue

            # Time to evaluate this signal
            if current_price is None or current_price <= 0:
                max_age = self.check_interval + 900
                if now - tracked.timestamp < max_age:
                    tracked.check_after = now + 300
                    still_pending.append(tracked)
                else:
                    tracked.outcome = "NEUTRAL"
                    tracked.exit_price = 0
                    tracked.outcome_result = OutcomeResult("NEUTRAL", 0, 0, 0, 0, 0, False, False)
                    self._record_outcome(tracked)
                continue

            tracked.exit_price = current_price

            # Use rich outcome evaluator
            result = evaluate_outcome(
                direction=tracked.direction,
                entry_price=tracked.entry_price,
                exit_price=current_price,
                target_price=tracked.target_price,
                stop_loss=tracked.stop_loss,
                high_since_entry=tracked._high_since_entry,
                low_since_entry=tracked._low_since_entry,
                signal_timestamp=tracked.timestamp,
                eval_timestamp=now,
            )

            tracked.outcome = result.outcome
            tracked.outcome_result = result
            self._record_outcome(tracked)

        self._pending = still_pending

    def _record_outcome(self, tracked: TrackedSignal):
        """Record outcome and feed to confidence scorer + database."""
        self._completed.append(tracked)
        if len(self._completed) > 500:
            self._completed = self._completed[-500:]

        result = tracked.outcome_result
        stats = self._stats[tracked.signal_type]
        stats['total'] += 1

        if tracked.outcome == "WIN":
            stats['wins'] += 1
        elif tracked.outcome == "LOSS":
            stats['losses'] += 1
        elif tracked.outcome == "PARTIAL":
            stats['partial'] += 1
        else:
            stats['neutral'] += 1

        pnl_pct = result.pnl_pct if result else 0

        self.logger.info(
            f"{tracked.outcome} {tracked.symbol} {tracked.signal_type} {tracked.direction} "
            f"entry=${tracked.entry_price:,.0f} exit=${tracked.exit_price or 0:,.0f} "
            f"P&L={pnl_pct:+.2f}% "
            f"MFE={result.mfe_pct:.2f}% MAE={result.mae_pct:.2f}% "
            f"ExR={result.excursion_ratio:.1f} "
            f"t={result.time_to_resolution:.0f}s"
            if result else f"{tracked.outcome} {tracked.symbol} (no eval data)"
        )

        # Feed back to confidence scorer (WIN and PARTIAL count as success, LOSS as failure)
        if self.confidence_scorer and tracked.outcome in ("WIN", "LOSS", "PARTIAL"):
            was_successful = tracked.outcome in ("WIN", "PARTIAL")
            outcome_details = None
            if result:
                outcome_details = {
                    "pnl_pct": result.pnl_pct,
                    "mfe_pct": result.mfe_pct,
                    "mae_pct": result.mae_pct,
                    "excursion_ratio": result.excursion_ratio,
                    "time_to_resolution": result.time_to_resolution,
                    "hit_target": result.hit_target,
                    "hit_sl": result.hit_sl,
                }
            self.confidence_scorer.record_result(
                tracked.signal_type,
                was_successful=was_successful,
                setup_key=tracked.setup_key,
                outcome_details=outcome_details,
            )

        # Notify database callback
        if self._on_outcome and tracked._db_id:
            try:
                asyncio.create_task(self._on_outcome(tracked, pnl_pct))
            except Exception:
                pass

    def get_track_record(self, signal_type: str) -> dict:
        stats = self._stats.get(signal_type, {'total': 0, 'wins': 0, 'losses': 0, 'neutral': 0, 'partial': 0})
        decided = stats['wins'] + stats['losses'] + stats['partial']
        wins_and_partial = stats['wins'] + stats['partial']
        win_rate = (wins_and_partial / decided * 100) if decided > 0 else 0

        return {
            'total': stats['total'],
            'wins': stats['wins'],
            'losses': stats['losses'],
            'partial': stats.get('partial', 0),
            'neutral': stats['neutral'],
            'win_rate': win_rate,
        }

    def get_stats(self) -> dict:
        total_decided = sum(s['wins'] + s['losses'] + s.get('partial', 0) for s in self._stats.values())
        total_wins = sum(s['wins'] + s.get('partial', 0) for s in self._stats.values())

        pnl_list = []
        mfe_list = []
        mae_list = []
        for sig in self._completed:
            if sig.outcome_result and sig.outcome in ("WIN", "LOSS", "PARTIAL"):
                pnl_list.append(sig.outcome_result.pnl_pct)
                mfe_list.append(sig.outcome_result.mfe_pct)
                mae_list.append(sig.outcome_result.mae_pct)

        avg_pnl = sum(pnl_list) / len(pnl_list) if pnl_list else 0
        avg_win = sum(p for p in pnl_list if p > 0) / max(sum(1 for p in pnl_list if p > 0), 1)
        avg_loss = sum(p for p in pnl_list if p < 0) / max(sum(1 for p in pnl_list if p < 0), 1)
        avg_mfe = sum(mfe_list) / len(mfe_list) if mfe_list else 0
        avg_mae = sum(mae_list) / len(mae_list) if mae_list else 0

        return {
            'pending': len(self._pending),
            'completed': len(self._completed),
            'overall_win_rate': (total_wins / total_decided * 100) if total_decided > 0 else 0,
            'avg_pnl_pct': avg_pnl,
            'avg_win_pct': avg_win,
            'avg_loss_pct': avg_loss,
            'avg_mfe_pct': avg_mfe,
            'avg_mae_pct': avg_mae,
            'per_type': {
                stype: self.get_track_record(stype)
                for stype in self._stats
            },
        }
