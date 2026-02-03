# TELEGLAS Pro - Main Entry Point
# Real-Time Market Intelligence System - PRODUCTION READY

"""
TELEGLAS Pro - Complete Integration

Connects all layers into working system:
WebSocket → Processors → Analyzers → Signals → Alerts → Telegram

Provides 30-90 second information edge through:
- Stop Hunt Detection ($2M+ liquidation cascades)
- Order Flow Analysis (whale tracking)
- Event Pattern Detection (market anomalies)
"""

import asyncio
import sys
import signal
import os
from pathlib import Path
from dotenv import load_dotenv
import yaml

# Import all components
from src.connection.websocket_client import WebSocketClient
from src.processors.message_parser import MessageParser
from src.processors.data_validator import DataValidator
from src.processors.buffer_manager import BufferManager
from src.analyzers.stop_hunt_detector import StopHuntDetector
from src.analyzers.order_flow_analyzer import OrderFlowAnalyzer
from src.analyzers.event_pattern_detector import EventPatternDetector
from src.signals.signal_generator import SignalGenerator
from src.signals.confidence_scorer import ConfidenceScorer
from src.signals.signal_validator import SignalValidator
from src.alerts.message_formatter import MessageFormatter
from src.alerts.telegram_bot import TelegramBot
from src.alerts.alert_queue import AlertQueue
from src.utils.logger import setup_logger

# Global flag for shutdown
shutdown_event = asyncio.Event()

