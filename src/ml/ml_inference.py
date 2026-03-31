# ML Inference - Score signals with trained model
#
# Loads the latest model and produces ml_probability for new signals.
# Runs in SHADOW mode by default: logs ML score but does not affect alerts.
#
# Modes:
#   "off"      — disabled, no scoring
#   "shadow"   — score + log, don't affect pipeline
#   "blended"  — blend with rule-based confidence (configurable weight)
#   "advisory" — show in dashboard/API only, don't affect Telegram
#
# Usage:
#   engine = MLInferenceEngine()
#   engine.load_model()
#   result = engine.predict(features_dict)

import time
from pathlib import Path
from typing import Optional

import joblib
import numpy as np
import pandas as pd

from ..utils.logger import setup_logger
from .model_trainer import MODEL_DIR

# Default blend weight: how much ML vs rule-based
DEFAULT_ML_WEIGHT = 0.3  # 30% ML, 70% rule-based


class MLInferenceEngine:
    """Score signals with the latest trained model."""

    def __init__(self, mode: str = "shadow", ml_weight: float = DEFAULT_ML_WEIGHT):
        """
        Args:
            mode: "off", "shadow", "blended", "advisory"
            ml_weight: weight for ML score in blended mode (0-1)
        """
        self.mode = mode
        self.ml_weight = max(0.0, min(1.0, ml_weight))
        self.logger = setup_logger("MLInference", "INFO")

        self._model = None
        self._feature_names = None
        self._model_type = None
        self._model_version = None
        self._loaded_at = 0
        self._predictions_made = 0

    @property
    def is_active(self) -> bool:
        return self.mode != "off" and self._model is not None

    def load_model(self) -> bool:
        """Load latest model from disk. Returns True if loaded."""
        latest = MODEL_DIR / "latest.joblib"
        if not latest.exists():
            self.logger.info("No trained model found")
            return False

        try:
            data = joblib.load(latest.resolve())
            self._model = data["model"]
            self._feature_names = data["feature_names"]
            self._model_type = data["model_type"]
            self._model_version = data.get("version", 0)
            self._loaded_at = time.time()

            self.logger.info(
                f"Model loaded: {self._model_type} v{self._model_version} "
                f"({len(self._feature_names)} features)"
            )
            return True
        except Exception as e:
            self.logger.error(f"Model load failed: {e}")
            return False

    def predict(self, features: dict) -> Optional[dict]:
        """
        Score a signal using the ML model.

        Args:
            features: flat dict matching signal_features columns
                     (from FeatureLogger.extract_features())

        Returns:
            dict with ml_probability, ml_label, model_type, model_version
            or None if model not loaded / mode is off
        """
        if not self.is_active:
            return None

        try:
            # Build feature vector in correct column order
            row = {}
            for fname in self._feature_names:
                row[fname] = features.get(fname, 0)

            X = pd.DataFrame([row])
            X = X.fillna(0)

            # Handle one-hot encoded columns that may be missing
            for col in self._feature_names:
                if col not in X.columns:
                    X[col] = 0
            X = X[self._feature_names]

            proba = self._model.predict_proba(X)[0]
            # proba[1] = probability of class 1 (WIN/PARTIAL)
            ml_probability = float(proba[1]) if len(proba) > 1 else float(proba[0])

            self._predictions_made += 1

            return {
                "ml_probability": round(ml_probability, 4),
                "ml_confidence": round(ml_probability * 100, 1),
                "ml_label": "WIN" if ml_probability >= 0.5 else "LOSS",
                "model_type": self._model_type,
                "model_version": self._model_version,
            }

        except Exception as e:
            self.logger.error(f"Prediction failed: {e}")
            return None

    def blend_score(self, rule_confidence: float, ml_result: dict) -> float:
        """
        Blend rule-based confidence with ML probability.

        Only used in "blended" mode.
        Returns adjusted confidence score (55-99 range).
        """
        if not ml_result or self.mode != "blended":
            return rule_confidence

        ml_conf = ml_result["ml_confidence"]
        blended = (
            (1 - self.ml_weight) * rule_confidence +
            self.ml_weight * ml_conf
        )
        return max(55.0, min(99.0, blended))

    def get_stats(self) -> dict:
        return {
            "mode": self.mode,
            "model_loaded": self._model is not None,
            "model_type": self._model_type,
            "model_version": self._model_version,
            "predictions_made": self._predictions_made,
            "ml_weight": self.ml_weight,
            "loaded_at": self._loaded_at,
        }
