# Dashboard API - FastAPI Server
# Production-ready web interface for TELEGLAS Pro

"""
Dashboard API Module

Provides REST API and WebSocket endpoints for:
- Real-time system statistics
- Coin management (add/remove/toggle)
- Signal monitoring
- Order flow visualization
- Mobile-responsive web interface
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, HTTPException, WebSocketDisconnect, Depends, Header, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncio
import hmac
import html as html_mod
import json
import logging
import queue
import re
import threading
import time
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from typing import List, Optional
from pathlib import Path
import yaml

# Load dashboard config
_config_path = Path(__file__).parent.parent.parent / "config" / "config.yaml"
_dashboard_config = {}
if _config_path.exists():
    with open(_config_path) as _f:
        _full_config = yaml.safe_load(_f)
        _dashboard_config = _full_config.get("dashboard", {})

_raw_token = _dashboard_config.get("api_token", "")
# Reject placeholder token — treat as no auth (force user to set real token)
if _raw_token and "GANTI" in _raw_token.upper():
    logging.getLogger("DashboardAPI").warning(
        "api_token is still the default placeholder — auth DISABLED. "
        "Set a real token in config/config.yaml -> dashboard.api_token"
    )
    _raw_token = ""
API_TOKEN = _raw_token
CORS_ORIGINS = _dashboard_config.get("cors_origins", ["http://localhost:3000"])

# Symbol validation regex: uppercase alphanumeric, 3-20 chars (e.g. BTCUSDT, 1000PEPEUSDT)
_SYMBOL_RE = re.compile(r'^[A-Z0-9]{3,20}$')

def _validate_symbol(symbol: str) -> str:
    """Validate and sanitize a trading symbol from URL path or query params."""
    symbol = symbol.upper().strip()
    if not _SYMBOL_RE.match(symbol):
        raise HTTPException(
            status_code=400,
            detail="Invalid symbol format. Use only uppercase letters/numbers (3-20 chars)"
        )
    return symbol

async def verify_token(authorization: str = Header(None)):
    """Simple Bearer token auth with constant-time comparison."""
    if not API_TOKEN:
        return True  # Skip auth if token not set in config
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    provided = authorization.replace("Bearer ", "", 1)
    if not hmac.compare_digest(provided, API_TOKEN):
        raise HTTPException(status_code=403, detail="Invalid token")
    return True

# Rate limiting
_rate_limit_store: dict = defaultdict(list)
_RATE_LIMIT = 30  # requests per minute
_RATE_LIMIT_MAX_IPS = 10000  # evict oldest IPs if store exceeds this

async def check_rate_limit(request: Request):
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    # Cleanup old entries for this IP
    _rate_limit_store[client_ip] = [
        t for t in _rate_limit_store[client_ip] if now - t < 60
    ]
    # Evict empty IPs to prevent unbounded memory growth
    if not _rate_limit_store[client_ip]:
        del _rate_limit_store[client_ip]
    if len(_rate_limit_store) > _RATE_LIMIT_MAX_IPS:
        # Remove oldest IPs (those with oldest last-request)
        sorted_ips = sorted(_rate_limit_store.keys(),
                            key=lambda ip: max(_rate_limit_store[ip]) if _rate_limit_store[ip] else 0)
        for ip in sorted_ips[:len(_rate_limit_store) - _RATE_LIMIT_MAX_IPS]:
            del _rate_limit_store[ip]
    entries = _rate_limit_store.get(client_ip, [])
    if len(entries) >= _RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Too many requests")
    _rate_limit_store[client_ip].append(now)

# Lifespan context manager (replaces deprecated on_event)
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown logic."""
    global _event_loop
    _event_loop = asyncio.get_running_loop()
    print("=" * 60)
    print("TELEGLAS Dashboard API Started")
    print("=" * 60)
    print("Dashboard URL: http://localhost:8080")
    print("WebSocket URL: ws://localhost:8080/ws")
    print("API Endpoints: http://localhost:8080/docs")
    if not API_TOKEN:
        print("WARNING: No API token configured - auth is DISABLED")
    print("=" * 60)
    yield
    # Shutdown
    for connection in list(active_connections):
        try:
            await connection.close()
        except Exception:
            pass
    active_connections.clear()
    print("Dashboard API Shutdown Complete")

