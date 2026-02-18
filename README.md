# TELEGLAS Pro - Real-Time Trading Intelligence System

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.108.0-green.svg)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

> **Professional cryptocurrency trading intelligence system providing 30-90 second information edge through real-time stop hunt detection, order flow analysis, and event pattern recognition.**

---

## Features

### Core Intelligence
- **Stop Hunt Detection** - Identify liquidation cascades in real-time with tier-aware thresholds
- **Order Flow Analysis** - Track whale accumulation and distribution patterns
- **Event Pattern Detection** - Volume spikes, whale accumulation & distribution windows
- **Market Context Filter** - OI + Funding Rate from CoinGlass REST API v4 filters false signals
- **LONG + SHORT Signals** - Both directions fully supported across entire pipeline
- **Adaptive Confidence Scoring** - Learns from signal outcomes, tier-aware quality boost
- **Anti-Spam System** - Dedup (5% confidence bands), cooldown, rate limiting

### All-Coin Monitoring
- **Every Coin Detected** - Processes ALL liquidation events from CoinGlass, not just configured pairs
- **Dynamic Tier Thresholds** - BTC/ETH ($2M), mid-caps ($200K), small coins ($50K)
- **Auto-Discovery** - New coins detected automatically from liquidation data
- **Fair Scoring** - Volume ratios relative to tier threshold (small coin $500K = same score as BTC $10M)

### Web Dashboard
- **Real-Time Updates** - WebSocket-powered live data with first-message protocol auth
- **Dynamic Coin Management** - Add/remove/toggle pairs without restart
- **Order Flow Visualization** - Buy/sell ratio progress bars
- **Live Signal Feed** - Real-time trading signals with confidence scores
- **CSV Export** - Download signal history and baselines as spreadsheets
- **Mobile-Responsive** - Works on desktop, tablet, and phone

### Persistent Storage (SQLite)
- **Signal History** - All generated signals saved with outcome tracking
- **Auto WIN/LOSS** - SignalTracker evaluates after 15min, requires >= 50% to target for WIN
- **OI & Funding Snapshots** - Historical OI and funding rate data persisted (7-day retention)
- **State Restore** - Confidence state, dashboard coins, baselines survive restart
- **CSV Export** - `/api/export/signals.csv`, `/api/export/baselines.csv`

### Alert System
- **Telegram Integration** - Professional formatting with LONG/SHORT labels and correct +/- targets
- **Market Context in Messages** - OI/funding alignment shown on every Telegram alert
- **Smart Price Formatting** - Works across all price scales (BTC $96,200 to PEPE $0.00001182)
- **Priority Queue** - Urgent/watch/info classification with retry logic
- **Rate Limiting** - Configurable max signals per hour + per-symbol cooldown

### Production Features
- **Auto-Reconnect** - Iterative loop with exponential backoff + force reconnect after 3 consecutive timeouts
- **Thread Safety** - All dashboard state access under locks, deepcopy on reads
- **Graceful Shutdown** - Timeout on all async tasks, state saved to DB before exit
- **Absolute Config Paths** - Works regardless of working directory

---

## Architecture

```
CoinGlass WebSocket API                    CoinGlass REST API v4
  |                                              |
  |  liquidationOrders (ALL coins)               |  OI aggregated-history
  |  futures_trades@all_{symbol}@10000           |  Funding rate oi-weight-history
  v                                              v
+---------------------------------------------------------+
|  TELEGLAS Pro Pipeline                                   |
|                                                          |
|  1. WebSocket Client     - Auth via URL query param      |
|  2. Field Normalizer     - volUsd→vol, exName→exchange   |
|  3. Data Validator       - Schema validation (str types) |
|  4. Buffer Manager       - Rolling time-series per coin  |
|  5. Analyzers            - StopHunt, OrderFlow, Events  |
|  6. Signal Generator     - Merge + direction + metadata  |
|  7. Signal Validator     - Dedup, cooldown, rate limit   |
|  8. Confidence Scorer    - Tier-aware adaptive scoring   |
|  9. Market Context Filter - OI/Funding alignment check   |
|                                                          |
|  REST Poller: 5-min polling for OI + funding rate        |
|  Storage: SQLite (signals, state, baselines, OI/funding) |
+-----------+-------------------------------+--------------+
            |                               |
            v                               v
     +-----------+                 +------------------+
     | Telegram  |                 | Web Dashboard    |
     | - Alerts  |                 | - localhost:8080 |
     | - Context |                 | - REST + WS API  |
     +-----------+                 +------------------+
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
# Dashboard: http://localhost:8080
```

### Configuration

Edit `config/config.yaml`:

