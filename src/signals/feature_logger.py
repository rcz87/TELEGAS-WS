# Feature Logger - Captures full feature snapshot at signal birth
#
# Every signal gets a complete feature row saved to DB.
# This becomes the training dataset for future ML models.
# Purely observational — never modifies signals or blocks the pipeline.

import time
from typing import Optional

from ..utils.logger import setup_logger


class FeatureLogger:
    """Extracts and saves signal features to database for ML training."""

    def __init__(self, db=None):
        self.db = db
        self.logger = setup_logger("FeatureLogger", "INFO")
        self._logged = 0

    def set_db(self, db):
        self.db = db

    def extract_features(
        self,
        signal,
        symbol: str,
        setup_key: str,
        base_confidence: float,
        adjusted_confidence: float,
        final_confidence: float,
        filter_assessment: str = "",
        leading_score: float = 0,
        signal_id: int = 0,
    ) -> dict:
        """
        Extract flat feature dict from a TradingSignal and its metadata.

        Returns a dict matching the signal_features table columns.
        """
        meta = signal.metadata or {}
        sh = meta.get("stop_hunt", {})
        of = meta.get("order_flow", {})
        ctx = meta.get("market_context", {})
        events = meta.get("events", [])

        # Parse tier and session from setup_key
        parts = setup_key.split("|") if setup_key else []
        symbol_tier = parts[2] if len(parts) > 2 else ""
        session = parts[6] if len(parts) > 6 else ""

        return {
            "signal_id": signal_id,
            "symbol": symbol,
            "signal_type": signal.signal_type,
            "direction": signal.direction,
            "setup_key": setup_key,
            "symbol_tier": symbol_tier,
            "session": session,
            # Confidence stages
            "base_confidence": base_confidence,
            "adjusted_confidence": adjusted_confidence,
            "final_confidence": final_confidence,
            # Stop hunt
            "liq_volume": sh.get("total_volume", 0),
            "liq_count": sh.get("liquidation_count", 0),
            "directional_pct": sh.get("directional_pct", 0),
            "absorption_volume": sh.get("absorption_volume", 0),
            # Order flow
            "buy_ratio": of.get("buy_ratio", 0),
            "net_delta": of.get("net_delta", 0),
            "large_buys": of.get("large_buys", 0),
            "large_sells": of.get("large_sells", 0),
            "total_trades": of.get("total_trades", 0),
            # Events
            "event_count": len(events) if isinstance(events, list) else 0,
            # Market context
            "oi_usd": ctx.get("oi_usd", 0),
            "oi_change_pct": ctx.get("oi_change_1h_pct", 0),
            "funding_rate": ctx.get("funding_rate", 0),
            "spot_cvd_slope": ctx.get("spot_cvd_slope", 0),
            "spot_cvd_direction": ctx.get("spot_cvd_direction", ""),
            "futures_cvd_slope": ctx.get("futures_cvd_slope", 0),
            "futures_cvd_direction": ctx.get("futures_cvd_direction", ""),
            "orderbook_delta": ctx.get("orderbook_delta", 0),
            "orderbook_dominant": ctx.get("orderbook_dominant", ""),
            "whale_count": ctx.get("whale_count", 0),
            "whale_max_usd": ctx.get("whale_max_usd", 0),
            "price": ctx.get("price", 0),
            "volume_24h": ctx.get("volume_24h", 0),
            # Filter / leading
            "filter_assessment": filter_assessment,
            "leading_score": leading_score,
            # Timestamp
            "created_at": time.time(),
        }

    async def log_signal(self, features: dict) -> Optional[int]:
        """Save features to DB. Returns feature row ID or None on failure."""
        if not self.db:
            return None
        try:
            row_id = await self.db.save_signal_features(features)
            self._logged += 1
            return row_id
        except Exception as e:
            self.logger.error(f"Feature logging failed: {e}")
            return None

    async def update_outcome(
        self, signal_id: int, outcome: str, pnl_pct: float,
        mfe_pct: float = 0, mae_pct: float = 0,
        excursion_ratio: float = 0, time_to_resolution: float = 0,
    ):
        """Update feature row with outcome after evaluation."""
        if not self.db:
            return
        try:
            await self.db.update_signal_features_outcome(
                signal_id, outcome, pnl_pct,
                mfe_pct, mae_pct, excursion_ratio, time_to_resolution,
            )
        except Exception as e:
            self.logger.error(f"Feature outcome update failed: {e}")

    def get_stats(self) -> dict:
        return {"features_logged": self._logged}
