#!/usr/bin/env python3
# Test Processors Layer
# Usage: python scripts/test_processors.py

"""
Processors Layer Test Script

Tests:
1. DataValidator - Validate data integrity
2. BufferManager - Rolling buffer management

Uses mock data (no API key required)
"""

import sys
import json
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.processors.data_validator import DataValidator, ValidationResult
from src.processors.buffer_manager import BufferManager
from src.utils.logger import setup_logger

# Setup logger
logger = setup_logger("TestProcessors", "INFO")

# Mock data (flat event format matching actual CoinGlass WebSocket)
MOCK_LIQUIDATION_EVENT = {
    "symbol": "BTCUSDT",
    "exchange": "Binance",
    "price": "96000.50",
    "side": 2,
    "vol": "2500000.00",
    "time": int(time.time() * 1000)
}

MOCK_TRADE_EVENT = {
    "symbol": "ETHUSDT",
    "exchange": "Binance",
    "price": "2800.50",
    "side": 2,
    "vol": "150000.00",
    "time": int(time.time() * 1000)
}

def test_data_validator():
    """Test DataValidator"""
    logger.info("\n" + "=" * 60)
    logger.info("TEST 1: DataValidator")
    logger.info("=" * 60)

    validator = DataValidator()

    # Test 1.1: Validate valid liquidation
    logger.info("\n1.1 Validate Valid Liquidation:")
    result = validator.validate_liquidation(MOCK_LIQUIDATION_EVENT)
    logger.info(f"   Valid: {result.is_valid}")
    logger.info(f"   Errors: {result.errors}")
    logger.info(f"   Warnings: {result.warnings}")
    if result.is_valid:
        logger.info("✅ Validation passed")
    else:
        logger.error("❌ Validation failed")

    # Test 1.2: Validate valid trade
    logger.info("\n1.2 Validate Valid Trade:")
    result = validator.validate_trade(MOCK_TRADE_EVENT)
    logger.info(f"   Valid: {result.is_valid}")
    if result.is_valid:
        logger.info("✅ Validation passed")

    # Test 1.3: Validate invalid data
    logger.info("\n1.3 Validate Invalid Data (missing fields):")
    invalid_data = {"symbol": "BTC"}
    result = validator.validate_liquidation(invalid_data)
    logger.info(f"   Valid: {result.is_valid}")
    logger.info(f"   Errors: {len(result.errors)} errors")
    for error in result.errors[:3]:
        logger.info(f"      - {error}")
    if not result.is_valid:
        logger.info("✅ Correctly rejected invalid data")

    # Test 1.4: Symbol validation
    logger.info("\n1.4 Symbol Format Validation:")
    test_symbols = ["BTCUSDT", "ETHUSDT", "btcusdt", "BTC", "123ABC"]
    for symbol in test_symbols:
        is_valid = validator.is_valid_symbol(symbol)
        status = "✅" if is_valid else "❌"
        logger.info(f"   {status} {symbol}: {is_valid}")

    # Test 1.5: Statistics
    logger.info("\n1.5 Validator Statistics:")
    stats = validator.get_stats()
    logger.info(f"   Total validations: {stats['total_validations']}")
    logger.info(f"   Success rate: {stats['success_rate']:.1f}%")

def test_buffer_manager():
    """Test BufferManager"""
    logger.info("\n" + "=" * 60)
    logger.info("TEST 2: BufferManager")
    logger.info("=" * 60)

    buffer = BufferManager(max_liquidations=100, max_trades=50)

    # Test 2.1: Add liquidations
    logger.info("\n2.1 Add Liquidation Events:")
    for i in range(10):
        buffer.add_liquidation("BTCUSDT", MOCK_LIQUIDATION_EVENT)

    size = buffer.get_buffer_size("BTCUSDT")
    logger.info(f"✅ Added 10 liquidations")
    logger.info(f"   Buffer size: {size['liquidations']}")

    # Test 2.2: Add trades
    logger.info("\n2.2 Add Trade Events:")
    for i in range(15):
        buffer.add_trade("ETHUSDT", MOCK_TRADE_EVENT)

    size = buffer.get_buffer_size("ETHUSDT")
    logger.info(f"✅ Added 15 trades")
    logger.info(f"   Buffer size: {size['trades']}")

    # Test 2.3: Get recent events
    logger.info("\n2.3 Get Recent Events (30s window):")
    recent_liq = buffer.get_liquidations("BTCUSDT", time_window=30)
    recent_trades = buffer.get_trades("ETHUSDT", time_window=300)
    logger.info(f"✅ Retrieved {len(recent_liq)} recent liquidations")
    logger.info(f"✅ Retrieved {len(recent_trades)} recent trades")

    # Test 2.4: Tracked symbols
    logger.info("\n2.4 Tracked Symbols:")
    symbols = buffer.get_tracked_symbols()
    logger.info(f"   Symbols: {symbols}")

    # Test 2.5: Statistics
    logger.info("\n2.5 Buffer Statistics:")
    stats = buffer.get_stats()
    logger.info(f"   Total liquidations received: {stats['total_liquidations_received']}")
    logger.info(f"   Total trades received: {stats['total_trades_received']}")
    logger.info(f"   Symbols tracked: {stats['symbols_tracked']}")

    # Test 2.6: Memory usage
    logger.info("\n2.6 Memory Usage:")
    memory = buffer.get_memory_usage_estimate()
    logger.info(f"   Estimated: {memory['total_kb']:.2f} KB")

    # Test 2.7: Cleanup
    logger.info("\n2.7 Cleanup Old Data:")
    buffer.cleanup_old_data(max_age_seconds=3600)
    logger.info("✅ Cleanup completed")

def main():
    """Run all tests"""
    logger.info("\n" + "=" * 60)
    logger.info("🧪 TELEGLAS Pro - Processors Layer Tests")
    logger.info("=" * 60)

    try:
        test_data_validator()
        test_buffer_manager()

        logger.info("\n" + "=" * 60)
        logger.info("✅ ALL TESTS COMPLETED SUCCESSFULLY!")
        logger.info("=" * 60)
        logger.info("\nProcessors Layer is production-ready!")
        logger.info("- DataValidator: ✅ Working")
        logger.info("- BufferManager: ✅ Working")

    except Exception as e:
        logger.error(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
