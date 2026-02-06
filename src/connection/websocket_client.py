# WebSocket Client - Connection Management
# Production-ready WebSocket client for CoinGlass API

"""
WebSocket Client Module

Responsibilities:
- Establish WebSocket connection to CoinGlass
- Auto-reconnect with exponential backoff
- Connection state management
- Error handling
- Event callbacks
"""

import asyncio
import json
import websockets
from typing import Optional, Callable, Dict, Any
from enum import Enum
import logging

# Import logger
from ..utils.logger import setup_logger

class ConnectionState(Enum):
    """WebSocket connection states"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    CLOSED = "closed"

class WebSocketClient:
    """
    Production-ready WebSocket client for CoinGlass API
    
    Features:
    - Auto-reconnect with exponential backoff
    - Heartbeat mechanism
    - Event callbacks
    - Comprehensive error handling
    """
    
    def __init__(
        self, 
        api_key: str, 
        url: str = "wss://open-ws.coinglass.com/ws-api",
        reconnect_delay: int = 1,
        max_reconnect_delay: int = 60,
        heartbeat_interval: int = 20
    ):
        """
        Initialize WebSocket client
        
        Args:
            api_key: CoinGlass API key
            url: WebSocket URL
            reconnect_delay: Initial reconnect delay in seconds
            max_reconnect_delay: Maximum reconnect delay in seconds
            heartbeat_interval: Heartbeat interval in seconds
        """
        self.api_key = api_key
        self.url = url
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_delay = max_reconnect_delay
        self.heartbeat_interval = heartbeat_interval
        
        # Connection state
        self.connection: Optional[websockets.WebSocketClientProtocol] = None
        self.state = ConnectionState.DISCONNECTED
        self._reconnect_attempts = 0
        self._should_reconnect = True
        self._is_authenticated = False
        
        # Event callbacks
        self.on_connect_callback: Optional[Callable] = None
        self.on_disconnect_callback: Optional[Callable] = None
        self.on_message_callback: Optional[Callable] = None
        self.on_error_callback: Optional[Callable] = None
        
        # Tasks
        self._receive_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._consecutive_timeouts = 0
        self._max_consecutive_timeouts = 3

        # Logger
        self.logger = setup_logger("WebSocketClient", "INFO")
        
    async def connect(self) -> bool:
        """
        Establish WebSocket connection to CoinGlass.
        Uses iterative reconnect loop instead of recursion to avoid RecursionError.

        Returns:
            True if connected successfully, False otherwise
        """
        while True:
            if self.state == ConnectionState.CONNECTED:
                self.logger.warning("Already connected")
                return True

            try:
                self.state = ConnectionState.CONNECTING
                self.logger.info(f"Connecting to {self.url}...")

                # Cancel old tasks before creating new ones
                if self._heartbeat_task and not self._heartbeat_task.done():
                    self._heartbeat_task.cancel()
                if self._receive_task and not self._receive_task.done():
                    self._receive_task.cancel()

                # Establish WebSocket connection
                self.connection = await websockets.connect(
                    self.url,
                    ping_interval=None,  # We'll handle ping manually
                    close_timeout=10
                )

                # Authenticate
                if not await self._authenticate():
                    self.logger.error("Authentication failed")
                    await self.disconnect()
                    return False

                self.state = ConnectionState.CONNECTED
                self._reconnect_attempts = 0
                self.logger.info("✅ Connected successfully")

                # Start background tasks
                self._receive_task = asyncio.create_task(self._receive_loop())
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

                # Call connect callback
                if self.on_connect_callback:
                    await self.on_connect_callback()

                return True

            except Exception as e:
                self.logger.error(f"Connection failed: {e}")
                self.state = ConnectionState.DISCONNECTED

                if self.on_error_callback:
                    await self.on_error_callback(e)

                # Auto-reconnect: wait with backoff then loop
                if not self._should_reconnect:
                    return False

                self._reconnect_attempts += 1
                delay = min(
                    self.reconnect_delay * (2 ** (self._reconnect_attempts - 1)),
                    self.max_reconnect_delay
                )
                self.logger.info(
                    f"Reconnecting in {delay}s (attempt {self._reconnect_attempts})..."
                )
                self.state = ConnectionState.RECONNECTING
                await asyncio.sleep(delay)
                # Loop continues to retry
    
    async def disconnect(self):
        """
        Close WebSocket connection gracefully
        
        CRITICAL FIX Bug #8: Improved graceful shutdown with timeout
        """
        self.logger.info("Disconnecting...")
        self._should_reconnect = False
        self.state = ConnectionState.CLOSED
        
        # Cancel background tasks with timeout
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                # CRITICAL FIX: Add 5s timeout for task cleanup
                await asyncio.wait_for(self._receive_task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
                
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                # CRITICAL FIX: Add 5s timeout for task cleanup
                await asyncio.wait_for(self._heartbeat_task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        
        # Close connection with timeout
        if self.connection and not self.connection.closed:
            try:
                # CRITICAL FIX: Add 5s timeout for connection close
                await asyncio.wait_for(self.connection.close(), timeout=5.0)
            except asyncio.TimeoutError:
                self.logger.warning("Connection close timeout - forcing")
            except Exception as e:
                self.logger.warning(f"Error closing connection: {e}")
            
        self.connection = None
        self._is_authenticated = False
        self.logger.info("✅ Disconnected")
        
        # Call disconnect callback
        if self.on_disconnect_callback:
            try:
                await asyncio.wait_for(self.on_disconnect_callback(), timeout=3.0)
            except asyncio.TimeoutError:
                self.logger.warning("Disconnect callback timeout")
            except Exception as e:
                self.logger.warning(f"Disconnect callback error: {e}")
    
    async def send_message(self, message: Dict[str, Any]) -> bool:
        """
        Send message to WebSocket
        
        Args:
            message: Message dictionary to send
            
        Returns:
            True if sent successfully, False otherwise
        """
        if not self.is_connected():
            self.logger.error("Cannot send message: Not connected")
            return False
            
        try:
            message_str = json.dumps(message)
            await self.connection.send(message_str)
            self.logger.debug(f"Sent: {message_str}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to send message: {e}")
            return False
    
    async def _authenticate(self) -> bool:
        """
        Authenticate with CoinGlass API
        
        Returns:
            True if authenticated, False otherwise
        """
        try:
            # Send authentication message
            auth_message = {
                "event": "login",
                "params": {
                    "apiKey": self.api_key
                }
            }
            
            await self.connection.send(json.dumps(auth_message))
            self.logger.info("Sent authentication request")
            
            # Wait for authentication response
            response = await asyncio.wait_for(
                self.connection.recv(),
                timeout=10
            )
            
            data = json.loads(response)
            self.logger.debug(f"Auth response: {data}")
            
            # Check if authentication successful
            if data.get("event") == "login" and data.get("code") == 0:
                self._is_authenticated = True
                self.logger.info("✅ Authentication successful")
                return True
            else:
                self.logger.error(f"Authentication failed: {data}")
                return False
                
        except asyncio.TimeoutError:
            self.logger.error("Authentication timeout")
            return False
        except Exception as e:
            self.logger.error(f"Authentication error: {e}")
            return False
    
    async def _receive_loop(self):
        """
        Background task to receive messages
        
        CRITICAL FIX Bug #7: Added timeout to prevent hanging indefinitely
        """
        try:
            while self.is_connected():
                try:
                    # CRITICAL FIX: Add 60s timeout to prevent hanging
                    message = await asyncio.wait_for(
                        self.connection.recv(),
                        timeout=60.0
                    )
                    self._consecutive_timeouts = 0
                    await self._handle_message(message)
                    
                except asyncio.TimeoutError:
                    self._consecutive_timeouts += 1
                    self.logger.warning(
                        f"Receive timeout (60s) - {self._consecutive_timeouts}/"
                        f"{self._max_consecutive_timeouts} consecutive"
                    )
                    if self._consecutive_timeouts >= self._max_consecutive_timeouts:
                        self.logger.error("Max consecutive timeouts reached - forcing reconnect")
                        break
                    continue
                    
                except websockets.exceptions.ConnectionClosed:
                    self.logger.warning("Connection closed by server")
                    break
                    
        except asyncio.CancelledError:
            self.logger.debug("Receive loop cancelled")
            raise
            
        except Exception as e:
            self.logger.error(f"Receive loop error: {e}")
            
        finally:
            # Connection lost, trigger reconnect
            if self._should_reconnect and self.state != ConnectionState.CLOSED:
                self.logger.warning("Connection lost, reconnecting...")
                await self._schedule_reconnect()
    
    async def _handle_message(self, message: str):
        """
        Handle received message
        
        Args:
            message: Raw message string
        """
        try:
            data = json.loads(message)
            self.logger.debug(f"Received: {data}")
            
            # Handle pong response
            if data.get("event") == "pong":
                self.logger.debug("Received pong")
                return
            
            # Call message callback
            if self.on_message_callback:
                await self.on_message_callback(data)
                
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse message: {e}")
        except Exception as e:
            self.logger.error(f"Error handling message: {e}")
    
    async def _heartbeat_loop(self):
        """
        Background task to send heartbeat (ping)
        """
        try:
            while self.is_connected():
                await asyncio.sleep(self.heartbeat_interval)
                
                if self.is_connected():
                    ping_message = {"event": "ping"}
                    await self.send_message(ping_message)
                    self.logger.debug("Sent ping")
                    
        except asyncio.CancelledError:
            self.logger.debug("Heartbeat loop cancelled")
            raise
        except Exception as e:
            self.logger.error(f"Heartbeat loop error: {e}")
    
    async def _schedule_reconnect(self):
        """
        Schedule reconnection by resetting state and calling connect().
        connect() handles backoff internally with an iterative loop.
        """
        if not self._should_reconnect:
            return

        self.state = ConnectionState.DISCONNECTED
        self._is_authenticated = False
        self.connection = None
        await self.connect()
    
    def is_connected(self) -> bool:
        """
        Check if WebSocket is connected
        
        Returns:
            True if connected, False otherwise
        """
        return (
            self.connection is not None 
            and not self.connection.closed 
            and self.state == ConnectionState.CONNECTED
            and self._is_authenticated
        )
    
    def is_authenticated(self) -> bool:
        """
        Check if client is authenticated
        
        Returns:
            True if authenticated, False otherwise
        """
        return self._is_authenticated
    
    def get_state(self) -> ConnectionState:
        """
        Get current connection state
        
        Returns:
            Current ConnectionState
        """
        return self.state
    
    # Event callback setters
    def on_connect(self, callback: Callable):
        """Set on_connect callback"""
        self.on_connect_callback = callback
        
    def on_disconnect(self, callback: Callable):
        """Set on_disconnect callback"""
        self.on_disconnect_callback = callback
        
    def on_message(self, callback: Callable):
        """Set on_message callback"""
        self.on_message_callback = callback
        
    def on_error(self, callback: Callable):
        """Set on_error callback"""
        self.on_error_callback = callback