class TeleglasPro:
    """
    Main application class - integrates all components
    """
    
    def __init__(self, config: dict):
        """Initialize all components"""
        self.config = config
        self.logger = setup_logger("TeleglasPro", "INFO")
        
        # Processors
        self.message_parser = MessageParser()
        self.data_validator = DataValidator()
        self.buffer_manager = BufferManager(
            max_liquidations=config.get('buffer', {}).get('max_liquidations', 1000),
            max_trades=config.get('buffer', {}).get('max_trades', 500)
        )
        
        # Analyzers
        self.stop_hunt_detector = StopHuntDetector(
            self.buffer_manager,
            threshold=config.get('analyzers', {}).get('stop_hunt_threshold', 2_000_000)
        )
        self.order_flow_analyzer = OrderFlowAnalyzer(
            self.buffer_manager,
            large_order_threshold=config.get('analyzers', {}).get('large_order_threshold', 10_000)
        )
        self.event_detector = EventPatternDetector(self.buffer_manager)
        
        # Signals
        self.signal_generator = SignalGenerator(
            min_confidence=config.get('signals', {}).get('min_confidence', 65.0)
        )
        self.confidence_scorer = ConfidenceScorer(
            learning_rate=config.get('signals', {}).get('learning_rate', 0.1)
        )
        self.signal_validator = SignalValidator(
            max_signals_per_hour=config.get('signals', {}).get('max_per_hour', 50),
            min_confidence=config.get('signals', {}).get('min_confidence', 65.0),
            cooldown_minutes=config.get('signals', {}).get('cooldown_minutes', 5)
        )
        
        # Alerts
        self.message_formatter = MessageFormatter()
        self.alert_queue = AlertQueue(max_size=1000)
        
        # Telegram (optional - only if configured)
        telegram_config = config.get('telegram', {})
        self.telegram_bot = None
        if telegram_config.get('enabled', False):
            self.telegram_bot = TelegramBot(
                bot_token=telegram_config.get('bot_token', ''),
                chat_id=telegram_config.get('chat_id', ''),
                rate_limit_delay=telegram_config.get('rate_limit', 3.0)
            )
        
        # WebSocket
        ws_config = config.get('coinglass', {})
        self.websocket_client = WebSocketClient(
            api_key=ws_config.get('api_key', ''),
            reconnect_delay=ws_config.get('reconnect_delay', 5),
            max_reconnect_delay=ws_config.get('max_reconnect_delay', 60)
        )
        
        # Statistics
        self.stats = {
            'messages_received': 0,
            'messages_processed': 0,
            'signals_generated': 0,
            'alerts_sent': 0,
            'errors': 0
        }
        
        self.logger.info("✅ All components initialized")
    
    async def on_message(self, raw_message: str):
        """
        Main message processing pipeline
        
        Pipeline:
        1. Parse message
        2. Validate data
        3. Buffer data
        4. Run analyzers
        5. Generate signal
        6. Validate signal
        7. Format message
        8. Queue alert
        9. Send to Telegram
        """
        try:
            self.stats['messages_received'] += 1
            
            # Step 1: Parse
            parsed = self.message_parser.parse(raw_message)
            if not parsed:
                return
            
            # Step 2 & 3: Validate and Buffer
            if parsed.event == "liquidation":
                validation = self.data_validator.validate_liquidation(parsed.raw)
                if validation.is_valid:
                    liq_event = self.message_parser.parse_liquidation(parsed.raw)
                    if liq_event:
                        self.buffer_manager.add_liquidation(liq_event.symbol, liq_event.raw_data)
                        
            elif parsed.event == "trade":
                validation = self.data_validator.validate_trade(parsed.raw)
                if validation.is_valid:
                    trade_event = self.message_parser.parse_trade(parsed.raw)
                    if trade_event:
                        self.buffer_manager.add_trade(trade_event.symbol, trade_event.raw_data)
            
            # Step 4-9: Analysis and alerting (async to not block)
            # Only analyze for configured symbols
            symbols_to_analyze = self.config.get('symbols', ['BTCUSDT', 'ETHUSDT'])
            
            for symbol in symbols_to_analyze:
                asyncio.create_task(self.analyze_and_alert(symbol))
            
            self.stats['messages_processed'] += 1
            
        except Exception as e:
            self.stats['errors'] += 1
            self.logger.error(f"Message processing error: {e}")
    
    async def analyze_and_alert(self, symbol: str):
        """
        Run analysis and send alerts for symbol
        """
        try:
            # Step 4: Run analyzers
            stop_hunt_signal = await self.stop_hunt_detector.analyze(symbol)
            order_flow_signal = await self.order_flow_analyzer.analyze(symbol)
            event_signals = await self.event_detector.analyze(symbol)
            
            # Step 5: Generate unified signal
            trading_signal = await self.signal_generator.generate(
                symbol=symbol,
                stop_hunt_signal=stop_hunt_signal,
                order_flow_signal=order_flow_signal,
                event_signals=event_signals
            )
            
            if not trading_signal:
                return
            
            # Step 6: Adjust confidence
            adjusted_confidence = self.confidence_scorer.adjust_confidence(
                trading_signal.confidence,
                trading_signal.signal_type,
                trading_signal.metadata
            )
            trading_signal.confidence = adjusted_confidence
            
            # Step 7: Validate signal
            is_valid, reason = self.signal_validator.validate(trading_signal)
            if not is_valid:
                self.logger.debug(f"Signal rejected: {reason}")
                return
            
            self.stats['signals_generated'] += 1
            
            # Step 8: Format message
            formatted_message = self.message_formatter.format_signal(trading_signal)
            
            # Step 9: Queue alert
            await self.alert_queue.add(
                formatted_message,
                priority=trading_signal.priority
            )
            
            self.logger.info(f"🎯 Signal queued: {symbol} {trading_signal.signal_type}")
            
        except Exception as e:
            self.logger.error(f"Analysis error for {symbol}: {e}")
    
    async def alert_processor(self):
        """
        Background task: process alert queue and send to Telegram
        """
        self.logger.info("🚀 Alert processor started")
        
        while not shutdown_event.is_set():
            try:
                # Get next alert from queue
                queued_alert = await self.alert_queue.get(timeout=1.0)
                
                if queued_alert:
                    # Send to Telegram
                    if self.telegram_bot:
                        success = await self.telegram_bot.send_alert(queued_alert.alert)
                        
                        if success:
                            await self.alert_queue.mark_processed(success=True)
                            self.stats['alerts_sent'] += 1
                        else:
                            # Retry failed alert
                            await self.alert_queue.retry(queued_alert)
                    else:
                        # No Telegram configured - just log
                        self.logger.info(f"📤 Alert (Telegram disabled):\n{queued_alert.alert[:100]}...")
                        await self.alert_queue.mark_processed(success=True)
                        
            except Exception as e:
                self.logger.error(f"Alert processor error: {e}")
                await asyncio.sleep(1)
        
        self.logger.info("Alert processor stopped")
    
    async def stats_reporter(self):
        """
        Background task: report statistics every 5 minutes
        """
        while not shutdown_event.is_set():
            await asyncio.sleep(300)  # 5 minutes
            
            self.logger.info("📊 Statistics Report:")
            self.logger.info(f"   Messages: {self.stats['messages_received']} received, {self.stats['messages_processed']} processed")
            self.logger.info(f"   Signals: {self.stats['signals_generated']} generated")
            self.logger.info(f"   Alerts: {self.stats['alerts_sent']} sent")
            self.logger.info(f"   Errors: {self.stats['errors']}")
            self.logger.info(f"   Buffer: {self.buffer_manager.get_stats()}")
            self.logger.info(f"   Queue: {self.alert_queue.get_stats()}")
    
    async def cleanup_task(self):
        """
        Background task: cleanup old data every hour
        """
        while not shutdown_event.is_set():
            await asyncio.sleep(3600)  # 1 hour
            
            self.logger.info("🧹 Running cleanup...")
            self.buffer_manager.cleanup_old_data(max_age_seconds=7200)  # 2 hours
    
    async def run(self):
        """
        Run the complete system
        """
        self.logger.info("=" * 60)
        self.logger.info("🚀 TELEGLAS Pro - Starting")
        self.logger.info("=" * 60)
        
        try:
            # Setup callbacks
            self.websocket_client.on_message(self.on_message)
            
            # Connect WebSocket
            self.logger.info("Connecting to CoinGlass WebSocket...")
            connected = await self.websocket_client.connect()
            
            if not connected:
                self.logger.error("❌ Failed to connect to WebSocket")
                return
            
            # Test Telegram if enabled
            if self.telegram_bot:
                self.logger.info("Testing Telegram connection...")
                if await self.telegram_bot.test_connection():
                    self.logger.info("✅ Telegram connected")
                else:
                    self.logger.warning("⚠️ Telegram connection failed")
            
            # Start background tasks
            tasks = [
                asyncio.create_task(self.alert_processor()),
                asyncio.create_task(self.stats_reporter()),
                asyncio.create_task(self.cleanup_task())
            ]
            
            self.logger.info("=" * 60)
            self.logger.info("✅ TELEGLAS Pro - Running")
            self.logger.info("=" * 60)
            self.logger.info("Press Ctrl+C to stop")
            
            # Wait for shutdown
            await shutdown_event.wait()
            
            # Cleanup
            self.logger.info("Shutting down...")
            await self.websocket_client.disconnect()
            
            # Cancel background tasks
            for task in tasks:
                task.cancel()
            
            # Wait for tasks to finish
            await asyncio.gather(*tasks, return_exceptions=True)
            
            self.logger.info("✅ Shutdown complete")
            
        except Exception as e:
            self.logger.error(f"Runtime error: {e}")
            raise

