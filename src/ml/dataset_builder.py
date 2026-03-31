# Dataset Builder - Builds ML-ready datasets from signal_features table
#
# Reads labeled feature rows from SQLite, cleans, encodes, and returns
# a pandas DataFrame ready for model training.
#
# Usage:
#   builder = DatasetBuilder(db)
#   df = await builder.build()
#   X, y = builder.split_features_labels(df)

import time
from typing import Optional, Tuple

import numpy as np
import pandas as pd

from ..utils.logger import setup_logger


# Numeric feature columns used for training
NUMERIC_FEATURES = [
    "base_confidence",
    "adjusted_confidence",
    # Stop hunt
    "liq_volume",
    "liq_count",
    "directional_pct",
    "absorption_volume",
    # Order flow
    "buy_ratio",
    "net_delta",
    "large_buys",
    "large_sells",
    "total_trades",
    # Events
    "event_count",
    # Market context
    "oi_usd",
    "oi_change_pct",
    "funding_rate",
    "spot_cvd_slope",
    "futures_cvd_slope",
    "orderbook_delta",
    "whale_count",
    "whale_max_usd",
    "price",
    "volume_24h",
    # Leading score
    "leading_score",
    # Rich outcome metrics (for analysis, not training input)
    "mfe_pct",
    "mae_pct",
    "excursion_ratio",
    "time_to_resolution",
]

# Categorical columns to one-hot encode
CATEGORICAL_FEATURES = [
    "signal_type",
    "direction",
    "symbol_tier",
    "session",
    "spot_cvd_direction",
    "futures_cvd_direction",
    "orderbook_dominant",
    "filter_assessment",
]

# Columns that are input features (not outcome/metadata)
INPUT_FEATURES = [
    "base_confidence", "adjusted_confidence",
    "liq_volume", "liq_count", "directional_pct", "absorption_volume",
    "buy_ratio", "net_delta", "large_buys", "large_sells", "total_trades",
    "event_count",
    "oi_usd", "oi_change_pct", "funding_rate",
    "spot_cvd_slope", "futures_cvd_slope",
    "orderbook_delta", "whale_count", "whale_max_usd",
    "price", "volume_24h", "leading_score",
]


class DatasetBuilder:
    """Builds ML-ready training datasets from signal_features table."""

    def __init__(self, db=None):
        self.db = db
        self.logger = setup_logger("DatasetBuilder", "INFO")

    def set_db(self, db):
        self.db = db

    async def build(
        self,
        min_age_hours: int = 1,
        limit: int = 10000,
        min_rows: int = 20,
    ) -> Optional[pd.DataFrame]:
        """
        Build training DataFrame from DB.

        Returns None if insufficient data (< min_rows labeled samples).
        """
        if not self.db:
            self.logger.warning("No database connection")
            return None

        rows = await self.db.get_training_dataset(
            min_age_hours=min_age_hours, limit=limit
        )

        if len(rows) < min_rows:
            self.logger.info(
                f"Insufficient data: {len(rows)} rows (need {min_rows})"
            )
            return None

        df = pd.DataFrame(rows)
        df = self._clean(df)
        self.logger.info(f"Dataset built: {len(df)} rows, {len(df.columns)} cols")
        return df

    def _clean(self, df: pd.DataFrame) -> pd.DataFrame:
        """Clean and type-cast the raw DataFrame."""
        # Fill numeric NaN with 0
        for col in NUMERIC_FEATURES:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

        # Fill categorical NaN with "unknown"
        for col in CATEGORICAL_FEATURES:
            if col in df.columns:
                df[col] = df[col].fillna("unknown").astype(str)

        # Binary label: 1 = WIN or PARTIAL, 0 = LOSS
        # Drop NEUTRAL (ambiguous for training)
        df = df[df["outcome"].isin(["WIN", "PARTIAL", "LOSS"])].copy()
        df["label"] = (df["outcome"].isin(["WIN", "PARTIAL"])).astype(int)

        return df

    def split_features_labels(
        self, df: pd.DataFrame, encode_categorical: bool = True
    ) -> Tuple[pd.DataFrame, pd.Series]:
        """
        Split DataFrame into feature matrix X and label vector y.

        Args:
            df: Cleaned DataFrame from build()
            encode_categorical: If True, one-hot encode categorical columns

        Returns:
            (X, y) tuple
        """
        y = df["label"]

        # Start with numeric input features
        feature_cols = [c for c in INPUT_FEATURES if c in df.columns]
        X = df[feature_cols].copy()

        # One-hot encode categoricals
        if encode_categorical:
            for col in CATEGORICAL_FEATURES:
                if col in df.columns:
                    dummies = pd.get_dummies(df[col], prefix=col, drop_first=True)
                    X = pd.concat([X, dummies], axis=1)

        # Fill any remaining NaN
        X = X.fillna(0)

        return X, y

    def get_summary(self, df: pd.DataFrame) -> dict:
        """Get dataset summary statistics."""
        if df is None or df.empty:
            return {"total": 0}

        wins = (df["label"] == 1).sum() if "label" in df.columns else 0
        losses = (df["label"] == 0).sum() if "label" in df.columns else 0

        summary = {
            "total": len(df),
            "wins": int(wins),
            "losses": int(losses),
            "win_rate": float(wins / max(wins + losses, 1)),
            "unique_symbols": int(df["symbol"].nunique()) if "symbol" in df.columns else 0,
            "unique_setups": int(df["setup_key"].nunique()) if "setup_key" in df.columns else 0,
            "date_range_days": 0,
        }

        if "created_at" in df.columns and len(df) > 1:
            span = df["created_at"].max() - df["created_at"].min()
            summary["date_range_days"] = round(span / 86400, 1)

        # Per signal type breakdown
        if "signal_type" in df.columns and "label" in df.columns:
            summary["per_type"] = {}
            for st in df["signal_type"].unique():
                subset = df[df["signal_type"] == st]
                st_wins = (subset["label"] == 1).sum()
                st_total = len(subset)
                summary["per_type"][st] = {
                    "count": int(st_total),
                    "win_rate": float(st_wins / max(st_total, 1)),
                }

        return summary
