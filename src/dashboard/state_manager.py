# State Manager - Thread-safe centralized state for dashboard
#
# Replaces direct system_state dict + state_lock access with a single class.
# All reads return deepcopy (safe for cross-thread use).
# All writes are atomic (under lock).

import threading
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class StateManager:
    """Thread-safe state manager for dashboard data.

    Encapsulates system_state dict + lock + all mutation operations.
    Provides typed read methods that return deepcopied snapshots.
    """

    MAX_SIGNALS = 200

    def __init__(self):
        self._lock = threading.Lock()
        self._signal_id_counter = 0
        self._state: Dict[str, Any] = {
            "stats": {
                "messages_received": 0,
                "messages_processed": 0,
                "liquidations_processed": 0,
                "trades_processed": 0,
                "signals_generated": 0,
                "alerts_sent": 0,
                "errors": 0,
                "uptime_seconds": 0,
            },
            "coins": [],
            "signals": [],
            "order_flow": {},
        }

    # ── Read methods (all return deepcopy) ──────────────────────────

    def get_stats(self) -> dict:
        with self._lock:
            return deepcopy(self._state["stats"])

    def get_coins(self) -> List[dict]:
        with self._lock:
            return deepcopy(self._state["coins"])

    def get_coins_count(self) -> int:
        with self._lock:
            return len(self._state["coins"])

    def get_signals(self, limit: int = 50) -> List[dict]:
        with self._lock:
            return deepcopy(self._state["signals"][-limit:])

    def get_order_flow(self, symbol: str) -> dict:
        with self._lock:
            return deepcopy(self._state["order_flow"].get(symbol, {}))

    def get_full_snapshot(self) -> dict:
        with self._lock:
            return deepcopy(self._state)

    def get_monitored_coins(self) -> List[str]:
        with self._lock:
            return [c["symbol"] for c in self._state["coins"] if c.get("active", True)]

    def get_uptime(self) -> float:
        with self._lock:
            return self._state["stats"].get("uptime_seconds", 0)

    def coin_exists(self, symbol: str) -> bool:
        with self._lock:
            return any(c["symbol"] == symbol for c in self._state["coins"])

    # ── Write methods (all atomic under lock) ───────────────────────

    def update_stats(self, stats: dict):
        with self._lock:
            self._state["stats"] = deepcopy(stats)

    def update_order_flow(self, symbol: str, flow_data: dict):
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        with self._lock:
            self._state["order_flow"][symbol] = {
                **flow_data,
                "last_update": now,
            }
            coin = next((c for c in self._state["coins"] if c["symbol"] == symbol), None)
            if coin:
                coin.update(flow_data)
                coin["last_update"] = "just now"

    def add_signal(self, signal: dict) -> dict:
        """Add signal, returns the enriched signal_data dict (with id/time)."""
        now = datetime.now(timezone.utc)
        with self._lock:
            self._signal_id_counter += 1
            signal_data = {
                "id": self._signal_id_counter,
                "time": now.strftime("%H:%M:%S"),
                "timestamp": now.isoformat(),
                **signal,
            }
            self._state["signals"].append(signal_data)
            if len(self._state["signals"]) > self.MAX_SIGNALS:
                self._state["signals"] = self._state["signals"][-self.MAX_SIGNALS:]
        return signal_data

    def add_coin(self, symbol: str) -> bool:
        """Add coin if not duplicate. Returns True if added."""
        with self._lock:
            if any(c["symbol"] == symbol for c in self._state["coins"]):
                return False
            self._state["coins"].append({
                "symbol": symbol,
                "active": True,
                "buy_ratio": 0,
                "sell_ratio": 0,
                "large_buys": 0,
                "large_sells": 0,
                "last_update": "N/A",
            })
            return True

    def remove_coin(self, symbol: str) -> bool:
        """Remove coin. Returns True if found and removed."""
        with self._lock:
            before = len(self._state["coins"])
            self._state["coins"] = [c for c in self._state["coins"] if c["symbol"] != symbol]
            return len(self._state["coins"]) < before

    def toggle_coin(self, symbol: str, active: bool) -> bool:
        """Toggle coin active state. Returns True if coin found."""
        with self._lock:
            coin = next((c for c in self._state["coins"] if c["symbol"] == symbol), None)
            if coin is None:
                return False
            coin["active"] = active
            return True

    def initialize_coins(self, symbols: List[str]):
        with self._lock:
            self._state["coins"] = [
                {
                    "symbol": s,
                    "active": True,
                    "buy_ratio": 0,
                    "sell_ratio": 0,
                    "large_buys": 0,
                    "large_sells": 0,
                    "last_update": "N/A",
                }
                for s in symbols
            ]

    # ── Direct access (for main.py coin list manipulation) ──────────

    def with_lock(self):
        """Context manager for direct locked access when needed.

        Usage:
            with state.with_lock():
                coins = state._state["coins"]  # direct access under lock
        """
        return self._lock
