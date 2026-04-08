"""
TELEGLAS Dashboard — FastAPI WebSocket Server
WS1: Push state JSON every 2s (existing)
WS3: 2-way — browser requests data, server queries CoinGlass and responds

Part of TELEGAS-WS project (src/dashboard/server.py)
Previously standalone at /root/tg-dashboard/server.py
"""

import asyncio
import json
import logging
import os
import sqlite3
import time as _time
from pathlib import Path
from typing import Set, Dict

import httpx
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse

# Project root = 3 levels up from this file (src/dashboard/server.py -> root)
_PROJECT_ROOT = Path(__file__).parent.parent.parent

# Load secrets from project config (try multiple paths)
for _try_path in [
    _PROJECT_ROOT / "config" / "secrets.env",
    Path("/root/TELEGAS-WS/config/secrets.env"),
]:
    if _try_path.exists():
        load_dotenv(_try_path)
        break

# Load config.yaml for dashboard settings
_config_path = _PROJECT_ROOT / "config" / "config.yaml"
_config = {}
if _config_path.exists():
    with open(_config_path) as _f:
        _config = yaml.safe_load(_f) or {}

app = FastAPI(title="TELEGLAS Dashboard")

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
STATE_FILE = Path("/tmp/tg_state.json")

# ── AI Analysis Database ──────────────────
_AI_DB = Path("/root/TELEGAS-WS/data/ai_analysis.db")
_AI_DB.parent.mkdir(parents=True, exist_ok=True)

def _init_ai_db():
    con = sqlite3.connect(str(_AI_DB))
    con.execute("""CREATE TABLE IF NOT EXISTS analyses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        regime TEXT,
        bias TEXT,
        grade TEXT,
        confidence REAL,
        analysis TEXT,
        entry_price REAL,
        price_15m REAL,
        price_30m REAL,
        price_60m REAL,
        outcome_15m TEXT,
        outcome_30m TEXT,
        outcome_60m TEXT,
        pnl_15m REAL,
        pnl_30m REAL,
        pnl_60m REAL,
        coin_snapshot TEXT,
        regime_snapshot TEXT,
        created_at REAL NOT NULL,
        checked_15m INTEGER DEFAULT 0,
        checked_30m INTEGER DEFAULT 0,
        checked_60m INTEGER DEFAULT 0
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS ai_stats (
        symbol TEXT,
        regime TEXT,
        total INTEGER DEFAULT 0,
        wins INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0,
        neutral INTEGER DEFAULT 0,
        avg_pnl REAL DEFAULT 0,
        last_updated REAL,
        PRIMARY KEY (symbol, regime)
    )""")
    con.commit()
    con.close()

_init_ai_db()

