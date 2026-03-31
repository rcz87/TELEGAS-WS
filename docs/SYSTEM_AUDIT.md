# TELEGLAS Pro — System Audit & Dashboard Blueprint

> **Tanggal:** 30 Maret 2026
> **Versi:** v4.1
> **Status:** Running (uptime ~39 jam, 103 coins tracked)
> **Dashboard URL:** `https://teleglas.guardiansofthetoken.org/dashboard/`
> **Dashboard Port:** 8082 (via Nginx reverse proxy)
> **Internal API Port:** 8081 (legacy, dipakai main.py untuk signal pipeline)

---

## Daftar Isi

1. [Arsitektur Umum](#1-arsitektur-umum)
2. [Data Source](#2-data-source)
3. [Fetch Logic](#3-fetch-logic)
4. [Data Pipeline](#4-data-pipeline)
5. [Transformasi Data](#5-transformasi-data)
6. [Dashboard Rendering](#6-dashboard-rendering)
7. [Update Mechanism](#7-update-mechanism)
8. [Database Schema](#8-database-schema)
9. [API Endpoints](#9-api-endpoints)
10. [Security](#10-security)
11. [Limitasi & Masalah](#11-limitasi--masalah)
12. [Rekomendasi Pengembangan](#12-rekomendasi-pengembangan)
13. [File Map](#13-file-map)

---

## 1. Arsitektur Umum

```
┌─────────────────────────────────────────────────────────────────┐
│                      COINGLASS DATA SOURCES                     │
├────────────────────────────┬────────────────────────────────────┤
│  WebSocket (real-time)     │  REST API (polling 120 detik)      │
│  - Liquidation orders      │  - Open Interest OHLC              │
│  - Futures trades          │  - Funding Rate OHLC               │
│                            │  - Spot CVD (5m, 12 bars)          │
│                            │  - Futures CVD (5m, 12 bars)       │
│                            │  - Whale Alerts (Hyperliquid)      │
│                            │  - Orderbook Delta (bid/ask)       │
│                            │  - Per-Exchange Funding Rates      │
│                            │  - Price OHLC + Volume (1d)        │
└────────────┬───────────────┴──────────────────┬─────────────────┘
             ↓                                  ↓
┌────────────────────────┐        ┌──────────────────────────────┐
│  BufferManager         │        │  MarketContextBuffer         │
│  (in-memory deque)     │        │  (rolling window, 72 slots)  │
│  - 1000 liqs/symbol    │        │  - OI, Funding, CVD          │
│  - 500 trades/symbol   │        │  - Whale, Orderbook, Price   │
└────────────┬───────────┘        └──────────────┬───────────────┘
             ↓                                   ↓
┌────────────────────────────────────────────────────────────────┐
│                        ANALYZERS                               │
│  StopHuntDetector (30s) │ OrderFlowAnalyzer (5m) │ EventPattern│
└────────────────────────────────┬───────────────────────────────┘
                                 ↓
┌────────────────────────────────────────────────────────────────┐
│                     SIGNAL PIPELINE                            │
│  SignalGenerator → ConfidenceScorer → SignalValidator           │
│  → MarketContextFilter (CVD/Whale VETO) → LeadingIndicatorScorer│
└──────────┬──────────────────┬──────────────────┬───────────────┘
           ↓                  ↓                  ↓
    ┌──────────────┐  ┌──────────────┐  ┌────────────────┐
    │  Telegram Bot │  │  Dashboard   │  │  SQLite DB     │
    │  (3s rate)    │  │  (port 8081) │  │  (persistence) │
    └──────────────┘  └──────────────┘  └────────────────┘
```

---

## 2. Data Source

| # | Source | Tipe | Endpoint |
|---|--------|------|----------|
| 1 | CoinGlass WebSocket | WebSocket (real-time) | `wss://open-ws.coinglass.com/ws-api?cg-api-key={key}` |
| 2 | CoinGlass REST API | REST (polling 120s) | `https://open-api-v4.coinglass.com/api/*` |
| 3 | SQLite Database | Local DB (async) | `data/teleglas.db` |
| 4 | Telegram Bot API | REST (outbound) | `https://api.telegram.org/bot{token}/sendMessage` |

**TIDAK ADA:** Google Sheets integration, Binance direct API, koneksi MCP, external database.

---

## 3. Fetch Logic

### 3.1 CoinGlass WebSocket — Real-Time Stream

| Property | Value |
|----------|-------|
| **File** | `src/connection/websocket_client.py` |
| **Class** | `WebSocketClient` |
| **Method** | `connect()` → `_receive_loop()` |
| **Frekuensi** | Real-time (continuous) |
| **Heartbeat** | 20 detik ping |
| **Reconnect** | Exponential backoff 1s → 60s max |

**Channel yang di-subscribe:**

| Channel | Data | Scope |
|---------|------|-------|
| `liquidationOrders` | Event likuidasi | Semua coin |
| `futures_trades@all_{symbol}@10000` | Futures trades | Per-pair (primary + secondary) |

**Primary pairs (dari config):** BTC, ETH, SOL, ADA, MATIC, AVAX, DOGE
**Secondary pairs:** Di-assign dinamis berdasarkan aktivitas

### 3.2 CoinGlass REST API — Polling 120 Detik

| Property | Value |
|----------|-------|
| **File** | `src/connection/rest_poller.py` |
| **Class** | `CoinGlassRestPoller` |
| **HTTP Client** | `aiohttp.ClientSession` (30s timeout) |
| **Auth** | Header `CG-API-KEY: {key}` |
| **Rate limit** | 0.5s delay antar request |
| **Max symbols** | 120 per siklus |

**Endpoint yang di-poll setiap 120 detik:**

| Endpoint | Fungsi | Return Type |
|----------|--------|-------------|
| `/api/futures/open-interest/aggregated-history` | `_fetch_oi()` | `OISnapshot` |
| `/api/futures/funding-rate/oi-weight-history` | `_fetch_funding()` | `FundingSnapshot` |
| `/api/spot/aggregated-cvd/history` | `_fetch_spot_cvd()` | `CVDSnapshot` |
| `/api/futures/aggregated-cvd/history` | `_fetch_futures_cvd()` | `CVDSnapshot` |
| `/api/hyperliquid/whale-alert` | `_fetch_whale_alerts()` | `List[WhaleAlert]` |
| `/api/futures/orderbook/aggregated-ask-bids-history` | `_fetch_orderbook_delta()` | `OrderbookDelta` |
| `/api/futures/funding-rate/exchange-list` | `_fetch_all_funding_per_exchange()` | Per-exchange rates |
| `/api/futures/price/history` | `_fetch_price()` | `PriceSnapshot` |

**Callback chain (di-register di `main.py`):**

```
rest_poller._fetch_oi()
  → callback: _on_oi_data()
    → market_context_buffer.add_oi_snapshot()
```

Pola yang sama untuk semua endpoint REST.

---

## 4. Data Pipeline

### Pipeline A: WebSocket → Signal → Dashboard + Telegram

```
Step 1:  CoinGlass WebSocket push liquidation/trade event
            ↓
Step 2:  websocket_client.py → _receive_loop() → on_message callback
            ↓
Step 3:  main.py → on_message() routes by channel:
           - liquidationOrders → _handle_liquidation_message()
           - futures_trades    → _handle_trade_message()
            ↓
Step 4:  data_validator.py → DataValidator.validate()
           (schema check, type coercion string→float, range validation)
            ↓
Step 5:  buffer_manager.py → BufferManager.add_liquidation() / add_trade()
           (per-symbol deque, max 1000 liqs / 500 trades)
            ↓
Step 6:  main.py → analyze_and_alert(symbol)
           [debounced, max 30 concurrent]
            ↓
Step 7:  ANALYZERS (parallel):
           - stop_hunt_detector.py  → analyze() [30s window, tier-based threshold]
           - order_flow_analyzer.py → analyze() [5m window, buy/sell ratio]
           - event_pattern_detector.py → analyze() [cascade, whale, volume spike]
            ↓
Step 8:  signal_generator.py → generate()
           Weighted merge: StopHunt 50% + OrderFlow 35% + Events 15%
           Alignment bonus +10% jika sinyal setuju
            ↓
Step 9:  confidence_scorer.py → adjust_confidence()
           Win rate history ±5%, absorption bonus, combo bonuses
            ↓
Step 10: signal_validator.py → validate()
           Min confidence 70%, dedup hash (10m window),
           per-symbol cooldown (Tier1: 60m, Tier4: 15m), rate limit 50/hr
            ↓
Step 11: market_context_filter.py → evaluate()
           CVD VETO: block jika SpotCVD berlawanan arah
           Whale VETO: block jika whale >$5M konflik
           FAVORABLE: +5 confidence, UNFAVORABLE: -10
            ↓
Step 12: leading_indicator_scorer.py → score()
           SpotCVD flip: 35pts, FutCVD flip: 25pts,
           OI spike >3%: 20pts, Orderbook: 10pts
            ↓
Step 13: message_formatter.py → format_signal()
           Data-first Telegram markdown dengan semua indikator
            ↓
Step 14: OUTPUT (parallel):
           a. dashboard_api.add_signal()    → WS broadcast → Browser
           b. telegram_bot.send_message()   → Telegram API → HP user
           c. database.save_signal()        → SQLite persistence
           d. signal_tracker.track()        → cek outcome tiap 15 menit
```

### Pipeline B: REST → Market Context (background, 120s)

```
Step 1:  rest_poller.py → poll_cycle() setiap 120 detik
Step 2:  Fetch OI, Funding, CVD, Whale, Orderbook, Price per symbol
Step 3:  Callback di main.py: _on_oi_data(), _on_funding_data(), dll.
Step 4:  market_context_buffer.py → simpan di rolling deque (max 72 = 6 jam)
Step 5:  Dipakai oleh market_context_filter.py saat evaluasi sinyal (Pipeline A Step 11)
```

### Pipeline C: Dashboard State → Browser

```
Step 1:  main.py panggil fungsi dashboard_api:
           - update_stats(stats)              → setiap ~30 detik
           - update_order_flow(symbol, data)  → setelah setiap analisis
           - add_signal(signal)               → saat sinyal valid
Step 2:  api.py → update state thread-safe (state_lock)
Step 3:  api.py → _schedule_broadcast() → call_soon_threadsafe()
Step 4:  WebSocket broadcast ke SEMUA browser yang terkoneksi
Step 5:  app.js → handleWebSocketMessage() → update Alpine.js state
Step 6:  Alpine.js reactivity → DOM re-render otomatis
```

---

## 5. Transformasi Data

| Stage | File | Transformasi |
|-------|------|-------------|
| Field normalization | `main.py` | `volUsd` → `vol`, `exName` → `exchange` |
| Type coercion | `data_validator.py` | String → float untuk price/volume (CoinGlass kirim string) |
| Range validation | `data_validator.py` | Volume $1K-$100M, Price $0-$10M |
| Time windowing | `buffer_manager.py` | Filter event berdasarkan recency (30s liqs, 5m trades) |
| Baseline calculation | `buffer_manager.py` | Rata-rata volume per jam untuk anomaly detection |
| Tier classification | `stop_hunt_detector.py` | BTC/ETH=Tier1 ($3M), mid-caps=Tier2 ($500K), small=Tier3 ($50K) |
| Signal merging | `signal_generator.py` | Weighted merge 3 analyzer → 1 signal |
| Confidence adjustment | `confidence_scorer.py` | Win rate, absorption, combo → ±20% |
| CVD/Whale veto | `market_context_filter.py` | Hard block jika SpotCVD atau whale berlawanan |
| Leading scoring | `leading_indicator_scorer.py` | Composite 0-99 dari CVD flips, OI spikes, orderbook |

---

## 6. Dashboard Rendering (AKTIF — port 8082)

> **URL Publik:** `https://teleglas.guardiansofthetoken.org/dashboard/`
> **File:** `src/dashboard/server.py` + `src/dashboard/static/index.html`
> **Dashboard lama (port 8081):** disimpan di `src/dashboard/_legacy/`, dipakai main.py sebagai internal API saja.

### Tech Stack

| Property | Value |
|----------|-------|
| **Framework** | Vanilla JavaScript (no framework) |
| **Charting** | TradingView Lightweight Charts widget |
| **CSS** | Custom CSS Grid + Flexbox (dark theme `#0a0e17`) |
| **Build process** | Tidak ada — static files |
| **Entry HTML** | `src/dashboard/static/index.html` (39KB) |
| **PWA** | `manifest.json` + `sw.js` (service worker) |
| **Backend** | FastAPI + httpx, port 8082 |
| **Nginx** | Reverse proxy `teleglas.guardiansofthetoken.org/dashboard/` → `127.0.0.1:8082` |

### Komponen UI

| Komponen | Isi |
|----------|-----|
| **Header** | "TELEGLAS LIVE" + connection status (LIVE/OFFLINE) + timestamp |
| **BTC Ticker Bar** | BTC price, 24h change, CVD, OI, funding rate, long/short % |
| **Coin Selector** | Pill buttons (SOL, ETH, AVAX, SUI, HYPE, XRP, BNB) + search dropdown |
| **2-Panel Layout** | MACRO REFERENCE (BTC) + ANALYSIS TARGET (selected coin) |
| **Per-Coin Card** | Price, 24h change, bias badge (LONG/SHORT/NEUTRAL), order flow |
| **Taker Volume** | Buy/sell volume bars dengan visual gradient |
| **Long/Short Ratio** | Rasio long/short dari Binance |
| **OI Delta** | Open Interest change + interpretation badge |
| **Liq Clusters** | Level likuidasi di atas/bawah harga saat ini |
| **Spot CVD Chart** | 20-bar sparkline dengan gradient (5m interval) |
| **Funding Rate Extremes** | Top most-positive & most-negative FR (expandable) |
| **TradingView Chart** | 5-minute candle chart Binance perp (dynamic per coin) |
| **Whale Intelligence** | Tab Alerts (recent whale trades >$500K) + Positions (active >$1M) |

### Warna Indikator

| Kondisi | Warna |
|---------|-------|
| Bullish / LONG / Rising | Hijau `#34d399` |
| Bearish / SHORT / Falling | Merah `#f87171` |
| Active / Headers | Biru `#60a5fa` |
| Neutral / Secondary | Abu-abu `#6b7280` |

### Data Flow ke Browser

```
Channel 1 (WS1) — State Push:
  main.py menulis /tmp/tg_state.json setiap ~2 detik
  → server.py state_poller() baca file
  → broadcast ke semua browser via WebSocket
  → Data: 103 coins (price, OI, CVD, funding, bias, scores, whale alerts)

Channel 2 (WS3) — On-Demand:
  Browser kirim request via WebSocket → server.py fetch CoinGlass REST API
  → Response dikirim balik ke browser yang request
  → Data: liq clusters, CVD history, taker, long/short, whale positions

Auto-Push Loop:
  Setiap 30 detik, server.py push data on-demand otomatis
  ke semua client berdasarkan coin yang sedang mereka lihat

Reconnect: Otomatis saat koneksi putus
```

### WebSocket Message Types

| Type | Arah | Data |
|------|------|------|
| (raw JSON state) | Server → Browser (WS1) | Full state: 103 coins + whale alerts + FR extremes |
| `liq_cluster` | Server → Browser (WS3) | Cluster likuidasi atas/bawah harga |
| `cvd_history` | Server → Browser (WS3) | 20 bar SpotCVD 5-menit |
| `taker` | Server → Browser (WS3) | Taker buy/sell volume 15 menit |
| `long_short` | Server → Browser (WS3) | Long/short ratio Binance |
| `whale_alerts` | Server → Browser (auto) | Whale trades >$500K dari Hyperliquid |
| `whale_positions` | Server → Browser (auto) | Posisi whale aktif >$1M + PnL |

### CoinGlass Endpoints (dipanggil langsung oleh server.py)

| Endpoint | Fungsi | Data |
|----------|--------|------|
| `/api/futures/liquidation/order` | `fetch_liq_cluster()` | Likuidasi 24h, agregasi per zona harga |
| `/api/spot/aggregated-cvd/history` | `fetch_cvd_history()` | 20 candle SpotCVD 5m |
| `/api/futures/aggregated-cvd/history` | `fetch_taker()` | Taker buy/sell 3x5m |
| `/api/futures/global-long-short-account-ratio/history` | `fetch_long_short()` | L/S ratio Binance 1h |
| `/api/hyperliquid/whale-alert` | `fetch_whale_positions()` | Whale alerts + positions |

---

## 7. Update Mechanism

| Data | Tipe Update | Frekuensi |
|------|-------------|-----------|
| Liquidations & Trades | Real-time (WS push) | Continuous |
| Order Flow metrics | Event-driven | Per-event setelah analisis |
| Signals | Event-driven | Per-event saat sinyal valid |
| System stats | Push dari main.py | ~30 detik |
| Market context (OI, CVD, FR) | REST polling | 120 detik |
| Signal outcomes | Background checker | 15 menit |
| Fallback polling (WS drop) | HTTP fetch | 10 detik |
| WS reconnect | Auto-retry | 3 detik |

---

## 8. Database Schema

**Engine:** SQLite via aiosqlite
**File:** `data/teleglas.db`

| Tabel | Fungsi | Kolom Utama | Retention |
|-------|--------|-------------|-----------|
| `signals` | Riwayat sinyal trading | id, symbol, signal_type, direction, confidence, entry_price, stop_loss, target_price, exit_price, outcome, pnl_pct, created_at, checked_at | Permanen |
| `confidence_state` | State confidence scorer | signal_type (PK), win_rate, history_json, updated_at | Permanen |
| `dashboard_coins` | Toggle coin yang dimonitor | symbol (PK), active, added_at | Permanen |
| `hourly_baselines` | Snapshot baseline per jam | id, symbol, liq_volume, trade_volume, recorded_at | 72 jam |
| `oi_snapshots` | Riwayat OI | id, symbol, current_oi_usd, previous_oi_usd, oi_change_pct, recorded_at | 7 hari |
| `funding_snapshots` | Riwayat funding rate | id, symbol, current_rate, previous_rate, rate_high, rate_low, recorded_at | 7 hari |

**Export:**
- `GET /api/export/signals.csv` → download CSV (max 5000 rows)
- `GET /api/export/baselines.csv` → download CSV per symbol

---

## 9. API Endpoints

**Base URL:** `http://127.0.0.1:8081`

### Public (tanpa auth)

| Method | Endpoint | Response |
|--------|----------|----------|
| GET | `/` | Dashboard HTML |
| GET | `/health` | `{status, uptime_seconds, coins_tracked}` |

### Rate-Limited (30 req/menit per IP)

| Method | Endpoint | Response |
|--------|----------|----------|
| GET | `/api/stats` | `{messages_received, signals_generated, alerts_sent, errors, uptime_seconds}` |
| GET | `/api/coins` | `{coins: [{symbol, active, buy_ratio, sell_ratio, large_buys, large_sells, last_update}]}` |
| GET | `/api/signals?limit=50` | `{signals: [{id, time, symbol, type, confidence}]}` |
| GET | `/api/orderflow/{symbol}` | `{buy_ratio, sell_ratio, large_buys, large_sells, last_update}` |

### Authenticated (Bearer token required)

| Method | Endpoint | Body / Params | Response |
|--------|----------|---------------|----------|
| POST | `/api/coins/add` | `{symbol: "PEPE"}` | `{success, coin}` |
| DELETE | `/api/coins/remove/{symbol}` | — | `{success, symbol}` |
| PATCH | `/api/coins/{symbol}/toggle` | `{active: bool}` | `{success, coin}` |
| GET | `/api/export/signals.csv` | — | CSV file download |
| GET | `/api/export/baselines.csv?symbol=BTCUSDT` | — | CSV file download |
| GET | `/api/stats/signals` | — | `{overall, by_type}` |
| GET | `/api/signals/history?symbol=X&limit=100` | — | `{signals: [...]}` |

### WebSocket

| URL | Auth | Pesan |
|-----|------|-------|
| `ws://127.0.0.1:8081/ws` | Kirim `{type: "auth", token: "Bearer ..."}` sebagai pesan pertama | Lihat tabel WebSocket Message Types di atas |

---

## 10. Security

| Fitur | Implementasi |
|-------|-------------|
| **Auth** | Bearer token (config.yaml → `dashboard.api_token`) |
| **Token comparison** | HMAC constant-time (`hmac.compare_digest`) — anti timing attack |
| **WS auth** | Token di pesan pertama, bukan di URL (anti log exposure) |
| **Rate limiting** | 30 req/menit per IP, auto-evict setelah 10K IPs |
| **Input validation** | Regex `[A-Z0-9]{3,20}` untuk symbol |
| **HTML escaping** | Token di-escape sebelum inject ke HTML meta tag |
| **CORS** | Configurable origins (default: `localhost:3000`) |
| **Thread safety** | `threading.Lock` untuk semua akses ke `system_state` |

---

## 11. Limitasi & Masalah

### 🔴 Kritis

| # | Masalah | Detail |
|---|---------|--------|
| 1 | **Tidak ada koneksi MCP** | CoinGlass MCP, Nansen MCP, Glassnode MCP, LunarCrush MCP tersedia tapi **tidak ada yang terhubung ke pipeline dashboard**. |
| 2 | **2 proses terpisah** | main.py (port 8081) dan server.py (port 8082) berkomunikasi lewat file `/tmp/tg_state.json`. Idealnya satu proses atau direct memory. |

### 🟡 Sedang

| # | Masalah | Detail |
|---|---------|--------|
| 3 | **Sinyal tidak tampil di dashboard browser** | Signal pipeline (StopHunt, OrderFlow) hanya dikirim ke Telegram + port 8081. Dashboard browser (port 8082) tidak menerima sinyal trading. |
| 4 | **Tidak ada signal history view** | Dashboard browser tidak ada halaman riwayat sinyal + outcome (win/loss). |
| 5 | **Whale positions tanpa filter per coin** | Semua posisi whale ditampilkan global, belum bisa filter per coin yang sedang dilihat. |

### 🟢 Minor

| # | Masalah | Detail |
|---|---------|--------|
| 6 | Tidak ada dark/light toggle | Hardcoded dark theme saja. |
| 7 | PWA service worker basic | `sw.js` ada tapi hanya cache minimal. |
| 8 | Search dropdown lambat | 103 coins di-render langsung, bisa lag di mobile. |

---

## 12. Rekomendasi Pengembangan

### 12.1 Tampilkan Market Context di Dashboard

Data sudah ada di `MarketContextBuffer`. Perlu:

```python
# Backend: tambah endpoint baru di api.py
@app.get("/api/market-context/{symbol}")
async def get_market_context(symbol: str):
    return market_context_buffer.get_assessment(symbol)

# Backend: tambah WS message type baru
def update_market_context(symbol, context):
    _schedule_broadcast("market_context_update", {symbol, ...context})
```

```javascript
// Frontend: handle message type baru di app.js
case 'market_context_update':
    this.updateMarketContext(msg.data)
    break
```

### 12.2 Koneksi MCP

MCP tools yang tersedia dan bisa langsung dipakai:

| MCP Tool | Data | Kegunaan |
|----------|------|----------|
| `coinglass_open_interest` | OI real-time | Gantikan REST polling OI |
| `coinglass_funding_rate` | FR real-time | Gantikan REST polling FR |
| `coinglass_liquidation_history` | Riwayat likuidasi | Chart likuidasi |
| `coinglass_orderbook_heatmap` | Heatmap orderbook | Visualisasi support/resistance |
| `nansen_smart_money` | Smart money flow | Indikator tambahan |
| `nansen_token_flows` | Token flows | Exchange inflow/outflow |
| `glassnode_fetch_metric` | On-chain (SOPR, NUPL) | Indikator makro |
| `lunarcrush_Cryptocurrencies` | Social sentiment | Sentimen pasar |

### 12.3 Tambah Chart Ringan

Opsi library (tanpa build process):

```html
<!-- Lightweight Charts by TradingView — 40KB -->
<script src="https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js"></script>

<!-- Atau Chart.js — 60KB -->
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
```

Chart yang bisa ditambah:
- OI time-series (dari tabel `oi_snapshots`)
- Funding rate history (dari tabel `funding_snapshots`)
- CVD delta (dari `MarketContextBuffer`)
- Signal outcome P&L tracking (dari tabel `signals`)

### 12.4 Perkaya Kartu Sinyal

Data sudah tersedia di `add_signal()`, tinggal dikirim lebih lengkap:

```python
# Sekarang:
add_signal({"symbol": "BTCUSDT", "type": "stop_hunt", "confidence": 85})

# Seharusnya:
add_signal({
    "symbol": "BTCUSDT",
    "type": "stop_hunt",
    "direction": "LONG",           # ← tambah
    "confidence": 85,
    "entry_price": 96500.0,        # ← tambah
    "stop_loss": 95800.0,          # ← tambah
    "target_price": 98000.0,       # ← tambah
    "leading_score": 72,           # ← tambah
    "market_verdict": "FAVORABLE", # ← tambah
    "absorption_pct": 45.2,        # ← tambah
})
```

### 12.5 Halaman Detail Per-Coin

Tambah route `/coin/{symbol}` dengan:
- Live order flow (buy/sell delta over time)
- Market context assessment (OI, FR, CVD gauges)
- Riwayat sinyal untuk coin tersebut
- Whale activity (posisi Hyperliquid)
- Mini price chart

### 12.6 Optimasi Performa

| Area | Masalah | Solusi |
|------|---------|-------|
| WS broadcast | Setiap `update_order_flow()` trigger broadcast individual | Batch multiple symbol per pesan |
| REST polling | 120 detik interval, banyak request serial | Parallel fetch dengan `asyncio.gather()` |
| Memory | `system_state["signals"]` tumbuh sampai 200 | Sudah di-cap, OK |
| DB cleanup | Hourly baselines 72 jam, OI 7 hari | Sudah ada, OK |

---

## 13. File Map

```
/root/TELEGAS-WS/
├── main.py                                    # Orchestrator utama (port 8081 internal)
├── config/
│   ├── config.yaml                            # Semua konfigurasi sistem
│   └── secrets.env                            # API keys, tokens
├── data/
│   └── teleglas.db                            # SQLite database
├── logs/
│   └── teleglas.log                           # Log file
├── docs/
│   └── SYSTEM_AUDIT.md                        # Dokumen audit ini
├── src/
│   ├── connection/
│   │   ├── websocket_client.py                # CoinGlass WebSocket client
│   │   └── rest_poller.py                     # CoinGlass REST API poller (120s)
│   ├── processors/
│   │   ├── data_validator.py                  # Schema + range validation
│   │   ├── buffer_manager.py                  # In-memory event buffers
│   │   └── market_context_buffer.py           # Rolling market data (OI, FR, CVD)
│   ├── analyzers/
│   │   ├── stop_hunt_detector.py              # Likuidasi cascade detection
│   │   ├── order_flow_analyzer.py             # Buy/sell pressure analysis
│   │   └── event_pattern_detector.py          # Pattern detection
│   ├── signals/
│   │   ├── signal_generator.py                # Merge analyzer outputs → 1 signal
│   │   ├── confidence_scorer.py               # Dynamic confidence adjustment
│   │   ├── signal_validator.py                # Dedup, cooldown, rate limit
│   │   ├── market_context_filter.py           # CVD/Whale veto logic
│   │   ├── leading_indicator_scorer.py        # CVD + OI primary scoring
│   │   └── signal_tracker.py                  # Outcome tracking (15m check)
│   ├── alerts/
│   │   ├── telegram_bot.py                    # Telegram message sender
│   │   └── message_formatter.py               # Format sinyal → markdown
│   ├── dashboard/                             # === DASHBOARD (AKTIF di browser) ===
│   │   ├── server.py                          # FastAPI + WS server (port 8082)
│   │   ├── api.py                             # Bridge → re-export _legacy/api.py
│   │   ├── __init__.py                        # Package marker
│   │   ├── static/
│   │   │   ├── index.html                     # Dashboard UI (39KB, vanilla JS)
│   │   │   ├── manifest.json                  # PWA manifest
│   │   │   ├── sw.js                          # Service worker
│   │   │   ├── icon-192.png                   # PWA icon
│   │   │   └── icon-512.png                   # PWA icon large
│   │   └── _legacy/                           # Dashboard lama (tidak dipakai di browser)
│   │       ├── api.py                         # FastAPI internal API (port 8081)
│   │       ├── app.js                         # Alpine.js frontend (lama)
│   │       ├── index.html                     # Alpine.js HTML (lama)
│   │       └── manifest.json                  # PWA manifest (lama)
│   ├── storage/
│   │   └── database.py                        # SQLite async operations
│   └── utils/
│       └── symbol_normalizer.py               # Symbol normalization helper
└── watch_coins.py                             # Standalone coin watcher
```

### Nginx Config (`/etc/nginx/sites-available/teleglas`)

```
teleglas.guardiansofthetoken.org
  /dashboard/*  →  proxy_pass http://127.0.0.1:8082/  (server.py)
  /*            →  proxy_pass http://127.0.0.1:8081   (main.py internal API)
```

---

## Analyzer Detail: Threshold per Tier

| Tier | Contoh Coin | Cascade Threshold | Absorption Min | Large Order | Cooldown |
|------|-------------|-------------------|----------------|-------------|----------|
| Tier 1 | BTC, ETH | $3,000,000 | $200,000 | >$10,000 | 60 menit |
| Tier 2 | SOL, ADA, AVAX | $500,000 | $50,000 | >$5,000 | 30 menit |
| Tier 3 | DOGE, PEPE, WIF | $50,000 | $5,000 | >$2,000 | 20 menit |
| Tier 4 | Micro-caps | $10,000 | $1,000 | >$2,000 | 15 menit |

---

## Signal Scoring Detail

### Weighted Merge (SignalGenerator)

```
Final Confidence = (StopHunt × 0.50) + (OrderFlow × 0.35) + (Events × 0.15)
                 + Alignment Bonus (+10 jika semua setuju)
```

### Leading Indicator Points (0-99)

| Indikator | Poin Max | Kategori |
|-----------|----------|----------|
| SpotCVD flip (negatif → positif untuk LONG) | 35 | Leading |
| SpotCVD sustained direction | 20 | Leading |
| FuturesCVD flip | 25 | Leading |
| FuturesCVD sustained | 15 | Leading |
| OI spike >3% dalam 15 menit | 20 | Leading |
| OI sustained rise | 10 | Leading |
| Orderbook bids dominan | 10 | Leading |
| Funding rate favorable | 8 | Lagging |
| Taker buy dominance | 8 | Lagging |
| Price momentum | 5 | Lagging |
| Liquidation trigger | 7 | Lagging |

**Special Patterns:**
- **COILING:** Low volume + CVD aligned + OI rising = breakout imminent (+10 bonus)
- **CVD RECOVERY:** Recovering from extreme = potential reversal flag
- **FR EXTREME:** Auto-skip untuk Tier 3/4 (noise terlalu tinggi)

---

> **Catatan:** Dokumen ini di-generate dari audit kode aktual pada 30 Maret 2026.
> Semua path file, endpoint, dan logic merujuk ke kode yang sedang berjalan di production.