def load_config() -> dict:
    """Load configuration from files"""
    # Load secrets from .env
    load_dotenv("config/secrets.env")
    
    # Load main config
    config_path = Path("config/config.yaml")
    if config_path.exists():
        with open(config_path) as f:
            config = yaml.safe_load(f)
    else:
        # Default config
        config = {
            'coinglass': {
                'api_key': os.getenv('COINGLASS_API_KEY', ''),
                'reconnect_delay': 5,
                'max_reconnect_delay': 60
            },
            'telegram': {
                'enabled': bool(os.getenv('TELEGRAM_BOT_TOKEN')),
                'bot_token': os.getenv('TELEGRAM_BOT_TOKEN', ''),
                'chat_id': os.getenv('TELEGRAM_CHAT_ID', ''),
                'rate_limit': 3.0
            },
            'symbols': ['BTCUSDT', 'ETHUSDT', 'BNBUSDT'],
            'buffer': {
                'max_liquidations': 1000,
                'max_trades': 500
            },
            'analyzers': {
                'stop_hunt_threshold': 2_000_000,
                'large_order_threshold': 10_000
            },
            'signals': {
                'min_confidence': 65.0,
                'learning_rate': 0.1,
                'max_per_hour': 50,
                'cooldown_minutes': 5
            }
        }
    
    return config

def handle_shutdown(signum=None, frame=None):
    """Handle shutdown signals"""
    print("\n🛑 Received shutdown signal")
    shutdown_event.set()

async def main():
    """Main entry point"""
    logger = setup_logger("Main", "INFO")
    
    try:
        # Load configuration
        logger.info("Loading configuration...")
        config = load_config()
        
        # Validate required config
        if not config['coinglass'].get('api_key'):
            logger.error("❌ COINGLASS_API_KEY not configured!")
            logger.info("Please set it in config/secrets.env")
            return
        
        # Initialize application
        app = TeleglasPro(config)
        
        # Run
        await app.run()
        
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise

if __name__ == "__main__":
    # Setup signal handlers
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)
    
    # Run
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
