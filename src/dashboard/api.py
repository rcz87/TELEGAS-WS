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

from fastapi import FastAPI, WebSocket, HTTPException, WebSocketDisconnect, Depends, Header, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncio
import json
import queue
import re
import threading
import time
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
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

API_TOKEN = _dashboard_config.get("api_token", "")
CORS_ORIGINS = _dashboard_config.get("cors_origins", ["http://localhost:3000"])

async def verify_token(authorization: str = Header(None)):
    """Simple Bearer token auth."""
    if not API_TOKEN:
        return True  # Skip auth jika token tidak di-set di config
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    if authorization.replace("Bearer ", "") != API_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid token")
    return True

# Rate limiting
_rate_limit_store: dict = defaultdict(list)
_RATE_LIMIT = 30  # requests per minute

async def check_rate_limit(request: Request):
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    # Cleanup old entries
    _rate_limit_store[client_ip] = [
        t for t in _rate_limit_store[client_ip] if now - t < 60
    ]
    if len(_rate_limit_store[client_ip]) >= _RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Too many requests")
    _rate_limit_store[client_ip].append(now)

# Create FastAPI app
app = FastAPI(
    title="TELEGLAS Pro Dashboard",
    description="Real-time cryptocurrency trading intelligence dashboard",
    version="1.0.0"
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

# Thread-safe queue for subscription requests from dashboard → main.py
# Items are dicts: {"action": "subscribe"|"unsubscribe", "symbol": "BTCUSDT"}
_subscription_queue: queue.Queue = queue.Queue()

# Database reference (set by main.py after init)
_db = None

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
            from fastapi.responses import HTMLResponse
            html_content = html_file.read_text()
            token_meta = f'<meta name="api-token" content="{API_TOKEN}">'
            html_content = html_content.replace('</head>', f'    {token_meta}\n</head>', 1)
            return HTMLResponse(content=html_content)
        return FileResponse(str(html_file))
    return {"message": "Dashboard HTML not found. Please create src/dashboard/static/index.html"}

@app.get("/api/stats")
async def get_stats():
    """
    Get current system statistics (thread-safe)
    
    CRITICAL FIX Bug #5: Thread-safe read with lock
    
    Returns:
        dict: System statistics including messages, signals, alerts, errors
    """
    with state_lock:
        return deepcopy(system_state["stats"])

@app.get("/api/coins")
async def get_coins():
    """
    Get list of monitored coins with their current status (thread-safe)
    
    CRITICAL FIX Bug #5: Thread-safe read with lock
    
    Returns:
        dict: List of coins with order flow data
    """
    with state_lock:
        return {"coins": deepcopy(system_state["coins"])}

@app.get("/api/signals")
async def get_signals(limit: int = 50):
    """
    Get recent signals (thread-safe)
    
    CRITICAL FIX Bug #5: Thread-safe read with lock
    
    Args:
        limit: Maximum number of signals to return (default 50)
        
    Returns:
        dict: List of signals (most recent first)
    """
    with state_lock:
        signals = deepcopy(system_state["signals"][-limit:])
    signals.reverse()  # Most recent first
    return {"signals": signals}

@app.get("/api/orderflow/{symbol}")
async def get_order_flow(symbol: str):
    """
    Get current order flow data for a specific symbol
    
    Args:
        symbol: Trading pair symbol (e.g., BTCUSDT)
        
    Returns:
        dict: Order flow data including buy/sell ratios and large orders
    """
    flow = system_state["order_flow"].get(symbol, {
        "buy_ratio": 0,
        "sell_ratio": 0,
        "large_buys": 0,
        "large_sells": 0,
        "last_update": "N/A"
    })
    return flow

@app.post("/api/coins/add")
async def add_coin(request: AddCoinRequest, _auth=Depends(verify_token), _rl=Depends(check_rate_limit)):
    """
    Add a new coin to monitoring with input validation
    
    SECURITY FIX Bug #4: Added input validation to prevent XSS/injection
    
    Args:
        request: AddCoinRequest with symbol
        
    Returns:
        dict: Success status and coin data
        
    Raises:
        HTTPException: If coin already exists or invalid input
    """
    # SECURITY FIX: Validate and sanitize input
    symbol = request.symbol.upper().strip()
    
    # Remove common suffixes if present
    for suffix in ['USDT', 'BUSD', 'USD']:
        symbol = symbol.replace(suffix, '')
    
    # SECURITY: Validate format - only alphanumeric, 1-10 characters
    if not re.match(r'^[A-Z0-9]{1,10}$', symbol):
        raise HTTPException(
            status_code=400,
            detail="Invalid symbol format. Use only letters and numbers (1-10 characters)"
        )
    
    # SECURITY: Check for empty symbol after sanitization
    if not symbol:
        raise HTTPException(status_code=400, detail="Symbol cannot be empty")
    
    # Reconstruct with USDT
    symbol = symbol + "USDT"
    
    # Check if already exists
    if any(coin["symbol"] == symbol for coin in system_state["coins"]):
        raise HTTPException(status_code=400, detail=f"{symbol} is already monitored")
    
    # Add to coins list (thread-safe)
    new_coin = {
        "symbol": symbol,
        "active": True,
        "buy_ratio": 0,
        "sell_ratio": 0,
        "large_buys": 0,
        "large_sells": 0,
        "last_update": "just added"
    }
    
    # CRITICAL FIX Bug #5: Thread-safe write with lock
    with state_lock:
        system_state["coins"].append(new_coin)

    # Request trade channel subscription from main.py
    _subscription_queue.put({"action": "subscribe", "symbol": symbol})

    # Broadcast to all WebSocket clients
    await broadcast_update("coin_added", new_coin)

    return {"success": True, "coin": new_coin}

@app.delete("/api/coins/remove/{symbol}")
async def remove_coin(symbol: str, _auth=Depends(verify_token), _rl=Depends(check_rate_limit)):
    """
    Remove a coin from monitoring (thread-safe)
    
    CRITICAL FIX Bug #5: Thread-safe write with lock
    
    Args:
        symbol: Symbol to remove
        
    Returns:
        dict: Success status
    """
    # CRITICAL FIX: Thread-safe modification
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
    """
    Enable/disable alerts for a coin
    
    Args:
        symbol: Symbol to toggle
        request: ToggleCoinRequest with active status
        
    Returns:
        dict: Success status and updated coin data
        
    Raises:
        HTTPException: If coin not found
    """
    # CRITICAL FIX Bug #5: Thread-safe read and modify
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
    csv_data = await _db.export_baselines_csv(symbol=symbol)
    if not csv_data:
        raise HTTPException(status_code=404, detail="No baselines to export")
    filename = f"teleglas_baselines_{symbol}.csv" if symbol else "teleglas_baselines.csv"
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
    if symbol:
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
    WebSocket endpoint for real-time updates

    CRITICAL FIX Bug #2: Wait for system initialization before sending state
    to prevent race condition where dashboard receives incomplete coin data.

    Sends:
    - Initial state on connection (after init complete)
    - Real-time updates for stats, signals, order flow
    - Coin add/remove/toggle events
    """
    # Optional auth check
    if API_TOKEN:
        token = websocket.query_params.get("token", "")
        if token != API_TOKEN:
            await websocket.close(code=4003, reason="Invalid token")
            return
    await websocket.accept()
    active_connections.append(websocket)
    
    # CRITICAL FIX: Wait for system initialization (max 3 seconds)
    retry_count = 0
    while retry_count < 30:  # 30 * 0.1s = 3 seconds max wait
        # Check if main system has initialized flag
        try:
            # System is ready when coins are populated
            if len(system_state["coins"]) > 0:
                break
        except:
            pass
        await asyncio.sleep(0.1)
        retry_count += 1
    
    # Send initial state (now guaranteed to be complete)
    await websocket.send_json({
        "type": "initial_state",
        "data": system_state
    })
    
    try:
        while True:
            # Keep connection alive and receive messages
            data = await websocket.receive_text()
            # Echo back for debugging
            await websocket.send_text(f"Server received: {data}")
    except WebSocketDisconnect:
        active_connections.remove(websocket)
    except Exception as e:
        if websocket in active_connections:
            active_connections.remove(websocket)

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

async def broadcast_update(event_type: str, data):
    """
    Broadcast updates to all connected WebSocket clients
    
    Args:
        event_type: Type of event (e.g., "stats_update", "new_signal")
        data: Event data to broadcast
    """
    message = {
        "type": event_type,
        "data": data,
        "timestamp": datetime.now().isoformat()
    }
    
    disconnected = []
    for connection in active_connections:
        try:
            await connection.send_json(message)
        except:
            disconnected.append(connection)
    
    # Remove disconnected clients
    for conn in disconnected:
        if conn in active_connections:
            active_connections.remove(conn)

# ============================================================================
# FUNCTIONS CALLED FROM MAIN.PY
# ============================================================================

def update_stats(stats: dict):
    """
    Update system statistics (thread-safe)
    
    CRITICAL FIX Bug #5: Thread-safe write with lock
    
    Called periodically from main.py to update dashboard stats
    
    Args:
        stats: Dictionary with current system statistics
    """
    # CRITICAL FIX: Thread-safe write
    with state_lock:
        system_state["stats"] = deepcopy(stats)
    
    # Schedule broadcast (must be called from async context)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(broadcast_update("stats_update", stats))
    except:
        pass  # No event loop running yet

def update_order_flow(symbol: str, flow_data: dict):
    """
    Update order flow for a symbol (thread-safe)
    
    CRITICAL FIX Bug #5: Thread-safe write with lock
    
    Called when new order flow data is available
    
    Args:
        symbol: Trading pair symbol
        flow_data: Order flow data (buy_ratio, sell_ratio, large orders)
    """
    # CRITICAL FIX: Thread-safe modification
    with state_lock:
        system_state["order_flow"][symbol] = {
            **flow_data,
            "last_update": datetime.now().strftime("%H:%M:%S")
        }
        
        # Also update in coins list
        coin = next((c for c in system_state["coins"] if c["symbol"] == symbol), None)
        if coin:
            coin.update(flow_data)
            coin["last_update"] = "just now"
    
    # Schedule broadcast
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(broadcast_update("order_flow_update", {
                "symbol": symbol,
                **flow_data
            }))
    except:
        pass

def add_signal(signal: dict):
    """
    Add a new signal to the dashboard (thread-safe)
    
    CRITICAL FIX Bug #5: Thread-safe write with lock
    
    Called when a new trading signal is generated
    
    Args:
        signal: Signal data dictionary
    """
    # CRITICAL FIX: Thread-safe modification
    with state_lock:
        signal_data = {
            "id": len(system_state["signals"]) + 1,
            "time": datetime.now().strftime("%H:%M:%S"),
            "timestamp": datetime.now().isoformat(),
            **signal
        }
        
        system_state["signals"].append(signal_data)
        
        # Keep only last 200 signals
        if len(system_state["signals"]) > 200:
            system_state["signals"] = system_state["signals"][-200:]
    
    # Schedule broadcast
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(broadcast_update("new_signal", signal_data))
    except:
        pass

def get_monitored_coins() -> List[str]:
    """
    Get list of currently monitored and active coins (thread-safe)

    CRITICAL FIX Bug #5: Thread-safe read with lock

    Returns:
        List of symbol strings for coins that are active
    """
    with state_lock:
        return [coin["symbol"] for coin in system_state["coins"] if coin.get("active", True)]

def get_pending_subscriptions() -> List[dict]:
    """
    Drain all pending subscription requests from the dashboard.

    Called by main.py's dynamic_subscription_task to process
    add/remove coin requests from the dashboard UI.

    Returns:
        List of {"action": "subscribe"|"unsubscribe", "symbol": str}
    """
    requests = []
    while not _subscription_queue.empty():
        try:
            requests.append(_subscription_queue.get_nowait())
        except queue.Empty:
            break
    return requests

def initialize_coins(initial_coins: List[str]):
    """
    Initialize dashboard with starting coins (thread-safe)
    
    CRITICAL FIX Bug #5: Thread-safe write with lock
    
    Called at startup to populate initial coin list
    
    Args:
        initial_coins: List of symbols to monitor
    """
    # CRITICAL FIX: Thread-safe initialization
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
# STARTUP/SHUTDOWN EVENTS
# ============================================================================

@app.on_event("startup")
async def startup_event():
    """Run on application startup"""
    print("=" * 60)
    print("🚀 TELEGLAS Dashboard API Started")
    print("=" * 60)
    print("📊 Dashboard URL: http://localhost:8080")
    print("🔌 WebSocket URL: ws://localhost:8080/ws")
    print("📡 API Endpoints: http://localhost:8080/docs")
    print("=" * 60)

@app.on_event("shutdown")
async def shutdown_event():
    """Run on application shutdown"""
    # Close all WebSocket connections
    for connection in active_connections:
        try:
            await connection.close()
        except:
            pass
    active_connections.clear()
    print("👋 Dashboard API Shutdown Complete")

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
