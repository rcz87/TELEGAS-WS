# TELEGLAS Pro - Real-Time Trading Intelligence System

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.108.0-green.svg)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

> **Professional cryptocurrency trading intelligence system providing 30-90 second information edge through real-time stop hunt detection, order flow analysis, CVD-based leading indicators, and multi-source market data fusion.**

---

## Features

### Core Intelligence
- **Stop Hunt Detection** - Identify liquidation cascades in real-time with 4-tier thresholds
- **Order Flow Analysis** - Track whale accumulation and distribution patterns (72%/28% strong-only ratios)
- **Event Pattern Detection** - Volume spikes, whale accumulation & distribution windows
- **Leading Indicator Scorer** - Hierarchy: SpotCVD (35pts) > FuturesCVD (25pts) > OI (20pts) > lagging confirmations
- **CVD VETO** - SpotCVD flip opposing signal direction = hard block (no false signals through)
- **Market Context Filter** - OI + Funding Rate + CVD + Whale + Orderbook filters false signals
- **LONG + SHORT Signals** - Both directions fully supported across entire pipeline
- **Adaptive Confidence Scoring** - Learns from signal outcomes, tier-aware quality boost
- **Anti-Spam System** - Dedup (5% confidence bands), per-tier cooldowns (30min-2hr), rate limiting

### 4-Tier Coin Classification
- **Tier 1 (Mega Cap)** - BTC, ETH — $3M cascade, $2M CVD flip min, 2hr cooldown
- **Tier 2 (Large Cap)** - SOL, BNB, XRP, DOGE, etc. — $500K cascade, 1hr cooldown
- **Tier 3 (Mid Cap)** - TRUMP, HYPE, ARB, PEPE, XAU, etc. — $150K cascade, 45min cooldown
- **Tier 4 (Small Cap)** - Everything else (dynamic) — $50K cascade, 30min cooldown
- **Volume Gate** - Per-tier minimum 24h volume ($100M/$50M/$15M/$5M)
- **FR Extreme Auto-Skip** - Tier 3/4 coins skipped if funding rate > 1%

### Proactive Watchlist Scanner
- **80+ Coins** monitored every 2 minutes via REST polling
- **Leading Indicators** - SpotCVD, FuturesCVD, OI, Orderbook, Whale alerts fetched proactively
- **Pattern Detection** - COILING pattern (low vol + CVD+ + OI rising = breakout imminent)
- **Commodities** - XAU, XAG, PAXG included in watchlist

### PWA Web Dashboard
- **Progressive Web App** - Installable, offline-capable with service worker
- **TradingView Charts** - Integrated charting with real-time price data
- **Live State Push** - WebSocket state updates every 2 seconds
- **AI Analysis Database** - Stores analyses with regime, bias, grade, tracks 15m/30m/60m outcomes
- **Data-First Alerts** - Raw SpotCVD, FutCVD, OI, orderbook shown prominently; system bias as reference
- **Dynamic Coin Management** - Add/remove/toggle pairs without restart
- **Mobile-Responsive** - Dark theme, JetBrains Mono font, works on all devices

### Multi-Source Data Fusion (CoinGlass REST API v4)
- **SpotCVD** - Cumulative Volume Delta with slope (linear regression) and direction
- **FuturesCVD** - Derivatives CVD with flip detection
- **Open Interest** - Aggregated OI history with % change tracking
- **Funding Rate** - OI-weighted across exchanges + per-exchange breakdown
- **Whale Alerts** - Hyperliquid positions > $1M ($5M = VETO, $1M = CAUTION)
- **Orderbook Delta** - Aggregated bid/ask imbalance (BIDS/ASKS/BALANCED)
- **Price OHLC** - 24h candles with volume for context

### Persistent Storage (SQLite)
- **Signal History** - All generated signals saved with outcome tracking
- **Auto WIN/LOSS** - SignalTracker evaluates after 15min, requires >= 50% to target for WIN
- **OI & Funding Snapshots** - Historical data persisted (7-day retention)
- **AI Analysis DB** - Separate database for analysis regime/bias/grade tracking
- **State Restore** - Confidence state, dashboard coins, baselines survive restart
- **CSV Export** - `/api/export/signals.csv`, `/api/export/baselines.csv`