# Create FastAPI app
app = FastAPI(
    title="TELEGLAS Pro Dashboard",
    description="Real-time cryptocurrency trading intelligence dashboard",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files (HTML, JS, CSS)
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# CRITICAL FIX Bug #5: Thread-safe lock for global state
# Prevents data corruption when multiple threads access system_state
state_lock = threading.Lock()

# Thread-safe queue for subscription requests from dashboard -> main.py
# Items are dicts: {"action": "subscribe"|"unsubscribe", "symbol": "BTCUSDT"}
_subscription_queue: queue.Queue = queue.Queue()

# Event loop reference (set by lifespan) for cross-thread broadcasting
_event_loop: Optional[asyncio.AbstractEventLoop] = None

# Database reference (set by main.py after init)
_db = None

# Monotonic signal ID counter (thread-safe via state_lock)
_signal_id_counter = 0

# Global state (updated by main.py)
system_state = {
    "stats": {
        "messages_received": 0,
        "messages_processed": 0,
        "liquidations_processed": 0,
        "trades_processed": 0,
        "signals_generated": 0,
        "alerts_sent": 0,
        "errors": 0,
        "uptime_seconds": 0
    },
    "coins": [],
    "signals": [],
    "order_flow": {}
}

# WebSocket connections
active_connections: List[WebSocket] = []

# Pydantic models for API requests
class AddCoinRequest(BaseModel):
    """Request model for adding a coin"""
    symbol: str

class ToggleCoinRequest(BaseModel):
    """Request model for toggling coin alerts"""
    active: bool

# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.get("/")
async def root():
    """Serve dashboard HTML with auth token injected."""
    html_file = Path(__file__).parent / "static" / "index.html"
    if html_file.exists():
        if API_TOKEN:
            # Inject token as meta tag so frontend can authenticate
            html_content = html_file.read_text()
            safe_token = html_mod.escape(API_TOKEN, quote=True)
            token_meta = f'<meta name="api-token" content="{safe_token}">'
            html_content = html_content.replace('</head>', f'    {token_meta}\n</head>', 1)
            return HTMLResponse(content=html_content)
        return FileResponse(str(html_file))
    return {"message": "Dashboard HTML not found. Please create src/dashboard/static/index.html"}

@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring/load balancers."""
    with state_lock:
        uptime = system_state["stats"].get("uptime_seconds", 0)
        coins_count = len(system_state["coins"])
    return {"status": "ok", "uptime_seconds": uptime, "coins_tracked": coins_count}

@app.get("/api/stats")
async def get_stats(_rl=Depends(check_rate_limit)):
    """Get current system statistics (thread-safe, rate-limited)."""
    with state_lock:
        return deepcopy(system_state["stats"])

@app.get("/api/coins")
async def get_coins(_rl=Depends(check_rate_limit)):
    """Get list of monitored coins with their current status (thread-safe, rate-limited)."""
    with state_lock:
        return {"coins": deepcopy(system_state["coins"])}

@app.get("/api/signals")
async def get_signals(limit: int = 50, _rl=Depends(check_rate_limit)):
    """Get recent signals (thread-safe, rate-limited)."""
    limit = min(max(limit, 1), 200)  # Clamp to 1-200
    with state_lock:
        signals = deepcopy(system_state["signals"][-limit:])
    signals.reverse()  # Most recent first
    return {"signals": signals}

@app.get("/api/orderflow/{symbol}")
async def get_order_flow(symbol: str, _rl=Depends(check_rate_limit)):
    """Get current order flow data for a specific symbol."""
    symbol = _validate_symbol(symbol)
    with state_lock:
        flow = deepcopy(system_state["order_flow"].get(symbol, {
            "buy_ratio": 0,
            "sell_ratio": 0,
            "large_buys": 0,
            "large_sells": 0,
            "last_update": "N/A"
        }))
    return flow

@app.post("/api/coins/add")
async def add_coin(request: AddCoinRequest, _auth=Depends(verify_token), _rl=Depends(check_rate_limit)):
    """Add a new coin to monitoring with input validation."""
    # Validate and sanitize input
    symbol = request.symbol.upper().strip()

    # Remove common suffixes using endswith (not replace, to avoid mangling USDC etc.)
    for suffix in ['USDT', 'BUSD', 'USD']:
        if symbol.endswith(suffix):
            symbol = symbol[:-len(suffix)]
            break  # Only strip one suffix

    # Validate format - only alphanumeric, 1-10 characters
    if not re.match(r'^[A-Z0-9]{1,10}$', symbol):
        raise HTTPException(
            status_code=400,
            detail="Invalid symbol format. Use only letters and numbers (1-10 characters)"
        )

    if not symbol:
        raise HTTPException(status_code=400, detail="Symbol cannot be empty")

    # Reconstruct with USDT
    symbol = symbol + "USDT"

    # Add to coins list (thread-safe: check + append under same lock)
    new_coin = {
        "symbol": symbol,
        "active": True,
        "buy_ratio": 0,
        "sell_ratio": 0,
        "large_buys": 0,
        "large_sells": 0,
        "last_update": "just added"
    }

    with state_lock:
        if any(coin["symbol"] == symbol for coin in system_state["coins"]):
            raise HTTPException(status_code=400, detail=f"{symbol} is already monitored")
        system_state["coins"].append(new_coin)

    # Request trade channel subscription from main.py
    _subscription_queue.put({"action": "subscribe", "symbol": symbol})

    # Broadcast to all WebSocket clients
    await broadcast_update("coin_added", new_coin)

    return {"success": True, "coin": new_coin}

@app.delete("/api/coins/remove/{symbol}")
async def remove_coin(symbol: str, _auth=Depends(verify_token), _rl=Depends(check_rate_limit)):
    """Remove a coin from monitoring (thread-safe, validated)."""
    symbol = _validate_symbol(symbol)

    with state_lock:
        system_state["coins"] = [c for c in system_state["coins"] if c["symbol"] != symbol]

    # Request trade channel unsubscription from main.py
    _subscription_queue.put({"action": "unsubscribe", "symbol": symbol})

    # Broadcast to all WebSocket clients
    await broadcast_update("coin_removed", {"symbol": symbol})

    return {"success": True, "symbol": symbol}

@app.patch("/api/coins/{symbol}/toggle")
async def toggle_coin(symbol: str, request: ToggleCoinRequest,
                      _auth=Depends(verify_token), _rl=Depends(check_rate_limit)):
    """Enable/disable alerts for a coin (validated)."""
    symbol = _validate_symbol(symbol)

    with state_lock:
        coin = next((c for c in system_state["coins"] if c["symbol"] == symbol), None)
        if not coin:
            raise HTTPException(status_code=404, detail="Coin not found")

        coin["active"] = request.active
        coin_copy = deepcopy(coin)

    # Broadcast to all WebSocket clients
    await broadcast_update("coin_toggled", coin_copy)

    return {"success": True, "coin": coin_copy}

# ============================================================================
# CSV EXPORT & DB STATS ENDPOINTS
# ============================================================================

@app.get("/api/export/signals.csv")
async def export_signals_csv(_auth=Depends(verify_token)):
    """Export all signals as CSV file (downloadable as spreadsheet)."""
    if not _db:
        raise HTTPException(status_code=503, detail="Database not initialized")
    csv_data = await _db.export_signals_csv(limit=5000)
    if not csv_data:
        raise HTTPException(status_code=404, detail="No signals to export")
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=teleglas_signals.csv"}
    )

@app.get("/api/export/baselines.csv")
async def export_baselines_csv(symbol: str = None, _auth=Depends(verify_token)):
    """Export hourly baselines as CSV file."""
    if not _db:
        raise HTTPException(status_code=503, detail="Database not initialized")
    # Validate symbol if provided
    if symbol:
        symbol = _validate_symbol(symbol)
    csv_data = await _db.export_baselines_csv(symbol=symbol)
    if not csv_data:
        raise HTTPException(status_code=404, detail="No baselines to export")
    # Sanitize filename: only allow safe characters
    safe_sym = re.sub(r'[^A-Z0-9]', '', symbol) if symbol else ""
    filename = f"teleglas_baselines_{safe_sym}.csv" if safe_sym else "teleglas_baselines.csv"
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@app.get("/api/stats/signals")
async def get_signal_stats(_auth=Depends(verify_token)):
    """Get aggregate signal statistics from database."""
    if not _db:
        raise HTTPException(status_code=503, detail="Database not initialized")
    stats = await _db.get_signal_stats()
    by_type = await _db.get_signal_stats_by_type()
    return {"overall": stats, "by_type": by_type}

@app.get("/api/signals/history")
async def get_signal_history(symbol: str = None, limit: int = 100, _auth=Depends(verify_token)):
    """Get signal history from database (persisted across restarts)."""
    if not _db:
        raise HTTPException(status_code=503, detail="Database not initialized")
    limit = min(max(limit, 1), 5000)  # Clamp to 1-5000
    if symbol:
        symbol = _validate_symbol(symbol)
        signals = await _db.get_signals_by_symbol(symbol, limit)
    else:
        signals = await _db.get_recent_signals(limit)
    return {"signals": signals}

# ============================================================================
# WEBSOCKET ENDPOINT
# ============================================================================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time updates.

    Sends:
    - Initial state on connection (after init complete)
    - Real-time updates for stats, signals, order flow
    - Coin add/remove/toggle events
    """
    # Accept connection first, then authenticate via first message
    # (avoids exposing token in URL query params / server logs)
    await websocket.accept()

    if API_TOKEN:
        try:
            # Wait for auth message within 5 seconds
            auth_msg = await asyncio.wait_for(websocket.receive_json(), timeout=5.0)
            if auth_msg.get("type") != "auth" or not hmac.compare_digest(str(auth_msg.get("token", "")), API_TOKEN):
                await websocket.send_json({"type": "error", "message": "Invalid token"})
                await websocket.close(code=4003, reason="Invalid token")
                return
        except (asyncio.TimeoutError, Exception):
            await websocket.close(code=4003, reason="Auth timeout")
            return

    active_connections.append(websocket)

    # Wait for system initialization (max 3 seconds)
    retry_count = 0
    while retry_count < 30:  # 30 * 0.1s = 3 seconds max wait
        try:
            with state_lock:
                if len(system_state["coins"]) > 0:
                    break
        except Exception:
            pass
        await asyncio.sleep(0.1)
        retry_count += 1

    # Send initial state (thread-safe copy)
    with state_lock:
        state_snapshot = deepcopy(system_state)
    await websocket.send_json({
        "type": "initial_state",
        "data": state_snapshot
    })

    try:
        while True:
            # Keep connection alive - receive messages but don't echo back
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in active_connections:
            active_connections.remove(websocket)
    except Exception:
        if websocket in active_connections:
            active_connections.remove(websocket)

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

async def broadcast_update(event_type: str, data):
    """Broadcast updates to all connected WebSocket clients."""
    message = {
        "type": event_type,
        "data": data,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    disconnected = []
    for connection in list(active_connections):
        try:
            await connection.send_json(message)
        except Exception:
            disconnected.append(connection)

    # Remove disconnected clients
    for conn in disconnected:
        if conn in active_connections:
            active_connections.remove(conn)

# ============================================================================
# FUNCTIONS CALLED FROM MAIN.PY
# ============================================================================

def update_stats(stats: dict):
    """Update system statistics (thread-safe)."""
    with state_lock:
        system_state["stats"] = deepcopy(stats)

    # Schedule broadcast via stored event loop (safe from any thread)
    _schedule_broadcast("stats_update", stats)

def update_order_flow(symbol: str, flow_data: dict):
    """Update order flow for a symbol (thread-safe)."""
    with state_lock:
        system_state["order_flow"][symbol] = {
            **flow_data,
            "last_update": datetime.now(timezone.utc).strftime("%H:%M:%S")
        }

        # Also update in coins list
        coin = next((c for c in system_state["coins"] if c["symbol"] == symbol), None)
        if coin:
            coin.update(flow_data)
            coin["last_update"] = "just now"

    _schedule_broadcast("order_flow_update", {"symbol": symbol, **flow_data})

def add_signal(signal: dict):
    """Add a new signal to the dashboard (thread-safe, monotonic IDs)."""
    global _signal_id_counter
    now = datetime.now(timezone.utc)
    with state_lock:
        _signal_id_counter += 1
        signal_data = {
            "id": _signal_id_counter,
            "time": now.strftime("%H:%M:%S"),
            "timestamp": now.isoformat(),
            **signal
        }

        system_state["signals"].append(signal_data)

        # Keep only last 200 signals
        if len(system_state["signals"]) > 200:
            system_state["signals"] = system_state["signals"][-200:]

    _schedule_broadcast("new_signal", signal_data)

def _schedule_broadcast(event_type: str, data):
    """Schedule an async broadcast from any thread using the stored event loop."""
    try:
        if _event_loop and _event_loop.is_running():
            _event_loop.call_soon_threadsafe(
                asyncio.ensure_future,
                broadcast_update(event_type, data)
            )
    except RuntimeError:
        pass  # Event loop closed during shutdown - safe to ignore

def get_monitored_coins() -> List[str]:
    """Get list of currently monitored and active coins (thread-safe)."""
    with state_lock:
        return [coin["symbol"] for coin in system_state["coins"] if coin.get("active", True)]

def get_pending_subscriptions() -> List[dict]:
    """Drain all pending subscription requests from the dashboard."""
    requests = []
    while not _subscription_queue.empty():
        try:
            requests.append(_subscription_queue.get_nowait())
        except queue.Empty:
            break
    return requests

def initialize_coins(initial_coins: List[str]):
    """Initialize dashboard with starting coins (thread-safe)."""
    with state_lock:
        system_state["coins"] = [
            {
                "symbol": symbol,
                "active": True,
                "buy_ratio": 0,
                "sell_ratio": 0,
                "large_buys": 0,
                "large_sells": 0,
                "last_update": "N/A"
            }
            for symbol in initial_coins
        ]

# ============================================================================
# MAIN (for standalone testing)
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8080,
        log_level="info"
    )
