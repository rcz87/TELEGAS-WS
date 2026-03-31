# Signal Lifecycle Manager
#
# Manages signal states: NEW → ACTIVE → WEAKENING → EXPIRED / SUPERSEDED
# Provides per-coin primary signal selection with anti-flip logic.
#
# Non-destructive: signals still flow through existing pipeline.
# This layer sits AFTER add_signal() and manages lifecycle state.

import time
import threading
from copy import deepcopy
from typing import Dict, List, Optional, Tuple

from ..utils.logger import setup_logger


# ── Default expiry by signal type (seconds) ─────────────────────────

DEFAULT_EXPIRY = {
    "STOP_HUNT": 600,         # 10 minutes
    "ACCUMULATION": 900,      # 15 minutes
    "DISTRIBUTION": 900,      # 15 minutes
    "WHALE_ACCUMULATION": 600,
    "WHALE_DISTRIBUTION": 600,
    "VOLUME_SPIKE": 300,      # 5 minutes
    "EVENT": 600,
    "DISCOVERY": 120,         # 2 minutes
}

# Bonus expiry for high confidence (added to base)
HIGH_CONFIDENCE_BONUS = 300     # +5 min if confidence >= 85
VERY_HIGH_CONFIDENCE_BONUS = 600  # +10 min if confidence >= 92

# Freshness thresholds
WEAKENING_THRESHOLD = 0.35      # freshness below this → WEAKENING

# Anti-flip: minimum seconds before an opposite signal can supersede
REVERSAL_CONFIRMATION_WINDOW = 60  # 1 minute

# Supersede: how much stronger effective score must be to replace
SUPERSEDE_SCORE_MARGIN = 5.0

# How long to keep expired/superseded in recent history
HISTORY_RETENTION_SECONDS = 1800  # 30 minutes


# ── Lifecycle states ────────────────────────────────────────────────

STATUS_NEW = "NEW"
STATUS_ACTIVE = "ACTIVE"
STATUS_WEAKENING = "WEAKENING"
STATUS_EXPIRED = "EXPIRED"
STATUS_SUPERSEDED = "SUPERSEDED"


class ManagedSignal:
    """A signal with lifecycle state."""

    __slots__ = (
        "id", "symbol", "signal_type", "direction", "confidence",
        "description", "market_context", "leading_label",
        "created_at", "expires_at", "status",
        "invalidation_reason", "superseded_by_id",
        "metadata",
    )

    def __init__(self, signal_data: dict, expiry_config: dict = None):
        cfg = expiry_config or DEFAULT_EXPIRY
        now = time.time()

        self.id = signal_data.get("id", 0)
        self.symbol = signal_data.get("symbol", "")
        self.signal_type = signal_data.get("type", "")
        self.direction = signal_data.get("direction", "")
        self.confidence = signal_data.get("confidence", 0)
        self.description = signal_data.get("description", "")
        self.market_context = signal_data.get("market_context", "")
        self.leading_label = signal_data.get("leading_label", "")
        self.metadata = {k: v for k, v in signal_data.items()
                         if k not in ("id", "symbol", "type", "direction",
                                      "confidence", "description",
                                      "market_context", "leading_label")}

        self.created_at = now
        self.status = STATUS_NEW
        self.invalidation_reason = ""
        self.superseded_by_id = None

        # Calculate expiry
        base_expiry = cfg.get(self.signal_type, 600)
        if self.confidence >= 92:
            base_expiry += VERY_HIGH_CONFIDENCE_BONUS
        elif self.confidence >= 85:
            base_expiry += HIGH_CONFIDENCE_BONUS
        self.expires_at = now + base_expiry

    @property
    def age_seconds(self) -> float:
        return time.time() - self.created_at

    @property
    def remaining_seconds(self) -> float:
        return max(0, self.expires_at - time.time())

    @property
    def freshness(self) -> float:
        """1.0 = brand new, 0.0 = expired. Linear decay."""
        total = self.expires_at - self.created_at
        if total <= 0:
            return 0.0
        return max(0.0, min(1.0, self.remaining_seconds / total))

    @property
    def effective_score(self) -> float:
        """Confidence × freshness for ranking. Decays over time."""
        return self.confidence * self.freshness

    @property
    def is_alive(self) -> bool:
        return self.status in (STATUS_NEW, STATUS_ACTIVE, STATUS_WEAKENING)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "signal_type": self.signal_type,
            "direction": self.direction,
            "confidence": self.confidence,
            "description": self.description,
            "market_context": self.market_context,
            "leading_label": self.leading_label,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "age_seconds": round(self.age_seconds, 1),
            "remaining_seconds": round(self.remaining_seconds, 1),
            "freshness": round(self.freshness, 3),
            "effective_score": round(self.effective_score, 1),
            "status": self.status,
            "invalidation_reason": self.invalidation_reason,
            "superseded_by_id": self.superseded_by_id,
        }


