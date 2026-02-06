#!/usr/bin/env python3
"""
TELEGLAS Pro - Dashboard Preview Mode
Run dashboard without WebSocket connection for preview
"""

import uvicorn
from src.dashboard import api as dashboard_api
from src.utils.logger import setup_logger

def main():
    logger = setup_logger("Dashboard", "INFO")
    
    # Initialize with demo symbols
    demo_symbols = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT']
    dashboard_api.initialize_coins(demo_symbols)
    
    # Add some demo data
    logger.info("=" * 60)
    logger.info("📊 TELEGLAS Pro - Dashboard Preview Mode")
    logger.info("=" * 60)
    logger.info("")
    logger.info("Running dashboard WITHOUT WebSocket connection")
    logger.info("This is for UI preview only - no real data")
    logger.info("")
    logger.info("Dashboard URL: http://0.0.0.0:8080")
    logger.info("Public URL: http://31.97.107.243:8080")
    logger.info("")
    logger.info("Press Ctrl+C to stop")
    logger.info("=" * 60)
    
    # Start dashboard server
    uvicorn.run(
        dashboard_api.app,
        host="0.0.0.0",
        port=8080,
        log_level="info"
    )

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 Dashboard stopped")
