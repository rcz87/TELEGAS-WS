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

from fastapi import FastAPI, WebSocket, HTTPException, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncio
import json
from datetime import datetime
from typing import List, Optional
from pathlib import Path

# Create FastAPI app
app = FastAPI(
    title="TELEGLAS Pro Dashboard",
    description="Real-time cryptocurrency trading intelligence dashboard",
    version="1.0.0"
)

# CORS middleware for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files (HTML, JS, CSS)
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

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
    """Serve dashboard HTML"""
    html_file = Path(__file__).parent / "static" / "index.html"
    if html_file.exists():
        return FileResponse(str(html_file))
    return {"message": "Dashboard HTML not found. Please create src/dashboard/static/index.html"}

@app.get("/api/stats")
async def get_stats():
    """
    Get current system statistics
    
    Returns:
        dict: System statistics including messages, signals, alerts, errors
    """
    return system_state["stats"]

@app.get("/api/coins")
async def get_coins():
    """
    Get list of monitored coins with their current status
    
    Returns:
        dict: List of coins with order flow data
    """
    return {"coins": system_state["coins"]}

@app.get("/api/signals")
async def get_signals(limit: int = 50):
    """
    Get recent signals
    
    Args:
        limit: Maximum number of signals to return (default 50)
        
    Returns:
        dict: List of signals (most recent first)
    """
    signals = system_state["signals"][-limit:]
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
async def add_coin(request: AddCoinRequest):
    """
    Add a new coin to monitoring
    
    Args:
        request: AddCoinRequest with symbol
        
    Returns:
        dict: Success status and coin data
        
    Raises:
        HTTPException: If coin already exists
    """
    symbol = request.symbol.upper()
    if not symbol.endswith("USDT"):
        symbol = symbol + "USDT"
    
    # Check if already exists
    if any(coin["symbol"] == symbol for coin in system_state["coins"]):
        raise HTTPException(status_code=400, detail="Coin already monitored")
    
    # Add to coins list
    new_coin = {
        "symbol": symbol,
        "active": True,
        "buy_ratio": 0,
        "sell_ratio": 0,
        "large_buys": 0,
        "large_sells": 0,
        "last_update": "just added"
    }
    system_state["coins"].append(new_coin)
    
    # Broadcast to all WebSocket clients
    await broadcast_update("coin_added", new_coin)
    
    return {"success": True, "coin": new_coin}

@app.delete("/api/coins/remove/{symbol}")
async def remove_coin(symbol: str):
    """
    Remove a coin from monitoring
    
    Args:
        symbol: Symbol to remove
        
    Returns:
        dict: Success status
    """
    system_state["coins"] = [c for c in system_state["coins"] if c["symbol"] != symbol]
    
    # Broadcast to all WebSocket clients
    await broadcast_update("coin_removed", {"symbol": symbol})
    
    return {"success": True, "symbol": symbol}

@app.patch("/api/coins/{symbol}/toggle")
async def toggle_coin(symbol: str, request: ToggleCoinRequest):
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
    coin = next((c for c in system_state["coins"] if c["symbol"] == symbol), None)
    if not coin:
        raise HTTPException(status_code=404, detail="Coin not found")
    
    coin["active"] = request.active
    
    # Broadcast to all WebSocket clients
    await broadcast_update("coin_toggled", coin)
    
    return {"success": True, "coin": coin}

# ============================================================================
# WEBSOCKET ENDPOINT
# ============================================================================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time updates
    
    Sends:
    - Initial state on connection
    - Real-time updates for stats, signals, order flow
    - Coin add/remove/toggle events
    """
    await websocket.accept()
    active_connections.append(websocket)
    
    # Send initial state
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
    Update system statistics
    
    Called periodically from main.py to update dashboard stats
    
    Args:
        stats: Dictionary with current system statistics
    """
    system_state["stats"] = stats
    # Schedule broadcast (must be called from async context)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(broadcast_update("stats_update", stats))
    except:
        pass  # No event loop running yet

def update_order_flow(symbol: str, flow_data: dict):
    """
    Update order flow for a symbol
    
    Called when new order flow data is available
    
    Args:
        symbol: Trading pair symbol
        flow_data: Order flow data (buy_ratio, sell_ratio, large orders)
    """
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
    Add a new signal to the dashboard
    
    Called when a new trading signal is generated
    
    Args:
        signal: Signal data dictionary
    """
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
    Get list of currently monitored and active coins
    
    Returns:
        List of symbol strings for coins that are active
    """
    return [coin["symbol"] for coin in system_state["coins"] if coin.get("active", True)]

def initialize_coins(initial_coins: List[str]):
    """
    Initialize dashboard with starting coins
    
    Called at startup to populate initial coin list
    
    Args:
        initial_coins: List of symbols to monitor
    """
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
