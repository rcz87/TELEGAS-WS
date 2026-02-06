# TELEGLAS Pro - Main Entry Point
# Real-Time Market Intelligence System - PRODUCTION READY v4.0
# ALL-COIN monitoring with dynamic tiered thresholds

"""
TELEGLAS Pro - Complete Integration v4.0

Connects all layers into working system:
WebSocket → Processors → Analyzers → Signals → Alerts → Telegram

Provides 30-90 second information edge through:
- Stop Hunt Detection (dynamic thresholds per coin tier)
- Order Flow Analysis (whale tracking)
- Event Pattern Detection (market anomalies)
- ALL-coin monitoring via liquidationOrders channel
- Dynamic trade subscriptions for active coins

v4.0 Changes:
- ALL coins from CoinGlass are now monitored (not just 3)
- Dynamic tiered thresholds: BTC $2M, mid-caps $200K, small coins $50K
- Auto-discovery of new coins from liquidation data
- Expanded trade subscriptions (primary + secondary)
- Fixed parameter name bugs in buffer calls
"""

import asyncio
import sys
import signal
import os
import threading
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime, timedelta
import yaml
import uvicorn

# Import all components
from src.connection.websocket_client import WebSocketClient
from src.dashboard import api as dashboard_api
from src.processors.data_validator import DataValidator
from src.processors.buffer_manager import BufferManager
from src.analyzers.stop_hunt_detector import StopHuntDetector
from src.analyzers.order_flow_analyzer import OrderFlowAnalyzer
from src.analyzers.event_pattern_detector import EventPatternDetector
from src.signals.signal_generator import SignalGenerator
from src.signals.confidence_scorer import ConfidenceScorer
from src.signals.signal_validator import SignalValidator
from src.signals.signal_tracker import SignalTracker
from src.alerts.message_formatter import MessageFormatter
from src.alerts.telegram_bot import TelegramBot
from src.alerts.alert_queue import AlertQueue
from src.storage.database import Database
from src.utils.logger import setup_logger

# Global flag for shutdown
shutdown_event = asyncio.Event()

def start_dashboard_server():
    """Start dashboard server in background thread"""
    uvicorn.run(
        dashboard_api.app,
        host="0.0.0.0",
        port=8080,
        log_level="warning"
    )