```yaml
pairs:
  primary: [BTCUSDT, ETHUSDT, BNBUSDT]
  secondary: [SOLUSDT, ADAUSDT, DOGEUSDT, AVAXUSDT]

thresholds:
  liquidation_cascade: 2000000  # $2M (tier1, see monitoring for tier2/3)
  large_order_threshold: 10000  # $10K tier1, $5K tier2, $2K tier3 (auto)

signals:
  min_confidence: 70.0
  max_signals_per_hour: 50
  cooldown_minutes: 5

monitoring:
  mode: "all"                   # Process ALL coins
  tier1_symbols: [BTCUSDT, ETHUSDT]
  tier2_symbols: [BNBUSDT, SOLUSDT, XRPUSDT, ...]
  tier1_cascade: 2000000        # $2M for BTC, ETH
  tier2_cascade: 200000         # $200K for mid-caps
  tier3_cascade: 50000          # $50K for small coins

market_context:
  enabled: true
  poll_interval: 300            # 5 minutes between REST API polls
  max_snapshots: 72             # 6 hours buffer at 5-min intervals
  filter_mode: "normal"         # "strict" | "normal" | "permissive"
  confidence_adjust: true       # +5 FAVORABLE, -10 UNFAVORABLE

dashboard:
  api_token: "SET_A_REAL_TOKEN_HERE"  # Required for auth
```

---

## Environment Variables

Create `config/secrets.env` from the example file:

```bash
cp config/secrets.env.example config/secrets.env
```

| Variable | Required | Description |
|----------|----------|-------------|
| `COINGLASS_API_KEY` | Yes | CoinGlass API key for WebSocket data feed |
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
    - "http://localhost:8080"
    - "http://your_vps_ip:8080"
```

---

## Project Structure

```
TELEGLAS-WS/
├── main.py                       # Orchestrator + message handlers
├── requirements.txt
├── config/
│   ├── config.yaml               # All settings including tier thresholds
│   ├── secrets.env.example
│   └── secrets.env               # API keys (create this)
│
├── src/
│   ├── connection/
│   │   ├── websocket_client.py   # CoinGlass WebSocket (auth via URL query param)
│   │   └── rest_poller.py        # CoinGlass REST API v4 (OI + funding rate)
│   │
│   ├── processors/
│   │   ├── data_validator.py     # Schema validation (accepts string numerics)
│   │   ├── buffer_manager.py     # Rolling time-series buffers per symbol
│   │   └── market_context_buffer.py  # OI/funding buffer + context assessment
│   │
│   ├── analyzers/
│   │   ├── stop_hunt_detector.py # Liquidation cascade → LONG/SHORT
│   │   ├── order_flow_analyzer.py# Buy/sell whale analysis → ACCUM/DISTRIB
│   │   └── event_pattern_detector.py  # Volume spike, whale windows
│   │
│   ├── signals/
│   │   ├── signal_generator.py   # Merge analyzers → TradingSignal
│   │   ├── signal_validator.py   # Anti-spam, dedup, cooldown
│   │   ├── signal_tracker.py     # Auto WIN/LOSS after 15min
│   │   ├── confidence_scorer.py  # Adaptive confidence with tier scaling
│   │   └── market_context_filter.py  # OI/funding signal filter
│   │
│   ├── alerts/
│   │   ├── telegram_bot.py       # Telegram sender
│   │   ├── message_formatter.py  # Smart price formatting, market context
│   │   └── alert_queue.py        # Priority queue with retry
│   │
│   ├── dashboard/
│   │   ├── api.py                # FastAPI REST + WebSocket endpoints
│   │   └── static/
│   │       ├── index.html
│   │       ├── app.js            # Alpine.js frontend with auth
│   │       └── manifest.json
│   │
│   ├── storage/
│   │   └── database.py           # SQLite async (signals, state, baselines, OI/funding)
│   │
│   └── utils/
│       ├── logger.py
│       └── helpers.py
│
├── scripts/                      # Test scripts
│   ├── test_websocket.py
│   ├── test_processors.py
│   ├── test_analyzers.py
│   ├── test_signals.py
│   └── test_alerts.py
│
└── data/                         # SQLite DB (auto-created)
    └── teleglas.db
