# TELEGLAS Pro — Project Guide for Claude

## Project Overview
Real-time cryptocurrency trading intelligence system. Detects stop hunts, order flow patterns, and market structure shifts across 80+ coins. Sends alerts via Telegram and displays analysis on a web dashboard.

## Architecture
- **main.py**: Central orchestrator — WebSocket ingestion → analyzers → signal pipeline → Telegram/dashboard
- **Legacy API** (`src/dashboard/_legacy/api.py`): FastAPI on port 8081 (localhost only) — signal endpoints, lifecycle, calibration
- **Dashboard** (`src/dashboard/server.py`): FastAPI on port 8082 (public) — serves UI, proxies lifecycle endpoints from 8081
- **Frontend**: `src/dashboard/static/index.html` — single-file PWA with TradingView charts

## Critical Deployment Detail
PM2 runs `/root/tg-dashboard/server.py`, which is a **symlink** to the repo copy.
No manual sync needed — editing repo files updates production automatically.
After editing dashboard files, just restart:
```bash
pm2 restart tg-dashboard
```
Main system: `pm2 restart teleglas`

## Production URL
Dashboard: https://teleglas.guardiansofthetoken.org/dashboard/

## Tech Stack
- Python 3.12, FastAPI, aiosqlite, aiohttp, websockets
- scikit-learn, pandas, numpy (ML layer)
- TradingView widget, JetBrains Mono font, PWA with service worker
- PM2 process manager, SQLite storage

## Key Config Files
- `config/config.yaml` — all thresholds, tiers, signals, ML mode, lifecycle expiry
- `config/secrets.env` — API keys (never commit)

---

# Dashboard Guide — How to Analyze Coins

## Layout (Top to Bottom)

### 1. HEADER
Connection status (`LIVE` / `RECONNECTING` / `OFFLINE`) and last data timestamp. If not `LIVE`, data is stale — do not make decisions.

### 2. BTC MACRO TICKER
BTC price, 24h change, OI, funding rate as market barometer.
- BTC up + OI up = risk-on, altcoins follow
- BTC down + OI drop = risk-off / deleveraging
- BTC sideways + OI flat = range, look for coin-specific setups

### 3. COIN SELECTOR
Quick pills: SOL, ETH, AVAX, SUI, HYPE, XRP, BNB. Search box for others. Search results ranked by highest score.

### 4. COIN HEADER
Selected coin: name, bias badge (LONG green / SHORT red / NEUTRAL gray), price, 24h change.

### 5. TRADINGVIEW CHART
Binance Perpetual full-size chart. Use for price action — support/resistance, trend, patterns. Dashboard provides the *why* (data), chart provides the *where* (levels).

### 6A. REGIME + SNIPER DECISION (Most Important Section)

#### REGIME DETECTION
System detects one of 5 regimes:
- **ACCUMULATION** — smart money accumulating, price sideways/slow rise
- **DISTRIBUTION** — smart money distributing, price weakening
- **LIQUIDATION** — forced selling/buying, OI dropping, price flushing
- **SQUEEZE** — extreme funding + crowded positions, ready to squeeze
- **RANGE** — no edge, conflicting CVD, flat OI

#### STATUS
- **READY** (Grade A) = setup worth executing
- **WATCH** (Grade B) = interesting but incomplete
- **WAIT** (Grade C) = no setup yet
- **AVOID** (Grade D) = bad conditions

#### CONFIDENCE + LONG/SHORT SCORE
- Confidence: how certain the system is about this regime
- Long Score / Short Score: directional score breakdown per indicator (0-100)

#### TRIGGER
Specific conditions that must be met before entry. Examples:
- "SpotCVD sustained rise + OI break above recent high"
- "FutCVD RISING sustain + funding staying negative"

**Wait for trigger before entering.**

#### INVALIDATION
Conditions that cancel the setup. If this happens after entry = cut loss signal.

#### VETO RULES
Built-in safety: even Grade A gets auto-downgraded to B if contradictions exist (OI spike too fast for accumulation, price already extended, etc).