class TeleglasPro:
    """
    Main application class - integrates all components
    """
    
    def __init__(self, config: dict):
        """Initialize all components with correct config"""
        self.config = config
        self.logger = setup_logger("TeleglasPro", "INFO")
        
        # Get config sections
        pairs_config = config.get('pairs', {})
        thresholds = config.get('thresholds', {})
        signals_config = config.get('signals', {})
        buffers_config = config.get('buffers', {})
        analysis_config = config.get('analysis', {})
        
        # Processors
        self.data_validator = DataValidator()
        self.buffer_manager = BufferManager(
            max_liquidations=buffers_config.get('max_liquidations', 1000),
            max_trades=buffers_config.get('max_trades', 500)
        )
        
        # Monitoring config for dynamic all-coin thresholds
        monitoring_config = config.get('monitoring', {})

        # Analyzers (using correct config paths + monitoring tiers)
        detection_config = config.get('detection', {})
        self.stop_hunt_detector = StopHuntDetector(
            self.buffer_manager,
            threshold=thresholds.get('liquidation_cascade', 2_000_000),
            absorption_min_order_usd=detection_config.get('absorption_min_order_usd', 5000),
            monitoring_config=monitoring_config
        )
        self.order_flow_analyzer = OrderFlowAnalyzer(
            self.buffer_manager,
            large_order_threshold=thresholds.get('large_order_threshold', 10_000),
            monitoring_config=monitoring_config
        )
        self.event_detector = EventPatternDetector(
            self.buffer_manager,
            monitoring_config=monitoring_config,
            large_order_threshold=thresholds.get('large_order_threshold', 10_000)
        )
        
        # Signals
        self.signal_generator = SignalGenerator(
            min_confidence=signals_config.get('min_confidence', 65.0)
        )
        self.confidence_scorer = ConfidenceScorer(
            learning_rate=0.1,
            monitoring_config=monitoring_config
        )
        # CRITICAL FIX Bug #6: Load cooldown from config (was hardcoded)
        self.signal_validator = SignalValidator(
            min_confidence=signals_config.get('min_confidence', 70.0),
            max_signals_per_hour=signals_config.get('max_signals_per_hour', 50),
            cooldown_minutes=signals_config.get('cooldown_minutes', 5)
        )
        
        # Alerts
        self.message_formatter = MessageFormatter()
        self.alert_queue = AlertQueue(max_size=1000)

        # Database for persistent storage
        storage_config = config.get('storage', {})
        db_path = storage_config.get('database_url', 'data/teleglas.db')
        self.db = Database(db_path=db_path)

        # Signal outcome tracker (with DB callback for persistence)
        self.signal_tracker = SignalTracker(
            buffer_manager=self.buffer_manager,
            confidence_scorer=self.confidence_scorer,
            check_interval_seconds=config.get('analysis', {}).get('signal_check_interval', 900),
            on_outcome_callback=self._on_signal_outcome
        )
        
        # Telegram (optional - only if configured)
        telegram_config = config.get('telegram', {})
        self.telegram_bot = None
        if telegram_config.get('enabled', False):
            self.telegram_bot = TelegramBot(
                bot_token=telegram_config.get('bot_token', ''),
                chat_id=telegram_config.get('chat_id', ''),
                rate_limit_delay=config.get('alerts', {}).get('rate_limit_delay', 3.0)
            )
        
        # WebSocket
        ws_config = config.get('websocket', {})
        coinglass_config = config.get('coinglass', {})
        self.websocket_client = WebSocketClient(
            api_key=coinglass_config.get('api_key', ''),
            url=ws_config.get('url', "wss://open-ws.coinglass.com/ws-api"),
            reconnect_delay=ws_config.get('reconnect_delay', 5),
            max_reconnect_delay=ws_config.get('max_reconnect_delay', 60),
            heartbeat_interval=ws_config.get('heartbeat_interval', 20)
        )
        
        # Symbols configuration
        # primary = coins with trade data subscription
        # secondary = additional coins with trade data subscription
        # ALL coins from liquidationOrders are monitored regardless
        self.primary_symbols = pairs_config.get('primary', ['BTCUSDT', 'ETHUSDT'])
        self.secondary_symbols = pairs_config.get('secondary', [])
        self.trade_symbols = self.primary_symbols + self.secondary_symbols
        self.monitoring_mode = monitoring_config.get('mode', 'all')
        self.max_concurrent_analysis = monitoring_config.get('max_concurrent_analysis', 30)

        # Track dynamically discovered coins (from liquidation data)
        self.discovered_symbols: set = set()
        self._trade_subscribed: set = set()  # Symbols with active trade subscriptions

        # Debouncing (FIX: Prevent task explosion)
        self.analysis_locks = {}  # Per-symbol locks
        self.last_analysis = {}   # Per-symbol last analysis time
        self._analysis_tasks: set = set()
        
        # Statistics
        self.stats = {
            'messages_received': 0,
            'messages_processed': 0,
            'liquidations_processed': 0,
            'trades_processed': 0,
            'signals_generated': 0,
            'alerts_sent': 0,
            'errors': 0,
            'uptime_seconds': 0
        }
        
        # Start time
        self.start_time = datetime.now()
        
        # Initialize dashboard with configured coins (more will be auto-added)
        dashboard_api.initialize_coins(self.trade_symbols)
        
        # Start dashboard server in background thread
        self.dashboard_thread = threading.Thread(
            target=start_dashboard_server,
            daemon=True
        )
        self.dashboard_thread.start()
        self.logger.info("📊 Dashboard started at http://localhost:8080")
        
        self.logger.info("✅ All components initialized")
    
    async def on_connect(self):
        """
        Called when WebSocket connects
        FIX BUG #1: Add subscription logic here!
        """
        self.logger.info("✅ WebSocket connected")
        
        # Subscribe to liquidation orders channel
        subscribe_msg = {
            "method": "subscribe",
            "channels": ["liquidationOrders"]
        }
        success = await self.websocket_client.send_message(subscribe_msg)
        if success:
            self.logger.info("📡 Subscribed to liquidationOrders channel")
        else:
            self.logger.error("❌ Failed to subscribe to liquidationOrders")
        
        # Subscribe to futures trades for all configured coins (primary + secondary)
        for symbol in self.trade_symbols:
            trade_channel = f"futures_trades@all_{symbol}@0"
            subscribe_msg = {
                "method": "subscribe",
                "channels": [trade_channel]
            }
            success = await self.websocket_client.send_message(subscribe_msg)
            if success:
                self._trade_subscribed.add(symbol)
                self.logger.info(f"📡 Subscribed to {trade_channel}")

        self.logger.info(
            f"📡 Trade subscriptions: {len(self._trade_subscribed)} coins | "
            f"Liquidations: ALL coins (mode={self.monitoring_mode})"
        )
    
    async def on_message(self, raw_message):
        """
        Main message processing pipeline
        
        FIX BUG #2: raw_message is already a dict (not string)!
        WebSocket client parses JSON, we receive dict directly.
        
        Pipeline:
        1. Route by channel type
        2. Validate data
        3. Buffer data
        4. Trigger analysis (debounced)
        """
        try:
            self.stats['messages_received'] += 1
            
            # Message is already parsed as dict by WebSocket client
            if not isinstance(raw_message, dict):
                self.logger.warning(f"Unexpected message type: {type(raw_message)}")
                return
            
            channel = raw_message.get('channel', '')
            event = raw_message.get('event', '')
            
            # Route by channel type
            if channel == 'liquidationOrders' or event == 'liquidationOrders':
                await self._handle_liquidation_message(raw_message)
            elif 'futures_trades' in channel:
                await self._handle_trade_message(raw_message)
            else:
                # Ignore ping/pong and other system messages
                if event not in ['ping', 'pong', 'login']:
                    self.logger.debug(f"Unknown channel/event: {channel}/{event}")
            
            self.stats['messages_processed'] += 1
            
        except Exception as e:
            self.stats['errors'] += 1
            self.logger.error(f"Message processing error: {e}")
    
    async def _handle_liquidation_message(self, message: dict):
        """
        Process liquidation order messages from ALL coins.

        CoinGlass liquidationOrders channel sends data for ALL coins.
        We now process ALL of them (not just primary 3) with dynamic thresholds.
        """
        try:
            # Extract liquidation events from message
            data = message.get('data', [])
            if not isinstance(data, list):
                data = [data] if data else []

            for liq_event in data:
                # Validate structure
                validation = self.data_validator.validate_liquidation(liq_event)
                if validation.is_valid:
                    symbol = liq_event.get('symbol', 'UNKNOWN')

                    # Add to buffer (FIX: was 'liquidation_data=' - wrong param name)
                    self.buffer_manager.add_liquidation(
                        symbol=symbol,
                        event=liq_event
                    )

                    self.stats['liquidations_processed'] += 1

                    # Track newly discovered coins
                    if symbol not in self.discovered_symbols and symbol not in self.trade_symbols:
                        self.discovered_symbols.add(symbol)
                        self.logger.info(f"🔍 New coin discovered: {symbol}")

                    # Trigger analysis for ALL coins (debounced, resource-limited)
                    if len(self._analysis_tasks) < self.max_concurrent_analysis:
                        task = asyncio.create_task(self.analyze_and_alert(symbol))
                        self._analysis_tasks.add(task)
                        task.add_done_callback(self._analysis_tasks.discard)

        except Exception as e:
            self.logger.error(f"Error handling liquidation: {e}")
            self.stats['errors'] += 1
    
    async def _handle_trade_message(self, message: dict):
        """
        Process trade messages for subscribed coins.
        Trade data enhances analysis (absorption detection, order flow).
        """
        try:
            # Extract trade events from message
            data = message.get('data', [])
            if not isinstance(data, list):
                data = [data] if data else []

            for trade in data:
                # Validate structure
                validation = self.data_validator.validate_trade(trade)
                if validation.is_valid:
                    symbol = trade.get('symbol', 'UNKNOWN')

                    # Add to buffer (FIX: was 'trade_data=' - wrong param name)
                    self.buffer_manager.add_trade(
                        symbol=symbol,
                        event=trade
                    )

                    self.stats['trades_processed'] += 1

                    # Trigger analysis for this symbol (debounced, resource-limited)
                    if len(self._analysis_tasks) < self.max_concurrent_analysis:
                        task = asyncio.create_task(self.analyze_and_alert(symbol))
                        self._analysis_tasks.add(task)
                        task.add_done_callback(self._analysis_tasks.discard)

        except Exception as e:
            self.logger.error(f"Error handling trade: {e}")
            self.stats['errors'] += 1
    
    def _is_coin_active(self, symbol: str) -> bool:
        """
        Check if coin is active (not disabled) on the dashboard.

        Coins not in the dashboard list are considered active by default
        (newly discovered coins). Only explicitly toggled-off coins are inactive.
        """
        with dashboard_api.state_lock:
            all_coins = dashboard_api.system_state.get("coins", [])
            all_symbols = [c["symbol"] for c in all_coins]
            active_coins = [c["symbol"] for c in all_coins if c.get("active", True)]
        if symbol in all_symbols:
            return symbol in active_coins
        return True  # New/undiscovered coins default to active

    async def _on_signal_outcome(self, tracked, pnl_pct: float):
        """Callback: save signal outcome to database."""
        try:
            if tracked._db_id:
                await self.db.update_signal_outcome(
                    signal_id=tracked._db_id,
                    outcome=tracked.outcome,
                    exit_price=tracked.exit_price or 0,
                    pnl_pct=pnl_pct
                )
        except Exception as e:
            self.logger.debug(f"DB outcome save error: {e}")

    async def _init_database(self):
        """Connect to database and restore saved state."""
        try:
            await self.db.connect()

            # Restore confidence scorer state
            saved_confidence = await self.db.load_confidence_state()
            if saved_confidence:
                for signal_type, state in saved_confidence.items():
                    self.confidence_scorer.win_rates[signal_type] = state["win_rate"]
                    self.confidence_scorer.signal_history[signal_type] = state["history"]
                self.logger.info(
                    f"Restored confidence state: "
                    f"{len(saved_confidence)} signal types loaded"
                )

            # Restore dashboard coin toggle state
            saved_coins = await self.db.load_dashboard_coins()
            if saved_coins:
                # Merge: keep config coins, overlay saved active/inactive state
                saved_map = {c["symbol"]: c["active"] for c in saved_coins}
                with dashboard_api.state_lock:
                    for coin in dashboard_api.system_state["coins"]:
                        if coin["symbol"] in saved_map:
                            coin["active"] = saved_map[coin["symbol"]]
                    # Add coins that were saved but not in config
                    existing = {c["symbol"] for c in dashboard_api.system_state["coins"]}
                    for sc in saved_coins:
                        if sc["symbol"] not in existing:
                            dashboard_api.system_state["coins"].append({
                                "symbol": sc["symbol"],
                                "active": sc["active"],
                                "buy_ratio": 0, "sell_ratio": 0,
                                "large_buys": 0, "large_sells": 0,
                                "last_update": "restored"
                            })
                self.logger.info(f"Restored dashboard state: {len(saved_coins)} coins")

            # Restore baselines into buffer_manager
            for symbol in self.trade_symbols:
                baselines = await self.db.load_baselines(symbol, hours=24)
                if baselines:
                    from collections import deque
                    if symbol not in self.buffer_manager._hourly_liq_volume:
                        self.buffer_manager._hourly_liq_volume[symbol] = deque(maxlen=24)
                    if symbol not in self.buffer_manager._hourly_trade_volume:
                        self.buffer_manager._hourly_trade_volume[symbol] = deque(maxlen=24)
                    for b in baselines:
                        self.buffer_manager._hourly_liq_volume[symbol].append(
                            (b["recorded_at"], b["liq_volume"])
                        )
                        self.buffer_manager._hourly_trade_volume[symbol].append(
                            (b["recorded_at"], b["trade_volume"])
                        )
            baseline_count = sum(
                len(v) for v in self.buffer_manager._hourly_liq_volume.values()
            )
            if baseline_count:
                self.logger.info(f"Restored baselines: {baseline_count} data points")

            # Share DB reference with dashboard for export endpoints
            dashboard_api._db = self.db

        except Exception as e:
            self.logger.error(f"Database init error: {e} (continuing without persistence)")

    async def _save_state(self):
        """Save all state to database before shutdown."""
        try:
            # Save confidence scorer state
            for signal_type, history in self.confidence_scorer.signal_history.items():
                if history:
                    await self.db.save_confidence_state(
                        signal_type,
                        self.confidence_scorer.win_rates.get(signal_type, 0.5),
                        history
                    )

            # Save dashboard coin state
            with dashboard_api.state_lock:
                coins = [
                    {"symbol": c["symbol"], "active": c.get("active", True)}
                    for c in dashboard_api.system_state["coins"]
                ]
            await self.db.save_dashboard_coins(coins)

            self.logger.info("State saved to database")

        except Exception as e:
            self.logger.error(f"State save error: {e}")

    async def analyze_and_alert(self, symbol: str):
        """
        Run analysis and send alerts for symbol.

        Analysis runs for ALL coins (data is always collected).
        Telegram alerts only sent for coins that are ACTIVE on dashboard.
        Dashboard signals are always shown regardless.
        """
        try:
            # Debounce: Don't analyze same symbol within 5 seconds
            now = asyncio.get_event_loop().time()
            if symbol in self.last_analysis:
                if now - self.last_analysis[symbol] < 5:
                    return

            # Lock to prevent concurrent analysis of same symbol
            if symbol not in self.analysis_locks:
                self.analysis_locks[symbol] = asyncio.Lock()

            async with self.analysis_locks[symbol]:
                self.last_analysis[symbol] = now

                # Run analyzers (always - data collection doesn't depend on toggle)
                stop_hunt_signal = await self.stop_hunt_detector.analyze(symbol)
                order_flow_signal = await self.order_flow_analyzer.analyze(symbol)
                event_signals = await self.event_detector.analyze(symbol)

                # Generate unified signal
                trading_signal = await self.signal_generator.generate(
                    symbol=symbol,
                    stop_hunt_signal=stop_hunt_signal,
                    order_flow_signal=order_flow_signal,
                    event_signals=event_signals
                )

                if not trading_signal:
                    return

                # Adjust confidence
                adjusted_confidence = self.confidence_scorer.adjust_confidence(
                    trading_signal.confidence,
                    trading_signal.signal_type,
                    trading_signal.metadata,
                    symbol=symbol
                )
                trading_signal.confidence = adjusted_confidence

                # Validate signal
                is_valid, reason = self.signal_validator.validate(trading_signal)
                if not is_valid:
                    self.logger.debug(f"Signal rejected: {reason}")
                    return

                self.stats['signals_generated'] += 1

                # Inject track record into metadata for message_formatter
                track_record = self.signal_tracker.get_track_record(trading_signal.signal_type)
                trading_signal.metadata.setdefault('stop_hunt', {})['track_record'] = track_record

                # Inject baseline context into metadata
                baseline = self.buffer_manager.get_baseline(symbol)
                trading_signal.metadata['baseline'] = baseline

                # Always send to dashboard (user can see all signals in web UI)
                dashboard_api.add_signal({
                    'symbol': symbol,
                    'type': trading_signal.signal_type,
                    'confidence': int(trading_signal.confidence),
                    'description': f"{trading_signal.signal_type} detected"
                })

                # Check dashboard toggle: only send Telegram alert if coin is ACTIVE
                if self._is_coin_active(symbol):
                    # Format message
                    formatted_message = self.message_formatter.format_signal(trading_signal)

                    # Queue alert (sends to Telegram)
                    await self.alert_queue.add(
                        formatted_message,
                        priority=trading_signal.priority
                    )
                    self.logger.info(f"🎯 Signal queued: {symbol} {trading_signal.signal_type}")
                else:
                    self.logger.info(f"🔇 Signal skipped (coin inactive): {symbol} {trading_signal.signal_type}")

                # Track signal for outcome measurement (always, regardless of toggle)
                price_zone = trading_signal.metadata.get('stop_hunt', {}).get('price_zone', (0, 0))
                if price_zone[1] > 0:
                    zone_spread = abs(price_zone[1] - price_zone[0])
                    is_long = trading_signal.direction == "LONG"
                    entry = price_zone[1] if is_long else price_zone[0]
                    sl = price_zone[0] - (zone_spread * 0.3) if is_long else price_zone[1] + (zone_spread * 0.3)
                    risk = abs(entry - sl)
                    tp = entry + (risk * 2) if is_long else entry - (risk * 2)
                    tracked = self.signal_tracker.track_signal(trading_signal, entry, sl, tp)

                    # Save signal to database
                    try:
                        db_id = await self.db.save_signal(
                            symbol=symbol,
                            signal_type=trading_signal.signal_type,
                            direction=trading_signal.direction,
                            confidence=trading_signal.confidence,
                            entry_price=entry,
                            stop_loss=sl,
                            target_price=tp
                        )
                        tracked._db_id = db_id
                    except Exception:
                        pass  # DB save is non-critical

        except Exception as e:
            self.logger.error(f"Analysis error for {symbol}: {e}")
    
    async def alert_processor(self):
        """Background task: process alert queue and send to Telegram"""
        self.logger.info("🚀 Alert processor started")
        
        while not shutdown_event.is_set():
            try:
                queued_alert = await self.alert_queue.get(timeout=1.0)
                
                if queued_alert:
                    if self.telegram_bot:
                        success = await self.telegram_bot.send_alert(queued_alert.alert)
                        
                        if success:
                            await self.alert_queue.mark_processed(success=True)
                            self.stats['alerts_sent'] += 1
                        else:
                            retried = await self.alert_queue.retry(queued_alert)
                            if not retried:
                                # Max retries exhausted — mark as failed so queue doesn't hang
                                await self.alert_queue.mark_processed(success=False)
                                self.logger.warning(f"Alert dropped after {queued_alert.max_retries} retries")
                    else:
                        # No Telegram - just log
                        self.logger.info(f"📤 Alert (Telegram disabled):\n{queued_alert.alert[:100]}...")
                        await self.alert_queue.mark_processed(success=True)
                        
            except Exception as e:
                self.logger.error(f"Alert processor error: {e}")
                await asyncio.sleep(1)
        
        self.logger.info("Alert processor stopped")
    
    async def stats_reporter(self):
        """Background task: report statistics every 5 minutes"""
        while not shutdown_event.is_set():
            await asyncio.sleep(30)  # Every 30 seconds for dashboard
            
            # Update uptime
            uptime = (datetime.now() - self.start_time).total_seconds()
            self.stats['uptime_seconds'] = int(uptime)

            # Collect analyzer stats for dashboard visibility
            self.stats['analyzers'] = {
                'stop_hunt': self.stop_hunt_detector.get_stats(),
                'order_flow': self.order_flow_analyzer.get_stats(),
                'events': self.event_detector.get_stats(),
                'validator': self.signal_validator.get_stats(),
                'confidence': self.confidence_scorer.get_overall_stats(),
                'tracker': self.signal_tracker.get_overall_stats(),
            }

            # Update dashboard
            dashboard_api.update_stats(self.stats)
            
            # Log every 5 minutes
            if int(uptime) % 300 == 0:
                self.logger.info("📊 Statistics Report:")
                self.logger.info(f"   Messages: {self.stats['messages_received']} received, {self.stats['messages_processed']} processed")
                self.logger.info(f"   Liquidations: {self.stats['liquidations_processed']}, Trades: {self.stats['trades_processed']}")
                self.logger.info(f"   Signals: {self.stats['signals_generated']} generated")
                self.logger.info(f"   Alerts: {self.stats['alerts_sent']} sent")
                self.logger.info(f"   Errors: {self.stats['errors']}")
                self.logger.info(f"   Coins tracked: {len(self.buffer_manager.get_tracked_symbols())} (discovered: {len(self.discovered_symbols)})")
    
    async def signal_tracker_task(self):
        """Background task: check signal outcomes every 60 seconds"""
        self.logger.info("📊 Signal tracker started")
        while not shutdown_event.is_set():
            await asyncio.sleep(60)
            try:
                await self.signal_tracker.check_outcomes()
            except Exception as e:
                self.logger.error(f"Signal tracker error: {e}")

    async def dynamic_subscription_task(self):
        """
        Background task: manage trade channel subscriptions.

        Two responsibilities:
        1. Auto-subscribe to newly discovered coins with significant activity
        2. Process dashboard add/remove coin requests (subscribe/unsubscribe)

        Runs every 10 seconds for responsive dashboard actions,
        with auto-discovery check every 5 minutes.
        """
        last_discovery_check = 0

        while not shutdown_event.is_set():
            await asyncio.sleep(10)  # Check dashboard requests every 10s
            try:
                # --- Process dashboard subscription requests ---
                pending = dashboard_api.get_pending_subscriptions()
                for req in pending:
                    symbol = req["symbol"]
                    action = req["action"]

                    if action == "subscribe" and symbol not in self._trade_subscribed:
                        trade_channel = f"futures_trades@all_{symbol}@0"
                        subscribe_msg = {
                            "method": "subscribe",
                            "channels": [trade_channel]
                        }
                        success = await self.websocket_client.send_message(subscribe_msg)
                        if success:
                            self._trade_subscribed.add(symbol)
                            self.logger.info(f"📡 Dashboard subscription: {trade_channel}")

                    elif action == "unsubscribe" and symbol in self._trade_subscribed:
                        trade_channel = f"futures_trades@all_{symbol}@0"
                        unsubscribe_msg = {
                            "method": "unsubscribe",
                            "channels": [trade_channel]
                        }
                        success = await self.websocket_client.send_message(unsubscribe_msg)
                        if success:
                            self._trade_subscribed.discard(symbol)
                            self.logger.info(f"📡 Dashboard unsubscription: {trade_channel}")

                # --- Auto-discovery check (every 5 minutes) ---
                now = asyncio.get_event_loop().time()
                if now - last_discovery_check < 300:
                    continue
                last_discovery_check = now

                for symbol in list(self.discovered_symbols):
                    if symbol in self._trade_subscribed:
                        continue

                    # Check if this coin has recent liquidation activity
                    liqs = self.buffer_manager.get_liquidations(symbol, time_window=300)
                    if len(liqs) >= 3:  # At least 3 liquidations in 5 min = worth subscribing
                        trade_channel = f"futures_trades@all_{symbol}@0"
                        subscribe_msg = {
                            "method": "subscribe",
                            "channels": [trade_channel]
                        }
                        success = await self.websocket_client.send_message(subscribe_msg)
                        if success:
                            self._trade_subscribed.add(symbol)
                            self.logger.info(
                                f"📡 Dynamic subscription: {trade_channel} "
                                f"({len(liqs)} liquidations detected)"
                            )

                            # Also add to dashboard
                            dashboard_api.add_signal({
                                'symbol': symbol,
                                'type': 'DISCOVERY',
                                'confidence': 0,
                                'description': f"New coin discovered with {len(liqs)} liquidations"
                            })

            except Exception as e:
                self.logger.error(f"Dynamic subscription error: {e}")

    async def cleanup_task(self):
        """Background task: cleanup old data every hour, save baselines to DB"""
        while not shutdown_event.is_set():
            await asyncio.sleep(3600)  # 1 hour

            self.logger.info("🧹 Running cleanup...")
            self.buffer_manager.cleanup_old_data(max_age_seconds=7200)  # 2 hours

            # Update hourly baseline for context comparison
            self.buffer_manager.update_hourly_baseline()

            # Save baselines to database
            try:
                for symbol in self.buffer_manager.get_tracked_symbols():
                    liqs = self.buffer_manager.get_liquidations(symbol, time_window=3600)
                    liq_vol = sum(float(l.get("vol", 0)) for l in liqs)
                    trades = self.buffer_manager.get_trades(symbol, time_window=3600)
                    trade_vol = sum(float(t.get("vol", 0)) for t in trades)
                    if liq_vol > 0 or trade_vol > 0:
                        await self.db.save_baseline(symbol, liq_vol, trade_vol)
                await self.db.cleanup_old_baselines(max_age_hours=72)
            except Exception as e:
                self.logger.error(f"Baseline save error: {e}")

            # Periodically save confidence state
            try:
                for signal_type, history in self.confidence_scorer.signal_history.items():
                    if history:
                        await self.db.save_confidence_state(
                            signal_type,
                            self.confidence_scorer.win_rates.get(signal_type, 0.5),
                            history
                        )
            except Exception as e:
                self.logger.error(f"Confidence save error: {e}")
    
    async def run(self):
        """Run the complete system"""
        self.logger.info("=" * 60)
        self.logger.info("🚀 TELEGLAS Pro v4.0 - Starting (ALL-COIN Monitoring)")
        self.logger.info("=" * 60)

        try:
            # Initialize database and restore saved state
            await self._init_database()

            # Setup callbacks
            self.websocket_client.on_connect(self.on_connect)
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
            
            # CRITICAL FIX Bug #2: Mark as initialized before starting tasks
            self.logger.info("✅ System initialization complete")
            
            # Start background tasks
            tasks = [
                asyncio.create_task(self.alert_processor()),
                asyncio.create_task(self.stats_reporter()),
                asyncio.create_task(self.cleanup_task()),
                asyncio.create_task(self.signal_tracker_task()),
                asyncio.create_task(self.dynamic_subscription_task())
            ]
            
            self.logger.info("=" * 60)
            self.logger.info("✅ TELEGLAS Pro v4.0 - Running (ALL-COIN Monitoring)")
            self.logger.info("=" * 60)
            self.logger.info(f"Trade subscriptions: {', '.join(self.trade_symbols)}")
            self.logger.info(f"Liquidation monitoring: ALL coins (mode={self.monitoring_mode})")
            self.logger.info("Press Ctrl+C to stop")
            
            # Wait for shutdown
            await shutdown_event.wait()
            
            # Cleanup
            self.logger.info("Shutting down...")
            await self.websocket_client.disconnect()

            # Cancel background tasks
            for task in tasks:
                task.cancel()

            await asyncio.gather(*tasks, return_exceptions=True)

            # Save state to database before exit
            await self._save_state()
            await self.db.close()

            self.logger.info("✅ Shutdown complete")
            
        except Exception as e:
            self.logger.error(f"Runtime error: {e}")
            raise

