# Storage Module - SQLite persistence for TELEGLAS Pro
# Saves signals, outcomes, confidence history, and dashboard state

"""
Database Module

Provides async SQLite storage so data survives restarts:
- Signal history with WIN/LOSS outcomes
- Confidence scorer win rates
- Dashboard coin toggle state
- Hourly baseline snapshots

Uses aiosqlite for non-blocking async I/O.
"""

import aiosqlite
import csv
import io
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from ..utils.logger import setup_logger

# Default database path
DEFAULT_DB_PATH = Path(__file__).parent.parent.parent / "data" / "teleglas.db"


class Database:
    """
    Async SQLite storage for TELEGLAS Pro.

    All write operations are non-blocking (aiosqlite).
    Data is saved incrementally — no big batch writes.
    """

    def __init__(self, db_path: str = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.logger = setup_logger("Database", "INFO")
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self):
        """Open database connection and create tables."""
        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")  # Better concurrent reads
        await self._db.execute("PRAGMA synchronous=NORMAL")  # Good balance speed/safety
        await self._create_tables()
        self.logger.info(f"Database connected: {self.db_path}")

    def _ensure_connected(self):
        """Raise if database not connected."""
        if self._db is None:
            raise RuntimeError("Database not connected. Call connect() first.")

    async def close(self):
        """Close database connection."""
        if self._db:
            await self._db.close()
            self._db = None
            self.logger.info("Database closed")

    async def _create_tables(self):
        """Create tables if they don't exist."""
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                direction TEXT NOT NULL,
                confidence REAL NOT NULL,
                entry_price REAL,
                stop_loss REAL,
                target_price REAL,
                exit_price REAL,
                outcome TEXT,
                pnl_pct REAL,
                metadata_json TEXT,
                created_at REAL NOT NULL,
                checked_at REAL
            );

            CREATE TABLE IF NOT EXISTS confidence_state (
                signal_type TEXT PRIMARY KEY,
                win_rate REAL NOT NULL,
                history_json TEXT NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS dashboard_coins (
                symbol TEXT PRIMARY KEY,
                active INTEGER NOT NULL DEFAULT 1,
                added_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS hourly_baselines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                liq_volume REAL NOT NULL,
                trade_volume REAL NOT NULL,
                recorded_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS oi_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                current_oi_usd REAL NOT NULL,
                previous_oi_usd REAL,
                oi_high_usd REAL,
                oi_low_usd REAL,
                oi_change_pct REAL,
                recorded_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS funding_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                current_rate REAL NOT NULL,
                previous_rate REAL,
                rate_high REAL,
                rate_low REAL,
                recorded_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol);
            CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at);
            CREATE INDEX IF NOT EXISTS idx_signals_outcome ON signals(outcome);
            CREATE INDEX IF NOT EXISTS idx_baselines_symbol ON hourly_baselines(symbol);
            CREATE INDEX IF NOT EXISTS idx_oi_symbol ON oi_snapshots(symbol);
            CREATE INDEX IF NOT EXISTS idx_oi_recorded ON oi_snapshots(recorded_at);
            CREATE INDEX IF NOT EXISTS idx_funding_symbol ON funding_snapshots(symbol);
            CREATE INDEX IF NOT EXISTS idx_funding_recorded ON funding_snapshots(recorded_at);
        """)
        await self._db.commit()

    # ==========================================================================
    # SIGNALS
    # ==========================================================================

    async def save_signal(self, symbol: str, signal_type: str, direction: str,
                          confidence: float, entry_price: float = 0,
                          stop_loss: float = 0, target_price: float = 0,
                          metadata: dict = None) -> int:
        """Save a new signal. Returns the signal ID."""
        self._ensure_connected()
        cursor = await self._db.execute(
            """INSERT INTO signals
               (symbol, signal_type, direction, confidence,
                entry_price, stop_loss, target_price, metadata_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (symbol, signal_type, direction, confidence,
             entry_price, stop_loss, target_price,
             json.dumps(metadata or {}), time.time())
        )
        await self._db.commit()
        return cursor.lastrowid

    async def update_signal_outcome(self, signal_id: int, outcome: str,
                                     exit_price: float, pnl_pct: float):
        """Update signal with outcome after checking."""
        self._ensure_connected()
        await self._db.execute(
            """UPDATE signals
               SET outcome = ?, exit_price = ?, pnl_pct = ?, checked_at = ?
               WHERE id = ?""",
            (outcome, exit_price, pnl_pct, time.time(), signal_id)
        )
        await self._db.commit()

    async def get_recent_signals(self, limit: int = 100) -> List[dict]:
        """Get most recent signals."""
        self._ensure_connected()
        limit = min(max(limit, 1), 5000)  # Clamp to 1-5000
        cursor = await self._db.execute(
            "SELECT * FROM signals ORDER BY created_at DESC LIMIT ?",
            (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_signals_by_symbol(self, symbol: str, limit: int = 50) -> List[dict]:
        """Get signals for a specific symbol."""
        self._ensure_connected()
        limit = min(max(limit, 1), 5000)
        cursor = await self._db.execute(
            "SELECT * FROM signals WHERE symbol = ? ORDER BY created_at DESC LIMIT ?",
            (symbol, limit)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_signal_stats(self) -> dict:
        """Get aggregate signal statistics."""
        self._ensure_connected()
        cursor = await self._db.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN outcome = 'WIN' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN outcome = 'LOSS' THEN 1 ELSE 0 END) as losses,
                SUM(CASE WHEN outcome = 'NEUTRAL' THEN 1 ELSE 0 END) as neutral,
                SUM(CASE WHEN outcome IS NULL THEN 1 ELSE 0 END) as pending,
                AVG(CASE WHEN outcome IN ('WIN','LOSS') THEN pnl_pct END) as avg_pnl,
                AVG(CASE WHEN outcome = 'WIN' THEN pnl_pct END) as avg_win,
                AVG(CASE WHEN outcome = 'LOSS' THEN pnl_pct END) as avg_loss
            FROM signals
        """)
        row = await cursor.fetchone()
        return dict(row) if row else {}

    async def get_signal_stats_by_type(self) -> Dict[str, dict]:
        """Get signal stats grouped by type."""
        self._ensure_connected()
        cursor = await self._db.execute("""
            SELECT
                signal_type,
                COUNT(*) as total,
                SUM(CASE WHEN outcome = 'WIN' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN outcome = 'LOSS' THEN 1 ELSE 0 END) as losses,
                AVG(CASE WHEN outcome IN ('WIN','LOSS') THEN pnl_pct END) as avg_pnl
            FROM signals
            GROUP BY signal_type
        """)
        rows = await cursor.fetchall()
        return {row["signal_type"]: dict(row) for row in rows}

    # ==========================================================================
    # CONFIDENCE STATE
    # ==========================================================================

    async def save_confidence_state(self, signal_type: str, win_rate: float,
                                     history: list):
        """Save confidence scorer state for a signal type."""
        self._ensure_connected()
        await self._db.execute(
            """INSERT OR REPLACE INTO confidence_state
               (signal_type, win_rate, history_json, updated_at)
               VALUES (?, ?, ?, ?)""",
            (signal_type, win_rate, json.dumps(history), time.time())
        )
        await self._db.commit()

    async def load_confidence_state(self) -> Dict[str, dict]:
        """Load all confidence scorer states."""
        self._ensure_connected()
        cursor = await self._db.execute("SELECT * FROM confidence_state")
        rows = await cursor.fetchall()
        result = {}
        for row in rows:
            result[row["signal_type"]] = {
                "win_rate": row["win_rate"],
                "history": json.loads(row["history_json"])
            }
        return result

    # ==========================================================================
    # DASHBOARD COINS
    # ==========================================================================

    async def save_dashboard_coins(self, coins: List[dict]):
        """Save dashboard coin states (bulk replace, atomic transaction)."""
        self._ensure_connected()
        await self._db.execute("BEGIN")
        try:
            await self._db.execute("DELETE FROM dashboard_coins")
            for coin in coins:
                await self._db.execute(
                    "INSERT INTO dashboard_coins (symbol, active, added_at) VALUES (?, ?, ?)",
                    (coin["symbol"], 1 if coin.get("active", True) else 0, time.time())
                )
            await self._db.commit()
        except Exception:
            await self._db.rollback()
            raise

    async def load_dashboard_coins(self) -> List[dict]:
        """Load saved dashboard coin states."""
        self._ensure_connected()
        cursor = await self._db.execute("SELECT * FROM dashboard_coins")
        rows = await cursor.fetchall()
        return [{"symbol": row["symbol"], "active": bool(row["active"])} for row in rows]

    # ==========================================================================
    # HOURLY BASELINES
    # ==========================================================================

    async def save_baseline(self, symbol: str, liq_volume: float, trade_volume: float):
        """Save hourly baseline snapshot."""
        self._ensure_connected()
        await self._db.execute(
            """INSERT INTO hourly_baselines (symbol, liq_volume, trade_volume, recorded_at)
               VALUES (?, ?, ?, ?)""",
            (symbol, liq_volume, trade_volume, time.time())
        )
        await self._db.commit()

    async def load_baselines(self, symbol: str, hours: int = 24) -> List[dict]:
        """Load baseline history for a symbol."""
        self._ensure_connected()
        cutoff = time.time() - (hours * 3600)
        cursor = await self._db.execute(
            """SELECT * FROM hourly_baselines
               WHERE symbol = ? AND recorded_at > ?
               ORDER BY recorded_at""",
            (symbol, cutoff)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def cleanup_old_baselines(self, max_age_hours: int = 72):
        """Remove baselines older than max_age_hours."""
        self._ensure_connected()
        cutoff = time.time() - (max_age_hours * 3600)
        await self._db.execute(
            "DELETE FROM hourly_baselines WHERE recorded_at < ?", (cutoff,)
        )
        await self._db.commit()

    # ==========================================================================
    # CSV EXPORT
    # ==========================================================================

    async def export_signals_csv(self, limit: int = 1000) -> str:
        """Export signals to CSV string (for spreadsheet/Google Drive)."""
        self._ensure_connected()
        limit = min(max(limit, 1), 10000)
        cursor = await self._db.execute(
            "SELECT * FROM signals ORDER BY created_at DESC LIMIT ?",
            (limit,)
        )
        rows = await cursor.fetchall()

        if not rows:
            return ""

        output = io.StringIO()
        writer = csv.writer(output)

        # Header
        columns = ["id", "symbol", "signal_type", "direction", "confidence",
                    "entry_price", "stop_loss", "target_price", "exit_price",
                    "outcome", "pnl_pct", "created_at", "checked_at"]
        writer.writerow(columns)

        # Data
        for row in rows:
            row_dict = dict(row)
            # Convert timestamps to readable UTC format
            for ts_field in ["created_at", "checked_at"]:
                if row_dict.get(ts_field):
                    row_dict[ts_field] = datetime.fromtimestamp(
                        row_dict[ts_field], tz=timezone.utc
                    ).strftime("%Y-%m-%d %H:%M:%S UTC")
            writer.writerow([row_dict.get(col, "") for col in columns])

        return output.getvalue()

    # ==========================================================================
    # OI SNAPSHOTS
    # ==========================================================================

    async def save_oi_snapshot(self, symbol: str, current_oi_usd: float,
                               previous_oi_usd: float = 0,
                               oi_high_usd: float = 0,
                               oi_low_usd: float = 0,
                               oi_change_pct: float = 0):
        """Save OI snapshot from CoinGlass v4 OHLC candle data."""
        self._ensure_connected()
        await self._db.execute(
            """INSERT INTO oi_snapshots
               (symbol, current_oi_usd, previous_oi_usd, oi_high_usd,
                oi_low_usd, oi_change_pct, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (symbol, current_oi_usd, previous_oi_usd, oi_high_usd,
             oi_low_usd, oi_change_pct, time.time())
        )
        await self._db.commit()

    async def get_oi_history(self, symbol: str, hours: int = 24) -> List[dict]:
        """Get OI history for a symbol."""
        self._ensure_connected()
        cutoff = time.time() - (hours * 3600)
        cursor = await self._db.execute(
            """SELECT * FROM oi_snapshots
               WHERE symbol = ? AND recorded_at > ?
               ORDER BY recorded_at""",
            (symbol, cutoff)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def cleanup_old_oi_snapshots(self, max_age_hours: int = 168):
        """Remove OI snapshots older than max_age_hours (default 7 days)."""
        self._ensure_connected()
        cutoff = time.time() - (max_age_hours * 3600)
        await self._db.execute(
            "DELETE FROM oi_snapshots WHERE recorded_at < ?", (cutoff,)
        )
        await self._db.commit()

    # ==========================================================================
    # FUNDING SNAPSHOTS
    # ==========================================================================

    async def save_funding_snapshot(self, symbol: str, current_rate: float,
                                     previous_rate: float = 0,
                                     rate_high: float = 0,
                                     rate_low: float = 0):
        """Save funding rate snapshot from CoinGlass v4 OHLC candle data."""
        self._ensure_connected()
        await self._db.execute(
            """INSERT INTO funding_snapshots
               (symbol, current_rate, previous_rate, rate_high, rate_low, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (symbol, current_rate, previous_rate, rate_high, rate_low, time.time())
        )
        await self._db.commit()

    async def get_funding_history(self, symbol: str, hours: int = 24) -> List[dict]:
        """Get funding rate history for a symbol."""
        self._ensure_connected()
        cutoff = time.time() - (hours * 3600)
        cursor = await self._db.execute(
            """SELECT * FROM funding_snapshots
               WHERE symbol = ? AND recorded_at > ?
               ORDER BY recorded_at""",
            (symbol, cutoff)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def cleanup_old_funding_snapshots(self, max_age_hours: int = 168):
        """Remove funding snapshots older than max_age_hours (default 7 days)."""
        self._ensure_connected()
        cutoff = time.time() - (max_age_hours * 3600)
        await self._db.execute(
            "DELETE FROM funding_snapshots WHERE recorded_at < ?", (cutoff,)
        )
        await self._db.commit()

    async def export_baselines_csv(self, symbol: str = None) -> str:
        """Export baselines to CSV string."""
        self._ensure_connected()
        if symbol:
            cursor = await self._db.execute(
                "SELECT * FROM hourly_baselines WHERE symbol = ? ORDER BY recorded_at DESC",
                (symbol,)
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM hourly_baselines ORDER BY recorded_at DESC LIMIT 5000"
            )
        rows = await cursor.fetchall()

        if not rows:
            return ""

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["symbol", "liq_volume", "trade_volume", "recorded_at"])

        for row in rows:
            row_dict = dict(row)
            if row_dict.get("recorded_at"):
                row_dict["recorded_at"] = datetime.fromtimestamp(
                    row_dict["recorded_at"], tz=timezone.utc
                ).strftime("%Y-%m-%d %H:%M:%S UTC")
            writer.writerow([row_dict.get("symbol", ""),
                             row_dict.get("liq_volume", 0),
                             row_dict.get("trade_volume", 0),
                             row_dict.get("recorded_at", "")])

        return output.getvalue()
