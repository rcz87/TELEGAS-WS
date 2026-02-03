#!/usr/bin/env python3
# Test WebSocket Connection
# Usage: python scripts/test_websocket.py

"""
WebSocket Connection Test Script

Tests:
1. Connection to CoinGlass WebSocket API
2. Authentication
3. Heartbeat mechanism
4. Message receiving
5. Auto-reconnect

Requirements:
- Set COINGLASS_API_KEY in config/secrets.env
"""

import asyncio
import sys
import os
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.connection.websocket_client import WebSocketClient, ConnectionState
from src.utils.logger import setup_logger
from dotenv import load_dotenv

# Load environment variables
load_dotenv("config/secrets.env")

# Setup logger
logger = setup_logger("TestWebSocket", "INFO")

class WebSocketTester:
    """Test WebSocket client"""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = None
        self.message_count = 0
        
    async def on_connect(self):
        """Called when connected"""
        logger.info("🎉 CALLBACK: Connected!")
        
    async def on_disconnect(self):
        """Called when disconnected"""
        logger.info("👋 CALLBACK: Disconnected!")
        
    async def on_message(self, data: dict):
        """Called when message received"""
        self.message_count += 1
        logger.info(f"📨 CALLBACK: Message #{self.message_count}")
        logger.info(f"   Data: {data}")
        
    async def on_error(self, error: Exception):
        """Called on error"""
        logger.error(f"❌ CALLBACK: Error - {error}")
    
    async def test_connection(self):
        """Test basic connection"""
        logger.info("="*60)
        logger.info("TEST 1: Basic Connection")
        logger.info("="*60)
        
        self.client = WebSocketClient(
            api_key=self.api_key,
            reconnect_delay=1,
            max_reconnect_delay=10,
            heartbeat_interval=20
        )
        
        # Set callbacks
        self.client.on_connect(self.on_connect)
        self.client.on_disconnect(self.on_disconnect)
        self.client.on_message(self.on_message)
        self.client.on_error(self.on_error)
        
        # Connect
        success = await self.client.connect()
        
        if success:
            logger.info("✅ Connection successful!")
            logger.info(f"   State: {self.client.get_state()}")
            logger.info(f"   Authenticated: {self.client.is_authenticated()}")
        else:
            logger.error("❌ Connection failed!")
            return False
            
        return True
    
    async def test_heartbeat(self):
        """Test heartbeat mechanism"""
        logger.info("")
        logger.info("="*60)
        logger.info("TEST 2: Heartbeat Mechanism")
        logger.info("="*60)
        logger.info("Waiting 60 seconds to observe heartbeat...")
        logger.info("(You should see 'Sent ping' every 20 seconds)")
        
        await asyncio.sleep(60)
        
        if self.client.is_connected():
            logger.info("✅ Heartbeat working! Still connected after 60s")
        else:
            logger.error("❌ Connection lost during heartbeat test")
    
    async def test_message_receiving(self):
        """Test message receiving"""
        logger.info("")
        logger.info("="*60)
        logger.info("TEST 3: Message Receiving")
        logger.info("="*60)
        logger.info("Waiting 30 seconds to receive messages...")
        
        initial_count = self.message_count
        await asyncio.sleep(30)
        
        messages_received = self.message_count - initial_count
        logger.info(f"✅ Received {messages_received} messages in 30 seconds")
    
    async def test_manual_message(self):
        """Test sending manual message"""
        logger.info("")
        logger.info("="*60)
        logger.info("TEST 4: Manual Message Sending")
        logger.info("="*60)
        
        # Try to send a custom ping
        test_message = {"event": "ping", "test": True}
        success = await self.client.send_message(test_message)
        
        if success:
            logger.info("✅ Message sent successfully!")
        else:
            logger.error("❌ Failed to send message")
    
    async def test_graceful_disconnect(self):
        """Test graceful disconnection"""
        logger.info("")
        logger.info("="*60)
        logger.info("TEST 5: Graceful Disconnection")
        logger.info("="*60)
        
        await self.client.disconnect()
        
        if not self.client.is_connected():
            logger.info("✅ Disconnected successfully!")
            logger.info(f"   State: {self.client.get_state()}")
        else:
            logger.error("❌ Still connected after disconnect!")
    
    async def run_all_tests(self):
        """Run all tests"""
        try:
            logger.info("")
            logger.info("🚀 Starting WebSocket Connection Tests")
            logger.info("")
            
            # Test 1: Connection
            if not await self.test_connection():
                logger.error("Connection test failed. Aborting.")
                return
            
            # Test 2: Heartbeat (60 seconds)
            await self.test_heartbeat()
            
            # Test 3: Message receiving (30 seconds)
            await self.test_message_receiving()
            
            # Test 4: Manual message
            await self.test_manual_message()
            
            # Test 5: Graceful disconnect
            await self.test_graceful_disconnect()
            
            # Summary
            logger.info("")
            logger.info("="*60)
            logger.info("TEST SUMMARY")
            logger.info("="*60)
            logger.info(f"✅ Total messages received: {self.message_count}")
            logger.info("✅ All tests completed!")
            
        except KeyboardInterrupt:
            logger.info("\n⚠️ Tests interrupted by user")
            if self.client:
                await self.client.disconnect()
        except Exception as e:
            logger.error(f"❌ Test error: {e}")
            if self.client:
                await self.client.disconnect()

async def main():
    """Main entry point"""
    # Get API key
    api_key = os.getenv("COINGLASS_API_KEY")
    
    if not api_key:
        logger.error("❌ COINGLASS_API_KEY not found in config/secrets.env")
        logger.info("Please create config/secrets.env and add your API key:")
        logger.info("  COINGLASS_API_KEY=your_key_here")
        return
    
    logger.info(f"API Key loaded: {api_key[:10]}...{api_key[-5:]}")
    
    # Run tests
    tester = WebSocketTester(api_key)
    await tester.run_all_tests()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