def validate_config(config: dict) -> tuple[bool, list[str]]:
    """
    Validate configuration structure
    
    CRITICAL FIX Bug #11: Config validation to catch errors early
    
    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []
    required_sections = ['pairs', 'thresholds', 'signals', 'alerts', 'buffers', 'websocket', 'monitoring']
    
    for section in required_sections:
        if section not in config:
            errors.append(f"Missing required section: {section}")
    
    # Validate pairs
    if 'pairs' in config:
        if 'primary' not in config['pairs'] or not config['pairs']['primary']:
            errors.append("Config error: pairs.primary must contain at least one symbol")
    
    # Validate numeric values
    numeric_checks = [
        ('thresholds.liquidation_cascade', config.get('thresholds', {}).get('liquidation_cascade')),
        ('signals.min_confidence', config.get('signals', {}).get('min_confidence')),
        ('buffers.max_liquidations', config.get('buffers', {}).get('max_liquidations')),
    ]
    
    for key, value in numeric_checks:
        if value is not None and (not isinstance(value, (int, float)) or value <= 0):
            errors.append(f"Config error: {key} must be a positive number")
    
    return (len(errors) == 0, errors)

def load_config() -> dict:
    """
    Load configuration from files
    
    CRITICAL FIX Bug #11: Added validation
    """
    # Use absolute paths relative to project root (works regardless of CWD)
    project_root = Path(__file__).parent
    load_dotenv(project_root / "config" / "secrets.env")

    # Load main config
    config_path = project_root / "config" / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            config = yaml.safe_load(f)
    else:
        # Default config matching config.yaml structure
        config = {
            'pairs': {
                'primary': ['BTCUSDT', 'ETHUSDT', 'BNBUSDT']
            },
            'thresholds': {
                'liquidation_cascade': 2_000_000,
                'large_order_threshold': 10_000,
                'accumulation_ratio': 0.65,
                'distribution_ratio': 0.35
            },
            'signals': {
                'min_confidence': 70.0,
                'max_signals_per_hour': 50,
                'cooldown_minutes': 5
            },
            'alerts': {
                'rate_limit_delay': 3.0,
                'max_retries': 3
            },
            'buffers': {
                'max_liquidations': 1000,
                'max_trades': 500
            },
            'websocket': {
                'url': "wss://open-ws.coinglass.com/ws-api",
                'heartbeat_interval': 20,
                'reconnect_delay': 1,
                'max_reconnect_delay': 60
            },
            'monitoring': {
                'mode': 'all',
                'tier1_symbols': ['BTCUSDT', 'ETHUSDT'],
                'tier2_symbols': ['BNBUSDT', 'SOLUSDT', 'XRPUSDT'],
                'tier1_cascade': 2_000_000,
                'tier2_cascade': 200_000,
                'tier3_cascade': 50_000,
                'tier1_absorption': 100_000,
                'tier2_absorption': 20_000,
                'tier3_absorption': 5_000,
                'max_concurrent_analysis': 30
            },
            'analysis': {
                'stop_hunt_window': 30,
                'order_flow_window': 300
            }
        }
    
    # Add secrets from environment
    config['coinglass'] = {
        'api_key': os.getenv('COINGLASS_API_KEY', '')
    }
    config['telegram'] = {
        'enabled': bool(os.getenv('TELEGRAM_BOT_TOKEN')),
        'bot_token': os.getenv('TELEGRAM_BOT_TOKEN', ''),
        'chat_id': os.getenv('TELEGRAM_CHAT_ID', '')
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
        
        # CRITICAL FIX Bug #11: Validate config structure
        is_valid, errors = validate_config(config)
        if not is_valid:
            logger.error("❌ Configuration validation failed:")
            for error in errors:
                logger.error(f"  - {error}")
            return
        
        # Validate required config
        if not config.get('coinglass', {}).get('api_key'):
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