class SignalLifecycleManager:
    """
    Manages signal lifecycle per coin.

    Thread-safe. Call from main.py after dashboard_api.add_signal().

    Methods:
        ingest(signal_data)   — register a new signal
        tick()                — update all states (call periodically)
        get_primary(symbol)   — get current primary signal for coin
        get_active_signals()  — get all alive signals
        get_coin_state(sym)   — full state for one coin
        get_all_coin_states() — full state for all coins
    """

    def __init__(self, expiry_config: dict = None):
        self.logger = setup_logger("SignalLifecycle", "INFO")
        self._expiry_config = expiry_config or DEFAULT_EXPIRY
        self._lock = threading.Lock()

        # Per-coin: current primary + recent history
        # Key: symbol, Value: {"primary": ManagedSignal or None, "history": [ManagedSignal]}
        self._coins: Dict[str, dict] = {}

    def _ensure_coin(self, symbol: str):
        if symbol not in self._coins:
            self._coins[symbol] = {"primary": None, "history": []}

    # ── Ingest new signal ───────────────────────────────────────────

    def ingest(self, signal_data: dict) -> dict:
        """
        Register a new signal from the pipeline.

        Returns the ManagedSignal as dict (with lifecycle fields).
        """
        ms = ManagedSignal(signal_data, self._expiry_config)
        symbol = ms.symbol

        with self._lock:
            self._ensure_coin(symbol)
            coin = self._coins[symbol]
            current = coin["primary"]

            # Transition NEW → ACTIVE immediately
            ms.status = STATUS_ACTIVE

            if current is None or not current.is_alive:
                # No active primary — this becomes primary
                if current and current.is_alive:
                    self._invalidate(current, STATUS_EXPIRED, "expired_by_time")
                coin["primary"] = ms
            else:
                # There's an active primary — evaluate supersede
                should_replace, reason = self._should_supersede(current, ms)
                if should_replace:
                    self._invalidate(current, STATUS_SUPERSEDED, reason,
                                     superseded_by=ms.id)
                    coin["primary"] = ms
                else:
                    # New signal is weaker — goes to history, not primary
                    coin["history"].append(ms)

            self.logger.info(
                f"Signal ingested: {symbol} {ms.signal_type} {ms.direction} "
                f"conf={ms.confidence} status={ms.status} "
                f"primary={'YES' if coin['primary'] is ms else 'NO'}"
            )

        return ms.to_dict()

    def _should_supersede(self, current: ManagedSignal, new: ManagedSignal) -> Tuple[bool, str]:
        """Decide if new signal should replace current primary."""

        # Same direction + same type: replace if stronger
        if new.direction == current.direction and new.signal_type == current.signal_type:
            if new.effective_score > current.effective_score + SUPERSEDE_SCORE_MARGIN:
                return True, "superseded_by_stronger_signal"
            return False, ""

        # Same direction, different type: replace if significantly stronger
        if new.direction == current.direction:
            if new.effective_score > current.effective_score + SUPERSEDE_SCORE_MARGIN:
                return True, "superseded_by_stronger_signal"
            return False, ""

        # Opposite direction — reversal
        # Anti-flip: require confirmation window
        if current.age_seconds < REVERSAL_CONFIRMATION_WINDOW:
            return False, ""  # Too soon to flip

        # Opposite direction + past confirmation window
        if new.effective_score > current.effective_score:
            return True, "superseded_by_opposite_signal"

        return False, ""

    def _invalidate(self, ms: ManagedSignal, status: str, reason: str,
                    superseded_by: int = None):
        """Move signal to invalidated state and push to history."""
        ms.status = status
        ms.invalidation_reason = reason
        ms.superseded_by_id = superseded_by

        symbol = ms.symbol
        self._ensure_coin(symbol)
        history = self._coins[symbol]["history"]
        history.append(ms)

    # ── Periodic tick — update all states ───────────────────────────

    def tick(self):
        """
        Update all signal states. Call every ~5-10 seconds.

        Transitions:
        - ACTIVE → WEAKENING (freshness below threshold)
        - ACTIVE/WEAKENING → EXPIRED (past expires_at)
        - Cleanup old history entries
        """
        now = time.time()

        with self._lock:
            for symbol, coin in self._coins.items():
                primary = coin["primary"]
                if primary and primary.is_alive:
                    # Check expiry
                    if now >= primary.expires_at:
                        self._invalidate(primary, STATUS_EXPIRED, "expired_by_time")
                        coin["primary"] = None
                    # Check weakening
                    elif primary.freshness < WEAKENING_THRESHOLD:
                        if primary.status == STATUS_ACTIVE:
                            primary.status = STATUS_WEAKENING

                # Cleanup old history
                cutoff = now - HISTORY_RETENTION_SECONDS
                coin["history"] = [
                    s for s in coin["history"]
                    if s.created_at > cutoff
                ]

    # ── Query methods ───────────────────────────────────────────────

    def get_primary(self, symbol: str) -> Optional[dict]:
        """Get current primary signal for a coin, or None."""
        with self._lock:
            coin = self._coins.get(symbol)
            if coin and coin["primary"] and coin["primary"].is_alive:
                return coin["primary"].to_dict()
            return None

    def get_coin_state(self, symbol: str) -> dict:
        """Full lifecycle state for one coin."""
        with self._lock:
            coin = self._coins.get(symbol)
            if not coin:
                return {"symbol": symbol, "primary": None, "recent": []}

            primary = coin["primary"]
            return {
                "symbol": symbol,
                "primary": primary.to_dict() if primary and primary.is_alive else None,
                "recent": [s.to_dict() for s in reversed(coin["history"][-10:])],
            }

    def get_all_coin_states(self) -> Dict[str, dict]:
        """Full lifecycle state for all coins with active/recent signals."""
        with self._lock:
            result = {}
            for symbol in self._coins:
                coin = self._coins[symbol]
                primary = coin["primary"]
                result[symbol] = {
                    "primary": primary.to_dict() if primary and primary.is_alive else None,
                    "recent": [s.to_dict() for s in reversed(coin["history"][-5:])],
                }
            return result

    def get_active_signals(self) -> List[dict]:
        """Get all alive primary signals across all coins."""
        with self._lock:
            result = []
            for coin in self._coins.values():
                primary = coin["primary"]
                if primary and primary.is_alive:
                    result.append(primary.to_dict())
            result.sort(key=lambda s: s["effective_score"], reverse=True)
            return result

    def get_stats(self) -> dict:
        with self._lock:
            active = sum(
                1 for c in self._coins.values()
                if c["primary"] and c["primary"].is_alive
            )
            total_history = sum(len(c["history"]) for c in self._coins.values())
            return {
                "coins_tracked": len(self._coins),
                "active_primaries": active,
                "history_entries": total_history,
            }