### Alert System
- **Telegram Integration** - Data-first formatting: raw CVD/OI/orderbook/whale data shown prominently
- **Per-Exchange Funding** - Funding rates per exchange in every alert
- **Whale Positions** - Hyperliquid whale data in alerts
- **Smart Price Formatting** - Works across all price scales (BTC $96,200 to PEPE $0.00001182)
- **Priority Queue** - URGENT (92%+), WATCH (85%+), INFO (70%+) with retry logic
- **Rate Limiting** - 10 signals/hour max + per-tier cooldowns

### Production Features
- **Auto-Reconnect** - Iterative loop with exponential backoff + force reconnect after 3 consecutive timeouts
- **Thread Safety** - All dashboard state access under locks, deepcopy on reads
- **Graceful Shutdown** - Timeout on all async tasks, state saved to DB before exit
- **Symbol Normalizer** - Handles multi-exchange formats (Binance, OKX, Bitget, Bybit, dYdX, Hyperliquid)
- **Absolute Config Paths** - Works regardless of working directory

---

## Architecture

```
CoinGlass WebSocket API                    CoinGlass REST API v4 (every 2 min)
  |                                              |
  |  liquidationOrders (ALL coins)               |  OI aggregated-history
  |  futures_trades@all_{symbol}@10000           |  Funding rate oi-weight-history
  v                                              |  SpotCVD + FuturesCVD history
+---------------------------------------------------------+  Whale alerts (Hyperliquid)
|  TELEGLAS Pro Pipeline                          |  Orderbook bid/ask delta
|                                                 |  Price OHLC + volume
|  1. WebSocket Client     - Auth via URL query   v
|  2. Symbol Normalizer    - Multi-exchange merge ------+
|  3. Field Normalizer     - volUsd→vol, exName→exchange|
|  4. Data Validator       - Schema validation    |     |
|  5. Buffer Manager       - Rolling time-series  |     |
|  6. Analyzers            - StopHunt, OrderFlow  |     |
|  7. Signal Generator     - Merge + direction    |     |
|  8. Leading Indicator Scorer - CVD/OI/Orderbook hierarchy
|  9. Market Context Filter - OI/FR alignment     |     |
| 10. CVD VETO             - Hard block if opposing|    |
| 11. Signal Validator     - Dedup, cooldown, rate|     |
| 12. Confidence Scorer    - Tier-aware adaptive  |     |
|                                                 |     |
|  REST Poller: 2-min polling (OI/CVD/Whale/OB)   |     |
|  Market Context Buffer: Rolling snapshots        |     |
|  Storage: SQLite (signals, state, OI/funding)    |     |
+-----------+-------------------------------+------+-----+
            |                               |
            v                               v
     +-----------+                 +-------------------+
     | Telegram  |                 | PWA Dashboard     |
     | - Data    |                 | - TradingView     |
     | - CVD/OI  |                 | - WebSocket push  |
     | - Whales  |                 | - AI Analysis DB  |
     +-----------+                 +-------------------+
                                            |
                                   +-------------------+
                                   | watch_coins.py    |
                                   | CLI live monitor  |
                                   +-------------------+
```

---

## Quick Start