```

---

## API Endpoints

```
GET  /                              # Dashboard UI (token injected)
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
GET  /docs                          # Auto-generated API docs
```

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
ufw allow 8080/tcp               # Dashboard
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

## Signal Types

| Signal | Direction | Trigger | Confidence |
|--------|-----------|---------|------------|
| **STOP_HUNT** | LONG | Short liquidation cascade → price reversal up | Tier-relative volume ratio |
| **STOP_HUNT** | SHORT | Long liquidation cascade → price reversal down | Tier-relative volume ratio |
| **ACCUMULATION** | LONG | Buy ratio > 65%, whale buys dominant | Order count + volume ratio |
| **DISTRIBUTION** | SHORT | Sell ratio > 65%, whale sells dominant | Order count + volume ratio |
| **WHALE_ACCUMULATION** | LONG | 5+ large buy orders in 5 minutes | Buy ratio weighted |
| **WHALE_DISTRIBUTION** | SHORT | 5+ large sell orders in 5 minutes | Sell ratio weighted |
| **VOLUME_SPIKE** | - | 3x+ normal volume in 1 minute | Spike ratio scaled |

### Market Context Filter

All signals pass through the market context filter before reaching Telegram:

| Assessment | Action | Confidence Adj | Condition |
|------------|--------|---------------|-----------|
| **FAVORABLE** | Pass | +5 | Funding opposes crowded side + OI confirming |
| **NEUTRAL** | Pass | 0 to +2 | Insufficient signal or mixed indicators |
| **UNFAVORABLE** | Block Telegram | -10 | Funding shows same-side crowding |

Filter modes: `strict` (only FAVORABLE passes), `normal` (block UNFAVORABLE), `permissive` (pass all, adjust confidence only)

Blocked signals still appear on the web dashboard — only Telegram delivery is filtered.

---

## Engineering Review Log

### v4.1 (Current) - Market Context Filter + 70+ fixes across 9 sessions

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
- P0: Fixed `signal_tracker.get_overall_stats()` → `get_stats()` (wrong method name crashed stats_reporter every 30s, dashboard never received updated statistics)
- P1: Added thread-safe locking to all buffer_manager read methods (`get_liquidations`, `get_trades`, `get_all_*`, `cleanup_old_data`) — prevented `RuntimeError: deque mutated during iteration` under concurrent load
- P2: Fixed unreliable periodic logging (`int(uptime) % 300 == 0` → elapsed-time tracking so 5-minute detailed log actually fires)
- P2: Added `close()` method to telegram_bot for aiohttp session cleanup on shutdown (prevented resource leak warning)
- P2: Fixed test_signals.py `MockOrderFlowSignal` missing `buy_volume`/`sell_volume` attributes (integration test was silently failing)

**Session 9** - Market Context Filter + CoinGlass API alignment
- Feature: Added OI + Funding Rate market context filter (3 new modules: `rest_poller.py`, `market_context_buffer.py`, `market_context_filter.py`)
- Feature: CoinGlass REST API v4 poller — polls OI aggregated-history and funding rate oi-weight-history every 5 minutes (OHLC candle format)
- Feature: Market context assessment (FAVORABLE/NEUTRAL/UNFAVORABLE) with confidence adjustment (+5/-10)
- Feature: Three filter modes (strict/normal/permissive) configurable via `config.yaml`
- Feature: Market context section in all Telegram messages (funding rate + OI alignment indicators)
- Feature: Smart price formatting — works from BTC ($96,200) to PEPE ($0.00001182)
- P0: Fixed WebSocket auth — API key now via URL query param `?cg-api-key=` per CoinGlass docs (was using non-existent login event)
- P0: Fixed field name mismatch — CoinGlass sends `volUsd`/`exName` but code expected `vol`/`exchange` (all analysis silently returned zero volumes). Added normalization at ingestion
- P1: Changed trade subscription minVol from `@0` to `@10000` to filter noise (only trades >= $10K)
- P1: Fixed price formatting for small coins — `$0.00001220` was displayed as `$0` (hardcoded `:,.0f` format)
- P1: Fixed `risk = max(..., 1)` hardcode that broke trading setup calculation for coins < $1
- DB: Added `oi_snapshots` and `funding_snapshots` tables with auto-cleanup (7-day retention)

---

## Status

**Current Version:** 4.1.0
**Status:** Production Ready
**Last Updated:** February 18, 2026

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## Troubleshooting

| Problem | Cause | Solution |
|---------|-------|----------|
| No signals generated | Events silently rejected | Check logs for "validation" errors. CoinGlass sends numeric strings — v4.0 handles this |
| Dashboard not loading | Port blocked or wrong CORS | `ufw allow 8080/tcp`, add VPS IP to `cors_origins` in `config.yaml` |
| WebSocket keeps reconnecting | Invalid API key or network issue | Verify `COINGLASS_API_KEY` in `secrets.env`, check `pm2 logs` |
| Telegram alerts not sending | Wrong token or chat ID | Test with `python scripts/test_alerts.py`, verify bot is added to chat |
| "Token not configured" warning | Using placeholder token | Generate a real token: `openssl rand -hex 32`, update `config.yaml` |
| High memory usage | Buffer accumulation | Restart with `./scripts/restart.sh`, PM2 auto-restarts at 1GB |
| SQLite locked errors | Concurrent write attempts | v4.0 uses atomic transactions and connection guards to prevent this |

---

## Disclaimer

> **This software is for informational and educational purposes only.** It is not financial advice. Cryptocurrency trading involves substantial risk of loss. Past performance of signals does not guarantee future results. The authors are not responsible for any financial losses incurred from using this system. Always do your own research (DYOR) and never trade with funds you cannot afford to lose.

---

**Built for crypto traders who need an edge.**
