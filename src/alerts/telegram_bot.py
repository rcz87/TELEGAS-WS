# Telegram Bot - Send Alerts
# Production-ready Telegram bot with retry logic

"""
Telegram Bot Module

Responsibilities:
- Send messages via Telegram Bot API
- Async/await for non-blocking operation
- Retry logic with exponential backoff
- Rate limiting (respect Telegram 20 msg/min limit)
- Error handling and logging
"""

import asyncio
import aiohttp
from typing import Optional
from datetime import datetime, timedelta

from ..utils.logger import setup_logger

class TelegramBot:
    """
    Production-ready Telegram bot
    
    Sends alerts to Telegram using Bot API with:
    - Retry logic
    - Rate limiting
    - Error handling
    - Delivery confirmation
    
    Features:
    - Async sending
    - Exponential backoff
    - Statistics tracking
    """
    
    def __init__(self, bot_token: str, chat_id: str, rate_limit_delay: float = 3.0):
        """
        Initialize Telegram bot
        
        Args:
            bot_token: Telegram bot token
            chat_id: Target chat ID
            rate_limit_delay: Seconds between messages (default 3s = 20 msg/min)
        """
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.rate_limit_delay = rate_limit_delay
        
        self.api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self.logger = setup_logger("TelegramBot", "INFO")
        
        # Statistics
        self._messages_sent = 0
        self._messages_failed = 0
        self._last_send_time = None
        
    async def send_message(self, message: str, parse_mode: str = "Markdown") -> bool:
        """
        Send message to Telegram
        
        Args:
            message: Message text
            parse_mode: Markdown or HTML (default: Markdown)
            
        Returns:
            True if sent successfully, False otherwise
        """
        try:
            # Rate limiting
            await self._wait_for_rate_limit()
            
            # Prepare payload
            payload = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": parse_mode
            }
            
            # Send via HTTP POST
            async with aiohttp.ClientSession() as session:
                async with session.post(self.api_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        self._messages_sent += 1
                        self._last_send_time = datetime.now()
                        self.logger.info(f"✅ Message sent successfully")
                        return True
                    else:
                        error_text = await response.text()
                        self.logger.error(f"❌ Send failed: {response.status} - {error_text}")
                        self._messages_failed += 1
                        return False
                        
        except asyncio.TimeoutError:
            self.logger.error("❌ Send timeout")
            self._messages_failed += 1
            return False
        except Exception as e:
            self.logger.error(f"❌ Send error: {e}")
            self._messages_failed += 1
            return False
    
    async def send_with_retry(self, message: str, max_retries: int = 3) -> bool:
        """
        Send message with retry logic and exponential backoff
        
        Args:
            message: Message text
            max_retries: Maximum retry attempts
            
        Returns:
            True if sent successfully, False after all retries failed
        """
        for attempt in range(max_retries):
            success = await self.send_message(message)
            
            if success:
                return True
            
            if attempt < max_retries - 1:
                # Exponential backoff: 1s, 2s, 4s
                wait_time = 2 ** attempt
                self.logger.warning(f"⚠️ Retry {attempt + 1}/{max_retries} in {wait_time}s")
                await asyncio.sleep(wait_time)
        
        self.logger.error(f"❌ Failed after {max_retries} retries")
        return False
    
    async def send_alert(self, formatted_message: str) -> bool:
        """
        Send formatted alert with retry
        
        Convenience method that combines formatting check and retry logic.
        
        Args:
            formatted_message: Pre-formatted message string
            
        Returns:
            True if sent, False otherwise
        """
        if not formatted_message or len(formatted_message) == 0:
            self.logger.error("❌ Empty message")
            return False
        
        # Telegram limit: 4096 characters
        if len(formatted_message) > 4096:
            self.logger.warning(f"⚠️ Message too long ({len(formatted_message)} chars), truncating")
            formatted_message = formatted_message[:4090] + "..."
        
        return await self.send_with_retry(formatted_message)
    
    async def test_connection(self) -> bool:
        """
        Test Telegram bot connection
        
        Sends a test message to verify connectivity.
        
        Returns:
            True if connection works, False otherwise
        """
        test_message = "🔔 TELEGLAS Pro - Connection Test\n\n✅ Bot is connected and ready!"
        
        try:
            result = await self.send_message(test_message)
            if result:
                self.logger.info("✅ Connection test passed")
            else:
                self.logger.error("❌ Connection test failed")
            return result
        except Exception as e:
            self.logger.error(f"❌ Connection test error: {e}")
            return False
    
    async def _wait_for_rate_limit(self):
        """Wait for rate limit delay if needed"""
        if self._last_send_time:
            elapsed = (datetime.now() - self._last_send_time).total_seconds()
            if elapsed < self.rate_limit_delay:
                wait_time = self.rate_limit_delay - elapsed
                self.logger.debug(f"Rate limit: waiting {wait_time:.1f}s")
                await asyncio.sleep(wait_time)
    
    def get_stats(self) -> dict:
        """Get bot statistics"""
        total = self._messages_sent + self._messages_failed
        success_rate = (self._messages_sent / max(total, 1)) * 100
        
        return {
            "messages_sent": self._messages_sent,
            "messages_failed": self._messages_failed,
            "success_rate": success_rate,
            "last_send": self._last_send_time.isoformat() if self._last_send_time else None
        }
