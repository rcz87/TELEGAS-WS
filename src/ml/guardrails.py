# ML Guardrails - Safety layer preventing ML from degrading signal quality
#
# Rules:
# 1. ML adjustment capped at ±MAX_ML_ADJUSTMENT points
# 2. Model must have AUC >= MIN_AUC before blended mode activates
# 3. Model must be trained on >= MIN_TRAINING_SIGNALS samples
# 4. If recent ML-influenced signals perform worse than rule-only baseline,
#    auto-rollback to shadow mode
# 5. Weekly max threshold drift capped at ±MAX_WEEKLY_DRIFT
#
# Non-destructive: guardrails only constrain, never modify pipeline logic.

import time
from typing import Optional

from ..utils.logger import setup_logger


# ── Guardrail constants ─────────────────────────────────────────────

# Max confidence adjustment from ML blending
MAX_ML_ADJUSTMENT = 10.0

# Minimum model quality to allow blended mode
MIN_AUC = 0.55
MIN_TRAINING_SIGNALS = 50

# Performance monitoring: rolling window
PERF_WINDOW_SIGNALS = 30  # check last N ML-scored signals
MIN_ML_WIN_RATE = 0.40    # if ML-scored signals win less than this, rollback

# Max weekly threshold drift (for future auto-tuning)
MAX_WEEKLY_DRIFT = 5.0


class MLGuardrails:
    """Safety layer for ML inference."""

    def __init__(self):
        self.logger = setup_logger("MLGuardrails", "INFO")

        # Track ML-scored signal outcomes
        self._ml_outcomes: list = []  # list of bools (True=win, False=loss)
        self._rollback_active = False
        self._rollback_at = 0
        self._rollback_reason = ""

        # Track adjustments for drift detection
        self._adjustments_this_week: list = []  # list of floats
        self._week_start = time.time()

    # ── Pre-inference gate ──────────────────────────────────────────

    def should_allow_blended(self, model_meta: Optional[dict]) -> tuple:
        """
        Check if blended mode should be active.

        Returns (allowed: bool, reason: str)
        """
        if self._rollback_active:
            return False, f"rollback_active: {self._rollback_reason}"

        if not model_meta:
            return False, "no_model_metadata"

        auc = model_meta.get("auc", 0)
        if auc < MIN_AUC:
            return False, f"auc_too_low: {auc:.3f} < {MIN_AUC}"

        n_samples = model_meta.get("n_samples", 0)
        if n_samples < MIN_TRAINING_SIGNALS:
            return False, f"insufficient_samples: {n_samples} < {MIN_TRAINING_SIGNALS}"

        return True, "ok"

    # ── Post-inference clamp ────────────────────────────────────────

    def clamp_adjustment(self, rule_confidence: float, blended_confidence: float) -> float:
        """
        Clamp the ML adjustment to ±MAX_ML_ADJUSTMENT.

        Returns the clamped blended confidence.
        """
        delta = blended_confidence - rule_confidence
        clamped_delta = max(-MAX_ML_ADJUSTMENT, min(delta, MAX_ML_ADJUSTMENT))

        if abs(delta) > MAX_ML_ADJUSTMENT:
            self.logger.debug(
                f"ML adjustment clamped: {delta:+.1f} → {clamped_delta:+.1f}"
            )

        result = rule_confidence + clamped_delta

        # Track for weekly drift
        self._track_adjustment(clamped_delta)

        return max(55.0, min(99.0, result))

    def _track_adjustment(self, delta: float):
        """Track adjustment for weekly drift monitoring."""
        now = time.time()
        # Reset weekly window
        if now - self._week_start > 7 * 86400:
            self._adjustments_this_week.clear()
            self._week_start = now

        self._adjustments_this_week.append(delta)

    # ── Outcome tracking + auto-rollback ────────────────────────────

    def record_ml_outcome(self, was_successful: bool):
        """Record outcome of an ML-scored signal for performance monitoring."""
        self._ml_outcomes.append(was_successful)
        # Keep rolling window
        if len(self._ml_outcomes) > PERF_WINDOW_SIGNALS * 3:
            self._ml_outcomes = self._ml_outcomes[-PERF_WINDOW_SIGNALS * 3:]

        # Check performance
        self._check_performance()

    def _check_performance(self):
        """Auto-rollback if ML-scored signals underperform."""
        if len(self._ml_outcomes) < PERF_WINDOW_SIGNALS:
            return  # Not enough data

        recent = self._ml_outcomes[-PERF_WINDOW_SIGNALS:]
        win_rate = sum(1 for r in recent if r) / len(recent)

        if win_rate < MIN_ML_WIN_RATE:
            self._rollback_active = True
            self._rollback_at = time.time()
            self._rollback_reason = (
                f"ml_win_rate={win_rate:.1%} < {MIN_ML_WIN_RATE:.0%} "
                f"(last {PERF_WINDOW_SIGNALS} signals)"
            )
            self.logger.warning(f"ML ROLLBACK: {self._rollback_reason}")

    def clear_rollback(self):
        """Manually clear rollback (after retraining or investigation)."""
        self._rollback_active = False
        self._rollback_reason = ""
        self._ml_outcomes.clear()
        self.logger.info("ML rollback cleared manually")

    # ── Weekly drift check ──────────────────────────────────────────

    def get_weekly_drift(self) -> dict:
        """Get stats on how much ML has drifted confidence this week."""
        if not self._adjustments_this_week:
            return {"count": 0, "avg_delta": 0, "max_delta": 0, "within_limit": True}

        deltas = self._adjustments_this_week
        avg = sum(deltas) / len(deltas)
        max_abs = max(abs(d) for d in deltas)

        return {
            "count": len(deltas),
            "avg_delta": round(avg, 2),
            "max_delta": round(max_abs, 2),
            "within_limit": max_abs <= MAX_WEEKLY_DRIFT,
        }

    # ── Stats ───────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        recent = self._ml_outcomes[-PERF_WINDOW_SIGNALS:] if self._ml_outcomes else []
        win_rate = sum(1 for r in recent if r) / len(recent) if recent else 0

        return {
            "rollback_active": self._rollback_active,
            "rollback_reason": self._rollback_reason,
            "ml_outcomes_tracked": len(self._ml_outcomes),
            "recent_ml_win_rate": round(win_rate, 3),
            "weekly_drift": self.get_weekly_drift(),
            "max_ml_adjustment": MAX_ML_ADJUSTMENT,
            "min_auc_required": MIN_AUC,
            "min_training_signals": MIN_TRAINING_SIGNALS,
        }