#### AI ANALYSIS
"ANALISA AI" button calls Claude for narrative analysis — combines all data into human-readable summary. Uses Anthropic API key (server-side or user-provided).

### 6B. ANALYSIS GRID (3 Columns)

#### ORDERFLOW INTELLIGENCE (spans 2 columns)
Most leading data on the dashboard:
- **Spot CVD**: Cumulative Volume Delta on spot. RISING = buy pressure, FALLING = sell pressure
- **Futures CVD**: Same for derivatives
- **Dominance bar**: Who leads — spot or futures? Spot leading = more organic
- **Tags**: Signal summaries (e.g., "CVD ALIGNED BULLISH", "OI BUILDING")
- **Insight text**: Auto-generated narrative from all flow data

Key reading:
- SpotCVD RISING + FutCVD RISING = strong buy signal
- SpotCVD RISING + FutCVD FALLING = divergence (caution)
- Both FALLING = strong sell signal

#### MARKET STRUCTURE (1 column)
- **Open Interest**: OI up = new money entering, OI down = positions closing
- **Funding Rate**: positive = longs pay shorts (crowded long), negative = opposite
- **Orderbook**: bid vs ask volume ratio
- **Positioning**: Long/Short ratio gauges

OI + Price interpretation:

| OI | Price | Meaning |
|---|---|---|
| Up | Up | MOMENTUM — new positions entering with trend |
| Up | Down | SHORT ADDING — aggressive shorts entering |
| Down | Up | SHORT COVERING — shorts closing (less reliable) |
| Down | Down | DELEVERAGING — forced liquidation |

#### Additional Cards:
- **TAKER + L/S**: Taker buy vs sell volume + long/short ratio gauges
- **LIQUIDITY**: Liquidation clusters above/below price — levels where cascades will happen
- **CVD HISTORY**: Sparkline history of SpotCVD and FuturesCVD — look at trend, not just snapshot

### 7. WHALE INTELLIGENCE

#### ALERTS tab
Whale movements from Hyperliquid — open/close, size, direction.
- **MEGA** (purple) = >$10M position
- **BIG** (blue) = >$1M position

MEGA whale opening LONG while SpotCVD also RISING → strong confirmation. Whale opposing your setup → caution.

#### POSITIONS tab
Active whale positions with entry price, liquidation price, size. Filter: ALL / LONG / SHORT.

Big whale near liquidation price = expect high volatility at that level.

### 7.5. SIGNAL INTELLIGENCE

Backend signal lifecycle panel:
- **Primary Signal Card**: One main signal per coin — direction, confidence, effective score, freshness bar, expiry countdown
- **Status**: ACTIVE (valid) / WEAKENING (near expiry) / SUPERSEDED (replaced by stronger signal)
- **Recent history**: Recently expired/superseded signals for context
- **All Active Signals**: All coins with active signals, sorted by effective score — click to switch

Reading freshness:
- Green bar = signal fresh, trustworthy
- Yellow bar = aging, verify with live data
- Red bar = near expiry, don't enter new positions based on this
- SUPERSEDED = replaced by stronger signal — read the reason

### 8. FR EXTREMES
Funding rate comparison across exchanges. One exchange with much higher/lower FR than average → crowding on that exchange or arbitrage opportunity.

---

## Optimal Analysis Workflow

1. **Macro check**: BTC ticker — risk-on or risk-off?
2. **Pick coin**: Check All Active Signals panel for highest effective scores, or browse coin selector
3. **Read regime**: ACCUMULATION/DISTRIBUTION/LIQUIDATION/SQUEEZE/RANGE? Status READY/WATCH/WAIT/AVOID?
4. **Read data flow**: SpotCVD + FuturesCVD aligned? OI supporting? Funding rate helping or opposing?
5. **Check whales**: Mega whale confirming or contradicting bias?
6. **Read trigger**: Trigger condition met? If not, **wait**.
7. **Check signal lifecycle**: Active primary signal with high freshness? Confidence level?
8. **Entry decision**: All aligned → enter per trigger. Conflict → skip, find another coin.
9. **Set invalidation**: Use invalidation condition from regime card as stop loss logic.