### Prerequisites
- Python 3.10+
- CoinGlass API key ([coinglass.com](https://www.coinglass.com))
- Telegram Bot token (optional, via [@BotFather](https://t.me/BotFather))

### Install & Run

```bash
git clone https://github.com/rcz87/TELEGAS-WS.git
cd TELEGAS-WS
pip install -r requirements.txt

cp config/secrets.env.example config/secrets.env
# Edit secrets.env with your API keys

python main.py
# Dashboard: http://localhost:8081
```

### Configuration

Edit `config/config.yaml`:

```yaml
thresholds:
  liquidation_cascade: 2000000   # Base threshold (tier-specific below)
  large_order_threshold: 25000   # $25K (filter out noise)
  accumulation_ratio: 0.72       # 72% buy ratio (strong accumulation only)
  distribution_ratio: 0.28       # 28% buy ratio (strong distribution only)
  min_large_orders: 5            # Need more whale confirmation

signals:
  min_confidence: 70.0           # CVD VETO handles quality filtering
  max_signals_per_hour: 10       # Quality over quantity
  cooldown_minutes: 15           # Between same signals

monitoring:
  mode: "all"                    # Process ALL coins
  # 4-Tier classification
  tier1_symbols: [BTCUSDT, ETHUSDT]
  tier2_symbols: [SOLUSDT, BNBUSDT, XRPUSDT, ...]
  tier3_symbols: [TRUMPUSDT, HYPEUSDT, ARBUSDT, ...]
  # Tier 4 = everything else (dynamic)

  # Per-tier thresholds
  tier1_cascade: 3000000         # $3M for BTC, ETH
  tier2_cascade: 500000          # $500K for large caps
  tier3_cascade: 150000          # $150K for mid caps
  tier4_cascade: 50000           # $50K for small caps

  # Per-tier cooldowns
  tier1_cooldown_minutes: 120    # 2 hours
  tier2_cooldown_minutes: 60     # 1 hour
  tier3_cooldown_minutes: 45
  tier4_cooldown_minutes: 30

market_context:
  enabled: true
  poll_interval: 120             # 2 minutes between REST polls
  filter_mode: "normal"          # "strict" | "normal" | "permissive"
  cvd_enabled: true              # SpotCVD + FuturesCVD
  whale_enabled: true            # Hyperliquid whale alerts
  whale_veto_threshold: 5000000  # $5M+ opposing = VETO
  orderbook_enabled: true        # Bid/ask imbalance
  price_enabled: true            # Price OHLC + volume

alerts:
  urgent_threshold: 92           # Very high conviction only
  watch_threshold: 85
  info_threshold: 70             # Minimum quality

dashboard:
  api_token: "SET_A_REAL_TOKEN_HERE"
  cors_origins:
    - "http://localhost:3000"
    - "http://127.0.0.1:8081"
```

---

## Environment Variables

Create `config/secrets.env` from the example file:

```bash
cp config/secrets.env.example config/secrets.env
```

| Variable | Required | Description |
|----------|----------|-------------|
| `COINGLASS_API_KEY` | Yes | CoinGlass API key for WebSocket + REST data |
| `TELEGRAM_BOT_TOKEN` | No | Telegram bot token from [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | No | Target Telegram chat/group ID for alerts |
| `DATABASE_URL` | No | SQLite path (default: `sqlite:///data/teleglas.db`) |
| `PROMETHEUS_PORT` | No | Prometheus metrics port (default: `9090`) |

> **Note:** `secrets.env` is in `.gitignore` and will never be committed.

---

## Security Features

### Authentication & Authorization
- **Bearer Token Auth** - Protects all write operations (POST, DELETE, PATCH)
- **Timing-Safe Token Comparison** - Uses `hmac.compare_digest` to prevent timing attacks
- **First-Message WebSocket Auth** - Token sent after connect, not in URL (avoids log exposure)
- **CORS Policy** - Restricted to specific origins
- **Rate Limiting** - 30 requests per minute per IP with automatic stale IP eviction
- **Input Validation** - Regex validation + sanitization on all inputs
- **Thread-Safe** - Lock-protected shared state access, deepcopy on cross-thread reads
- **Token Placeholder Detection** - Warns if default token not changed

### Security Configuration

**1. Generate Secure API Token:**
```bash
openssl rand -hex 32
```

**2. Update `config/config.yaml`:**
```yaml
dashboard:
  api_token: "your_generated_token_here"
  cors_origins:
    - "http://127.0.0.1:8081"
    - "http://your_vps_ip:8081"
```

---

## Project Structure

```
TELEGLAS-WS/
├── main.py                       # Orchestrator + message handlers
├── watch_coins.py                # CLI live monitor (reads live_state.json)
├── requirements.txt
├── config/
│   ├── config.yaml               # All settings (4-tier, watchlist, CVD, whale)
│   ├── secrets.env.example
│   └── secrets.env               # API keys (create this)
│
├── src/
│   ├── connection/
│   │   ├── websocket_client.py   # CoinGlass WebSocket (auth via URL query param)
│   │   └── rest_poller.py        # CoinGlass REST v4 (OI/CVD/Whale/Orderbook/Price/FR)
│   │
│   ├── processors/
│   │   ├── data_validator.py     # Schema validation (accepts string numerics)
│   │   ├── buffer_manager.py     # Rolling time-series buffers per symbol
│   │   └── market_context_buffer.py  # OI/funding/CVD/whale/orderbook buffer + assessment
│   │
│   ├── analyzers/
│   │   ├── stop_hunt_detector.py # Liquidation cascade → LONG/SHORT
│   │   ├── order_flow_analyzer.py# Buy/sell whale analysis → ACCUM/DISTRIB
│   │   └── event_pattern_detector.py  # Volume spike, whale windows
│   │
│   ├── signals/
│   │   ├── signal_generator.py   # Merge analyzers → TradingSignal
│   │   ├── signal_validator.py   # Anti-spam, dedup, per-tier cooldown
│   │   ├── signal_tracker.py     # Auto WIN/LOSS after 15min
│   │   ├── confidence_scorer.py  # Adaptive confidence with tier scaling
│   │   ├── market_context_filter.py  # OI/funding signal filter + CVD VETO
│   │   └── leading_indicator_scorer.py  # CVD/OI hierarchy scorer (35/25/20pt weights)
│   │
│   ├── alerts/
│   │   ├── telegram_bot.py       # Telegram sender (reusable aiohttp session)
│   │   ├── message_formatter.py  # Data-first format: CVD/OI/whale/orderbook
│   │   └── alert_queue.py        # Priority queue with retry
│   │
│   ├── dashboard/
│   │   ├── api.py                # FastAPI REST + WebSocket endpoints
│   │   ├── server.py             # WebSocket state push + AI analysis DB
│   │   ├── _legacy/              # Previous dashboard version (preserved)
│   │   └── static/
│   │       ├── index.html        # PWA dashboard with TradingView
│   │       ├── app.js            # Alpine.js frontend with auth
│   │       ├── sw.js             # Service worker (offline-first caching)
│   │       ├── manifest.json     # PWA manifest
│   │       ├── icon-192.png      # PWA icon
│   │       └── icon-512.png      # PWA icon
│   │
│   ├── storage/
│   │   └── database.py           # SQLite async (signals, state, baselines, OI/funding)
│   │
│   └── utils/
│       ├── logger.py
│       ├── helpers.py
│       └── symbol_normalizer.py  # Multi-exchange symbol mapper (OKX/Bitget/Bybit/dYdX)
│
├── scripts/                      # Management & test scripts
│   ├── start.sh / stop.sh / restart.sh
│   ├── status.sh / logs.sh / check.sh
│   ├── update.sh / verify-deployment.sh
│   ├── test_websocket.py
│   ├── test_processors.py
│   ├── test_analyzers.py
│   ├── test_signals.py
│   └── test_alerts.py
│
├── docs/
│   └── SYSTEM_AUDIT.md           # Engineering review documentation
│
├── data/                         # Runtime data (auto-created)
│   ├── teleglas.db               # Main SQLite database
│   ├── ai_analysis.db            # AI analysis tracking database
│   └── live_state.json           # Live state for watch_coins.py
│
└── logs/                         # Runtime logs (auto-created)
```

---

## API Endpoints

```
GET  /                              # Dashboard UI (PWA, TradingView)
GET  /api/stats                     # System + analyzer statistics
GET  /api/coins                     # Monitored coins with order flow
GET  /api/signals                   # Recent signals
GET  /api/orderflow/{symbol}        # Order flow data (thread-safe)
POST /api/coins/add                 # Add coin (auth required)
DELETE /api/coins/remove/{symbol}   # Remove coin (auth required)
PATCH /api/coins/{symbol}/toggle    # Toggle alerts (auth required)
GET  /api/export/signals.csv        # CSV export of all signals
GET  /api/export/baselines.csv      # CSV export of baselines
GET  /api/stats/signals             # Signal win/loss statistics
GET  /api/signals/history           # Full signal history from DB
WS   /ws                            # WebSocket (first-message auth)
WS   /ws1                           # Live state push (every 2s)
WS   /ws3                           # 2-way CoinGlass data queries
GET  /docs                          # Auto-generated API docs
```

---

## Signal Types

| Signal | Direction | Trigger | Confidence |
|--------|-----------|---------|------------|
| **STOP_HUNT** | LONG | Short liquidation cascade → price reversal up | Tier-relative volume ratio |
| **STOP_HUNT** | SHORT | Long liquidation cascade → price reversal down | Tier-relative volume ratio |
| **ACCUMULATION** | LONG | Buy ratio > 72%, 5+ whale buys | Order count + volume ratio |
| **DISTRIBUTION** | SHORT | Sell ratio > 72%, 5+ whale sells | Order count + volume ratio |
| **WHALE_ACCUMULATION** | LONG | 5+ large buy orders in 5 minutes | Buy ratio weighted |
| **WHALE_DISTRIBUTION** | SHORT | 5+ large sell orders in 5 minutes | Sell ratio weighted |
| **VOLUME_SPIKE** | - | 3x+ normal volume in 1 minute | Spike ratio scaled |

### Leading Indicator Scoring

| Indicator | Type | Points | Description |
|-----------|------|--------|-------------|
| SpotCVD Flip | Leading | 35 | Spot CVD changes direction (strongest signal) |
| SpotCVD Sustained | Leading | 20 | CVD continues in signal direction |
| FuturesCVD Flip | Leading | 25 | Futures CVD changes direction |
| FuturesCVD Sustained | Leading | 15 | Futures CVD continues in direction |
| OI Spike | Leading | 20 | Open Interest spike detected |
| OI Sustained | Leading | 10 | OI continues rising |
| Orderbook Dominant | Leading | 10 | Bid/ask imbalance confirms direction |
| Funding Rate | Lagging | 8 | Crowding indicator |
| Taker Volume | Lagging | 8 | Aggressive buying/selling |
| Momentum | Lagging | 5 | Price momentum confirmation |
| Liquidation Trigger | Lagging | 7 | Cascade event |

**Score Labels:** 80+ = EXECUTION READY | 60-79 = HIGH CONFIDENCE | 40-59 = WATCH | <40 = MONITOR

### Market Context Filter

All signals pass through the market context filter before reaching Telegram:

| Assessment | Action | Confidence Adj | Condition |
|------------|--------|---------------|-----------|
| **FAVORABLE** | Pass | +5 | Funding opposes crowded side + OI confirming |
| **NEUTRAL** | Pass | 0 to +2 | Insufficient signal or mixed indicators |
| **UNFAVORABLE** | Block Telegram | -10 | Funding shows same-side crowding |
| **CVD VETO** | Hard Block | - | SpotCVD flip opposing signal direction |
| **WHALE VETO** | Hard Block | - | $5M+ Hyperliquid position opposing signal |

Filter modes: `strict` (only FAVORABLE passes), `normal` (block UNFAVORABLE), `permissive` (pass all, adjust confidence only)

Blocked signals still appear on the web dashboard — only Telegram delivery is filtered.

---

## Deployment (Production VPS)

### Prerequisites
- Ubuntu 20.04+ VPS with 1GB+ RAM
- Node.js 16+ (for PM2 process manager)
- Python 3.10+

### Setup

```bash
# 1. Clone and install
git clone https://github.com/rcz87/TELEGAS-WS.git
cd TELEGAS-WS
pip3 install -r requirements.txt

# 2. Configure
cp config/secrets.env.example config/secrets.env
nano config/secrets.env          # Add API keys
nano config/config.yaml          # Set api_token, cors_origins with your VPS IP

# 3. Create required directories
mkdir -p logs data

# 4. Install PM2
npm install -g pm2

# 5. Start with PM2
./scripts/start.sh               # or: pm2 start ecosystem.config.js

# 6. Enable auto-start on reboot
pm2 save && pm2 startup

# 7. Configure firewall
ufw allow 22/tcp                 # SSH
ufw allow 8081/tcp               # Dashboard
ufw enable
```

### Management Scripts

| Script | Description |
|--------|-------------|
| `./scripts/start.sh` | Start via PM2 |
| `./scripts/stop.sh` | Stop the process |
| `./scripts/restart.sh` | Restart the process |
| `./scripts/status.sh` | Show PM2 status |
| `./scripts/logs.sh` | Tail live logs |
| `./scripts/update.sh` | Pull from GitHub + restart |
| `./scripts/check.sh` | System health check |
| `./scripts/verify-deployment.sh` | Full deployment verification (30+ checks) |

### CLI Live Monitor

```bash
# Watch specific coins
python watch_coins.py SOL BTC ETH

# Watch ALL coins ranked by score
python watch_coins.py
```

Shows per-coin: price, bias, SpotCVD slope, FutCVD, OI change, funding rate, orderbook imbalance. Refreshes every 5 seconds.

### PM2 Configuration

`ecosystem.config.js` provides:
- Auto-restart on crash (max 10 restarts with 4s delay)
- Memory limit: 1GB (auto-restart if exceeded)
- Log files: `logs/output.log`, `logs/error.log`
- Timestamped logs with merge

---

## Testing

### Component Test Scripts

```bash
# Test WebSocket connection to CoinGlass
python scripts/test_websocket.py

# Test data processors (validator + buffer)
python scripts/test_processors.py

# Test analyzers (stop hunt, order flow, events)
python scripts/test_analyzers.py

# Test signal pipeline (generator + validator + scorer)
python scripts/test_signals.py

# Test alert system (telegram + queue)
python scripts/test_alerts.py
```

> **Note:** `test_websocket.py` requires a valid `COINGLASS_API_KEY` in `config/secrets.env`.

### Deployment Verification

```bash
# Full system verification (dependencies, config, firewall, PM2, resources)
./scripts/verify-deployment.sh
```

---

## Engineering Review Log

### v4.2 (Current) - 4-Tier System, PWA Dashboard, CVD VETO, Leading Indicators

**Session 10** - Major upgrade: leading indicators + 4-tier + PWA
- Feature: Leading Indicator Scorer — CVD/OI hierarchy replaces lagging-only scoring (35/25/20pt weights)
- Feature: 4-tier coin classification (mega/large/mid/small) with per-tier thresholds, cooldowns, volume gates
- Feature: CVD VETO — SpotCVD flip opposing signal = hard block
- Feature: Whale VETO — $5M+ Hyperliquid position opposing = hard block
- Feature: PWA dashboard with TradingView charts, service worker, offline support
- Feature: Proactive watchlist scanner (80+ coins polled every 2 min)
- Feature: watch_coins.py CLI live monitor
- Feature: Symbol normalizer for multi-exchange formats (OKX, Bitget, Bybit, dYdX, Hyperliquid)
- Feature: AI analysis database with regime/bias/grade tracking + 15m/30m/60m outcome tracking
- Feature: Data-first Telegram format (raw CVD/OI/whale/orderbook shown, system bias as reference)
- Feature: Per-exchange funding rates in alerts
- Feature: REST poller expanded: SpotCVD, FuturesCVD, whale alerts, orderbook delta, price OHLC
- Tuning: Stricter accumulation/distribution (72%/28%), 5 min whale orders, $25K large order threshold
- Tuning: Per-tier cooldowns (2hr/1hr/45min/30min), stricter alert priorities (URGENT 92%, WATCH 85%)
- Tuning: Poll interval 5min → 2min, max signals 50 → 10/hour, cooldown 5min → 15min

### v4.1 - Market Context Filter + 70+ fixes across 9 sessions

**Session 1** - Initial security review (8 fixes)
- Input sanitization, rate limiting, auth on all endpoints
- Graceful shutdown with timeouts, config validation

**Session 2** - Core value improvements (3 major)
- Data-driven Entry/SL/Target from price zones (was hardcoded)
- SignalTracker auto WIN/LOSS with 15min check
- Rolling baselines for confidence scoring

**Session 3** - All-coin monitoring
- Removed symbol filter gate, ALL coins now processed
- Dynamic tiered thresholds, dashboard toggle wired to WebSocket

**Session 4** - SQLite persistent storage
- Created `src/storage/database.py` with 4 tables
- CSV export endpoints, state restore on startup

**Session 5** - Bug sweep (15 fixes)
- Tier-aware scoring across OrderFlowAnalyzer, ConfidenceScorer
- Dashboard auth on all endpoints + frontend Bearer token
- WebSocket reconnect after 3 consecutive timeouts
- Removed 3 dead code files (message_parser, heartbeat_manager, subscription_manager)
- Wired all get_stats() to /api/stats

**Session 6** - Second review (14 fixes)
- SignalTracker: require >= 50% to target for WIN (was $1 above entry)
- track_signal() returns ref (was fragile _pending[-1])
- HTML-escape token injection, state_lock on dashboard reads
- Volume spike uses actual timespan, isalnum() for numeric symbols
- add_coin duplicate check inside lock, alignment bonus scaled
- WebSocket first-message auth (token not in URL), placeholder detection
- Config absolute paths, monitoring in required_sections

**Session 6b** - SHORT signal fix
- Added `direction` to stop_hunt metadata (formatter always calculated LONG)
- LONG/SHORT label + correct +/- on target percentages

**Session 6c** - P0 pipeline fix
- CoinGlass sends price/vol as strings but schema expected (int,float) — ALL events were silently rejected. Fixed: accept (int, float, str)
- Alert processor marks failed alerts properly
- Bare except: replaced with except RuntimeError:

**Session 7** - Comprehensive review & bug sweep (16 files, P0/P1/P2)
- P0: Fixed infinite retry loop in alert_queue (retry_count never incremented)
- P0: Converted recursive websocket reconnect to iterative loop (prevented RecursionError)
- P0: Timing-safe token comparison (`hmac.compare_digest`), rate limiter IP eviction, thread-safe broadcast from sync thread
- P0: Removed hardcoded VPS IP from config, scripts, and dashboard_preview
- P1: Fixed SHORT entry level using wrong price zone boundary
- P1: Added `threading.Lock` to buffer_manager for full thread safety
- P1: Standardized all `datetime.now()` → `datetime.now(timezone.utc)` across 7 files
- P1: Fixed volume spike self-dilution (excludes recent 1-min from baseline)
- P2: Added WHALE_DISTRIBUTION detection (was only ACCUMULATION — bullish bias)
- P2: Tier-aware `large_order_threshold` ($10K/$5K/$2K by tier)
- P2: Reusable `aiohttp.ClientSession` in telegram_bot (was creating per message)
- P2: Atomic transactions + connection guard in database.py
- P2: Consistent `vol` field name across all analyzers (was mixed `volume_usd`/`vol`)

**Session 8** - Final remaining bugs (5 fixes)
- P0: Fixed `signal_tracker.get_overall_stats()` → `get_stats()` (wrong method name crashed stats_reporter every 30s)
- P1: Thread-safe locking on all buffer_manager read methods
- P2: Fixed unreliable periodic logging (elapsed-time tracking)
- P2: Added `close()` method to telegram_bot for session cleanup
- P2: Fixed test_signals.py `MockOrderFlowSignal` missing attributes

**Session 9** - Market Context Filter + CoinGlass API alignment
- Feature: OI + Funding Rate market context filter (3 new modules)
- Feature: CoinGlass REST API v4 poller (OI + funding rate every 5 min)
- Feature: Market context assessment (FAVORABLE/NEUTRAL/UNFAVORABLE)
- Feature: Three filter modes (strict/normal/permissive)
- Feature: Market context section in Telegram messages
- Feature: Smart price formatting (BTC $96,200 to PEPE $0.00001182)
- P0: Fixed WebSocket auth (API key via URL query param per CoinGlass docs)
- P0: Fixed field name mismatch (volUsd/exName → vol/exchange normalization)
- P1: Trade subscription minVol @0 → @10000 (filter noise)
- P1: Price formatting for sub-penny coins
- DB: Added oi_snapshots and funding_snapshots tables

---

## Status

**Current Version:** 4.2.0
**Status:** Production Ready
**Last Updated:** March 31, 2026

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## Troubleshooting

| Problem | Cause | Solution |
|---------|-------|----------|
| No signals generated | Events silently rejected | Check logs for "validation" errors. CoinGlass sends numeric strings — handled since v4.0 |
| Dashboard not loading | Port blocked or wrong CORS | `ufw allow 8081/tcp`, add VPS IP to `cors_origins` in `config.yaml` |
| WebSocket keeps reconnecting | Invalid API key or network issue | Verify `COINGLASS_API_KEY` in `secrets.env`, check `pm2 logs` |
| Telegram alerts not sending | Wrong token or chat ID | Test with `python scripts/test_alerts.py`, verify bot is added to chat |
| "Token not configured" warning | Using placeholder token | Generate a real token: `openssl rand -hex 32`, update `config.yaml` |
| High memory usage | Buffer accumulation | Restart with `./scripts/restart.sh`, PM2 auto-restarts at 1GB |
| SQLite locked errors | Concurrent write attempts | Uses atomic transactions and connection guards since v4.0 |
| CVD data stale (>2min) | REST poll delayed | Check CoinGlass API key quota, verify poll_interval in config |
| Too few signals | Strict thresholds | Lower accumulation_ratio, reduce cooldown_minutes, try filter_mode: "permissive" |
| Too many signals | Loose thresholds | Raise min_confidence, increase cooldown, use filter_mode: "strict" |

---

## Disclaimer

> **This software is for informational and educational purposes only.** It is not financial advice. Cryptocurrency trading involves substantial risk of loss. Past performance of signals does not guarantee future results. The authors are not responsible for any financial losses incurred from using this system. Always do your own research (DYOR) and never trade with funds you cannot afford to lose.

---

**Built for crypto traders who need an edge.**