def _save_analysis(symbol, regime, bias, grade, confidence, analysis, entry_price, coin_data, regime_data):
    con = sqlite3.connect(str(_AI_DB))
    con.execute(
        """INSERT INTO analyses (symbol, regime, bias, grade, confidence, analysis,
           entry_price, coin_snapshot, regime_snapshot, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (symbol, regime, bias, grade, confidence, analysis, entry_price,
         json.dumps(coin_data), json.dumps(regime_data), _time.time())
    )
    con.commit()
    aid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.close()
    return aid

def _get_recent_history(symbol, limit=5):
    """Get recent analyses for this symbol to feed back to Claude."""
    con = sqlite3.connect(str(_AI_DB))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """SELECT regime, bias, grade, confidence, analysis, entry_price,
                  outcome_30m, pnl_30m, created_at
           FROM analyses WHERE symbol=? AND outcome_30m IS NOT NULL
           ORDER BY created_at DESC LIMIT ?""",
        (symbol, limit)
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]

def _get_ai_stats(symbol=None):
    """Get win/loss stats."""
    con = sqlite3.connect(str(_AI_DB))
    con.row_factory = sqlite3.Row
    if symbol:
        rows = con.execute("SELECT * FROM ai_stats WHERE symbol=?", (symbol,)).fetchall()
    else:
        rows = con.execute("SELECT * FROM ai_stats ORDER BY total DESC").fetchall()
    con.close()
    return [dict(r) for r in rows]

def _check_outcomes():
    """Check pending analyses against current prices."""
    try:
        if not STATE_FILE.exists():
            return
        with open(STATE_FILE) as f:
            state = json.load(f)
        coins = state.get("coins", {})
        if not coins:
            return

        con = sqlite3.connect(str(_AI_DB))
        now = _time.time()

        # Find unchecked analyses
        pending = con.execute(
            """SELECT id, symbol, bias, entry_price, created_at,
                      checked_15m, checked_30m, checked_60m
               FROM analyses
               WHERE (checked_15m=0 OR checked_30m=0 OR checked_60m=0)
                     AND created_at > ?""",
            (now - 7200,)  # last 2 hours only
        ).fetchall()

        for row in pending:
            aid, sym, bias, entry, created, c15, c30, c60 = row
            coin = coins.get(sym)
            if not coin or not coin.get("price") or not entry:
                continue
            cur_price = coin["price"]
            age_min = (now - created) / 60

            for minutes, col_price, col_outcome, col_pnl, col_checked, checked in [
                (15, "price_15m", "outcome_15m", "pnl_15m", "checked_15m", c15),
                (30, "price_30m", "outcome_30m", "pnl_30m", "checked_30m", c30),
                (60, "price_60m", "outcome_60m", "pnl_60m", "checked_60m", c60),
            ]:
                if checked or age_min < minutes:
                    continue
                # Calculate PnL
                if bias == "LONG":
                    pnl = (cur_price - entry) / entry * 100
                elif bias == "SHORT":
                    pnl = (entry - cur_price) / entry * 100
                else:
                    pnl = 0

                # Classify: >0.3% = WIN, <-0.3% = LOSS, else NEUTRAL
                if pnl > 0.3:
                    outcome = "WIN"
                elif pnl < -0.3:
                    outcome = "LOSS"
                else:
                    outcome = "NEUTRAL"

                con.execute(
                    f"UPDATE analyses SET {col_price}=?, {col_outcome}=?, {col_pnl}=?, {col_checked}=1 WHERE id=?",
                    (cur_price, outcome, round(pnl, 3), aid)
                )

                # Update stats (use 30m as primary metric)
                if col_outcome == "outcome_30m":
                    regime = con.execute("SELECT regime FROM analyses WHERE id=?", (aid,)).fetchone()
                    regime = regime[0] if regime else "UNKNOWN"
                    existing = con.execute(
                        "SELECT total, wins, losses, neutral, avg_pnl FROM ai_stats WHERE symbol=? AND regime=?",
                        (sym, regime)
                    ).fetchone()
                    if existing:
                        t, w, l, n, ap = existing
                        t += 1
                        if outcome == "WIN": w += 1
                        elif outcome == "LOSS": l += 1
                        else: n += 1
                        ap = (ap * (t - 1) + pnl) / t
                        con.execute(
                            "UPDATE ai_stats SET total=?, wins=?, losses=?, neutral=?, avg_pnl=?, last_updated=? WHERE symbol=? AND regime=?",
                            (t, w, l, n, round(ap, 3), now, sym, regime)
                        )
                    else:
                        w = 1 if outcome == "WIN" else 0
                        l = 1 if outcome == "LOSS" else 0
                        n = 1 if outcome == "NEUTRAL" else 0
                        con.execute(
                            "INSERT INTO ai_stats (symbol, regime, total, wins, losses, neutral, avg_pnl, last_updated) VALUES (?,?,1,?,?,?,?,?)",
                            (sym, regime, w, l, n, round(pnl, 3), now)
                        )

        con.commit()
        con.close()
    except Exception:
        pass
POLL_INTERVAL = 2
CG_BASE = "https://open-api-v4.coinglass.com"
CG_KEY = os.environ.get("COINGLASS_API_KEY", "")

clients: Set[WebSocket] = set()
last_state: str = "{}"
# Track which coin each client is watching (for auto-push)
client_coins: Dict[WebSocket, str] = {}

_http: httpx.AsyncClient = None

async def get_http() -> httpx.AsyncClient:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(
            headers={"CG-API-KEY": CG_KEY, "Accept": "application/json"},
            timeout=10,
        )
    return _http


# ── TTL Cache for CoinGlass responses ─────────────────
# Prevents duplicate API calls when multiple clients watch the same coin
# and avoids hammering the API from auto_push_loop

_cg_cache: Dict[str, tuple] = {}  # key -> (data, timestamp)
_CG_CACHE_TTL = 25  # seconds (auto_push runs every 30s, so 25s cache avoids double-fetch)
_cg_429_until: float = 0.0  # global 429 cooldown timestamp

async def _cached_cg_get(url: str, params: dict = None, cache_ttl: int = None) -> dict | list | None:
    """
    CoinGlass GET with TTL cache and 429 backoff.

    Returns cached response if fresh, otherwise fetches from API.
    On 429, enters 30s cooldown and returns cached data (even if stale) or None.
    """
    global _cg_429_until

    ttl = cache_ttl or _CG_CACHE_TTL
    cache_key = f"{url}|{json.dumps(params or {}, sort_keys=True)}"
    now = _time.time()

    # Return cached if still fresh
    if cache_key in _cg_cache:
        cached_data, cached_at = _cg_cache[cache_key]
        if now - cached_at < ttl:
            return cached_data

    # If in 429 cooldown, return stale cache or None
    if now < _cg_429_until:
        if cache_key in _cg_cache:
            return _cg_cache[cache_key][0]  # stale but better than nothing
        return None

    try:
        http = await get_http()
        resp = await http.get(url, params=params)

        if resp.status_code == 429:
            _cg_429_until = now + 30.0  # 30s cooldown
            if cache_key in _cg_cache:
                return _cg_cache[cache_key][0]  # return stale
            return None

        if resp.status_code != 200:
            return None

        data = resp.json()
        _cg_cache[cache_key] = (data, now)

        # Evict old cache entries (keep max 200)
        if len(_cg_cache) > 200:
            oldest_key = min(_cg_cache, key=lambda k: _cg_cache[k][1])
            del _cg_cache[oldest_key]

        return data
    except Exception:
        # On error, return stale cache if available
        if cache_key in _cg_cache:
            return _cg_cache[cache_key][0]
        return None


# ── CoinGlass fetch functions ──────────────────────────

async def fetch_liq_cluster(symbol: str) -> dict:
    """Fetch liquidation clusters from recent liq orders — aggregate by price zone."""
    try:
        data = await _cached_cg_get(f"{CG_BASE}/api/futures/liquidation/order", params={
            "symbol": symbol, "range": "24h",
        })
        if data is None:
            return {"error": "API unavailable (rate limited)"}
        if str(data.get("code")) != "0":
            return {"error": data.get("msg", "API error")}

        orders = data.get("data", [])
        if not orders:
            return {"price": 0, "above": {"price": 0, "size": 0}, "below": {"price": 0, "size": 0}}

        # Get current price from state file
        price = 0
        try:
            with open("/tmp/tg_state.json") as f:
                st = json.load(f)
            price = st.get("coins", {}).get(symbol, {}).get("price", 0)
        except Exception:
            pass
        if price == 0:
            # Estimate from liq orders
            prices = [float(o.get("price", 0)) for o in orders if o.get("price")]
            price = sum(prices) / len(prices) if prices else 0

        # Aggregate: side 1 = long liq (below), side 2 = short liq (above)
        above_total = {}  # price_zone -> total_usd
        below_total = {}
        zone_size = price * 0.005 if price > 0 else 1  # 0.5% zones

        for o in orders:
            p = float(o.get("price", 0))
            usd = float(o.get("usd_value", 0))
            side = o.get("side", 0)
            zone = round(p / zone_size) * zone_size

            if side == 1 and p < price:  # long liq = below
                below_total[zone] = below_total.get(zone, 0) + usd
            elif side == 2 and p > price:  # short liq = above
                above_total[zone] = above_total.get(zone, 0) + usd

        above = {"price": 0, "size": 0}
        below = {"price": 0, "size": 0}

        if above_total:
            best_zone = max(above_total, key=above_total.get)
            above = {"price": best_zone, "size": above_total[best_zone]}
        if below_total:
            best_zone = max(below_total, key=below_total.get)
            below = {"price": best_zone, "size": below_total[best_zone]}

        return {"price": price, "above": above, "below": below}
    except Exception as e:
        return {"error": str(e)}


async def fetch_cvd_history(symbol: str) -> list:
    """Fetch 20 SpotCVD data points."""
    try:
        data = await _cached_cg_get(f"{CG_BASE}/api/spot/aggregated-cvd/history", params={
            "exchange_list": "Binance", "symbol": symbol, "interval": "5m", "limit": 20,
        })
        if data is None:
            return []
        if str(data.get("code")) != "0":
            return []
        candles = data.get("data", [])
        return [{"t": int(c.get("time", 0)), "v": float(c.get("cum_vol_delta", 0))} for c in candles]
    except Exception:
        return []


async def fetch_taker(symbol: str) -> dict:
    """Fetch taker buy/sell volume (last 3 x 5m candles = 15min)."""
    try:
        data = await _cached_cg_get(f"{CG_BASE}/api/futures/aggregated-cvd/history", params={
            "exchange_list": "Binance", "symbol": symbol, "interval": "5m", "limit": 3,
        })
        if data is None:
            return {"buy": 0, "sell": 0, "net": 0}
        if str(data.get("code")) != "0":
            return {"buy": 0, "sell": 0, "net": 0}
        candles = data.get("data", [])
        buy = sum(float(c.get("agg_taker_buy_vol", 0)) for c in candles)
        sell = sum(float(c.get("agg_taker_sell_vol", 0)) for c in candles)
        return {"buy": buy, "sell": sell, "net": buy - sell}
    except Exception:
        return {"buy": 0, "sell": 0, "net": 0}


async def fetch_long_short(symbol: str) -> dict:
    """Fetch global long/short account ratio from Binance (5m interval for freshness)."""
    try:
        pair = f"{symbol}USDT"
        data = await _cached_cg_get(f"{CG_BASE}/api/futures/global-long-short-account-ratio/history", params={
            "symbol": pair, "exchange": "Binance", "interval": "5m", "limit": 1,
        })
        if data is None:
            return {"long_pct": 50, "short_pct": 50}
        if str(data.get("code")) != "0":
            return {"long_pct": 50, "short_pct": 50}
        candles = data.get("data", [])
        if not candles:
            return {"long_pct": 50, "short_pct": 50}
        latest = candles[-1]
        long_pct = float(latest.get("global_account_long_percent", 50))
        short_pct = float(latest.get("global_account_short_percent", 50))
        return {"long_pct": long_pct, "short_pct": short_pct}
    except Exception:
        return {"long_pct": 50, "short_pct": 50}


# ── WebSocket handler ──────────────────────────

async def handle_client_message(ws: WebSocket, raw: str):
    """Handle incoming request from browser."""
    if raw == "ping":
        return

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return

    action = data.get("action")
    symbol = data.get("symbol", "SOL")

    # Track which coin this client watches
    client_coins[ws] = symbol

    result = None
    msg_type = None

    if action == "get_whale_positions":
        msg_type = "whale_positions"
        result = await fetch_whale_positions()
    elif action == "get_liq_cluster":
        msg_type = "liq_cluster"
        result = await fetch_liq_cluster(symbol)
    elif action == "get_cvd_history":
        msg_type = "cvd_history"
        result = await fetch_cvd_history(symbol)
    elif action == "get_taker":
        msg_type = "taker"
        result = await fetch_taker(symbol)
    elif action == "get_long_short":
        msg_type = "long_short"
        result = await fetch_long_short(symbol)

    if msg_type and result is not None:
        try:
            await ws.send_text(json.dumps({
                "type": msg_type,
                "symbol": symbol,
                "data": result,
            }))
        except Exception:
            pass


@app.get("/")
async def index():
    return FileResponse(str(_base / "static" / "index.html"))

@app.get("/static/sw.js")
async def service_worker():
    return FileResponse(str(_base / "static" / "sw.js"), media_type="application/javascript")


@app.get("/api/ai-status")
async def ai_status():
    """Check if server-side API key is configured."""
    return JSONResponse({"configured": bool(ANTHROPIC_KEY)})


@app.post("/api/analyze")
async def analyze_coin(request: Request):
    """Proxy to Claude API for AI-powered market analysis."""
    try:
        body = await request.json()
        api_key = body.get("api_key", "") or ANTHROPIC_KEY
        coin_data = body.get("coin_data", {})
        regime_data = body.get("regime_data", {})
        symbol = body.get("symbol", "")

        if not api_key:
            return JSONResponse({"error": "API key required"}, status_code=400)
        if not coin_data:
            return JSONResponse({"error": "No coin data"}, status_code=400)

        # Get past performance history for this coin (self-evolution)
        history = await asyncio.to_thread(_get_recent_history, symbol, 5)
        stats = await asyncio.to_thread(_get_ai_stats, symbol)

        history_block = ""
        if history:
            history_block = "\n\nYOUR PAST ANALYSES ON THIS COIN (learn from these):\n"
            for h in history:
                from datetime import datetime
                ts = datetime.fromtimestamp(h["created_at"]).strftime("%m/%d %H:%M")
                outcome = h.get("outcome_30m", "PENDING")
                pnl = h.get("pnl_30m", 0) or 0
                history_block += f"- [{ts}] Regime={h['regime']} Bias={h['bias']} Grade={h['grade']} → 30m Outcome: {outcome} ({pnl:+.2f}%)\n"
                # Show what the analysis said (first 100 chars)
                snippet = (h.get("analysis", "") or "")[:120].replace("\n", " ")
                if snippet:
                    history_block += f"  Said: {snippet}...\n"

        stats_block = ""
        if stats:
            total = sum(s["total"] for s in stats)
            wins = sum(s["wins"] for s in stats)
            losses = sum(s["losses"] for s in stats)
            wr = wins / total * 100 if total > 0 else 0
            stats_block = f"\n\nYOUR TRACK RECORD ON {symbol}:\n"
            stats_block += f"- Total calls: {total} | Wins: {wins} | Losses: {losses} | Win Rate: {wr:.0f}%\n"
            for s in stats:
                if s["total"] > 0:
                    swr = s["wins"] / s["total"] * 100
                    stats_block += f"- {s['regime']}: {s['total']} calls, {swr:.0f}% win rate, avg PnL {s['avg_pnl']:+.2f}%\n"
            if wr >= 60 and total >= 3:
                stats_block += f"- GOOD: Win rate {wr:.0f}% — your directional calls are WORKING. Keep giving entries confidently.\n"
            elif wr >= 50 and total >= 3:
                stats_block += f"- OK: Win rate {wr:.0f}% — profitable. Be more aggressive on high-conviction setups.\n"
            elif wr < 40 and total >= 5:
                stats_block += "- NOTE: Low win rate — check if you're calling NEUTRAL too often. Being wrong is better than being silent.\n"
            neutral_count = sum(1 for s in stats if s.get("regime") == "RANGE" and s["total"] > 0)
            if neutral_count > total * 0.4:
                stats_block += "- WARNING: >40% of your calls are NEUTRAL/NO TRADE. You are being too conservative. The market always has direction — find it.\n"

        # ── Build time-series context from coin data ──
        # SpotCVD sparkline (20 bars x 5min = 100 min history)
        spot_spark = coin_data.get('spot_cvd_spark', [])
        fut_spark = coin_data.get('fut_cvd_spark', [])

        def describe_spark(arr, name):
            if not arr or len(arr) < 3:
                return f"  {name}: no history\n"
            first, last = arr[0], arr[-1]
            mn, mx = min(arr), max(arr)
            mid = arr[len(arr)//2]
            # Trend detection
            first_half = sum(arr[:len(arr)//2]) / max(len(arr)//2, 1)
            second_half = sum(arr[len(arr)//2:]) / max(len(arr)//2, 1)
            trend = "ACCELERATING" if abs(second_half) > abs(first_half) * 1.2 else \
                    "DECELERATING" if abs(second_half) < abs(first_half) * 0.8 else "STEADY"
            # Reversal check
            reversal = ""
            if len(arr) >= 6:
                recent3 = arr[-3:]
                prev3 = arr[-6:-3]
                avg_recent = sum(recent3) / 3
                avg_prev = sum(prev3) / 3
                if avg_recent > 0 and avg_prev < 0:
                    reversal = " *** JUST FLIPPED POSITIVE ***"
                elif avg_recent < 0 and avg_prev > 0:
                    reversal = " *** JUST FLIPPED NEGATIVE ***"
            # Format values
            vals = [f"{v:,.0f}" for v in arr]
            return (f"  {name} (20 bars, oldest→newest, each bar=5min):\n"
                    f"    Raw: [{', '.join(vals[-10:])}] (last 10 bars)\n"
                    f"    100min ago: {first:,.0f} → now: {last:,.0f} | range: {mn:,.0f} to {mx:,.0f}\n"
                    f"    Trend: {trend}{reversal}\n")

        timeseries_block = "\nTIME-SERIES DATA (read carefully — this shows HOW we got here):\n"
        timeseries_block += describe_spark(spot_spark, "SpotCVD")
        timeseries_block += describe_spark(fut_spark, "FuturesCVD")
        if not spot_spark or len(spot_spark) < 3:
            timeseries_block += "  ⚠️ SpotCVD data UNAVAILABLE — do NOT reference SpotCVD in analysis.\n"
        if not fut_spark or len(fut_spark) < 3:
            timeseries_block += "  ⚠️ FuturesCVD data UNAVAILABLE — do NOT reference FuturesCVD in analysis.\n"

        # Whale context from state
        whale_block = ""
        extra = body.get("extra", {})
        whale_alerts = extra.get("whale_alerts", [])
        fr_extremes = extra.get("fr_extremes", {})
        liq_cluster = extra.get("liq_cluster", {})

        if whale_alerts:
            relevant = [a for a in whale_alerts if a.get("symbol") in [symbol, "BTC"]]
            if relevant:
                whale_block = f"\nWHALE ACTIVITY (BTC + {symbol}) — from Hyperliquid on-chain data:\n"
                for a in relevant[:8]:
                    whale_block += f"  - {a.get('time','')} {a.get('symbol','')} {a.get('action','')} {a.get('direction','')} ${a.get('value_usd',0):,.0f} @{a.get('entry_price',0)}\n"
                whale_block += "  NOTE: direction is derived from position_size sign (positive=LONG, negative=SHORT). OPEN=new position, CLOSE=closing position.\n"
            else:
                whale_block = f"\nWHALE ACTIVITY: No recent whale alerts for {symbol} or BTC.\n"
        else:
            whale_block = "\nWHALE ACTIVITY: NOT AVAILABLE (do NOT mention whale activity).\n"

        has_liq_data = False
        if liq_cluster and not liq_cluster.get("error") and liq_cluster.get("above", {}).get("price"):
            has_liq_data = True
            price = liq_cluster.get("price", 0)
            above = liq_cluster.get("above", {})
            below = liq_cluster.get("below", {})
            liq_block = f"\nLIQUIDATION CLUSTERS (magnets — price tends to move toward these):\n"
            if above.get("price"):
                dist = (above["price"] - price) / price * 100 if price else 0
                liq_block += f"  - ABOVE (short liq): ${above['price']:,.1f} size ${above.get('size',0):,.0f} (+{dist:.1f}% away)\n"
            if below.get("price"):
                dist = (below["price"] - price) / price * 100 if price else 0
                liq_block += f"  - BELOW (long liq): ${below['price']:,.1f} size ${below.get('size',0):,.0f} ({dist:.1f}% away)\n"
            whale_block += liq_block
        else:
            whale_block += "\nLIQUIDATION MAP: NOT AVAILABLE (do NOT invent liq cluster prices)\n"

        if fr_extremes:
            neg = fr_extremes.get("most_negative", [])
            pos = fr_extremes.get("most_positive", [])
            if neg or pos:
                whale_block += "\nFUNDING RATE EXTREMES (market-wide):\n"
                for x in neg[:3]:
                    whale_block += f"  - {x.get('coin','')} FR: {x.get('fr',0):.4f}% (shorts pay)\n"
                for x in pos[:3]:
                    whale_block += f"  - {x.get('coin','')} FR: {x.get('fr',0):.4f}% (longs pay)\n"

        # ── Determine session + funding proximity ──
        import datetime as _dt
        now_wib = _dt.datetime.utcnow() + _dt.timedelta(hours=7)
        hour_wib = now_wib.hour
        if 7 <= hour_wib < 15:
            session_label = "ASIA (low-medium volume)"
        elif 14 <= hour_wib < 19:
            session_label = "LONDON (medium-high volume)"
        elif 19 <= hour_wib or hour_wib < 3:
            session_label = "NY (HIGHEST volume)"
        else:
            session_label = "NY-ASIA OVERLAP (thin liquidity — AVOID)"

        # Funding settlement every 8h: 00:00, 08:00, 16:00 UTC
        utc_now = _dt.datetime.utcnow()
        funding_hours = [0, 8, 16]
        next_fund = None
        for fh in sorted(funding_hours):
            candidate = utc_now.replace(hour=fh, minute=0, second=0, microsecond=0)
            if candidate <= utc_now:
                candidate += _dt.timedelta(days=1) if fh == max(funding_hours) else _dt.timedelta(hours=0)
                candidate = utc_now.replace(hour=fh, minute=0, second=0) + _dt.timedelta(days=1 if candidate <= utc_now else 0)
            if candidate > utc_now:
                if next_fund is None or candidate < next_fund:
                    next_fund = candidate
        mins_to_funding = int((next_fund - utc_now).total_seconds() / 60) if next_fund else 999
        funding_warning = ""
        if mins_to_funding <= 60:
            funding_warning = f"\n⚠️ FUNDING SETTLEMENT IN {mins_to_funding} MINUTES — check squeeze/flush risk!"
        if mins_to_funding <= 15:
            funding_warning = f"\n🚨 FUNDING IN {mins_to_funding} MIN — DANGER ZONE. NO ENTRY unless extreme FR squeeze play."

        # ── Taker math ──
        taker_buy = coin_data.get('taker_buy_vol', 0) or 0
        taker_sell = coin_data.get('taker_sell_vol', 0) or 0
        taker_net = taker_buy - taker_sell
        price = coin_data.get('price', 0) or 0
        price_change_24h_pct = coin_data.get('change_24h', 0) or 0

        # ── OI interpretation (Rule 2 matrix) ──
        oi_change = coin_data.get('oi_change_1h', 0) or 0
        oi_price_interp = "N/A"
        if oi_change > 0.5 and price_change_24h_pct > 0:
            oi_price_interp = "MOMENTUM — new positions entering with trend"
        elif oi_change > 0.5 and price_change_24h_pct < 0:
            oi_price_interp = "SHORT ADDING — aggressive shorts entering"
        elif oi_change < -0.5 and price_change_24h_pct > 0:
            oi_price_interp = "SHORT COVERING — less reliable rally"
        elif oi_change < -0.5 and price_change_24h_pct < 0:
            oi_price_interp = "DELEVERAGING — forced liquidation"
        else:
            oi_price_interp = coin_data.get('oi_interp', 'FLAT / NO CLEAR SIGNAL')

        # Funding rate value
        fr_val = coin_data.get('funding_rate', 0) or 0
        fr_extreme = abs(fr_val) > 0.05

        # Build the analysis prompt
        prompt = f"""You are TELEGLAS MONEY FLOW ANALYST — a crypto derivatives execution system using the MONEY FLOW TRADING RULES v2 framework by Ricoz87.
You learn from your past calls. You CALCULATE money flow, not guess direction.
"Kita bukan menebak arah — kita MENGHITUNG uang yang mengalir."

COIN: {symbol}
SESSION: {session_label} (WIB {now_wib.strftime('%H:%M')})
FUNDING: Next settlement in {mins_to_funding} min{funding_warning}

═══ RAW DATA ═══
PRICE: ${price:,.4f} | 24h Change: {price_change_24h_pct:+.2f}%
VOLUME 24h: ${coin_data.get('volume_24h', 0):,.0f}

SPOT CVD: {coin_data.get('spot_cvd', 0):,.0f} | Dir: {coin_data.get('spot_cvd_dir', 'FLAT')} | Slope: {coin_data.get('spot_cvd_slope', 0):.3f}
FUTURES CVD: {coin_data.get('fut_cvd', 0):,.0f} | Dir: {coin_data.get('fut_cvd_dir', 'FLAT')} | Slope: {coin_data.get('fut_cvd_slope', 0):.3f}
OI: ${coin_data.get('oi_usd', 0):,.0f} | 1h Change: {oi_change:+.2f}% | Matrix: {oi_price_interp}
FUNDING RATE: {fr_val:.4f}%{' ⚠️ EXTREME' if fr_extreme else ''}
TAKER BUY: ${taker_buy:,.0f} | SELL: ${taker_sell:,.0f} | NET: ${taker_net:+,.0f}
ORDERBOOK: Bid ${coin_data.get('ob_bid', 0):,.0f} vs Ask ${coin_data.get('ob_ask', 0):,.0f} | Dominant: {coin_data.get('ob_dominant', 'N/A')}
LONG SCORE: {coin_data.get('long_score', 0)} | SHORT SCORE: {coin_data.get('short_score', 0)} | BIAS: {coin_data.get('bias', 'NEUTRAL')}
{timeseries_block}
REGIME ENGINE:
- Detected: {regime_data.get('regime', 'N/A')} | Confidence: {regime_data.get('regime_conf', 0)}%
- Grade: {regime_data.get('grade', 'N/A')} | Bias: {regime_data.get('bias', 'N/A')}
- Long Raw: {regime_data.get('long_raw', 0)} | Short Raw: {regime_data.get('short_raw', 0)}
{whale_block}{history_block}{stats_block}
═══ MONEY FLOW RULES v2.1 — BALANCED FRAMEWORK ═══

YOUR JOB IS TO FIND ENTRIES, NOT AVOID THEM.
You are a TRADER, not a risk manager. Every market has a trade — find it.
NEUTRAL/NO TRADE is only acceptable when data GENUINELY conflicts (SpotCVD vs FutCVD opposing AND taker flat). If flow is directional, you MUST give a directional bias with entry.

═══ WHEN TO ENTER (check these FIRST) ═══
✅ LONG kalau: SpotCVD RISING atau FutCVD RISING + taker net positif. Ga perlu SEMUA aligned — 2 dari 3 cukup.
✅ SHORT kalau: SpotCVD FALLING atau FutCVD FALLING + taker net negatif. 2 dari 3 cukup.
✅ CONTINUATION entry valid: setelah pullback dalam trend, selama CVD direction belum flip. Rally >2% bukan alasan skip kalau flow masih accelerating.
✅ REVERSAL entry valid: setelah exhaustion (taker drop >80% dari peak) + CVD flip. Ga perlu tunggu semua metric sempurna.
✅ SQUEEZE entry: FR extreme + crowded positioning = high R/R play.
✅ Kalau Long Score atau Short Score > 50 di regime engine → itu sudah sinyal directional, RESPECT IT.

═══ ANALYSIS RULES ═══
FLOW ACCELERATION — check if SpotCVD/FutCVD delta is ACCELERATING (each candle bigger) or DECELERATING (each candle smaller). 3+ candles same direction = signal.

OI + PRICE MATRIX — OI up + Price up = MOMENTUM. OI up + Price down = SHORT ADDING. OI down + Price up = SHORT COVERING. OI down + Price down = DELEVERAGING.

WHALE = CONFIRMATION — whale confirms or denies flow direction, not a standalone trigger.

FUNDING — within 60 min of settlement: FR negative + shorts >65% = squeeze risk. FR positive + longs >65% = flush risk.

═══ HARD BLOCKS (only these 3) ═══
❌ JANGAN LONG kalau SpotCVD DAN FutCVD keduanya FALLING + taker net negatif (semua 3 berlawanan)
❌ JANGAN SHORT kalau SpotCVD DAN FutCVD keduanya RISING + taker net positif (semua 3 berlawanan)
❌ JANGAN ENTRY 15 min sebelum funding KECUALI extreme FR squeeze

EVERYTHING ELSE = TRADEABLE. Find the entry.

═══ CONVICTION GUIDE ═══
HIGH: 3/3 aligned (CVD + taker + OI). Give specific entry price.
MEDIUM: 2/3 aligned. Give entry with tighter SL.
LOW: hanya kalau data genuinely mixed/conflicting. Still give conditional entry ("entry kalau X terjadi").
NO TRADE: ONLY jika semua data flat/zero/unavailable. Ini harus JARANG — <15% of calls.

DATA INTEGRITY:
- ONLY use data provided above. NEVER invent values.
- If data shows 0 or N/A → say "DATA UNAVAILABLE", do NOT fabricate.

═══ OUTPUT FORMAT (follow EXACTLY — RESPOND IN BAHASA INDONESIA) ═══
IMPORTANT: Write your ENTIRE analysis in Bahasa Indonesia. All explanations, evidence, verdicts, and danger notes must be in Indonesian. Technical terms (SpotCVD, FutCVD, OI, etc.) may stay in English but all descriptions and reasoning MUST be in Indonesian.

1. FLOW SNAPSHOT
SpotCVD: [direction] [magnitude] [candle ratio if available]
FutCVD: [direction] [magnitude] [candle ratio if available]
Taker: [net $] [buy/sell dominant %]
OI: [change] [matrix interpretation]
OB: [bid/ask dominant] [ratio]
Whale: [net direction] [key actions] (or "N/A")

2. FLOW STATE
[BUYING ACCELERATING / SELLING ACCELERATING / MOMENTUM DECELERATING / CHOPPY / EXHAUSTED]
Evidence: [2-3 data points]

3. FUNDING STATE
FR: {fr_val:.4f}% | Squeeze Risk: [LOW/MEDIUM/HIGH]
Settlement: {mins_to_funding} min | Action: [SAFE / AVOID / SQUEEZE PLAY]

4. MARKET CLASSIFICATION
State: [CONTINUATION / REVERSAL / CHOP / DISTRIBUTION / ABSORPTION / SQUEEZE / PROFIT-TAKING / FAILED REVERSAL]
Confidence: [HIGH / MEDIUM / LOW]
Evidence: [2-3 key data points]

5. VERDICT
BIAS: LONG / SHORT (NEUTRAL hanya kalau semua data flat/unavailable)
CONVICTION: HIGH / MEDIUM / LOW
ENTRY: [specific price — WAJIB kasih angka. Gunakan support/resistance terdekat atau current price ±0.3%]
STOP LOSS: [specific price — WAJIB]
TARGET: [specific price — WAJIB]
R/R: [ratio — minimum 1:1.5]

6. KEY SIGNALS
- [most important signal — apa yang bikin lo yakin arah ini]
- [second signal]
- [confirmation yang ditunggu kalau conviction LOW]

7. DANGER
[what invalidates this setup — 1 specific condition]"""

        http = await get_http()
        resp = await http.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 1500,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        result = resp.json()

        if resp.status_code != 200:
            error_msg = result.get("error", {}).get("message", "API error")
            return JSONResponse({"error": error_msg}, status_code=resp.status_code)

        text = result.get("content", [{}])[0].get("text", "No response")

        # Extract bias and grade from AI response for tracking
        ai_bias = "NEUTRAL"
        if "BIAS: LONG" in text: ai_bias = "LONG"
        elif "BIAS: SHORT" in text: ai_bias = "SHORT"

        ai_grade = regime_data.get("grade", "C")

        # Save to database for tracking
        entry_price = coin_data.get("price", 0)
        analysis_id = await asyncio.to_thread(
            _save_analysis,
            symbol, regime_data.get("regime", "UNKNOWN"),
            ai_bias, ai_grade, regime_data.get("confidence", 0),
            text, entry_price, coin_data, regime_data
        )

        # Return with tracking info
        return JSONResponse({
            "analysis": text,
            "model": result.get("model", ""),
            "symbol": symbol,
            "tracking_id": analysis_id,
            "entry_price": entry_price,
            "history_count": len(history),
            "total_calls": sum(s["total"] for s in stats) if stats else 0,
            "win_rate": (sum(s["wins"] for s in stats) / sum(s["total"] for s in stats) * 100)
                        if stats and sum(s["total"] for s in stats) > 0 else None,
        })

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    try:
        await ws.send_text(last_state)
    except Exception:
        pass
    try:
        while True:
            raw = await ws.receive_text()
            await handle_client_message(ws, raw)
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(ws)
        client_coins.pop(ws, None)


async def broadcast(data: str):
    dead = set()
    for ws in clients:
        try:
            await ws.send_text(data)
        except Exception:
            dead.add(ws)
    clients.difference_update(dead)
    for ws in dead:
        client_coins.pop(ws, None)


async def state_poller():
    """Poll state file and broadcast changes."""
    global last_state
    while True:
        try:
            if STATE_FILE.exists():
                raw = STATE_FILE.read_text()
                if raw and raw != last_state:
                    last_state = raw
                    await broadcast(raw)
        except Exception:
            pass
        await asyncio.sleep(POLL_INTERVAL)


async def fetch_whale_positions() -> list:
    """Build active whale positions from recent alerts — OPEN without matching CLOSE."""
    try:
        data = await _cached_cg_get(f"{CG_BASE}/api/hyperliquid/whale-alert", params={"limit": 100})
        if data is None:
            return []
        if str(data.get("code")) != "0":
            return []

        alerts = data.get("data", [])
        opens = {}
        closes = set()

        # Process chronologically — later events override
        for a in alerts:
            key = a.get("user", "") + "_" + a.get("symbol", "")
            if a.get("position_action") == 2:
                closes.add(key)
            elif a.get("position_action") == 1 and key not in closes:
                if key not in opens:
                    opens[key] = a

        # Get current prices from state
        prices = {}
        try:
            with open("/tmp/tg_state.json") as f:
                st = json.load(f)
            for sym, cd in st.get("coins", {}).items():
                if cd.get("price"):
                    prices[sym] = cd["price"]
        except Exception:
            pass

        positions = []
        for a in opens.values():
            val = abs(float(a.get("position_value_usd", 0)))
            if val < 1_000_000:
                continue
            coin = a.get("symbol", "")
            size = float(a.get("position_size", 0))
            side = "LONG" if size > 0 else "SHORT"
            entry = float(a.get("entry_price", 0))
            liq = float(a.get("liq_price", 0))
            cur_price = prices.get(coin, entry)

            # Calc PnL
            if entry > 0 and cur_price > 0:
                if side == "LONG":
                    pnl_pct = (cur_price - entry) / entry * 100
                else:
                    pnl_pct = (entry - cur_price) / entry * 100
                pnl_usd = val * pnl_pct / 100
            else:
                pnl_pct = 0
                pnl_usd = 0

            # Liq distance
            liq_dist_pct = abs(liq - cur_price) / cur_price * 100 if cur_price > 0 and liq > 0 else 999

            positions.append({
                "coin": coin,
                "side": side,
                "size_usd": val,
                "entry_price": entry,
                "liq_price": liq,
                "cur_price": cur_price,
                "pnl_usd": pnl_usd,
                "pnl_pct": pnl_pct,
                "liq_dist_pct": liq_dist_pct,
            })

        positions.sort(key=lambda x: x["size_usd"], reverse=True)
        return positions
    except Exception:
        return []


async def fetch_whale_alerts() -> list:
    """Fetch latest whale alerts from Hyperliquid via CoinGlass."""
    try:
        data = await _cached_cg_get(f"{CG_BASE}/api/hyperliquid/whale-alert", params={"limit": 20})
        if data is None:
            return []
        if str(data.get("code")) != "0":
            return []
        alerts = []
        for a in data.get("data", []):
            action = "OPEN" if a.get("position_action") == 1 else "CLOSE"
            direction = "LONG" if float(a.get("position_size", 0)) > 0 else "SHORT"
            val = abs(float(a.get("position_value_usd", 0)))
            if val < 500_000:
                continue
            from datetime import datetime, timezone, timedelta
            ts = int(a.get("create_time", 0))
            t_str = datetime.fromtimestamp(ts / 1000, tz=timezone(timedelta(hours=7))).strftime('%H:%M:%S') if ts else ""
            alerts.append({
                "symbol": a.get("symbol", ""),
                "direction": direction,
                "action": action,
                "value_usd": val,
                "entry_price": float(a.get("entry_price", 0)),
                "time": t_str,
            })
        return alerts
    except Exception:
        return []


async def auto_push_loop():
    """Every 45s, auto-push liq_cluster + cvd_history + taker + long_short + whales."""
    await asyncio.sleep(10)
    while True:
        try:
            watched = set(client_coins.values())

            # Fetch whale alerts + positions (global) and broadcast to all
            whales, positions = await asyncio.gather(
                fetch_whale_alerts(),
                fetch_whale_positions(),
            )
            if whales or positions:
                dead = set()
                for ws in clients:
                    try:
                        if whales:
                            await ws.send_text(json.dumps({"type": "whale_alerts", "data": whales}))
                        if positions:
                            await ws.send_text(json.dumps({"type": "whale_positions", "data": positions}))
                    except Exception:
                        dead.add(ws)
                clients.difference_update(dead)
                for ws in dead:
                    client_coins.pop(ws, None)

            if not watched:
                await asyncio.sleep(45)
                continue

            for symbol in watched:
                liq, cvd, taker, ls = await asyncio.gather(
                    fetch_liq_cluster(symbol),
                    fetch_cvd_history(symbol),
                    fetch_taker(symbol),
                    fetch_long_short(symbol),
                )

                for ws, coin in list(client_coins.items()):
                    if coin != symbol:
                        continue
                    try:
                        for msg_type, mdata in [
                            ("liq_cluster", liq),
                            ("cvd_history", cvd),
                            ("taker", taker),
                            ("long_short", ls),
                        ]:
                            await ws.send_text(json.dumps({
                                "type": msg_type,
                                "symbol": symbol,
                                "data": mdata,
                            }))
                    except Exception:
                        pass
        except Exception:
            pass
        await asyncio.sleep(45)


async def outcome_checker_loop():
    """Every 60s, check pending AI analyses against current prices."""
    await asyncio.sleep(30)
    while True:
        try:
            await asyncio.to_thread(_check_outcomes)
        except Exception:
            pass
        await asyncio.sleep(60)


def _query_ai_history(symbol: str = None, limit: int = 20) -> list:
    """Query AI analysis history (sync, run via to_thread)."""
    con = sqlite3.connect(str(_AI_DB))
    con.row_factory = sqlite3.Row
    if symbol:
        rows = con.execute(
            "SELECT * FROM analyses WHERE symbol=? ORDER BY created_at DESC LIMIT ?",
            (symbol, limit)
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT * FROM analyses ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    con.close()
    return [dict(r) for r in rows]

@app.get("/api/ai-history")
async def ai_history(symbol: str = None, limit: int = 20):
    """Get AI analysis history with outcomes."""
    result = await asyncio.to_thread(_query_ai_history, symbol, limit)
    return JSONResponse(result)


@app.get("/api/ai-stats")
async def ai_stats_endpoint(symbol: str = None):
    """Get AI win/loss stats per symbol per regime."""
    stats = await asyncio.to_thread(_get_ai_stats, symbol)
    # Calculate overall
    total = sum(s["total"] for s in stats)
    wins = sum(s["wins"] for s in stats)
    losses = sum(s["losses"] for s in stats)
    win_rate = wins / total * 100 if total > 0 else 0
    return JSONResponse({
        "overall": {"total": total, "wins": wins, "losses": losses, "win_rate": round(win_rate, 1)},
        "by_regime": stats
    })


_logger = logging.getLogger("tg-dashboard")

def _task_done_callback(task: asyncio.Task):
    """Log exceptions from background tasks instead of silently swallowing them."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        _logger.error(f"Background task {task.get_name()} crashed: {exc}", exc_info=exc)

@app.on_event("startup")
async def startup():
    for coro, name in [
        (state_poller(), "state_poller"),
        (auto_push_loop(), "auto_push_loop"),
        (outcome_checker_loop(), "outcome_checker"),
    ]:
        task = asyncio.create_task(coro, name=name)
        task.add_done_callback(_task_done_callback)


@app.on_event("shutdown")
async def shutdown():
    global _http
    if _http and not _http.is_closed:
        await _http.aclose()


# ── Signal Lifecycle Proxy ────────────────────────────
# Lifecycle endpoints live on the legacy API (port 8081).
# Proxy them here so the dashboard frontend can access them.
_LEGACY_API = "http://127.0.0.1:8081"

@app.get("/api/signals/active")
async def proxy_signals_active():
    """Proxy to legacy API: all alive primary signals."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{_LEGACY_API}/api/signals/active")
            return JSONResponse(r.json(), status_code=r.status_code)
    except Exception:
        return JSONResponse({"signals": []})

@app.get("/api/signals/primary/{symbol}")
async def proxy_signals_primary(symbol: str):
    """Proxy to legacy API: primary signal + recent history for a coin."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{_LEGACY_API}/api/signals/primary/{symbol}")
            return JSONResponse(r.json(), status_code=r.status_code)
    except Exception:
        return JSONResponse({"symbol": symbol, "primary": None, "recent": []})

@app.get("/api/signals/lifecycle")
async def proxy_signals_lifecycle():
    """Proxy to legacy API: full lifecycle overview."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{_LEGACY_API}/api/signals/lifecycle")
            return JSONResponse(r.json(), status_code=r.status_code)
    except Exception:
        return JSONResponse({"coins": {}, "stats": {}})

@app.get("/api/calibration")
async def proxy_calibration():
    """Proxy to legacy API: confidence calibration table."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{_LEGACY_API}/api/calibration")
            return JSONResponse(r.json(), status_code=r.status_code)
    except Exception:
        return JSONResponse({"table": [], "stats": {}})


_base = Path(__file__).parent
app.mount("/static", StaticFiles(directory=str(_base / "static")), name="static")


if __name__ == "__main__":
    import uvicorn
    _port = _config.get("dashboard", {}).get("port", 8082)
    uvicorn.run(app, host="0.0.0.0", port=_port, log_level="warning")
