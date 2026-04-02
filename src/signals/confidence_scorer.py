# Confidence Scorer - Dynamic Confidence Adjustment with Setup-Level Learning
#
# Learns from outcomes at two levels:
#   1. setup_key (granular): "STOP_HUNT|LONG|t2|oi_rising|fr_neutral|cvd_rising_aligned|ny|ev1"
#   2. signal_type (fallback): "STOP_HUNT"
#
# When a setup_key has enough samples (>= MIN_SETUP_SAMPLES), its win rate
# is used for adjustment. Otherwise, falls back to signal_type win rate.
# This prevents overfitting on sparse setups while rewarding specific edge.

from typing import Dict, Optional
from datetime import datetime
from collections import defaultdict

from ..utils.logger import setup_logger

# Minimum samples before a setup_key's own win rate is used
MIN_SETUP_SAMPLES = 5


class ConfidenceScorer:
    """
    Production-ready confidence scorer with setup-level learning.

    Adjusts confidence based on:
    - Historical win rate (per setup_key or signal_type fallback)
    - Recent performance trend (hot/cold streak)
    - Signal quality metrics (tier-aware)
    - Combination bonuses (CVD + orderbook alignment)
    """

    def __init__(self, learning_rate: float = 0.1, monitoring_config: dict = None):
        self.learning_rate = learning_rate
        self.logger = setup_logger("ConfidenceScorer", "INFO")

        # Tiered thresholds for fair quality scoring
        monitoring = monitoring_config or {}
        self._tier1_symbols = set(monitoring.get('tier1_symbols', ['BTCUSDT', 'ETHUSDT']))
        self._tier2_symbols = set(monitoring.get('tier2_symbols', []))
        self._tier3_symbols = set(monitoring.get('tier3_symbols', []))
        self._tier1_absorption = monitoring.get('tier1_absorption', 200_000)
        self._tier2_absorption = monitoring.get('tier2_absorption', 50_000)
        self._tier3_absorption = monitoring.get('tier3_absorption', 15_000)
        self._tier4_absorption = monitoring.get('tier4_absorption', 5_000)

        # ── Signal-type level learning (existing, always available) ──
        self.signal_history: Dict[str, list] = {
            "STOP_HUNT": [],
            "ACCUMULATION": [],
            "DISTRIBUTION": [],
            "EVENT": [],
        }
        self.win_rates: Dict[str, float] = {
            "STOP_HUNT": 0.5,
            "ACCUMULATION": 0.5,
            "DISTRIBUTION": 0.5,
            "EVENT": 0.5,
        }

        # ── Setup-key level learning (new, granular) ──
        # Key: setup_key string, Value: list of bools (True=WIN, False=LOSS)
        self._setup_history: Dict[str, list] = defaultdict(list)
        # Key: setup_key, Value: EMA win rate
        self._setup_win_rates: Dict[str, float] = {}

        self._scores_calculated = 0

    # ── Main scoring method ─────────────────────────────────────────

    def adjust_confidence(self, base_confidence: float, signal_type: str,
                          metadata: dict = None, symbol: str = "",
                          setup_key: str = "") -> float:
        """
        Adjust base confidence score based on historical performance.

        Args:
            base_confidence: Initial confidence from analyzers
            signal_type: Type of signal (STOP_HUNT, etc.)
            metadata: Additional signal metadata
            symbol: Trading pair for tier-aware thresholds
            setup_key: Granular setup key (if available)
        """
        try:
            adjusted = base_confidence

            # Factor 1: Historical win rate (setup-level if enough data, else signal-type)
            win_rate, source = self._get_effective_win_rate(signal_type, setup_key)

            if win_rate > 0.7:
                adjusted += 5
                self.logger.debug(f"{source}: +5% (strong: {win_rate:.1%})")
            elif win_rate > 0.6:
                adjusted += 3
            elif win_rate < 0.4:
                adjusted -= 5
                self.logger.debug(f"{source}: -5% (weak: {win_rate:.1%})")
            elif win_rate < 0.5:
                adjusted -= 3

            # Factor 2: Recent trend (setup-level if available, else signal-type)
            recent_trend = self._get_effective_trend(signal_type, setup_key, window=10)
            if recent_trend > 0.75:
                adjusted += 3
                self.logger.debug(f"Hot streak: +3%")
            elif recent_trend < 0.25:
                adjusted -= 3
                self.logger.debug(f"Cold streak: -3%")

            # Factor 3: Quality metrics from metadata (tier-aware)
            if metadata:
                quality_boost = self.calculate_quality_boost(metadata, symbol)
                adjusted += quality_boost

            # Factor 4: Combination bonuses
            if metadata:
                combo_boost = self._calculate_combo_bonus(metadata)
                adjusted += combo_boost

            self._scores_calculated += 1
            return max(55.0, min(adjusted, 99.0))

        except Exception as e:
            self.logger.error(f"Confidence adjustment failed: {e}")
            return base_confidence

    # ── Win rate resolution ─────────────────────────────────────────

    def _get_effective_win_rate(self, signal_type: str, setup_key: str = "") -> tuple:
        """Get win rate from setup_key if enough samples, else signal_type.

        Returns: (win_rate, source_label)
        """
        if setup_key and setup_key in self._setup_win_rates:
            samples = len(self._setup_history.get(setup_key, []))
            if samples >= MIN_SETUP_SAMPLES:
                return (self._setup_win_rates[setup_key], f"setup[{setup_key[:40]}]")

        return (self.win_rates.get(signal_type, 0.5), f"type[{signal_type}]")

    def _get_effective_trend(self, signal_type: str, setup_key: str = "", window: int = 10) -> float:
        """Get recent trend from setup_key if available, else signal_type."""
        if setup_key and setup_key in self._setup_history:
            history = self._setup_history[setup_key]
            if len(history) >= 3:  # Need at least 3 for meaningful trend
                recent = history[-window:]
                return sum(1 for r in recent if r) / len(recent)

        return self.get_recent_trend(signal_type, window)

    # ── Record outcomes (dual-level) ────────────────────────────────

    def record_result(self, signal_type: str, was_successful: bool,
                      setup_key: str = "", outcome_details: dict = None):
        """
        Record signal result at both signal_type and setup_key levels.

        Args:
            signal_type: Type of signal
            was_successful: Whether the signal was successful
            setup_key: Granular setup key (optional)
            outcome_details: Rich outcome from OutcomeEvaluator (optional)
        """
        try:
            # ── Signal-type level (always) ──
            if signal_type not in self.signal_history:
                self.signal_history[signal_type] = []

            self.signal_history[signal_type].append(was_successful)
            if len(self.signal_history[signal_type]) > 100:
                self.signal_history[signal_type] = self.signal_history[signal_type][-100:]

            history = self.signal_history[signal_type]
            current_wr = sum(1 for r in history if r) / len(history)
            old_rate = self.win_rates.get(signal_type, 0.5)
            self.win_rates[signal_type] = old_rate * (1 - self.learning_rate) + current_wr * self.learning_rate

            # ── Setup-key level (if provided) ──
            if setup_key:
                self._setup_history[setup_key].append(was_successful)
                if len(self._setup_history[setup_key]) > 100:
                    self._setup_history[setup_key] = self._setup_history[setup_key][-100:]

                setup_hist = self._setup_history[setup_key]
                setup_wr = sum(1 for r in setup_hist if r) / len(setup_hist)
                old_setup_rate = self._setup_win_rates.get(setup_key, 0.5)
                self._setup_win_rates[setup_key] = (
                    old_setup_rate * (1 - self.learning_rate) + setup_wr * self.learning_rate
                )

                self.logger.info(
                    f"{'WIN' if was_successful else 'LOSS'} {signal_type} "
                    f"setup_wr={self._setup_win_rates[setup_key]:.1%} "
                    f"(n={len(setup_hist)}) type_wr={self.win_rates[signal_type]:.1%} "
                    f"key={setup_key[:60]}"
                )
            else:
                self.logger.info(
                    f"{'WIN' if was_successful else 'LOSS'} {signal_type} "
                    f"type_wr={self.win_rates[signal_type]:.1%} "
                    f"(n={len(history)})"
                )

        except Exception as e:
            self.logger.error(f"Failed to record result: {e}")

    # ── Existing methods (unchanged interface) ──────────────────────

    def _get_absorption_threshold(self, symbol: str) -> float:
        if symbol in self._tier1_symbols:
            return self._tier1_absorption
        elif symbol in self._tier2_symbols:
            return self._tier2_absorption
        elif symbol in self._tier3_symbols:
            return self._tier3_absorption
        else:
            return self._tier4_absorption

    def _calculate_combo_bonus(self, metadata: dict) -> float:
        """Combo bonus when multiple indicators align. Returns 0/10/20."""
        bonus = 0.0
        ctx = metadata.get('market_context', {})
        if not ctx:
            return 0.0

        spot_dir = ctx.get('spot_cvd_direction', 'UNKNOWN')
        fut_dir = ctx.get('futures_cvd_direction', 'UNKNOWN')
        if spot_dir != 'UNKNOWN' and fut_dir != 'UNKNOWN' and spot_dir == fut_dir:
            bonus += 10

        spot_slope = ctx.get('spot_cvd_slope', 0)
        fut_slope = ctx.get('futures_cvd_slope', 0)
        avg_slope = (spot_slope + fut_slope) / 2 if (spot_slope or fut_slope) else 0
        cvd_accel_dir = 'BIDS' if avg_slope > 0 else 'ASKS' if avg_slope < 0 else None

        ob_dominant = ctx.get('orderbook_dominant', 'UNKNOWN')
        if cvd_accel_dir and ob_dominant in ('BIDS', 'ASKS') and cvd_accel_dir == ob_dominant:
            bonus += 10

        return bonus

    def calculate_quality_boost(self, metadata: dict, symbol: str = "") -> float:
        """Quality-based boost from metadata (tier-aware). Returns -5 to +5."""
        boost = 0.0

        if 'stop_hunt' in metadata:
            sh = metadata['stop_hunt']
            absorption = sh.get('absorption_volume', 0)
            abs_threshold = self._get_absorption_threshold(symbol)
            if absorption <= 0:
                boost -= 3
            elif absorption > abs_threshold * 5:
                boost += 2
            elif absorption > abs_threshold * 2:
                boost += 1

            if sh.get('directional_pct', 0) > 0.85:
                boost += 2

        if 'order_flow' in metadata:
            of = metadata['order_flow']
            buy_ratio = of.get('buy_ratio', 0.5)
            if buy_ratio > 0.75 or buy_ratio < 0.25:
                boost += 1.5
            elif buy_ratio > 0.65 or buy_ratio < 0.35:
                boost += 0.5

            large_count = of.get('large_buys', 0) + of.get('large_sells', 0)
            if large_count > 10:
                boost += 1.5
            elif large_count >= 5:
                boost += 0.5

        if 'events' in metadata and len(metadata['events']) >= 2:
            boost += 1

        return min(boost, 5.0)

    def get_recent_trend(self, signal_type: str, window: int = 10) -> float:
        history = self.signal_history.get(signal_type, [])
        if not history:
            return 0.5
        recent = history[-window:]
        return sum(1 for r in recent if r) / len(recent) if recent else 0.5

    def record_result_legacy(self, signal_type: str, was_successful: bool):
        """Legacy interface — calls record_result without setup_key."""
        self.record_result(signal_type, was_successful)

    def get_win_rate(self, signal_type: str) -> float:
        return self.win_rates.get(signal_type, 0.5)

    def get_setup_win_rate(self, setup_key: str) -> Optional[float]:
        """Get win rate for a specific setup_key, or None if not enough data."""
        if setup_key in self._setup_win_rates:
            if len(self._setup_history.get(setup_key, [])) >= MIN_SETUP_SAMPLES:
                return self._setup_win_rates[setup_key]
        return None

    def get_signal_count(self, signal_type: str) -> int:
        return len(self.signal_history.get(signal_type, []))

    def get_overall_stats(self) -> dict:
        total_signals = sum(len(h) for h in self.signal_history.values())
        total_wins = sum(sum(1 for r in h if r) for h in self.signal_history.values())

        # Setup-level stats summary
        active_setups = {
            k: {
                "samples": len(v),
                "win_rate": self._setup_win_rates.get(k, 0.5),
            }
            for k, v in self._setup_history.items()
            if len(v) >= MIN_SETUP_SAMPLES
        }

        return {
            "total_signals": total_signals,
            "total_wins": total_wins,
            "overall_win_rate": total_wins / max(total_signals, 1),
            "scores_calculated": self._scores_calculated,
            "active_setups": len(active_setups),
            "total_setups_tracked": len(self._setup_history),
            "per_type": {
                signal_type: {
                    "count": len(history),
                    "wins": sum(1 for r in history if r),
                    "win_rate": self.win_rates[signal_type],
                }
                for signal_type, history in self.signal_history.items()
                if history
            },
            "top_setups": dict(
                sorted(active_setups.items(), key=lambda x: x[1]["win_rate"], reverse=True)[:10]
            ),
        }

    def reset_history(self, signal_type: Optional[str] = None):
        if signal_type:
            self.signal_history[signal_type] = []
            self.win_rates[signal_type] = 0.5
            # Also clear setup keys starting with this signal_type
            to_remove = [k for k in self._setup_history if k.startswith(signal_type)]
            for k in to_remove:
                del self._setup_history[k]
                self._setup_win_rates.pop(k, None)
        else:
            for st in self.signal_history:
                self.signal_history[st] = []
                self.win_rates[st] = 0.5
            self._setup_history.clear()
            self._setup_win_rates.clear()

    def export_stats(self) -> dict:
        return {
            "timestamp": datetime.now().isoformat(),
            "win_rates": self.win_rates.copy(),
            "setup_win_rates": dict(self._setup_win_rates),
            "history_lengths": {st: len(h) for st, h in self.signal_history.items()},
            "setup_history_lengths": {k: len(v) for k, v in self._setup_history.items()},
            "overall_stats": self.get_overall_stats(),
        }
