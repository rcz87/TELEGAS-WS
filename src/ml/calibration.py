# Calibration Layer - Maps confidence scores to observed win rates
#
# Answers: "when the system says 80%, how often does it actually win?"
#
# Bins signals into score buckets (5-point ranges), computes observed
# win rate per bucket, and provides a calibration table + adjustment.
#
# Usage:
#   cal = CalibrationTable()
#   await cal.build_from_db(db)          # build from history
#   cal.calibrated_score(85.0)            # → observed win rate for 80-84 bucket
#   cal.adjustment(85.0)                  # → delta to apply to raw score
#   cal.get_table()                       # → full calibration table

import time
from typing import Dict, List, Optional, Tuple

from ..utils.logger import setup_logger


# Score bucket width
BUCKET_WIDTH = 5

# Minimum signals in a bucket before we trust its calibration
MIN_BUCKET_SAMPLES = 10

# Maximum confidence adjustment from calibration (guardrail)
MAX_CALIBRATION_ADJUSTMENT = 10.0


class CalibrationTable:
    """Maps raw confidence scores to observed win rates."""

    def __init__(self):
        self.logger = setup_logger("Calibration", "INFO")
        # Key: bucket_start (e.g., 55, 60, 65, ..., 95)
        # Value: {"count": int, "wins": int, "win_rate": float, "avg_pnl": float}
        self._buckets: Dict[int, dict] = {}
        self._built_at: float = 0
        self._total_signals: int = 0

    def _score_to_bucket(self, score: float) -> int:
        """Map score to bucket start. E.g., 83.7 → 80."""
        return int(score // BUCKET_WIDTH) * BUCKET_WIDTH

    async def build_from_db(self, db) -> bool:
        """
        Build calibration table from signal_features table.

        Returns True if table was built (enough data), False otherwise.
        """
        try:
            rows = await db.get_training_dataset(min_age_hours=0, limit=50000)

            if not rows:
                self.logger.info("No labeled data for calibration")
                return False

            self._buckets.clear()

            for row in rows:
                conf = row.get("final_confidence") or row.get("adjusted_confidence", 0)
                outcome = row.get("outcome", "")
                pnl = row.get("pnl_pct", 0) or 0

                if not conf or outcome not in ("WIN", "PARTIAL", "LOSS"):
                    continue

                bucket = self._score_to_bucket(conf)

                if bucket not in self._buckets:
                    self._buckets[bucket] = {
                        "count": 0, "wins": 0,
                        "pnl_sum": 0.0, "mfe_sum": 0.0, "mae_sum": 0.0,
                    }

                b = self._buckets[bucket]
                b["count"] += 1
                b["pnl_sum"] += pnl
                b["mfe_sum"] += row.get("mfe_pct", 0) or 0
                b["mae_sum"] += row.get("mae_pct", 0) or 0

                if outcome in ("WIN", "PARTIAL"):
                    b["wins"] += 1

            # Compute win rates
            for bucket, b in self._buckets.items():
                b["win_rate"] = b["wins"] / b["count"] if b["count"] > 0 else 0
                b["avg_pnl"] = b["pnl_sum"] / b["count"] if b["count"] > 0 else 0
                b["avg_mfe"] = b["mfe_sum"] / b["count"] if b["count"] > 0 else 0
                b["avg_mae"] = b["mae_sum"] / b["count"] if b["count"] > 0 else 0

            self._built_at = time.time()
            self._total_signals = sum(b["count"] for b in self._buckets.values())

            self.logger.info(
                f"Calibration table built: {len(self._buckets)} buckets, "
                f"{self._total_signals} signals"
            )
            return True

        except Exception as e:
            self.logger.error(f"Calibration build failed: {e}")
            return False

    def calibrated_score(self, raw_score: float) -> Optional[float]:
        """
        Get observed win rate for a score bucket.

        Returns None if bucket has insufficient data.
        """
        bucket = self._score_to_bucket(raw_score)
        b = self._buckets.get(bucket)

        if not b or b["count"] < MIN_BUCKET_SAMPLES:
            return None

        return b["win_rate"] * 100  # Return as percentage to match confidence scale

    def adjustment(self, raw_score: float) -> float:
        """
        Calculate calibration adjustment for a raw score.

        Returns delta to add to raw score, capped by MAX_CALIBRATION_ADJUSTMENT.
        Returns 0 if insufficient data for this bucket.
        """
        calibrated = self.calibrated_score(raw_score)
        if calibrated is None:
            return 0.0

        delta = calibrated - raw_score
        # Clamp adjustment
        return max(-MAX_CALIBRATION_ADJUSTMENT, min(delta, MAX_CALIBRATION_ADJUSTMENT))

    def get_table(self) -> List[dict]:
        """
        Get full calibration table as list of dicts.

        Sorted by bucket, each entry has:
        - bucket: "70-74"
        - count: number of signals
        - wins: number of wins
        - win_rate: observed win rate (0-1)
        - avg_pnl: average P&L %
        - avg_mfe: average max favorable excursion %
        - avg_mae: average max adverse excursion %
        - trusted: True if count >= MIN_BUCKET_SAMPLES
        """
        table = []
        for bucket in sorted(self._buckets.keys()):
            b = self._buckets[bucket]
            table.append({
                "bucket": f"{bucket}-{bucket + BUCKET_WIDTH - 1}",
                "count": b["count"],
                "wins": b["wins"],
                "win_rate": round(b["win_rate"], 3),
                "avg_pnl": round(b.get("avg_pnl", 0), 2),
                "avg_mfe": round(b.get("avg_mfe", 0), 2),
                "avg_mae": round(b.get("avg_mae", 0), 2),
                "trusted": b["count"] >= MIN_BUCKET_SAMPLES,
            })
        return table

    def get_stats(self) -> dict:
        """Get calibration summary."""
        trusted = [b for b in self._buckets.values() if b["count"] >= MIN_BUCKET_SAMPLES]
        return {
            "total_signals": self._total_signals,
            "total_buckets": len(self._buckets),
            "trusted_buckets": len(trusted),
            "built_at": self._built_at,
            "age_minutes": round((time.time() - self._built_at) / 60, 1) if self._built_at else 0,
        }

    def is_stale(self, max_age_hours: int = 6) -> bool:
        """Check if calibration table needs rebuilding."""
        if not self._built_at:
            return True
        return (time.time() - self._built_at) > max_age_hours * 3600
