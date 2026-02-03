# Message Parser - JSON Parsing
# Production-ready message parser for CoinGlass WebSocket API

"""
Message Parser Module

Responsibilities:
- Parse JSON messages from WebSocket
- Convert to Python objects (dataclasses)
- Handle malformed messages
- Type conversion and normalization
"""

import json
from typing import Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from ..utils.logger import setup_logger

class MessageType(Enum):
    """WebSocket message types"""
    LOGIN = "login"
    PING = "ping"
    PONG = "pong"
    SUBSCRIBE = "subscribe"
    UNSUBSCRIBE = "unsubscribe"
    LIQUIDATION = "liquidation"
    TRADE = "trade"
    UNKNOWN = "unknown"

@dataclass
class LiquidationEvent:
    """Liquidation event data structure"""
    symbol: str
    exchange: str
    price: float
    side: int  # 1 = Long liquidation, 2 = Short liquidation
    volume_usd: float
    timestamp: int
    raw_data: dict

@dataclass
class TradeEvent:
    """Trade event data structure"""
    symbol: str
    exchange: str
    price: float
    side: int  # 1 = Sell, 2 = Buy
    volume_usd: float
    timestamp: int
    raw_data: dict

@dataclass
class ParsedMessage:
    """Generic parsed message"""
    message_type: MessageType
    event: str
    data: Any
    timestamp: datetime
    raw: dict

class MessageParser:
    """
    Production-ready parser for WebSocket messages
    
    Handles:
    - JSON parsing
    - Type conversion
    - Data normalization
    - Error handling
    """
    
    def __init__(self):
        """Initialize message parser"""
        self.logger = setup_logger("MessageParser", "INFO")
        self._parse_count = 0
        self._error_count = 0
    
    def parse(self, raw_message: str) -> Optional[ParsedMessage]:
        """
        Parse raw JSON message
        
        Args:
            raw_message: Raw JSON string from WebSocket
            
        Returns:
            ParsedMessage if successful, None otherwise
        """
        try:
            self._parse_count += 1
            
            # Parse JSON
            data = json.loads(raw_message)
            
            # Determine message type
            event = data.get("event", "unknown")
            message_type = self._determine_message_type(event)
            
            # Create parsed message
            parsed = ParsedMessage(
                message_type=message_type,
                event=event,
                data=data,
                timestamp=datetime.now(),
                raw=data
            )
            
            self.logger.debug(f"Parsed message #{self._parse_count}: {event}")
            return parsed
            
        except json.JSONDecodeError as e:
            self._error_count += 1
            self.logger.error(f"JSON decode error: {e}")
            self.logger.debug(f"Raw message: {raw_message[:100]}...")
            return None
            
        except Exception as e:
            self._error_count += 1
            self.logger.error(f"Parse error: {e}")
            return None
    
    def parse_liquidation(self, data: dict) -> Optional[LiquidationEvent]:
        """
        Parse liquidation event
        
        CoinGlass liquidation format:
        {
            "event": "liquidation",
            "data": {
                "symbol": "BTCUSDT",
                "exchange": "Binance",
                "price": "96000.50",
                "side": 2,  # 1=Long liq, 2=Short liq
                "vol": "2500000.00",  # USD
                "time": 1709453520000
            }
        }
        
        Args:
            data: Liquidation data dictionary
            
        Returns:
            LiquidationEvent if successful, None otherwise
        """
        try:
            liquidation_data = data.get("data", {})
            
            return LiquidationEvent(
                symbol=str(liquidation_data.get("symbol", "")).upper(),
                exchange=str(liquidation_data.get("exchange", "")),
                price=float(liquidation_data.get("price", 0)),
                side=int(liquidation_data.get("side", 0)),
                volume_usd=float(liquidation_data.get("vol", 0)),
                timestamp=int(liquidation_data.get("time", 0)),
                raw_data=liquidation_data
            )
            
        except (ValueError, TypeError, KeyError) as e:
            self.logger.error(f"Failed to parse liquidation: {e}")
            self.logger.debug(f"Data: {data}")
            return None
    
    def parse_trade(self, data: dict) -> Optional[TradeEvent]:
        """
        Parse trade event
        
        CoinGlass trade format:
        {
            "event": "trade",
            "data": {
                "symbol": "ETHUSDT",
                "exchange": "Binance",
                "price": "2800.50",
                "side": 2,  # 1=Sell, 2=Buy
                "vol": "150000.00",  # USD
                "time": 1709453520000
            }
        }
        
        Args:
            data: Trade data dictionary
            
        Returns:
            TradeEvent if successful, None otherwise
        """
        try:
            trade_data = data.get("data", {})
            
            return TradeEvent(
                symbol=str(trade_data.get("symbol", "")).upper(),
                exchange=str(trade_data.get("exchange", "")),
                price=float(trade_data.get("price", 0)),
                side=int(trade_data.get("side", 0)),
                volume_usd=float(trade_data.get("vol", 0)),
                timestamp=int(trade_data.get("time", 0)),
                raw_data=trade_data
            )
            
        except (ValueError, TypeError, KeyError) as e:
            self.logger.error(f"Failed to parse trade: {e}")
            self.logger.debug(f"Data: {data}")
            return None
    
    def parse_batch(self, messages: list) -> list:
        """
        Parse multiple messages
        
        Args:
            messages: List of raw message strings
            
        Returns:
            List of ParsedMessage objects
        """
        results = []
        for msg in messages:
            parsed = self.parse(msg)
            if parsed:
                results.append(parsed)
        return results
    
    def _determine_message_type(self, event: str) -> MessageType:
        """Determine message type from event string"""
        event_lower = event.lower()
        
        if event_lower == "login":
            return MessageType.LOGIN
        elif event_lower == "ping":
            return MessageType.PING
        elif event_lower == "pong":
            return MessageType.PONG
        elif event_lower == "subscribe":
            return MessageType.SUBSCRIBE
        elif event_lower == "unsubscribe":
            return MessageType.UNSUBSCRIBE
        elif event_lower == "liquidation":
            return MessageType.LIQUIDATION
        elif event_lower == "trade":
            return MessageType.TRADE
        else:
            return MessageType.UNKNOWN
    
    def get_stats(self) -> dict:
        """Get parser statistics"""
        return {
            "total_parsed": self._parse_count,
            "total_errors": self._error_count,
            "success_rate": (self._parse_count - self._error_count) / max(self._parse_count, 1) * 100
        }
