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
import collections
import json as _json
import sys
import signal
import time
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
from src.connection.rest_poller import CoinGlassRestPoller
from src.processors.market_context_buffer import MarketContextBuffer
from src.signals.market_context_filter import MarketContextFilter
from src.signals.leading_indicator_scorer import LeadingIndicatorScorer
from src.utils.symbol_normalizer import normalize_symbol, to_base_symbol, display_symbol, is_tradeable_crypto
from src.utils.logger import setup_logger
from src.models.events import parse_liquidation, parse_trade
from src.signals.setup_classifier import classify_setup
from src.signals.feature_logger import FeatureLogger
from src.ml.dataset_builder import DatasetBuilder
from src.ml.calibration import CalibrationTable
from src.ml.model_trainer import ModelTrainer
from src.ml.ml_inference import MLInferenceEngine
from src.ml.guardrails import MLGuardrails
from src.signals.signal_lifecycle import SignalLifecycleManager
from src.signals.rest_signal_detector import RestSignalDetector
from src.alerts.movement_detector import MovementDetector

# Global flag for shutdown
shutdown_event = asyncio.Event()

def start_dashboard_server(host: str = "127.0.0.1", port: int = 8081):
    """Start dashboard server in background thread.

    WARNING: Default host 0.0.0.0 binds to all interfaces.
    Use 127.0.0.1 if you don't need remote access.
    """
    uvicorn.run(
        dashboard_api.app,
        host=host,
        port=port,
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
        # Tier-based cooldowns: T1=60min, T2=30min, T3=20min, T4=15min
        self.signal_validator = SignalValidator(
            min_confidence=signals_config.get('min_confidence', 70.0),
            max_signals_per_hour=signals_config.get('max_signals_per_hour', 50),
            cooldown_minutes=signals_config.get('cooldown_minutes', 5),
            monitoring_config=monitoring_config
        )
        
        # Alerts
        self.message_formatter = MessageFormatter()
        self.alert_queue = AlertQueue(max_size=1000)

        # Movement detector (stealth flow, flush/absorption, quiet-to-move)
        self.movement_detector = MovementDetector()

        # Leading indicator scorer (CVD + OI primary scoring, tier-aware)
        self.leading_scorer = LeadingIndicatorScorer(monitoring_config=monitoring_config)

        # Database for persistent storage
        storage_config = config.get('storage', {})
        db_path = storage_config.get('database_url', 'data/teleglas.db')
        self.db = Database(db_path=db_path)

        # Market Context (OI + Funding Rate from CoinGlass REST API)
        market_context_config = config.get('market_context', {})
        coinglass_config = config.get('coinglass', {})
        self.market_context_buffer = MarketContextBuffer(
            max_snapshots=market_context_config.get('max_snapshots', 72)
        )
        self.stop_hunt_detector.market_context_buffer = self.market_context_buffer
        self.market_context_filter = MarketContextFilter(
            market_context_buffer=self.market_context_buffer,
            mode=market_context_config.get('filter_mode', 'normal'),
            enable_confidence_adjust=market_context_config.get('confidence_adjust', True),
        )
        rest_symbols = self._build_rest_symbols(pairs_config)
        # Merge watchlist into REST symbols
        watchlist = config.get('watchlist', [])
        for sym in watchlist:
            s = sym.strip().upper()
            if s and s not in rest_symbols:
                rest_symbols.append(s)
        self.rest_poller = CoinGlassRestPoller(
            api_key=coinglass_config.get('api_key', ''),
            symbols=rest_symbols,
            poll_interval=market_context_config.get('poll_interval', 300),
            request_delay=0.25,
            on_oi_data=self._on_oi_data,
            on_funding_data=self._on_funding_data,
            on_spot_cvd_data=self._on_spot_cvd_data if market_context_config.get('cvd_enabled', True) else None,
            on_futures_cvd_data=self._on_futures_cvd_data if market_context_config.get('cvd_enabled', True) else None,
            on_whale_data=self._on_whale_data if market_context_config.get('whale_enabled', True) else None,
            on_orderbook_data=self._on_orderbook_data if market_context_config.get('orderbook_enabled', True) else None,
            on_funding_per_exchange_data=self._on_funding_per_exchange_data,
            on_price_data=self._on_price_data if market_context_config.get('price_enabled', True) else None,
            on_long_short_data=self._on_long_short_data,
            rate_limit_per_minute=market_context_config.get('rate_limit_per_minute', 90),
        )

        # Fast poll config — priority coins polled every 60s
        fast_poll_config = config.get('fast_poll', {})
        if fast_poll_config.get('enabled', False):
            self.rest_poller.fast_symbols = [
                s.strip().upper() for s in fast_poll_config.get('symbols', [])
            ]
            self.rest_poller.fast_poll_interval = fast_poll_config.get('interval', 60)
            self.rest_poller._on_fast_poll_done = self._on_fast_poll_done

        # Signal outcome tracker (with DB callback for persistence)
        self.signal_tracker = SignalTracker(
            buffer_manager=self.buffer_manager,
            confidence_scorer=self.confidence_scorer,
            check_interval_seconds=config.get('analysis', {}).get('signal_check_interval', 900),
            on_outcome_callback=self._on_signal_outcome
        )

        # Feature logger (ML training data)
        self.feature_logger = FeatureLogger()

        # ML components
        self.dataset_builder = DatasetBuilder()
        self.calibration = CalibrationTable()
        self.model_trainer = ModelTrainer()

        # ML inference (shadow mode by default — logs score, doesn't affect alerts)
        ml_config = config.get('ml', {})
        self.ml_engine = MLInferenceEngine(
            mode=ml_config.get('mode', 'shadow'),
            ml_weight=ml_config.get('weight', 0.3),
        )
        self.ml_guardrails = MLGuardrails()

        # Signal lifecycle (expiry, primary selection, anti-flip)
        lifecycle_expiry = config.get('signal_lifecycle', {}).get('expiry', {})
        self.lifecycle = SignalLifecycleManager(expiry_config=lifecycle_expiry or None)

        # REST-based signal detector (CVD flip, OI spike, whale activity)
        self.rest_signal_detector = RestSignalDetector(
            market_context_buffer=self.market_context_buffer,
            monitoring_config=monitoring_config,
        )

        # Dedup: prevent duplicate REST signals to Telegram (cooldown per signal key)
        self._rest_signal_cooldown: dict = {}  # key → last_sent_timestamp

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

        # Auto-add symbols to REST poller based on signal frequency
        self._signal_frequency: collections.Counter = collections.Counter()
        self._original_rest_symbols = set(rest_symbols)
        self.MAX_REST_SYMBOLS = 120

        # Proactive scan cooldowns (symbol -> last alert timestamp)
        self._proactive_cooldowns: dict = {}
        self.PROACTIVE_SCAN_INTERVAL = 120   # Scan every 2 minutes
        self.PROACTIVE_COOLDOWN = 2700       # 45 min cooldown per coin
        self.PROACTIVE_MIN_SCORE = 65        # Minimum score to alert (quality only)
        self.PROACTIVE_MAX_PER_CYCLE = 3     # Max alerts per scan cycle
        self.PROACTIVE_MAX_PER_HOUR = 5      # Max proactive alerts per hour
        self._proactive_hourly: list = []    # Timestamps for hourly rate limit

        # Debouncing (FIX: Prevent task explosion)
        self.analysis_locks = {}  # Per-symbol locks
        self.last_analysis = {}   # Per-symbol last analysis time
        self._analysis_tasks: set = set()
        self._max_tracked_symbols = 500  # Limit analysis tracking to prevent memory leak
        
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
        self.logger.info("📊 Dashboard started at http://127.0.0.1:8081")
        
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
            trade_channel = f"futures_trades@all_{symbol}@10000"
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
    
    @staticmethod
    def _normalize_ws_event(event: dict) -> dict:
        """
        Normalize CoinGlass WebSocket field names to internal format.

        CoinGlass API sends:  volUsd, exName, baseAsset
        Our internal code uses: vol, exchange
        """
        if "volUsd" in event and "vol" not in event:
            event["vol"] = event["volUsd"]
        if "exName" in event and "exchange" not in event:
            event["exchange"] = event["exName"]
        return event

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
                # Normalize CoinGlass field names (volUsd -> vol, exName -> exchange)
                self._normalize_ws_event(liq_event)

                # Validate structure
                validation = self.data_validator.validate_liquidation(liq_event)
                if validation.is_valid:
                    raw_symbol = liq_event.get('symbol', 'UNKNOWN')
                    symbol = normalize_symbol(raw_symbol)

                    # Skip non-crypto and blacklisted symbols
                    if not is_tradeable_crypto(symbol):
                        continue

                    # Parse into typed model (safe — returns None on bad data)
                    typed_event = parse_liquidation(liq_event, symbol=symbol)

                    # Add to buffer with normalized symbol
                    self.buffer_manager.add_liquidation(
                        symbol=symbol,
                        event=typed_event or liq_event
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
                # Normalize CoinGlass field names (volUsd -> vol, exName -> exchange)
                self._normalize_ws_event(trade)

                # Validate structure
                validation = self.data_validator.validate_trade(trade)
                if validation.is_valid:
                    raw_symbol = trade.get('symbol', 'UNKNOWN')
                    symbol = normalize_symbol(raw_symbol)

                    # Parse into typed model (safe — returns None on bad data)
                    typed_event = parse_trade(trade, symbol=symbol)

                    # Add to buffer with normalized symbol
                    self.buffer_manager.add_trade(
                        symbol=symbol,
                        event=typed_event or trade
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

    @staticmethod
    def _build_rest_symbols(pairs_config: dict) -> list:
        """Build list of base symbols for CoinGlass REST API (BTCUSDT -> BTC)."""
        base_symbols = set()
        all_pairs = pairs_config.get('primary', []) + pairs_config.get('secondary', [])
        for pair in all_pairs:
            base_symbols.add(to_base_symbol(pair))
        return sorted(base_symbols)

    async def _on_oi_data(self, oi_snapshot):
        """Callback: store OI snapshot in buffer + database."""
        self.market_context_buffer.add_oi_snapshot(oi_snapshot)
        try:
            await self.db.save_oi_snapshot(
                symbol=oi_snapshot.symbol,
                current_oi_usd=oi_snapshot.current_oi_usd,
                previous_oi_usd=oi_snapshot.previous_oi_usd,
                oi_high_usd=oi_snapshot.oi_high_usd,
                oi_low_usd=oi_snapshot.oi_low_usd,
                oi_change_pct=oi_snapshot.oi_change_pct,
            )
        except Exception as e:
            self.logger.debug(f"DB OI save error: {e}")

    async def _on_funding_data(self, funding_snapshot):
        """Callback: store funding snapshot in buffer + database."""
        self.market_context_buffer.add_funding_snapshot(funding_snapshot)
        try:
            await self.db.save_funding_snapshot(
                symbol=funding_snapshot.symbol,
                current_rate=funding_snapshot.current_rate,
                previous_rate=funding_snapshot.previous_rate,
                rate_high=funding_snapshot.rate_high,
                rate_low=funding_snapshot.rate_low,
            )
        except Exception as e:
            self.logger.debug(f"DB funding save error: {e}")

    async def _on_spot_cvd_data(self, snapshot):
        """Callback: store spot CVD snapshot in buffer."""
        self.market_context_buffer.add_spot_cvd_snapshot(snapshot)
        self.logger.debug(
            f"SpotCVD {snapshot.symbol}: {snapshot.cvd_direction} "
            f"(slope={snapshot.cvd_slope:.4f}, latest={snapshot.cvd_latest:,.0f})"
        )

    async def _on_futures_cvd_data(self, snapshot):
        """Callback: store futures CVD snapshot in buffer."""
        self.market_context_buffer.add_futures_cvd_snapshot(snapshot)
        self.logger.debug(
            f"FuturesCVD {snapshot.symbol}: {snapshot.cvd_direction} "
            f"(slope={snapshot.cvd_slope:.4f}, latest={snapshot.cvd_latest:,.0f})"
        )

    async def _on_whale_data(self, alert):
        """Callback: store whale alert in buffer. Log large positions."""
        self.market_context_buffer.update_whale_positions([alert])

        # Track recent whale alerts for dashboard
        if not hasattr(self, '_recent_whale_alerts'):
            self._recent_whale_alerts = []
        action = "OPEN" if alert.position_action == 1 else "CLOSE"
        self._recent_whale_alerts.append({
            "symbol": alert.symbol,
            "direction": alert.direction,
            "action": action,
            "value_usd": alert.position_value_usd,
            "entry_price": alert.entry_price,
            "time": datetime.now().strftime('%H:%M:%S'),
        })
        # Keep last 20
        self._recent_whale_alerts = self._recent_whale_alerts[-20:]

        if alert.position_value_usd >= 1_000_000:
            # Write state on significant whale alert
            try:
                symbols = list(self.rest_poller.symbols)
                self._write_live_state(symbols)
            except Exception:
                pass

        if alert.position_value_usd >= 5_000_000:
            self.logger.info(
                f"🐋 Whale alert: {alert.symbol} {alert.direction} "
                f"${alert.position_value_usd/1_000_000:.1f}M on Hyperliquid"
            )

    async def _on_fast_poll_done(self):
        """Callback: write state immediately after fast poll completes."""
        try:
            symbols = list(self.rest_poller.symbols)
            self._write_live_state(symbols)
        except Exception as e:
            self.logger.error(f"Fast poll state write error: {e}")

    async def _movement_detector_task(self):
        """Run MovementDetector every 30s — stealth flow, flush, absorption alerts."""
        self.logger.info("🔍 MovementDetector started (30s interval)")
        await asyncio.sleep(60)  # Wait for initial data

        while not shutdown_event.is_set():
            try:
                alerts = self.movement_detector.scan(
                    self.market_context_buffer, self.buffer_manager
                )
                for alert in alerts:
                    msg = alert.get("message", "")
                    priority = alert.get("priority", 2)
                    if msg:
                        await self.alert_queue.add(msg, priority=priority)
                        self.logger.info(
                            f"🔍 Movement: {alert.get('type')} {alert.get('coin')} "
                            f"{alert.get('direction')}"
                        )
            except Exception as e:
                self.logger.error(f"MovementDetector error: {e}")

            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=30)
                break
            except asyncio.TimeoutError:
                pass

        self.logger.info("MovementDetector stopped")

    async def _on_orderbook_data(self, snapshot):
        """Callback: store orderbook delta in buffer."""
        self.market_context_buffer.add_orderbook_snapshot(snapshot)
        self.logger.debug(
            f"Orderbook {snapshot.symbol}: {snapshot.dominant_side} "
            f"(bids={snapshot.total_bid_vol:,.0f}, asks={snapshot.total_ask_vol:,.0f})"
        )

    async def _on_funding_per_exchange_data(self, snapshot):
        """Callback: store per-exchange funding rates in buffer."""
        self.market_context_buffer.update_funding_per_exchange(snapshot)
        self.logger.debug(
            f"FundingPerEx {snapshot.symbol}: {len(snapshot.rates)} exchanges"
        )

    async def _on_price_data(self, snapshot):
        """Callback: store price snapshot in buffer."""
        self.market_context_buffer.add_price_snapshot(snapshot)
        self.logger.debug(
            f"Price {snapshot.symbol}: ${snapshot.price:,.2f} "
            f"({snapshot.change_24h_pct:+.1f}% 24h)"
        )

    async def _on_long_short_data(self, snapshot):
        """Callback: store long/short ratio snapshot in buffer."""
        self.market_context_buffer.add_long_short_snapshot(snapshot)

    async def _on_signal_outcome(self, tracked, pnl_pct: float):
        """Callback: save signal outcome to database + feature table."""
        try:
            if tracked._db_id:
                await self.db.update_signal_outcome(
                    signal_id=tracked._db_id,
                    outcome=tracked.outcome,
                    exit_price=tracked.exit_price or 0,
                    pnl_pct=pnl_pct
                )
                # Also update feature row with rich outcome metrics
                result = tracked.outcome_result
                if result:
                    await self.feature_logger.update_outcome(
                        signal_id=tracked._db_id,
                        outcome=tracked.outcome,
                        pnl_pct=result.pnl_pct,
                        mfe_pct=result.mfe_pct,
                        mae_pct=result.mae_pct,
                        excursion_ratio=result.excursion_ratio,
                        time_to_resolution=result.time_to_resolution,
                    )

                # Track ML-scored outcome for guardrail monitoring
                if tracked.outcome in ("WIN", "LOSS", "PARTIAL"):
                    self.ml_guardrails.record_ml_outcome(
                        tracked.outcome in ("WIN", "PARTIAL")
                    )
        except Exception as e:
            self.logger.debug(f"DB outcome save error: {e}")

    async def _init_database(self):
        """Connect to database and restore saved state."""
        try:
            await self.db.connect()

            # Share DB with feature logger + ML components
            self.feature_logger.set_db(self.db)
            self.dataset_builder.set_db(self.db)
            self.model_trainer.set_db(self.db)

            # Build calibration table from existing history
            await self.calibration.build_from_db(self.db)

            # Load latest ML model if available
            self.ml_engine.load_model()

            # Restore confidence scorer state (signal-type level)
            saved_confidence = await self.db.load_confidence_state()
            if saved_confidence:
                for signal_type, state in saved_confidence.items():
                    self.confidence_scorer.win_rates[signal_type] = state["win_rate"]
                    self.confidence_scorer.signal_history[signal_type] = state["history"]
                self.logger.info(
                    f"Restored confidence state: "
                    f"{len(saved_confidence)} signal types loaded"
                )

            # Restore setup-level learning state
            saved_setups = await self.db.load_setup_states()
            if saved_setups:
                for setup_key, state in saved_setups.items():
                    self.confidence_scorer._setup_history[setup_key] = state["history"]
                    self.confidence_scorer._setup_win_rates[setup_key] = state["win_rate"]
                self.logger.info(
                    f"Restored setup learning: {len(saved_setups)} setups loaded"
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

            # Share lifecycle + calibration with dashboard API for endpoints
            dashboard_api.set_lifecycle(self.lifecycle)
            dashboard_api.set_calibration(self.calibration)

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

            # Save setup-level learning state
            if self.confidence_scorer._setup_history:
                await self.db.save_all_setup_states(
                    dict(self.confidence_scorer._setup_history),
                    dict(self.confidence_scorer._setup_win_rates),
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

    def _rest_signal_dedup(self, symbol: str, signal_type: str, direction: str,
                           cooldown_seconds: int = 300) -> bool:
        """Check if this signal was already sent recently. Returns True if OK to send."""
        key = f"{symbol}_{signal_type}_{direction}"
        now = time.time()
        last = self._rest_signal_cooldown.get(key, 0)
        if now - last < cooldown_seconds:
            return False  # duplicate within cooldown
        self._rest_signal_cooldown[key] = now
        # Cleanup old entries
        if len(self._rest_signal_cooldown) > 200:
            cutoff = now - 600
            self._rest_signal_cooldown = {
                k: v for k, v in self._rest_signal_cooldown.items() if v > cutoff
            }
        return True

    async def _scan_rest_signals(self):
        """Scan all tracked symbols for REST-based signals (CVD flip, OI spike, whale)."""
        try:
            buffer_stats = self.market_context_buffer.get_stats()
            if buffer_stats.get("oi_symbols_tracked", 0) == 0:
                return

            for base_symbol in self.rest_poller.symbols[:50]:
                symbol = f"{base_symbol}USDT" if not base_symbol.endswith("USDT") else base_symbol

                signal = self.rest_signal_detector.evaluate(base_symbol)
                if not signal:
                    continue

                # Dedup check — skip if same signal sent within 5 minutes
                if not self._rest_signal_dedup(symbol, signal.signal_type, signal.direction):
                    continue

                # Push to dashboard + lifecycle
                signal_dict = {
                    "symbol": symbol,
                    "type": signal.signal_type,
                    "direction": signal.direction,
                    "confidence": int(signal.confidence),
                    "description": signal.description,
                    "market_context": "",
                    "leading_label": " + ".join(signal.sources),
                }
                dashboard_api.add_signal(signal_dict)
                self.lifecycle.ingest(signal_dict)
                self.stats["signals_generated"] += 1

                # Persist REST signal to database for ML training
                try:
                    price_snap = self.market_context_buffer.get_latest_price(base_symbol)
                    rest_price = price_snap.price if price_snap else 0
                    rest_risk = rest_price * 0.01 if rest_price > 0 else 0
                    is_long = signal.direction == "LONG"
                    rest_entry = rest_price
                    rest_sl = (rest_entry - rest_risk if is_long else rest_entry + rest_risk) if rest_risk > 0 else 0
                    rest_tp = (rest_entry + rest_risk * 2 if is_long else rest_entry - rest_risk * 2) if rest_risk > 0 else 0
                    await self.db.save_signal(
                        symbol=symbol,
                        signal_type=signal.signal_type,
                        direction=signal.direction,
                        confidence=signal.confidence,
                        entry_price=rest_entry,
                        stop_loss=rest_sl,
                        target_price=rest_tp,
                    )
                except Exception as e:
                    self.logger.debug(f"REST signal DB save error: {e}")

                self.logger.info(
                    f"REST SIGNAL → {symbol} {signal.signal_type} {signal.direction} "
                    f"conf={signal.confidence:.0f}% sources={signal.sources}"
                )

                # Telegram gate: confidence + active coin + CVD veto + funding check
                send_telegram = signal.confidence >= 72 and self._is_coin_active(symbol)

                if send_telegram:
                    spot_cvd = self.market_context_buffer.get_latest_spot_cvd(base_symbol)
                    fut_cvd = self.market_context_buffer.get_latest_futures_cvd(base_symbol)

                    # CVD VETO: both spot AND futures cumulative must not oppose signal direction
                    spot_cum = getattr(spot_cvd, 'cvd_cumulative', spot_cvd.cvd_latest) if spot_cvd else 0
                    fut_cum = getattr(fut_cvd, 'cvd_cumulative', fut_cvd.cvd_latest) if fut_cvd else 0
                    if spot_cvd and signal.direction == "LONG" and spot_cum < 0:
                        if not fut_cvd or fut_cum < 0:
                            send_telegram = False
                            self.logger.info(f"CVD VETO {symbol}: LONG blocked (SpotCVD {spot_cum:,.0f})")
                    elif spot_cvd and signal.direction == "SHORT" and spot_cum > 0:
                        if not fut_cvd or fut_cum > 0:
                            send_telegram = False
                            self.logger.info(f"CVD VETO {symbol}: SHORT blocked (SpotCVD {spot_cum:,.0f})")

                    # Funding rate sanity: don't long when funding extremely positive (crowded longs)
                    if send_telegram:
                        fpe = self.market_context_buffer.get_funding_per_exchange(base_symbol)
                        if fpe and fpe.rates:
                            sane = [v for v in fpe.rates.values() if abs(v) < 0.01]
                            avg_fr = sum(sane) / len(sane) if sane else 0
                            if signal.direction == "LONG" and avg_fr > 0.0005:
                                signal.confidence = max(55, signal.confidence - 8)
                                self.logger.info(f"FR PENALTY {symbol}: LONG -8 (avg FR {avg_fr*100:+.4f}%)")
                            elif signal.direction == "SHORT" and avg_fr < -0.0005:
                                signal.confidence = max(55, signal.confidence - 8)
                                self.logger.info(f"FR PENALTY {symbol}: SHORT -8 (avg FR {avg_fr*100:+.4f}%)")
                        # Re-check confidence after penalty
                        if signal.confidence < 72:
                            send_telegram = False

                if send_telegram:
                    try:
                        from src.utils.symbol_normalizer import display_symbol
                        from datetime import timezone as _tz, timedelta as _td
                        _wib = _tz(_td(hours=7))
                        _now = datetime.now(_wib).strftime("%H:%M:%S WIB")

                        # Build rich alert with context
                        price_snap = self.market_context_buffer.get_latest_price(base_symbol)
                        oi_snap = self.market_context_buffer.get_latest_oi(base_symbol)
                        spot_cvd_snap = self.market_context_buffer.get_latest_spot_cvd(base_symbol)
                        fut_cvd_snap = self.market_context_buffer.get_latest_futures_cvd(base_symbol)
                        ob_snap = self.market_context_buffer.get_latest_orderbook(base_symbol)
                        fpe = self.market_context_buffer.get_funding_per_exchange(base_symbol)

                        price_str = f"${price_snap.price:,.4f}" if price_snap else "N/A"
                        chg_str = f"{price_snap.change_24h_pct:+.1f}%" if price_snap else ""

                        lines = [
                            f"{display_symbol(symbol)} | {price_str} | {_now}",
                            f"Trigger: REST SCAN | {signal.direction}",
                            "",
                        ]

                        # Bullets from signal sources
                        for src in signal.sources:
                            meta = signal.metadata.get("sources", {}).get(src, signal.metadata)
                            if src == "SpotCVD":
                                d = meta.get("spot_cvd_direction", "")
                                lines.append(f"✦ SpotCVD {d} {meta.get('spot_cvd_latest', 0):+,.0f}")
                            elif src == "OpenInterest":
                                lines.append(f"✦ OI {meta.get('interpretation', '')} ({meta.get('oi_change_pct', 0):+.1f}%)")
                            elif src == "WhaleAlert":
                                lines.append(f"✦ Whale net {signal.direction} ${meta.get('total_flow_usd', 0):,.0f} ({meta.get('dominance', 0):.0%})")
                            else:
                                lines.append(f"✦ {src}: {signal.description[:60]}")

                        # Order flow section
                        lines.append("")
                        lines.append("ORDER FLOW")
                        if spot_cvd_snap:
                            spot_cum = getattr(spot_cvd_snap, 'cvd_cumulative', spot_cvd_snap.cvd_latest)
                            chg_arrow = "\u25b2" if spot_cvd_snap.cvd_change > 0 else "\u25bc" if spot_cvd_snap.cvd_change < 0 else "\u2192"
                            lines.append(f"SpotCVD : {spot_cum:+,.0f} | \u039460m: {chg_arrow}{spot_cvd_snap.cvd_change:+,.0f} | {spot_cvd_snap.cvd_direction}")
                        if fut_cvd_snap:
                            fut_cum = getattr(fut_cvd_snap, 'cvd_cumulative', fut_cvd_snap.cvd_latest)
                            chg_arrow = "\u25b2" if fut_cvd_snap.cvd_change > 0 else "\u25bc" if fut_cvd_snap.cvd_change < 0 else "\u2192"
                            lines.append(f"FutCVD  : {fut_cum:+,.0f} | \u039460m: {chg_arrow}{fut_cvd_snap.cvd_change:+,.0f} | {fut_cvd_snap.cvd_direction}")
                        if oi_snap:
                            lines.append(f"OI      : ${oi_snap.current_oi_usd:,.0f} {oi_snap.oi_change_pct:+.1f}% 1h")
                        if ob_snap:
                            total = ob_snap.total_bid_vol + ob_snap.total_ask_vol
                            bid_pct = ob_snap.total_bid_vol / total * 100 if total else 50
                            lines.append(f"OBDelta : Bid ${ob_snap.total_bid_vol:,.0f} ({bid_pct:.0f}%) / Ask ${ob_snap.total_ask_vol:,.0f}")

                        # Funding rate (with sanity check)
                        if fpe and fpe.rates:
                            sane = {k: v for k, v in fpe.rates.items() if abs(v) < 0.01}
                            if sane:
                                lines.append("")
                                lines.append("FUNDING RATE")
                                for ex, rate in sorted(sane.items(), key=lambda x: abs(x[1]), reverse=True)[:5]:
                                    lines.append(f"{ex:9s}: {rate * 100:+.4f}%")

                        # Price action
                        if price_snap:
                            lines.append("")
                            lines.append("PRICE ACTION")
                            lines.append(f"Harga  : {price_str} ({chg_str} 24h)")
                            lines.append(f"Volume : ${price_snap.volume_24h:,.0f}")

                        # Confidence bar
                        conf = int(signal.confidence)
                        filled = conf // 10
                        bar = "█" * filled + "░" * (10 - filled)
                        lines.append("")
                        lines.append(f"{signal.direction}  — {'HIGH' if conf >= 75 else 'MEDIUM' if conf >= 60 else 'LOW'} CONFIDENCE")
                        lines.append(f"{bar} {conf}%")
                        lines.append("DYOR — verifikasi sebelum entry")

                        alert_msg = "\n".join(lines)
                        await self.alert_queue.add(alert_msg, priority=2)
                    except Exception as e:
                        self.logger.debug(f"REST alert format error: {e}")

        except Exception as e:
            self.logger.error(f"REST signal scan error: {e}")

    def _update_order_flow_from_rest(self):
        """Update dashboard order flow using REST taker data (fixes buy_ratio=0)."""
        try:
            for base_symbol in self.rest_poller.symbols[:50]:
                symbol = f"{base_symbol}USDT" if not base_symbol.endswith("USDT") else base_symbol

                # Get taker data from CVD snapshot (has taker_buy_vol / taker_sell_vol)
                spot_cvd = self.market_context_buffer.get_latest_spot_cvd(base_symbol)
                if not spot_cvd:
                    continue

                buy_vol = spot_cvd.taker_buy_vol
                sell_vol = spot_cvd.taker_sell_vol
                total = buy_vol + sell_vol
                if total <= 0:
                    continue

                buy_ratio = buy_vol / total
                flow_data = {
                    "buy_ratio": round(buy_ratio * 100),
                    "sell_ratio": round((1 - buy_ratio) * 100),
                    "buy_volume": buy_vol,
                    "sell_volume": sell_vol,
                    "large_buys": 0,
                    "large_sells": 0,
                }
                dashboard_api.update_order_flow(symbol, flow_data)

        except Exception as e:
            self.logger.debug(f"Order flow REST update error: {e}")

    async def analyze_and_alert(self, symbol: str):
        """
        Run analysis and send alerts for symbol.

        Analysis runs for ALL coins (data is always collected).
        Telegram alerts only sent for coins that are ACTIVE on dashboard.
        Dashboard signals are always shown regardless.
        """
        try:
            # Debounce: Don't analyze same symbol within 5 seconds
            now = asyncio.get_running_loop().time()
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

                # Log analyzer results for debugging (only when something detected)
                if stop_hunt_signal:
                    self.logger.info(
                        f"🔎 {symbol} StopHunt: {stop_hunt_signal.direction} "
                        f"vol=${stop_hunt_signal.total_volume:,.0f} conf={stop_hunt_signal.confidence:.0f}%"
                    )
                if order_flow_signal:
                    self.logger.info(
                        f"🔎 {symbol} OrderFlow: {order_flow_signal.signal_type} "
                        f"buy_ratio={order_flow_signal.buy_ratio:.0%} conf={order_flow_signal.confidence:.0f}%"
                    )

                # Generate unified signal
                trading_signal = await self.signal_generator.generate(
                    symbol=symbol,
                    stop_hunt_signal=stop_hunt_signal,
                    order_flow_signal=order_flow_signal,
                    event_signals=event_signals
                )

                if not trading_signal:
                    return

                # Capture base confidence before adjustments
                base_confidence = trading_signal.confidence

                # Classify setup for granular learning
                setup_key = classify_setup(
                    signal_type=trading_signal.signal_type,
                    direction=trading_signal.direction,
                    symbol=symbol,
                    metadata=trading_signal.metadata,
                    monitoring_config=self.config.get('monitoring', {}),
                )
                trading_signal.metadata['setup_key'] = setup_key

                # Adjust confidence (setup-aware)
                adjusted_confidence = self.confidence_scorer.adjust_confidence(
                    trading_signal.confidence,
                    trading_signal.signal_type,
                    trading_signal.metadata,
                    symbol=symbol,
                    setup_key=setup_key,
                )
                trading_signal.confidence = adjusted_confidence

                # Validate signal
                is_valid, reason = self.signal_validator.validate(trading_signal)
                if not is_valid:
                    self.logger.debug(f"Signal rejected: {reason}")
                    return

                # Market context filter (OI + Funding Rate + CVD + Whale)
                filter_result = self.market_context_filter.evaluate(trading_signal)

                # HARD BLOCK: if filter says not passed, do not send alert
                # Exception: STOP_HUNT signals use crowding as their trigger —
                # the filter would block for the same condition the detector requires.
                # Downgrade to dashboard-only instead of hard block.
                is_stop_hunt = trading_signal.signal_type == "STOP_HUNT"
                if not filter_result.passed and not is_stop_hunt:
                    self.logger.info(
                        f"Signal BLOCKED by market context: {symbol} "
                        f"{trading_signal.signal_type} {trading_signal.direction} — "
                        f"{filter_result.assessment}: {filter_result.reason}"
                    )
                    return
                if not filter_result.passed and is_stop_hunt:
                    self.logger.info(
                        f"STOP_HUNT bypass: {symbol} {trading_signal.direction} — "
                        f"filter={filter_result.assessment} (crowding expected for pre-hunt)"
                    )

                # Apply confidence adjustment from market context
                if filter_result.confidence_adjustment != 0:
                    trading_signal.confidence = max(50.0, min(
                        trading_signal.confidence + filter_result.confidence_adjustment, 99.0
                    ))

                # Re-check confidence after adjustment (may drop below threshold)
                if trading_signal.confidence < self.signal_validator.min_confidence:
                    self.logger.debug(
                        f"Signal dropped below threshold after context adjustment: "
                        f"{trading_signal.confidence:.0f}%"
                    )
                    return

                self.stats['signals_generated'] += 1

                # Auto-subscribe to trade feed for this coin (enables order flow + event detection next time)
                if symbol not in self._trade_subscribed:
                    trade_channel = f"futures_trades@all_{symbol}@10000"
                    subscribe_msg = {"method": "subscribe", "channels": [trade_channel]}
                    if await self.websocket_client.send_message(subscribe_msg):
                        self._trade_subscribed.add(symbol)
                        self.logger.info(f"Auto-subscribed trades: {symbol}")

                # Auto-add symbol to REST poller for market context
                base_symbol = to_base_symbol(symbol)
                self._signal_frequency[base_symbol] += 1
                if base_symbol not in self.rest_poller.symbols:
                    if len(self.rest_poller.symbols) < self.MAX_REST_SYMBOLS:
                        self.rest_poller.update_symbols([base_symbol])
                        self.logger.info(
                            f"Auto-added {base_symbol} to REST poller "
                            f"({len(self.rest_poller.symbols)}/{self.MAX_REST_SYMBOLS})"
                        )
                    else:
                        # At cap: replace least-frequent non-original symbol
                        removable = [
                            s for s in self.rest_poller.symbols
                            if s not in self._original_rest_symbols
                        ]
                        if removable:
                            least = min(removable, key=lambda s: self._signal_frequency.get(s, 0))
                            if self._signal_frequency[base_symbol] > self._signal_frequency.get(least, 0):
                                self.rest_poller.symbols.remove(least)
                                self.rest_poller.update_symbols([base_symbol])
                                self.logger.info(
                                    f"Replaced {least} with {base_symbol} in REST poller"
                                )

                # Inject market context into metadata for message formatting
                if filter_result.market_context:
                    ctx = filter_result.market_context
                    trading_signal.metadata['market_context'] = {
                        'funding_rate': ctx.current_funding_rate,
                        'funding_alignment': ctx.funding_alignment,
                        'oi_usd': ctx.current_oi_usd,
                        'oi_change_1h_pct': ctx.oi_change_1h_pct,
                        'oi_alignment': ctx.oi_alignment,
                        'combined_assessment': filter_result.assessment,
                        # CVD data
                        'spot_cvd_direction': ctx.spot_cvd_direction,
                        'spot_cvd_slope': ctx.spot_cvd_slope,
                        'spot_cvd_latest': ctx.spot_cvd_latest,
                        'spot_cvd_change': ctx.spot_cvd_change,
                        'futures_cvd_direction': ctx.futures_cvd_direction,
                        'futures_cvd_slope': ctx.futures_cvd_slope,
                        'futures_cvd_latest': ctx.futures_cvd_latest,
                        'futures_cvd_change': ctx.futures_cvd_change,
                        'cvd_alignment': ctx.cvd_alignment,
                        # Whale data
                        'whale_conflicting': ctx.whale_conflicting,
                        'whale_largest_value_usd': ctx.whale_largest_value_usd,
                        'whale_largest_direction': ctx.whale_largest_direction,
                        'whale_alignment': ctx.whale_alignment,
                        # Orderbook data
                        'orderbook_bid_vol': ctx.orderbook_bid_vol,
                        'orderbook_ask_vol': ctx.orderbook_ask_vol,
                        'orderbook_dominant': ctx.orderbook_dominant,
                        # Per-exchange funding rates
                        'funding_per_exchange': ctx.funding_per_exchange,
                        # Price data
                        'current_price': ctx.current_price,
                        'price_change_24h_pct': ctx.price_change_24h_pct,
                        'volume_24h': ctx.volume_24h,
                    }

                # Ensure market_context dict exists even without REST data
                trading_signal.metadata.setdefault('market_context', {})

                # --- Volume Filter: skip low-volume coins per tier ---
                vol_24h = trading_signal.metadata.get('market_context', {}).get('volume_24h', 0)
                if vol_24h > 0:
                    tier = self.signal_validator._get_symbol_tier(symbol)
                    min_volumes = {
                        1: monitoring_config.get('tier1_min_volume_24h', 50_000_000),
                        2: monitoring_config.get('tier2_min_volume_24h', 20_000_000),
                        3: monitoring_config.get('tier3_min_volume_24h', 5_000_000),
                        4: monitoring_config.get('tier4_min_volume_24h', 1_000_000),
                    }
                    min_vol = min_volumes.get(tier, 1_000_000)
                    if vol_24h < min_vol:
                        self.logger.info(
                            f"⏭️ Low volume: skip | {symbol} tier {tier} "
                            f"vol=${vol_24h/1e6:.1f}M < min=${min_vol/1e6:.0f}M"
                        )
                        return

                # Fallback: derive price from WebSocket trade data if REST price missing
                mc = trading_signal.metadata['market_context']
                if mc.get('current_price', 0) == 0:
                    trades = self.buffer_manager.get_trades(symbol, time_window=60)
                    if trades:
                        ws_price = float(trades[-1].get('price', 0))
                        if ws_price > 0:
                            mc['current_price'] = ws_price
                            mc['_price_source'] = 'websocket'
                    # Last resort: price from liquidation data
                    if mc.get('current_price', 0) == 0:
                        liqs_recent = self.buffer_manager.get_liquidations(symbol, time_window=60)
                        if liqs_recent:
                            liq_price = float(liqs_recent[-1].get('price', 0))
                            if liq_price > 0:
                                mc['current_price'] = liq_price
                                mc['_price_source'] = 'liquidation'

                # Compute 24h liquidation volume from buffer
                liqs_24h = self.buffer_manager.get_liquidations(symbol, time_window=86400)
                liq_vol_24h = sum(float(l.get("vol", 0)) for l in liqs_24h) if liqs_24h else 0
                uptime_hours = (datetime.now() - self.start_time).total_seconds() / 3600
                mc.update({
                    'liquidation_24h_volume': liq_vol_24h,
                    'uptime_hours': uptime_hours,
                })

                # Inject track record into metadata for message_formatter
                track_record = self.signal_tracker.get_track_record(trading_signal.signal_type)
                trading_signal.metadata.setdefault('stop_hunt', {})['track_record'] = track_record

                # Inject baseline context into metadata
                baseline = self.buffer_manager.get_baseline(symbol)
                trading_signal.metadata['baseline'] = baseline

                # --- Leading Indicator Scoring (Upgrade 1-4) ---
                leading_score = self.leading_scorer.score(
                    direction=trading_signal.direction,
                    market_context_buffer=self.market_context_buffer,
                    base_symbol=base_symbol,
                    signal_metadata=trading_signal.metadata,
                )

                # Override confidence if leading score is higher
                if leading_score.total > trading_signal.confidence:
                    self.logger.info(
                        f"Leading score override: {trading_signal.confidence:.0f}% → "
                        f"{leading_score.total:.0f}% ({leading_score.label_text})"
                    )
                    trading_signal.confidence = leading_score.total

                # Store leading score in metadata for message formatter
                trading_signal.metadata['leading_score'] = {
                    'total': leading_score.total,
                    'label_emoji': leading_score.label_emoji,
                    'label_text': leading_score.label_text,
                    'indicators': [
                        {'name': i.name, 'points': i.points, 'detail': i.detail}
                        for i in leading_score.indicators
                    ],
                    'notes': leading_score.notes,
                    'leading_subtotal': leading_score.leading_subtotal,
                    'lagging_subtotal': leading_score.lagging_subtotal,
                }

                # Bias override: strong leading indicators can override UNFAVORABLE filter
                override, override_note = LeadingIndicatorScorer.should_override_bias(
                    leading_score.leading_subtotal,
                    filter_result.assessment,
                    trading_signal.direction,
                )
                if override:
                    filter_passed = True
                    leading_score.notes.append(override_note)
                    trading_signal.metadata['leading_score']['notes'] = leading_score.notes
                    self.logger.info(f"Bias override: {override_note}")
                else:
                    filter_passed = filter_result.passed

                # Always send to dashboard (user can see all signals in web UI)
                signal_dict = {
                    'symbol': symbol,
                    'type': trading_signal.signal_type,
                    'direction': trading_signal.direction,
                    'confidence': int(trading_signal.confidence),
                    'description': f"{trading_signal.signal_type} {trading_signal.direction}",
                    'market_context': filter_result.assessment,
                    'leading_label': leading_score.label_text,
                }
                dashboard_api.add_signal(signal_dict)

                # Register with lifecycle manager (expiry, primary selection)
                lifecycle_data = self.lifecycle.ingest(signal_dict)

                # Check dashboard toggle AND market context filter
                if self._is_coin_active(symbol) and filter_passed:
                    formatted_message = self.message_formatter.format_signal(trading_signal)
                    await self.alert_queue.add(
                        formatted_message,
                        priority=trading_signal.priority
                    )
                    self.logger.info(
                        f"🎯 Signal queued: {symbol} {trading_signal.signal_type} "
                        f"[context: {filter_result.assessment}]"
                    )
                elif not filter_result.passed:
                    self.logger.info(
                        f"📡 Signal filtered by market context: {symbol} "
                        f"{trading_signal.signal_type} [{filter_result.assessment}]"
                    )
                else:
                    self.logger.info(f"🔇 Signal skipped (coin inactive): {symbol} {trading_signal.signal_type}")

                # Track signal for outcome measurement (always, regardless of toggle)
                price_zone = trading_signal.metadata.get('stop_hunt', {}).get('price_zone', (0, 0))
                current_price = price_zone[0] if price_zone[0] > 0 else 0
                has_zone = price_zone[1] > 0

                if has_zone:
                    # Legacy post-cascade: use price zone for entry/SL/TP
                    zone_spread = abs(price_zone[1] - price_zone[0])
                    is_long = trading_signal.direction == "LONG"
                    entry = price_zone[1] if is_long else price_zone[0]
                    sl = price_zone[0] - (zone_spread * 0.3) if is_long else price_zone[1] + (zone_spread * 0.3)
                    risk = abs(entry - sl)
                    tp = entry + (risk * 2) if is_long else entry - (risk * 2)
                elif current_price > 0:
                    # Pre-hunt signals: use current price with 1% default risk
                    is_long = trading_signal.direction == "LONG"
                    entry = current_price
                    risk = current_price * 0.01
                    sl = entry - risk if is_long else entry + risk
                    tp = entry + (risk * 2) if is_long else entry - (risk * 2)
                else:
                    entry, sl, tp = 0, 0, 0

                if entry > 0:
                    tracked = self.signal_tracker.track_signal(
                        trading_signal, entry, sl, tp,
                        setup_key=trading_signal.metadata.get('setup_key', '')
                    )

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
                        db_id = 0

                    # Log full feature snapshot for ML training
                    try:
                        features = self.feature_logger.extract_features(
                            signal=trading_signal,
                            symbol=symbol,
                            setup_key=trading_signal.metadata.get('setup_key', ''),
                            base_confidence=base_confidence,
                            adjusted_confidence=adjusted_confidence,
                            final_confidence=trading_signal.confidence,
                            filter_assessment=filter_result.assessment if filter_result else '',
                            leading_score=leading_score.total if leading_score else 0,
                            signal_id=db_id,
                        )
                        await self.feature_logger.log_signal(features)

                        # ML inference (shadow/blended/advisory)
                        if self.ml_engine.is_active:
                            ml_result = self.ml_engine.predict(features)
                            if ml_result:
                                trading_signal.metadata['ml_score'] = ml_result
                                self.logger.info(
                                    f"ML [{self.ml_engine.mode}] {symbol}: "
                                    f"prob={ml_result['ml_probability']:.2f} "
                                    f"conf={ml_result['ml_confidence']:.0f}% "
                                    f"rule={trading_signal.confidence:.0f}%"
                                )
                                # In blended mode: guardrail check → blend → clamp
                                if self.ml_engine.mode == "blended":
                                    model_meta = ModelTrainer.get_latest_meta()
                                    allowed, gate_reason = self.ml_guardrails.should_allow_blended(model_meta)
                                    if allowed:
                                        rule_conf = trading_signal.confidence
                                        raw_blended = self.ml_engine.blend_score(rule_conf, ml_result)
                                        trading_signal.confidence = self.ml_guardrails.clamp_adjustment(
                                            rule_conf, raw_blended
                                        )
                                    else:
                                        self.logger.info(f"ML blended blocked: {gate_reason}")
                    except Exception:
                        pass  # Feature logging + ML scoring is non-critical

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
                            # Mark task_done BEFORE retry to keep queue accounting correct
                            await self.alert_queue.mark_processed(success=False)
                            retried = await self.alert_queue.retry(queued_alert)
                            if not retried:
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
        last_log_time = 0
        while not shutdown_event.is_set():
            await asyncio.sleep(30)  # Every 30 seconds for dashboard

            # Update uptime
            uptime = (datetime.now() - self.start_time).total_seconds()
            self.stats['uptime_seconds'] = int(uptime)

            # Tick signal lifecycle (expire, weaken, cleanup)
            self.lifecycle.tick()

            # REST signal scan — detect CVD flips, OI spikes, whale activity
            await self._scan_rest_signals()

            # Update order flow from REST taker data (fix buy_ratio=0)
            self._update_order_flow_from_rest()

            # Collect analyzer stats for dashboard visibility
            self.stats['analyzers'] = {
                'stop_hunt': self.stop_hunt_detector.get_stats(),
                'order_flow': self.order_flow_analyzer.get_stats(),
                'events': self.event_detector.get_stats(),
                'validator': self.signal_validator.get_stats(),
                'confidence': self.confidence_scorer.get_overall_stats(),
                'tracker': self.signal_tracker.get_stats(),
                'market_context_buffer': self.market_context_buffer.get_stats(),
                'market_context_filter': self.market_context_filter.get_stats(),
                'leading_scorer': self.leading_scorer.get_stats(),
                'rest_poller': self.rest_poller.get_stats(),
                'calibration': self.calibration.get_stats(),
                'feature_logger': self.feature_logger.get_stats(),
                'lifecycle': self.lifecycle.get_stats(),
                'rest_signal_detector': self.rest_signal_detector.get_stats(),
                'ml_inference': self.ml_engine.get_stats(),
                'ml_guardrails': self.ml_guardrails.get_stats(),
            }

            # Update dashboard
            dashboard_api.update_stats(self.stats)

            # Update Prometheus module metrics
            try:
                from src.utils.metrics import update_from_module_stats
                update_from_module_stats(self.stats.get('analyzers', {}))
            except Exception:
                pass

            # Log every 5 minutes (use elapsed time since last log)
            if uptime - last_log_time >= 300:
                self.logger.info("📊 Statistics Report:")
                self.logger.info(f"   Messages: {self.stats['messages_received']} received, {self.stats['messages_processed']} processed")
                self.logger.info(f"   Liquidations: {self.stats['liquidations_processed']}, Trades: {self.stats['trades_processed']}")
                self.logger.info(f"   Signals: {self.stats['signals_generated']} generated")
                self.logger.info(f"   Alerts: {self.stats['alerts_sent']} sent")
                self.logger.info(f"   Errors: {self.stats['errors']}")
                self.logger.info(f"   Coins tracked: {len(self.buffer_manager.get_tracked_symbols())} (discovered: {len(self.discovered_symbols)})")
                last_log_time = uptime
    
    async def signal_tracker_task(self):
        """Background task: check signal outcomes every 60 seconds"""
        self.logger.info("📊 Signal tracker started")
        while not shutdown_event.is_set():
            await asyncio.sleep(60)
            try:
                await self.signal_tracker.check_outcomes()
            except Exception as e:
                self.logger.error(f"Signal tracker error: {e}")

    async def proactive_scan_task(self):
        """
        Proactive scan: analyze ALL polled coins every 2 minutes using leading indicators.

        This is the PRIMARY signal source — does NOT depend on liquidation cascade.
        Scans CVD flip, OI spike, funding, orderbook for each coin in REST poller.
        """
        self.logger.info("🔍 Proactive scanner started")
        # Wait for first REST poll to complete
        await asyncio.sleep(30)

        while not shutdown_event.is_set():
            try:
                now = datetime.now()
                symbols = list(self.rest_poller.symbols)
                alerts_sent = 0

                # Hourly rate limit: clean old entries
                cutoff = now.timestamp() - 3600
                self._proactive_hourly = [t for t in self._proactive_hourly if t > cutoff]

                if len(self._proactive_hourly) >= self.PROACTIVE_MAX_PER_HOUR:
                    self.logger.debug(
                        f"Proactive hourly limit reached ({self.PROACTIVE_MAX_PER_HOUR}/hr)"
                    )
                else:
                    for base_symbol in symbols:
                        # Per-cycle limit
                        if alerts_sent >= self.PROACTIVE_MAX_PER_CYCLE:
                            break
                        # Hourly limit
                        if len(self._proactive_hourly) >= self.PROACTIVE_MAX_PER_HOUR:
                            break

                        # Check cooldown
                        last_alert = self._proactive_cooldowns.get(base_symbol, 0)
                        if (now.timestamp() - last_alert) < self.PROACTIVE_COOLDOWN:
                            continue

                        # Score using leading indicators
                        # Try LONG first, then SHORT, pick the higher score
                        score_long = self.leading_scorer.score(
                            direction="LONG",
                            market_context_buffer=self.market_context_buffer,
                            base_symbol=base_symbol,
                            signal_metadata={
                                'market_context': self._build_context_dict(base_symbol),
                            },
                        )
                        score_short = self.leading_scorer.score(
                            direction="SHORT",
                            market_context_buffer=self.market_context_buffer,
                            base_symbol=base_symbol,
                            signal_metadata={
                                'market_context': self._build_context_dict(base_symbol),
                            },
                        )

                        # Pick best direction
                        if score_long.total >= score_short.total:
                            best = score_long
                            direction = "LONG"
                        else:
                            best = score_short
                            direction = "SHORT"

                        if best.total < self.PROACTIVE_MIN_SCORE:
                            continue

                        # Build and send alert
                        ctx = self._build_context_dict(base_symbol)
                        price = ctx.get('current_price', 0)

                        msg = self._format_proactive_alert(
                            base_symbol, direction, best, price, ctx
                        )

                        if msg and self.telegram_bot:
                            await self.alert_queue.add(msg, priority=2)
                            self._proactive_cooldowns[base_symbol] = now.timestamp()
                            self._proactive_hourly.append(now.timestamp())
                            alerts_sent += 1
                            self.logger.info(
                                f"🔍 Proactive signal: {base_symbol} {direction} "
                                f"{best.total:.0f}% ({best.label_text})"
                            )

                if alerts_sent > 0:
                    self.logger.info(f"🔍 Proactive scan: {alerts_sent} alerts from {len(symbols)} coins")

                # Write live state snapshot for terminal analysis
                self._write_live_state(symbols)

            except Exception as e:
                self.logger.error(f"Proactive scan error: {e}")

            # Wait for next scan cycle
            try:
                await asyncio.wait_for(
                    shutdown_event.wait(), timeout=self.PROACTIVE_SCAN_INTERVAL
                )
                break
            except asyncio.TimeoutError:
                pass

    def _write_live_state(self, symbols: list):
        """Write JSON snapshot of all coin states for terminal analysis."""
        try:
            from datetime import timezone, timedelta
            wib = timezone(timedelta(hours=7))
            now = datetime.now(wib)
            now_ts = int(now.timestamp())

            # Read existing state to preserve accumulated history
            existing_coins = {}
            try:
                with open("/tmp/tg_state.json", "r") as _ef:
                    existing_coins = _json.load(_ef).get("coins", {})
            except Exception:
                pass

            coins = {}
            for base_symbol in symbols:
                ctx = self._build_context_dict(base_symbol)
                if not ctx:
                    continue

                score_long = self.leading_scorer.score(
                    direction="LONG",
                    market_context_buffer=self.market_context_buffer,
                    base_symbol=base_symbol,
                    signal_metadata={'market_context': ctx},
                )
                score_short = self.leading_scorer.score(
                    direction="SHORT",
                    market_context_buffer=self.market_context_buffer,
                    base_symbol=base_symbol,
                    signal_metadata={'market_context': ctx},
                )

                # Get Binance FR from per-exchange data
                fpe = ctx.get('funding_per_exchange', {})
                binance_fr = fpe.get('Binance', ctx.get('funding_rate', 0))

                # CVD sparkline history — persistent across restarts
                spot_hist = self.market_context_buffer.get_spot_cvd_history(base_symbol, 10)
                fut_hist = self.market_context_buffer.get_futures_cvd_history(base_symbol, 10)

                # Get current cumulative values for sparkline
                spot_val = getattr(spot_hist[-1], 'cvd_cumulative', spot_hist[-1].cvd_latest) if spot_hist else None
                fut_val = getattr(fut_hist[-1], 'cvd_cumulative', fut_hist[-1].cvd_latest) if fut_hist else None

                # Load existing history from previous state, append new value
                prev = existing_coins.get(base_symbol, {})
                spot_spark = prev.get('spot_cvd_spark', [])
                fut_spark = prev.get('fut_cvd_spark', [])

                if spot_val is not None:
                    if not spot_spark or abs(spot_spark[-1] - spot_val) > 0.01:
                        spot_spark.append(spot_val)
                    spot_spark = spot_spark[-20:]

                if fut_val is not None:
                    if not fut_spark or abs(fut_spark[-1] - fut_val) > 0.01:
                        fut_spark.append(fut_val)
                    fut_spark = fut_spark[-20:]

                # Taker buy/sell from latest futures CVD snapshot
                taker_buy = 0
                taker_sell = 0
                if fut_hist:
                    taker_buy = getattr(fut_hist[-1], 'taker_buy_vol', 0)
                    taker_sell = getattr(fut_hist[-1], 'taker_sell_vol', 0)

                # OI interpretation — use short-term price change, not 24h
                oi_chg = ctx.get('oi_change_1h_pct', 0)
                # Derive recent price change from price snapshots (last 2)
                price_history = self.market_context_buffer.get_price_history(base_symbol, limit=2)
                if len(price_history) >= 2:
                    p_now = price_history[-1].price
                    p_prev = price_history[0].price
                    price_chg = ((p_now - p_prev) / p_prev * 100) if p_prev > 0 else 0
                else:
                    price_chg = ctx.get('price_change_24h_pct', 0)

                if oi_chg < -0.3 and price_chg < 0:
                    oi_interp = "DELEVERAGING"
                elif oi_chg > 0.3 and price_chg > 0:
                    oi_interp = "MOMENTUM VALID"
                elif oi_chg > 0.3 and price_chg < 0:
                    oi_interp = "SHORT ADDING"
                elif oi_chg < -0.3 and price_chg > 0:
                    oi_interp = "SHORT COVERING"
                else:
                    oi_interp = "NEUTRAL"

                # Per-coin last update timestamp
                last_ts = None
                if spot_hist:
                    last_ts = getattr(spot_hist[-1], 'timestamp', None)
                elif fut_hist:
                    last_ts = getattr(fut_hist[-1], 'timestamp', None)
                updated_str = ""
                if last_ts:
                    from datetime import timezone as _tz, timedelta as _td
                    try:
                        updated_str = datetime.fromtimestamp(last_ts, tz=_tz(_td(hours=7))).strftime('%H:%M:%S')
                    except Exception:
                        pass

                coins[base_symbol] = {
                    "price": ctx.get('current_price', 0),
                    "change_24h": ctx.get('price_change_24h_pct', 0),
                    "volume_24h": ctx.get('volume_24h', 0),
                    "oi_usd": ctx.get('oi_usd', 0),
                    "oi_change_1h": ctx.get('oi_change_1h_pct', 0),
                    "oi_interp": oi_interp,
                    "funding_rate": binance_fr,
                    "taker_buy_vol": taker_buy,
                    "taker_sell_vol": taker_sell,
                    "spot_cvd": ctx.get('spot_cvd_latest', 0),
                    "spot_cvd_candle": ctx.get('spot_cvd_candle', 0),
                    "spot_cvd_change": ctx.get('spot_cvd_change', 0),
                    "spot_cvd_dir": ctx.get('spot_cvd_direction', 'UNKNOWN'),
                    "spot_cvd_slope": ctx.get('spot_cvd_slope', 0),
                    "spot_cvd_spark": spot_spark,
                    "fut_cvd": ctx.get('futures_cvd_latest', 0),
                    "fut_cvd_candle": ctx.get('futures_cvd_candle', 0),
                    "fut_cvd_change": ctx.get('futures_cvd_change', 0),
                    "fut_cvd_dir": ctx.get('futures_cvd_direction', 'UNKNOWN'),
                    "fut_cvd_slope": ctx.get('futures_cvd_slope', 0),
                    "fut_cvd_spark": fut_spark,
                    "ob_bid": ctx.get('orderbook_bid_vol', 0),
                    "ob_ask": ctx.get('orderbook_ask_vol', 0),
                    "ob_dominant": ctx.get('orderbook_dominant', 'UNKNOWN'),
                    "long_score": score_long.total,
                    "short_score": score_short.total,
                    "long_label": score_long.label_text,
                    "short_label": score_short.label_text,
                    "bias": "LONG" if score_long.total > score_short.total else "SHORT" if score_short.total > score_long.total else "NEUTRAL",
                    "updated": updated_str,
                }

            whale_alerts = getattr(self, '_recent_whale_alerts', [])

            # FR extremes: top 3 most negative + top 3 most positive across all coins
            fr_items = [(sym, d.get('funding_rate', 0)) for sym, d in coins.items() if d.get('funding_rate', 0) != 0]
            fr_sorted = sorted(fr_items, key=lambda x: x[1])
            fr_extremes = {
                "most_negative": [{"coin": s, "fr": v} for s, v in fr_sorted[:3]],
                "most_positive": [{"coin": s, "fr": v} for s, v in fr_sorted[-3:][::-1]],
            }

            state = {
                "timestamp": now.strftime('%Y-%m-%d %H:%M:%S WIB'),
                "coins_count": len(coins),
                "uptime_min": round((datetime.now() - self.start_time).total_seconds() / 60, 1),
                "stats": self.stats,
                "coins": coins,
                "whale_alerts": whale_alerts,
                "fr_extremes": fr_extremes,
            }

            state_json = _json.dumps(state, indent=2, default=str)

            # Write to internal state
            state_path = Path("data/live_state.json")
            tmp_path = state_path.with_suffix('.tmp')
            with open(tmp_path, 'w') as f:
                f.write(state_json)
            tmp_path.replace(state_path)

            # Write to dashboard state
            dash_path = Path("/tmp/tg_state.json")
            dash_tmp = dash_path.with_suffix('.tmp')
            with open(dash_tmp, 'w') as f:
                f.write(state_json)
            dash_tmp.replace(dash_path)

        except Exception as e:
            self.logger.error(f"State write error: {e}")

    def _build_context_dict(self, base_symbol: str) -> dict:
        """Build market context dict from buffer for a base symbol."""
        ctx = {}

        # OI
        oi = self.market_context_buffer.get_latest_oi(base_symbol)
        if oi:
            ctx['oi_usd'] = oi.current_oi_usd
            ctx['oi_change_1h_pct'] = oi.oi_change_pct

        # Funding
        funding = self.market_context_buffer.get_latest_funding(base_symbol)
        if funding:
            ctx['funding_rate'] = funding.current_rate

        # Funding per exchange
        fpe = self.market_context_buffer.get_funding_per_exchange(base_symbol)
        if fpe:
            ctx['funding_per_exchange'] = fpe.rates

        # CVD
        spot = self.market_context_buffer.get_latest_spot_cvd(base_symbol)
        if spot:
            ctx['spot_cvd_direction'] = spot.cvd_direction
            ctx['spot_cvd_latest'] = getattr(spot, 'cvd_cumulative', spot.cvd_latest)
            ctx['spot_cvd_candle'] = spot.cvd_latest
            ctx['spot_cvd_change'] = spot.cvd_change
            ctx['spot_cvd_slope'] = spot.cvd_slope

        fut = self.market_context_buffer.get_latest_futures_cvd(base_symbol)
        if fut:
            ctx['futures_cvd_direction'] = fut.cvd_direction
            ctx['futures_cvd_latest'] = getattr(fut, 'cvd_cumulative', fut.cvd_latest)
            ctx['futures_cvd_candle'] = fut.cvd_latest
            ctx['futures_cvd_change'] = fut.cvd_change
            ctx['futures_cvd_slope'] = fut.cvd_slope

        # Orderbook
        ob = self.market_context_buffer.get_latest_orderbook(base_symbol)
        if ob:
            ctx['orderbook_dominant'] = ob.dominant_side
            ctx['orderbook_bid_vol'] = ob.total_bid_vol
            ctx['orderbook_ask_vol'] = ob.total_ask_vol

        # Price
        price = self.market_context_buffer.get_latest_price(base_symbol)
        if price:
            ctx['current_price'] = price.price
            ctx['price_change_24h_pct'] = price.change_24h_pct
            ctx['volume_24h'] = price.volume_24h

        return ctx

    def _format_proactive_alert(
        self, symbol: str, direction: str, score, price: float, ctx: dict
    ) -> str:
        """Format proactive scan alert for Telegram (data-first, same depth as Stop Hunt)."""
        dir_emoji = "\U0001f4c8" if direction == "LONG" else "\U0001f4c9"
        fmt = self.message_formatter

        # --- Header ---
        from datetime import timezone, timedelta
        wib = timezone(timedelta(hours=7))
        time_str = datetime.now(wib).strftime('%H:%M:%S')
        price_str = fmt.format_price(price) if price > 0 else "N/A"
        header = f"\U0001f4a1 *{symbol}* | {price_str} | {time_str} WIB"

        # --- Trigger: Leading Indicator ---
        trigger_lines = [f"\U0001f514 *Trigger: LEADING SCAN* | {direction}"]
        for ind in score.indicators:
            if ind.detail:
                trigger_lines.append(f"\u2726 {ind.detail}")
        for note in score.notes:
            trigger_lines.append(f"\u2726 {note}")
        trigger = "\n".join(trigger_lines)

        # --- Order Flow ---
        of_lines = []
        has_spot = ctx.get('spot_cvd_direction', 'UNKNOWN') != 'UNKNOWN'
        has_fut = ctx.get('futures_cvd_direction', 'UNKNOWN') != 'UNKNOWN'
        has_oi = ctx.get('oi_usd', 0) > 0
        has_ob = ctx.get('orderbook_dominant', 'UNKNOWN') != 'UNKNOWN'

        if any([has_spot, has_fut, has_oi, has_ob]):
            of_lines.append("\U0001f4ca *ORDER FLOW*")
            if has_spot:
                arrow = fmt._dir_arrow(ctx['spot_cvd_direction'])
                chg = ctx.get('spot_cvd_change', 0)
                chg_arrow = "\u25b2" if chg > 0 else "\u25bc" if chg < 0 else "\u2192"
                of_lines.append(f"SpotCVD  : {fmt._fmt_value(ctx.get('spot_cvd_latest', 0))} | \u039460m: {chg_arrow}{fmt._fmt_value(chg)} | {ctx['spot_cvd_direction']}")
            if has_fut:
                arrow = fmt._dir_arrow(ctx['futures_cvd_direction'])
                chg = ctx.get('futures_cvd_change', 0)
                chg_arrow = "\u25b2" if chg > 0 else "\u25bc" if chg < 0 else "\u2192"
                of_lines.append(f"FutCVD   : {fmt._fmt_value(ctx.get('futures_cvd_latest', 0))} | \u039460m: {chg_arrow}{fmt._fmt_value(chg)} | {ctx['futures_cvd_direction']}")
            if has_spot or has_fut:
                of_lines.append(f"CVD sync : {ctx.get('cvd_alignment', 'NEUTRAL')}")
            if has_oi:
                oi_change = ctx.get('oi_change_1h_pct', 0)
                oi_arrow = "\u25b2" if oi_change > 0 else "\u25bc" if oi_change < 0 else "\u25b6"
                of_lines.append(f"OI       : {fmt._fmt_large_usd(ctx['oi_usd'])} {oi_arrow} {oi_change:+.1f}% 1h [{ctx.get('oi_alignment', 'NEUTRAL')}]")
            if has_ob:
                bid = ctx.get('orderbook_bid_vol', 0)
                ask = ctx.get('orderbook_ask_vol', 0)
                total = bid + ask
                bid_pct = (bid / total * 100) if total > 0 else 50
                of_lines.append(f"OBDelta  : Bid {fmt._fmt_large_usd(bid)} ({bid_pct:.0f}%) / Ask {fmt._fmt_large_usd(ask)} ({100-bid_pct:.0f}%)")
        order_flow = "\n".join(of_lines)

        # --- Funding Rate ---
        fr_lines = []
        per_exchange = ctx.get('funding_per_exchange', {})
        fr = ctx.get('funding_rate', 0)
        if per_exchange:
            sane = {k: v for k, v in per_exchange.items() if abs(v) < 0.01}
            if sane:
                fr_lines.append("\U0001f4b8 *FUNDING RATE*")
                for exchange, rate in sorted(sane.items(), key=lambda x: abs(x[1]), reverse=True)[:5]:
                    fr_lines.append(f"{exchange:9s}: {rate*100:+.4f}%")
                fr_lines.append(f"Alignment: {ctx.get('funding_alignment', 'NEUTRAL')}")
        elif fr != 0 and abs(fr) < 0.01:
            fr_lines.append("\U0001f4b8 *FUNDING RATE*")
            fr_lines.append(f"Avg      : {fr*100:+.4f}%")
            fr_lines.append(f"Alignment: {ctx.get('funding_alignment', 'NEUTRAL')}")
        funding = "\n".join(fr_lines)

        # --- Whale ---
        whale_lines = []
        whale_val = ctx.get('whale_largest_value_usd', 0)
        if whale_val >= 1_000_000:
            whale_lines.append("\U0001f40b *WHALE (Hyperliquid)*")
            w_dir = (ctx.get('whale_largest_direction', '') or '?').upper()
            detail = f"{w_dir} {fmt._fmt_large_usd(whale_val)}"
            w_entry = ctx.get('whale_entry_price', 0)
            w_liq = ctx.get('whale_liq_price', 0)
            if w_entry > 0:
                detail += f" @ {fmt.format_price(w_entry)}"
            if w_liq > 0:
                detail += f" | Liq: {fmt.format_price(w_liq)}"
            detail += f" [{ctx.get('whale_alignment', 'NEUTRAL')}]"
            whale_lines.append(detail)
        whale = "\n".join(whale_lines)

        # --- Price Action ---
        pa_lines = []
        volume = ctx.get('volume_24h', 0)
        change = ctx.get('price_change_24h_pct', 0)
        if price > 0 or volume > 0:
            pa_lines.append("\U0001f4c8 *PRICE ACTION*")
            if price > 0:
                if change != 0:
                    pa_lines.append(f"Harga    : {price_str} ({change:+.1f}% 24h)")
                else:
                    pa_lines.append(f"Harga    : {price_str}")
            if volume > 0:
                pa_lines.append(f"Volume   : {fmt._fmt_large_usd(volume)}")
        price_action = "\n".join(pa_lines)

        # --- Bias line ---
        bar = fmt.create_progress_bar(score.total, length=10)
        assessment = ctx.get('combined_assessment', '')
        assess_line = ""
        if assessment:
            assess_emoji = {"FAVORABLE": "\u2705", "NEUTRAL": "\u2796", "UNFAVORABLE": "\u274c"}.get(assessment, "")
            assess_line = f"\nContext  : {assess_emoji} {assessment}"

        label = f"{score.label_emoji} {direction} {dir_emoji} \u2014 {score.label_text}" if score.label_text else f"\U0001f4a1 {direction} {dir_emoji}"
        bias = f"{label}\n{bar} {score.total:.0f}%{assess_line}\n\u26a0\ufe0f DYOR \u2014 verifikasi sebelum entry"

        # --- Assemble ---
        sections = [header, trigger]
        if order_flow:
            sections.append(order_flow)
        if funding:
            sections.append(funding)
        if whale:
            sections.append(whale)
        if price_action:
            sections.append(price_action)
        sections.append(bias)

        return "\n\n".join(sections)

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
                        trade_channel = f"futures_trades@all_{symbol}@10000"
                        subscribe_msg = {
                            "method": "subscribe",
                            "channels": [trade_channel]
                        }
                        success = await self.websocket_client.send_message(subscribe_msg)
                        if success:
                            self._trade_subscribed.add(symbol)
                            self.logger.info(f"📡 Dashboard subscription: {trade_channel}")

                    elif action == "unsubscribe" and symbol in self._trade_subscribed:
                        trade_channel = f"futures_trades@all_{symbol}@10000"
                        unsubscribe_msg = {
                            "method": "unsubscribe",
                            "channels": [trade_channel]
                        }
                        success = await self.websocket_client.send_message(unsubscribe_msg)
                        if success:
                            self._trade_subscribed.discard(symbol)
                            self.logger.info(f"📡 Dashboard unsubscription: {trade_channel}")

                # --- Auto-discovery check (every 5 minutes) ---
                now = asyncio.get_running_loop().time()
                if now - last_discovery_check < 300:
                    continue
                last_discovery_check = now

                for symbol in list(self.discovered_symbols):
                    if symbol in self._trade_subscribed:
                        continue

                    # Check if this coin has recent liquidation activity
                    liqs = self.buffer_manager.get_liquidations(symbol, time_window=300)
                    if len(liqs) >= 3:  # At least 3 liquidations in 5 min = worth subscribing
                        trade_channel = f"futures_trades@all_{symbol}@10000"
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

            # Cleanup stale analysis locks/timestamps to prevent memory leak
            now = asyncio.get_running_loop().time()
            stale_symbols = [
                s for s, t in self.last_analysis.items()
                if now - t > 3600  # Not analyzed in last hour
            ]
            for s in stale_symbols:
                self.last_analysis.pop(s, None)
                self.analysis_locks.pop(s, None)
            if stale_symbols:
                self.logger.info(f"🧹 Cleaned {len(stale_symbols)} stale analysis entries")

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
                await self.db.cleanup_old_oi_snapshots(max_age_hours=168)
                await self.db.cleanup_old_funding_snapshots(max_age_hours=168)
            except Exception as e:
                self.logger.error(f"Baseline save error: {e}")

            # Periodically save confidence state (type-level + setup-level)
            try:
                for signal_type, history in self.confidence_scorer.signal_history.items():
                    if history:
                        await self.db.save_confidence_state(
                            signal_type,
                            self.confidence_scorer.win_rates.get(signal_type, 0.5),
                            history
                        )
                # Save setup-level learning state
                if self.confidence_scorer._setup_history:
                    await self.db.save_all_setup_states(
                        dict(self.confidence_scorer._setup_history),
                        dict(self.confidence_scorer._setup_win_rates),
                    )
            except Exception as e:
                self.logger.error(f"Confidence/setup save error: {e}")

            # Rebuild calibration table periodically (every 6 hours)
            try:
                if self.calibration.is_stale(max_age_hours=6):
                    await self.calibration.build_from_db(self.db)
                    table = self.calibration.get_table()
                    if table:
                        self.logger.info(
                            f"Calibration rebuilt: {len(table)} buckets | "
                            + " | ".join(
                                f"{b['bucket']}:{b['win_rate']:.0%}({b['count']})"
                                for b in table if b['trusted']
                            )
                        )
            except Exception as e:
                self.logger.error(f"Calibration rebuild error: {e}")

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
                asyncio.create_task(self.dynamic_subscription_task()),
                asyncio.create_task(self.rest_poller.start(shutdown_event)),
                asyncio.create_task(self.proactive_scan_task()),
            ]

            # Start fast poller if configured
            if self.rest_poller.fast_symbols:
                tasks.append(
                    asyncio.create_task(self.rest_poller.start_fast_poll(shutdown_event))
                )

            # Start movement detector scan loop
            tasks.append(
                asyncio.create_task(self._movement_detector_task())
            )

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

            # Drain remaining alerts before cancelling (max 10s)
            if not self.alert_queue.is_empty():
                remaining = self.alert_queue.size()
                self.logger.info(f"⏳ Draining {remaining} remaining alerts...")
                await self.alert_queue.wait_empty(timeout=10.0)

            # Cancel background tasks
            for task in tasks:
                task.cancel()

            await asyncio.gather(*tasks, return_exceptions=True)

            # Close Telegram bot session
            if self.telegram_bot:
                await self.telegram_bot.close()

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
    required_sections = ['pairs', 'thresholds', 'signals', 'alerts', 'buffers', 'websocket', 'monitoring', 'dashboard']
    
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
        # Fallback defaults matching config/config.yaml v4.2 structure
        logger = logging.getLogger("ConfigLoader")
        logger.warning("config/config.yaml not found — using hardcoded defaults")
        config = {
            'pairs': {
                'primary': ['BTCUSDT', 'ETHUSDT']
            },
            'thresholds': {
                'liquidation_cascade': 2_000_000,
                'large_order_threshold': 25_000,
                'accumulation_ratio': 0.72,
                'distribution_ratio': 0.28,
                'min_large_orders': 5,
                'absorption_minimum': 100_000,
                'funding_rate_extreme': 0.001,
            },
            'signals': {
                'min_confidence': 70.0,
                'max_signals_per_hour': 10,
                'cooldown_minutes': 15
            },
            'alerts': {
                'rate_limit_delay': 3.0,
                'max_retries': 3,
                'urgent_threshold': 92,
                'watch_threshold': 85,
                'info_threshold': 70,
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
                'tier2_symbols': ['SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT', 'AVAXUSDT',
                                  'SUIUSDT', 'ADAUSDT', 'DOTUSDT', 'LINKUSDT', 'LTCUSDT',
                                  'UNIUSDT', 'AAVEUSDT', 'MATICUSDT'],
                'tier3_symbols': [],
                'tier1_cascade': 3_000_000,
                'tier2_cascade': 500_000,
                'tier3_cascade': 150_000,
                'tier4_cascade': 50_000,
                'tier1_absorption': 200_000,
                'tier2_absorption': 50_000,
                'tier3_absorption': 15_000,
                'tier4_absorption': 5_000,
                'tier1_cooldown_minutes': 120,
                'tier2_cooldown_minutes': 60,
                'tier3_cooldown_minutes': 45,
                'tier4_cooldown_minutes': 30,
                'tier1_min_volume_24h': 100_000_000,
                'tier2_min_volume_24h': 50_000_000,
                'tier3_min_volume_24h': 15_000_000,
                'tier4_min_volume_24h': 5_000_000,
                'fr_extreme_threshold': 0.01,
                'max_concurrent_analysis': 30
            },
            'analysis': {
                'stop_hunt_window': 30,
                'order_flow_window': 300
            },
            'dashboard': {
                'api_token': '',
                'cors_origins': ['http://localhost:3000', 'http://127.0.0.1:8081']
            },
            'market_context': {
                'enabled': True,
                'poll_interval': 120,
                'max_snapshots': 72,
                'filter_mode': 'normal',
                'confidence_adjust': True,
                'cvd_enabled': True,
                'cvd_interval': '5m',
                'cvd_lookback': 12,
                'whale_enabled': True,
                'whale_veto_threshold': 5_000_000,
                'whale_caution_threshold': 1_000_000,
                'orderbook_enabled': True,
                'price_enabled': True,
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
    """Handle shutdown signals (async-safe via call_soon_threadsafe)"""
    print("\n🛑 Received shutdown signal")
    try:
        loop = asyncio.get_running_loop()
        loop.call_soon_threadsafe(shutdown_event.set)
    except RuntimeError:
        # No running loop — set directly as fallback
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
