# Subscription Manager - Channel Subscriptions
# NOTE: Currently unused - subscription logic implemented directly in main.py

"""
Subscription Manager Module

NOTE: This module was designed but not implemented.
The actual subscription logic is currently handled directly in main.py
in the on_connect() callback method.

Future enhancement: Refactor subscription logic from main.py to this module.

Original Responsibilities:
- Subscribe to liquidationOrders channel
- Subscribe to futures_trades channels
- Manage subscription state
"""

from typing import List

class SubscriptionManager:
    """
    Manages WebSocket channel subscriptions
    
    NOTE: Currently a stub class. Subscription logic is in main.py
    """
    
    def __init__(self, websocket_client):
        self.websocket_client = websocket_client
        self.subscribed_channels: List[str] = []
        
    async def subscribe_liquidations(self):
        """
        Subscribe to liquidation orders
        
        NOTE: Not implemented. See main.py TeleglasPro.on_connect() for actual implementation.
        """
        pass
    
    async def subscribe_trades(self, exchange: str, symbol: str, min_volume: int = 0):
        """
        Subscribe to futures trades
        
        NOTE: Not implemented. See main.py TeleglasPro.on_connect() for actual implementation.
        """
        pass
    
    async def unsubscribe(self, channel: str):
        """
        Unsubscribe from channel
        
        NOTE: Not implemented.
        """
        pass
    
    async def resubscribe_all(self):
        """
        Resubscribe to all channels (after reconnect)
        
        NOTE: Not implemented.
        """
        pass
