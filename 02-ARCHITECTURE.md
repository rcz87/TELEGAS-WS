# TELEGLAS Pro - Technical Architecture

## 🏗️ System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         TELEGLAS Pro System                         │
└─────────────────────────────────────────────────────────────────────┘

                    ┌──────────────────────┐
                    │   CoinGlass API      │
                    │   (Data Source)      │
                    └──────────┬───────────┘
                               │
                               │ WebSocket Stream
                               │ wss://open-ws.coinglass.com/ws-api
                               │
                    ┌──────────▼───────────┐
                    │  VPS Hostinger       │
                    │  Ubuntu 22.04 LTS    │
                    │  2GB RAM, 2 CPU      │
                    └──────────┬───────────┘
                               │
        ┌──────────────────────┼──────────────────────┐
        │                      │                      │
┌───────▼────────┐   ┌─────────▼────────┐   ┌────────▼────────┐
│  Connection    │   │   Processing     │   │   Storage       │
│  Layer         │   │   Layer          │   │   Layer         │
│                │   │                  │   │   (Optional)    │
│ • WebSocket    │   │ • Message Parser │   │                 │
│ • Heartbeat    │   │ • Validator      │   │ • SQLite        │
│ • Reconnect    │   │ • Buffers        │   │ • PostgreSQL    │
└───────┬────────┘   └─────────┬────────┘   └────────┬────────┘
        │                      │                      │
        └──────────────────────┼──────────────────────┘
                               │
                    ┌──────────▼───────────┐
                    │   Analysis Layer     │
                    ├──────────────────────┤
                    │                      │
                    │  ┌────────────────┐  │
                    │  │ Stop Hunt      │  │
                    │  │ Detector       │  │
                    │  └────────────────┘  │
                    │                      │
                    │  ┌────────────────┐  │
                    │  │ Order Flow     │  │
                    │  │ Analyzer       │  │
                    │  └────────────────┘  │
                    │                      │
                    │  ┌────────────────┐  │
                    │  │ Event Pattern  │  │
                    │  │ Detector       │  │
                    │  └────────────────┘  │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │   Signal Layer       │
                    ├──────────────────────┤
                    │ • Signal Generator   │
                    │ • Confidence Scorer  │
                    │ • Validator          │
                    │ • Deduplicator       │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │   Alert Layer        │
                    ├──────────────────────┤
                    │ • Message Formatter  │
                    │ • Queue Manager      │
                    │ • Telegram Bot       │
                    │ • Rate Limiter       │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │  Telegram Messenger  │
                    │  (User's Phone)      │
                    └──────────────────────┘
```

---

## 📦 Component Details

### 1. Connection Layer

**Responsibility:** Maintain stable WebSocket connection to CoinGlass

**Components:**

#### WebSocketClient
- **File:** `src/connection/websocket_client.py`
- **Function:** Establish and maintain WebSocket connection
- **Features:**
  - Auto-reconnect with exponential backoff
  - Connection state tracking
  - Error handling
  - Graceful shutdown

#### HeartbeatManager
- **Function:** Keep connection alive
- **Mechanism:** Send "ping" every 20 seconds, expect "pong"
- **Failure action:** Trigger reconnection

#### SubscriptionManager
- **Function:** Manage channel subscriptions
- **Channels:**
  - `liquidationOrders` - All liquidations, all exchanges
  - `futures_trades@{exchange}_{symbol}@{minVol}` - Filtered trades

**Data Flow:**
```
CoinGlass → WebSocket → Message Queue → Processors
                ↑
                └─ Heartbeat (every 20s)
```

**Error Handling:**
- Network failure → Auto-reconnect
- Invalid auth → Log and alert admin
- Rate limit → Back off and retry

---

### 2. Processing Layer

**Responsibility:** Parse, validate, and buffer incoming data

**Components:**

#### MessageParser
- **File:** `src/processors/message_parser.py`
- **Function:** Deserialize JSON messages
- **Output:** Structured Python objects

#### DataValidator
- **File:** `src/processors/data_validator.py`
- **Function:** Validate data integrity
- **Checks:**
  - Required fields present
  - Data types correct
  - Values within expected ranges

#### BufferManager
- **File:** `src/processors/buffer_manager.py`
- **Function:** Maintain rolling time-series buffers
- **Buffers:**
  - Liquidation buffer (per symbol, last 1000 events)
  - Trade buffer (per symbol, last 500 trades)
  - Time-based cleanup (hourly)

**Data Flow:**
```
Raw JSON → Parser → Validator → Buffers → Analyzers
             ↓
        Error Log (if invalid)
```

---

### 3. Analysis Layer

**Responsibility:** Detect patterns and generate insights

#### A. Stop Hunt Detector

**File:** `src/analyzers/stop_hunt_detector.py`

**Algorithm:**
```python
# Pseudo-code
FOR each symbol:
    recent_liq = get_last_30_seconds_liquidations()
    total_vol = sum(recent_liq.volume)
    
    IF total_vol > $2M:
        direction = majority_side(recent_liq)  # Long or Short hunt
        
        # Wait for absorption
        WAIT 30 seconds
        
        large_orders = get_large_orders(opposite_direction)
        absorption_vol = sum(large_orders.volume)
        
        IF absorption_vol > $100K:
            confidence = calculate_confidence(total_vol, absorption_vol)
            
            IF confidence > 70%:
                EMIT StopHuntSignal(symbol, direction, confidence)
```

**Output:**
```python
StopHuntSignal(
    symbol="BTCUSDT",
    total_volume=2800000,
    direction="SHORT_HUNT",
    price_zone=(95800, 96000),
    absorption_detected=True,
    absorption_volume=1200000,
    confidence=87,
    timestamp=datetime.now()
)
```

#### B. Order Flow Analyzer

**File:** `src/analyzers/order_flow_analyzer.py`

**Algorithm:**
```python
# Pseudo-code
FOR each symbol:
    trades = get_last_5_minutes_trades()
    
    buy_vol = sum(trades WHERE side == BUY)
    sell_vol = sum(trades WHERE side == SELL)
    buy_ratio = buy_vol / (buy_vol + sell_vol)
    
    large_buys = count(trades WHERE side == BUY AND vol > $10K)
    large_sells = count(trades WHERE side == SELL AND vol > $10K)
    
    IF buy_ratio > 0.65 AND large_buys >= 3:
        signal = "ACCUMULATION"
        confidence = calculate_confidence(buy_ratio, large_buys)
        
        IF confidence > 70%:
            EMIT OrderFlowSignal(symbol, "ACCUMULATION", confidence)
    
    ELIF buy_ratio < 0.35 AND large_sells >= 3:
        signal = "DISTRIBUTION"
        confidence = calculate_confidence(buy_ratio, large_sells)
        
        IF confidence > 70%:
            EMIT OrderFlowSignal(symbol, "DISTRIBUTION", confidence)
```

**Output:**
```python
OrderFlowSignal(
    symbol="MATICUSDT",
    buy_volume=2800000,
    sell_volume=1100000,
    buy_ratio=0.72,
    large_buys=9,
    large_sells=2,
    signal_type="ACCUMULATION",
    confidence=78,
    timestamp=datetime.now()
)
```

#### C. Event Pattern Detector

**File:** `src/analyzers/event_pattern_detector.py`

**Patterns Detected:**

1. **Liquidation Cascade**
   - Threshold: $2M+ in 30 seconds
   - Action: Alert user of potential reversal

2. **Whale Accumulation Window**
   - Pattern: Multiple large orders, flat price
   - Indicator: Breakout likely in 5-15 minutes

3. **Funding Rate Extreme**
   - Threshold: >0.1% or <-0.1% (8h rate)
   - Indicator: Squeeze potential

4. **Cross-Exchange Divergence**
   - Pattern: OI increasing on one exchange, decreasing on others
   - Indicator: Institutional positioning

---

### 4. Signal Layer

**Responsibility:** Generate high-quality, actionable signals

**Components:**

#### SignalGenerator
- **File:** `src/signals/signal_generator.py`
- **Function:** Combine multiple analyzer outputs
- **Logic:** Multi-factor scoring

#### ConfidenceScorer
- **File:** `src/signals/confidence_scorer.py`
- **Function:** Calculate confidence percentage
- **Factors:**
  - Volume (higher = more confident)
  - Direction clarity (one-sided = more confident)
  - Confirmation (multiple signals = more confident)
  - Historical accuracy (learn from past)

#### SignalValidator
- **File:** `src/signals/signal_validator.py`
- **Function:** Prevent spam and duplicates
- **Rules:**
  - Don't repeat signal within 5 minutes
  - Max 50 signals per hour
  - Require minimum confidence threshold

**Scoring Algorithm:**
```python
def calculate_confidence(signal):
    confidence = 50  # Base
    
    # Volume factor
    if signal.volume > 5M:
        confidence += 20
    elif signal.volume > 3M:
        confidence += 15
    elif signal.volume > 2M:
        confidence += 10
    
    # Clarity factor
    if signal.direction_pct > 0.8:
        confidence += 15
    elif signal.direction_pct > 0.7:
        confidence += 10
    
    # Confirmation factor
    if signal.has_absorption:
        confidence += 25
    
    # Historical factor
    win_rate = get_historical_win_rate(signal.type)
    if win_rate > 0.7:
        confidence += 10
    
    return min(confidence, 99)  # Cap at 99%
```

---

### 5. Alert Layer

**Responsibility:** Deliver formatted alerts to user

**Components:**

#### MessageFormatter
- **File:** `src/alerts/message_formatter.py`
- **Function:** Format signals into readable messages
- **Output:** Markdown-formatted text for Telegram

**Example Output:**
```
⚡ STOP HUNT DETECTED - BTC

Liquidations: $2.8M shorts cleared
Zone: $95,800-$96,000

🐋 Absorption: $1.2M buys detected

✅ SAFE ENTRY NOW
Entry: $96,000-$96,200
SL: $95,650 (below hunt zone)
Target: $97,500

Confidence: 87%
Time: 19:05:23 UTC
```

#### TelegramBot
- **File:** `src/alerts/telegram_bot.py`
- **Function:** Send messages via Telegram API
- **Features:**
  - Async sending (non-blocking)
  - Retry on failure
  - Rate limiting (20 msgs/min)

#### AlertQueue
- **File:** `src/alerts/alert_queue.py`
- **Function:** Manage alert queue
- **Priority:**
  1. 🔴 URGENT (confidence ≥85%)
  2. 🟡 WATCH (confidence ≥70%)
  3. 🔵 INFO (confidence ≥60%)

---

## 🔄 Data Flow Example

### Scenario: BTC Stop Hunt Detected

```
1. Event: $2.8M BTC short liquidations in 25 seconds
   Time: 19:05:00

2. WebSocket receives 150+ liquidation messages
   Time: 19:05:00 - 19:05:25
   
3. BufferManager stores in liquidation_buffer["BTCUSDT"]
   
4. StopHuntDetector.analyze("BTCUSDT") called
   - Calculates: total_vol = $2.8M
   - Determines: direction = "SHORT_HUNT" (78% shorts)
   
5. Detector waits for absorption (30s window)
   Time: 19:05:25 - 19:05:55
   
6. Large buy orders detected: $1.2M
   Time: 19:05:30 - 19:05:50
   
7. ConfidenceScorer calculates: 87%
   
8. SignalValidator checks:
   - Not signaled in last 5 min? ✅
   - Confidence > threshold? ✅ (87% > 70%)
   
9. MessageFormatter creates alert text
   
10. TelegramBot sends to user
    Time: 19:05:56
    
11. User receives alert on phone
    Time: 19:05:57
    
Total latency: 57 seconds from first liquidation
Retail awareness: ~2-5 minutes (via Twitter/social media)
Information edge: ~1-4 minutes
```

---

## 🗄️ Data Models

### Liquidation Event
```python
@dataclass
class LiquidationEvent:
    symbol: str
    exchange: str
    price: float
    side: int  # 1 = Long liq, 2 = Short liq
    volume_usd: float
    timestamp: datetime
```

### Trade Event
```python
@dataclass
class TradeEvent:
    symbol: str
    exchange: str
    price: float
    side: int  # 1 = Sell, 2 = Buy
    volume_usd: float
    timestamp: datetime
```

### Stop Hunt Signal
```python
@dataclass
class StopHuntSignal:
    symbol: str
    total_volume: float
    direction: str
    price_zone: Tuple[float, float]
    absorption_detected: bool
    absorption_volume: float
    confidence: float
    timestamp: datetime
```

### Order Flow Signal
```python
@dataclass
class OrderFlowSignal:
    symbol: str
    time_window: int
    buy_volume: float
    sell_volume: float
    buy_ratio: float
    large_buys: int
    large_sells: int
    signal_type: str
    confidence: float
    timestamp: datetime
```

---

## 🔐 Security Considerations

### API Key Protection
- Store in `config/secrets.env`
- File permissions: `chmod 600`
- Never commit to git
- Rotate keys quarterly

### WebSocket Security
- Always use WSS (not WS)
- Validate server certificate
- Timeout on stale connections

### System Access
- SSH key authentication only
- Firewall rules (allow only necessary ports)
- Regular security updates

### Data Privacy
- No sensitive user data stored
- Logs sanitized (no API keys)
- Optional encryption for database

---

## ⚡ Performance Optimization

### Connection Layer
- **Ping interval:** 20s (optimal for CoinGlass)
- **Reconnect backoff:** Exponential (1s, 2s, 4s, ..., max 60s)
- **Buffer size:** 1000 events (balance memory vs. analysis depth)

### Processing Layer
- **Async I/O:** All network operations non-blocking
- **Buffer cleanup:** Hourly (remove events older than analysis windows)
- **Memory limit:** ~500MB under normal load

### Analysis Layer
- **Analysis frequency:** 
  - Stop hunt: Real-time (on every liquidation)
  - Order flow: Every 60 seconds
  - Event patterns: Real-time
- **Optimization:** Skip analysis if no new data

### Alert Layer
- **Rate limit:** 20 messages/minute (Telegram limit)
- **Queue:** FIFO with priority override
- **Retry:** 3 attempts with 5s delay

---

## 📊 Scalability

### Current Design Handles:
- **Symbols:** Up to 50 pairs simultaneously
- **Messages:** 1000+ per minute
- **Alerts:** 50 per hour (configurable)
- **Storage:** 7 days of history (~500MB)

### To Scale Beyond:
1. **More symbols (50+):**
   - Open second WebSocket connection
   - Distribute pairs across connections
   
2. **More messages (5000+/min):**
   - Implement message batching
   - Use message queue (Redis/RabbitMQ)
   
3. **More alerts (100+/hr):**
   - Add alert aggregation
   - Summary alerts instead of individual

4. **Long-term storage:**
   - Migrate to PostgreSQL
   - Implement data partitioning
   - Setup backup routine

---

## 🧪 Testing Strategy

### Unit Tests
- **Coverage target:** 80%+
- **Focus:** Individual components
- **Tools:** pytest, pytest-asyncio

### Integration Tests
- **Focus:** Component interactions
- **Scenarios:**
  - WebSocket → Processor → Analyzer
  - Analyzer → Signal → Alert

### System Tests
- **Focus:** End-to-end functionality
- **Scenarios:**
  - Receive liquidation → Send alert
  - Connection loss → Auto-reconnect

### Load Tests
- **Focus:** Performance under stress
- **Metrics:**
  - Max messages per second
  - Memory usage over 24 hours
  - CPU usage peaks

### Paper Trading Tests
- **Focus:** Signal quality
- **Metrics:**
  - Accuracy rate
  - False positive rate
  - Latency distribution

---

## 📈 Monitoring & Observability

### Health Checks
- **Connection status:** Real-time
- **Message rate:** Messages per minute
- **Alert rate:** Alerts per hour
- **System resources:** CPU, RAM, disk

### Logging Levels
- **DEBUG:** All events (development only)
- **INFO:** Key events (connection, signals)
- **WARNING:** Unusual conditions
- **ERROR:** Failures requiring attention
- **CRITICAL:** System down

### Metrics to Track
- **Uptime:** Target 99.9%
- **Latency:** Event to alert (target <60s)
- **Accuracy:** Signal win rate (target 65%+)
- **Resource usage:** RAM (target <1GB)

---

## 🔄 Deployment Architecture

### Process Management (PM2)
```
PM2
├── teleglas-websocket (main process)
├── teleglas-monitor (health checker)
└── teleglas-cleanup (hourly cleanup)
```

### File System
```
/home/user/teleglas-pro/
├── venv/             # Python virtual environment
├── src/              # Source code
├── config/           # Configuration
├── logs/             # Log files (rotated)
├── data/             # Optional database
└── scripts/          # Management scripts
```

### Network Ports
- **WebSocket:** Outbound to CoinGlass (443/WSS)
- **Telegram:** Outbound to Telegram API (443/HTTPS)
- **Monitoring:** Optional web dashboard (8080/HTTP)

---

## 🎯 Architecture Benefits

### Modularity
- Each layer is independent
- Easy to test individual components
- Simple to extend functionality

### Reliability
- Auto-reconnect ensures uptime
- Redundant error handling
- Graceful degradation

### Performance
- Async I/O for efficiency
- Optimized buffers and analysis
- Minimal resource footprint

### Maintainability
- Clear separation of concerns
- Comprehensive logging
- Self-documenting code

---

**Next:** See `03-API-REFERENCE.md` for CoinGlass API details
