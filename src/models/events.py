# Typed models for WebSocket events flowing through the pipeline
#
# These replace raw dict access (event.get("vol", 0)) with typed fields.
# Parsers convert raw CoinGlass dicts into these models at ingestion;
# downstream code (buffer, analyzers) gets typed access.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Union


@dataclass(slots=True)
class LiquidationEvent:
    """Single liquidation event from CoinGlass WebSocket."""
    symbol: str          # Normalized symbol, e.g. "BTCUSDT"
    exchange: str        # Exchange name, e.g. "Binance"
    price: float         # Liquidation price
    side: int            # 1 = Sell (short liq), 2 = Buy (long liq)
    vol: float           # Volume in USD
    time: int            # Unix timestamp (ms)

    @classmethod
    def from_dict(cls, d: dict, symbol_override: str = "") -> LiquidationEvent:
        """Parse from raw CoinGlass dict (after field normalization)."""
        return cls(
            symbol=symbol_override or str(d.get("symbol", "UNKNOWN")),
            exchange=str(d.get("exchange", d.get("exName", ""))),
            price=float(d.get("price", 0)),
            side=int(d.get("side", 0)),
            vol=float(d.get("vol", d.get("volUsd", 0))),
            time=int(d.get("time", 0)),
        )

    def to_dict(self) -> dict:
        """Convert back to dict for backward compatibility."""
        return {
            "symbol": self.symbol,
            "exchange": self.exchange,
            "price": self.price,
            "side": self.side,
            "vol": self.vol,
            "time": self.time,
        }


@dataclass(slots=True)
class TradeEvent:
    """Single futures trade event from CoinGlass WebSocket."""
    symbol: str          # Normalized symbol, e.g. "BTCUSDT"
    exchange: str        # Exchange name
    price: float         # Trade price
    side: int            # 1 = Sell, 2 = Buy
    vol: float           # Volume in USD
    time: int            # Unix timestamp (ms)

    @classmethod
    def from_dict(cls, d: dict, symbol_override: str = "") -> TradeEvent:
        """Parse from raw CoinGlass dict (after field normalization)."""
        return cls(
            symbol=symbol_override or str(d.get("symbol", "UNKNOWN")),
            exchange=str(d.get("exchange", d.get("exName", ""))),
            price=float(d.get("price", 0)),
            side=int(d.get("side", 0)),
            vol=float(d.get("vol", d.get("volUsd", 0))),
            time=int(d.get("time", 0)),
        )

    def to_dict(self) -> dict:
        """Convert back to dict for backward compatibility."""
        return {
            "symbol": self.symbol,
            "exchange": self.exchange,
            "price": self.price,
            "side": self.side,
            "vol": self.vol,
            "time": self.time,
        }


def parse_liquidation(raw: dict, symbol: str = "") -> Optional[LiquidationEvent]:
    """Safe parser — returns None on invalid data instead of raising."""
    try:
        return LiquidationEvent.from_dict(raw, symbol_override=symbol)
    except (ValueError, TypeError, KeyError):
        return None


def parse_trade(raw: dict, symbol: str = "") -> Optional[TradeEvent]:
    """Safe parser — returns None on invalid data instead of raising."""
    try:
        return TradeEvent.from_dict(raw, symbol_override=symbol)
    except (ValueError, TypeError, KeyError):
        return None
