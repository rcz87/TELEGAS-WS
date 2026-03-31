# Model Trainer - Trains prediction model from signal_features history
#
# Uses GradientBoostingClassifier (sklearn, no extra deps) as primary model.
# Falls back to LogisticRegression if sample count is very low.
# Saves model + metadata to disk as versioned .joblib files.
#
# NOT called in the hot path. Run manually or on a schedule (e.g. nightly).
#
# Usage:
#   trainer = ModelTrainer(db)
#   result = await trainer.train()
#   if result: print(result["metrics"])

import time
import json
from pathlib import Path
from typing import Optional

import joblib
import numpy as np

from ..utils.logger import setup_logger
from .dataset_builder import DatasetBuilder

# Where models are saved
MODEL_DIR = Path(__file__).parent.parent.parent / "data" / "models"

# Minimum samples required for training
MIN_TRAINING_SAMPLES = 30

# Minimum samples for GradientBoosting (below this → LogisticRegression)
GB_MIN_SAMPLES = 80


class ModelTrainer:
    """Trains a binary classifier: signal → WIN/PARTIAL (1) vs LOSS (0)."""

    def __init__(self, db=None):
        self.db = db
        self.logger = setup_logger("ModelTrainer", "INFO")
        self._dataset_builder = DatasetBuilder(db)
        MODEL_DIR.mkdir(parents=True, exist_ok=True)

    def set_db(self, db):
        self.db = db
        self._dataset_builder.set_db(db)

    async def train(self) -> Optional[dict]:
        """
        Train model from DB history.

        Returns dict with model_path, metrics, model_type, sample_count.
        Returns None if insufficient data.
        """
        df = await self._dataset_builder.build(
            min_age_hours=0, min_rows=MIN_TRAINING_SAMPLES
        )
        if df is None:
            self.logger.info("Insufficient data for training")
            return None

        X, y = self._dataset_builder.split_features_labels(df)
        n_samples = len(X)
        self.logger.info(f"Training on {n_samples} samples, {X.shape[1]} features")

        # Choose model based on sample size
        if n_samples >= GB_MIN_SAMPLES:
            model, model_type = self._train_gradient_boosting(X, y)
        else:
            model, model_type = self._train_logistic(X, y)

        # Evaluate with cross-validation
        metrics = self._evaluate(model, X, y)
        metrics["model_type"] = model_type
        metrics["n_samples"] = n_samples
        metrics["n_features"] = X.shape[1]
        metrics["feature_names"] = list(X.columns)
        metrics["trained_at"] = time.time()

        # Save model + metadata
        version = int(time.time())
        model_path = MODEL_DIR / f"model_v{version}.joblib"
        meta_path = MODEL_DIR / f"model_v{version}_meta.json"

        joblib.dump({
            "model": model,
            "feature_names": list(X.columns),
            "model_type": model_type,
            "version": version,
        }, model_path)

        with open(meta_path, "w") as f:
            # Convert numpy types for JSON serialization
            json_metrics = {}
            for k, v in metrics.items():
                if isinstance(v, (np.floating, np.integer)):
                    json_metrics[k] = float(v)
                elif isinstance(v, np.ndarray):
                    json_metrics[k] = v.tolist()
                else:
                    json_metrics[k] = v
            json.dump(json_metrics, f, indent=2)

        # Update "latest" symlink
        latest_path = MODEL_DIR / "latest.joblib"
        latest_meta = MODEL_DIR / "latest_meta.json"
        if latest_path.exists():
            latest_path.unlink()
        if latest_meta.exists():
            latest_meta.unlink()
        latest_path.symlink_to(model_path.name)
        latest_meta.symlink_to(meta_path.name)

        self.logger.info(
            f"Model trained: {model_type}, AUC={metrics.get('auc', 0):.3f}, "
            f"accuracy={metrics.get('accuracy', 0):.3f}, "
            f"samples={n_samples}, saved={model_path.name}"
        )

        return {
            "model_path": str(model_path),
            "version": version,
            "metrics": metrics,
        }

    def _train_gradient_boosting(self, X, y):
        from sklearn.ensemble import GradientBoostingClassifier

        model = GradientBoostingClassifier(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.1,
            subsample=0.8,
            min_samples_leaf=5,
            random_state=42,
        )
        model.fit(X, y)
        return model, "GradientBoosting"

    def _train_logistic(self, X, y):
        from sklearn.linear_model import LogisticRegression

        model = LogisticRegression(
            max_iter=1000,
            C=1.0,
            random_state=42,
        )
        model.fit(X, y)
        return model, "LogisticRegression"

    def _evaluate(self, model, X, y) -> dict:
        from sklearn.model_selection import cross_val_predict
        from sklearn.metrics import (
            roc_auc_score, accuracy_score, precision_score,
            recall_score, f1_score, brier_score_loss,
        )

        # Cross-validated predictions
        n_splits = min(5, max(2, len(y) // 10))
        try:
            y_pred_proba = cross_val_predict(
                model, X, y, cv=n_splits, method="predict_proba"
            )[:, 1]
            y_pred = (y_pred_proba >= 0.5).astype(int)
        except Exception:
            # Fallback: train predictions (biased but better than nothing)
            y_pred_proba = model.predict_proba(X)[:, 1]
            y_pred = model.predict(X)

        metrics = {
            "accuracy": float(accuracy_score(y, y_pred)),
            "precision": float(precision_score(y, y_pred, zero_division=0)),
            "recall": float(recall_score(y, y_pred, zero_division=0)),
            "f1": float(f1_score(y, y_pred, zero_division=0)),
            "brier_score": float(brier_score_loss(y, y_pred_proba)),
        }

        try:
            metrics["auc"] = float(roc_auc_score(y, y_pred_proba))
        except ValueError:
            metrics["auc"] = 0.5  # Only one class present

        # Feature importance (for GradientBoosting)
        if hasattr(model, "feature_importances_"):
            importances = model.feature_importances_
            top_idx = np.argsort(importances)[-10:][::-1]
            metrics["top_features"] = [
                {"name": X.columns[i], "importance": float(importances[i])}
                for i in top_idx
            ]

        return metrics

    @staticmethod
    def get_latest_model_path() -> Optional[Path]:
        """Get path to latest trained model, or None."""
        latest = MODEL_DIR / "latest.joblib"
        if latest.exists():
            return latest.resolve()
        return None

    @staticmethod
    def get_latest_meta() -> Optional[dict]:
        """Get metadata of latest trained model."""
        meta = MODEL_DIR / "latest_meta.json"
        if meta.exists():
            with open(meta) as f:
                return json.load(f)
        return None