## What NOT to Do
- Don't enter just because of LONG/SHORT badge — it's bias, not a signal
- Don't enter when status is WAIT or AVOID
- Don't ignore VETO — system already checked for contradictions
- Don't enter when spot and futures CVD are opposing (divergence)
- Don't enter on signals with red freshness bar (near expiry)
- Don't ignore whales opposing your position
- Don't hold if invalidation condition is met

---

# Self-Learning System

## Current Learning Architecture
Signal → setup_key classified → confidence adjusted (setup-level learning) → 40+ features logged → ML model scores (shadow mode) → lifecycle manager → outcome evaluated after 15min (MFE/MAE/excursion) → feedback to confidence_scorer + ML guardrails → calibration rebuilt every 6h

## Key Files
| File | Purpose |
|---|---|
| `src/signals/setup_classifier.py` | Classifies signals into granular setup_keys |
| `src/signals/outcome_evaluator.py` | MFE/MAE/excursion ratio evaluation |
| `src/signals/feature_logger.py` | Logs 40+ features per signal to DB |
| `src/signals/confidence_scorer.py` | Dual-level learning (setup + type fallback) |
| `src/signals/signal_tracker.py` | Tracks outcomes, feeds back to scorer |
| `src/signals/signal_lifecycle.py` | Expiry, primary selection, anti-flip |
| `src/ml/model_trainer.py` | Trains GradientBoosting/LogisticRegression |
| `src/ml/ml_inference.py` | Scores signals (shadow/blended/advisory) |
| `src/ml/calibration.py` | Maps score buckets to observed win rates |
| `src/ml/guardrails.py` | AUC gate, ±10 clamp, auto-rollback |
| `src/ml/dataset_builder.py` | Builds pandas DataFrames for training |

## ML Mode (config.yaml)
```yaml
ml:
  mode: "shadow"   # shadow=log only, blended=affects confidence, advisory=dashboard only, off=disabled
  weight: 0.3      # ML weight in blended mode
```

## Training a Model
```python
from src.ml.model_trainer import ModelTrainer
trainer = ModelTrainer(db=your_db)
result = await trainer.train()  # needs 30+ labeled signals
```

## 4-Tier Coin Classification
- Tier 1 (Mega): BTC, ETH — $3M cascade, 2hr cooldown
- Tier 2 (Large): SOL, BNB, XRP, etc. — $500K cascade, 1hr cooldown
- Tier 3 (Mid): TRUMP, HYPE, ARB, etc. — $150K cascade, 45min cooldown
- Tier 4 (Small): everything else — $50K cascade, 30min cooldown

## API Endpoints
```
GET /api/signals/active          — all alive primary signals
GET /api/signals/primary/{symbol} — primary + history for one coin
GET /api/signals/lifecycle       — full overview all coins
GET /api/calibration             — score-to-win-rate mapping
GET /api/stats                   — system statistics
GET /api/coins                   — monitored coins
GET /metrics                     — Prometheus metrics
```

## Database Tables
- `signals` — signal history with outcomes
- `signal_features` — 40+ features per signal (ML training data)
- `setup_state` — setup-level learning persistence
- `confidence_state` — type-level learning persistence
- `oi_snapshots`, `funding_snapshots` — market data history

## Confidence Score System
Score range: 55-99. Not a probability — it's a composite quality ranking:
1. Base confidence from analyzers (pattern strength)
2. Weighted merge: StopHunt 50%, OrderFlow 35%, Events 15%
3. Historical win rate adjustment (setup-level if ≥5 samples, else type-level)
4. Quality boost from metadata (absorption, directionality, whale count)
5. Combo bonus (CVD + orderbook alignment: +10/+20)
6. Market context filter (+5 favorable, -10 unfavorable, CVD/whale VETO = hard block)
7. Leading indicator override (if leading score > confidence)
8. ML blending (in blended mode: 70% rule + 30% ML, capped ±10)

Alert thresholds: URGENT ≥92, WATCH ≥85, INFO ≥70
