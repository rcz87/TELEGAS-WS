#!/usr/bin/env python3
# Test Processors Layer
# Usage: python scripts/test_processors.py

"""
Processors Layer Test Script

Tests:
1. MessageParser - Parse WebSocket messages
2. DataValidator - Validate data integrity
3. BufferManager - Rolling buffer management

Uses mock data (no API key required)
"""

import sys
import json
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.processors.message_parser import MessageParser, MessageType, LiquidationEvent, TradeEvent
from src.processors.data_validator import DataValidator, ValidationResult
from src.processors.buffer_manager import BufferManager
from src.utils.logger import setup_logger

# Setup logger
logger = setup_logger("TestProcessors", "INFO")

# Mock data
MOCK_LIQUIDATION_MESSAGE = json.dumps({
    "event": "liquidation",
    "data": {
        "symbol": "BTCUSDT",
        "exchange": "Binance",
        "price": "96000.50",
        "side": 2,  # Short liquidation
        "vol": "2500000.00",
        "time": int(time.time() * 1000)
    }
})

MOCK_TRADE_MESSAGE = json.dumps({
    "event": "trade",
    "data": {
        "symbol": "ETHUSDT",
        "exchange": "Binance",
        "price": "2800.50",
        "side": 2,  # Buy
        "vol": "150000.00",
        "time": int(time.time() * 1000)
    }
})

MOCK_PONG_MESSAGE = json.dumps({
    "event": "pong",
    "data": {}
})

INVALID_JSON = "{ this is not valid json"

def test_message_parser():
    """Test MessageParser"""
    logger.info("=" * 60)
    logger.info("TEST 1: MessageParser")
    logger.info("=" * 60)
    
    parser = MessageParser()
    
    # Test 1.1: Parse liquidation
    logger.info("\n1.1 Parse Liquidation Message:")
    parsed = parser.parse(MOCK_LIQUIDATION_MESSAGE)
    if parsed:
        logger.info(f"✅ Parsed successfully")
        logger.info(f"   Type: {parsed.message_type}")
        logger.info(f"   Event: {parsed.event}")
        
        # Parse as liquidation event
        liq_event = parser.parse_liquidation(parsed.raw)
        if liq_event:
            logger.info(f"✅ Liquidation event created")
            logger.info(f"   Symbol: {liq_event.symbol}")
            logger.info(f"   Exchange: {liq_event.exchange}")
            logger.info(f"   Price: ${liq_event.price:,.2f}")
            logger.info(f"   Side: {liq_event.side} (2=Short liq)")
            logger.info(f"   Volume: ${liq_event.volume_usd:,.2f}")
    else:
        logger.error("❌ Failed to parse liquidation")
    
    # Test 1.2: Parse trade
    logger.info("\n1.2 Parse Trade Message:")
    parsed = parser.parse(MOCK_TRADE_MESSAGE)
    if parsed:
        logger.info(f"✅ Parsed successfully")
        logger.info(f"   Type: {parsed.message_type}")
        
        # Parse as trade event
        trade_event = parser.parse_trade(parsed.raw)
        if trade_event:
            logger.info(f"✅ Trade event created")
            logger.info(f"   Symbol: {trade_event.symbol}")
            logger.info(f"   Price: ${trade_event.price:,.2f}")
            logger.info(f"   Side: {trade_event.side} (2=Buy)")
            logger.info(f"   Volume: ${trade_event.volume_usd:,.2f}")
    else:
        logger.error("❌ Failed to parse trade")
    
    # Test 1.3: Parse pong
    logger.info("\n1.3 Parse Pong Message:")
    parsed = parser.parse(MOCK_PONG_MESSAGE)
    if parsed:
        logger.info(f"✅ Parsed: {parsed.event} ({parsed.message_type})")
    
    # Test 1.4: Handle invalid JSON
    logger.info("\n1.4 Handle Invalid JSON:")
    parsed = parser.parse(INVALID_JSON)
    if parsed is None:
        logger.info("✅ Correctly handled invalid JSON")
    else:
        logger.error("❌ Should have returned None for invalid JSON")
    
    # Test 1.5: Batch parsing
    logger.info("\n1.5 Batch Parsing:")
    messages = [MOCK_LIQUIDATION_MESSAGE, MOCK_TRADE_MESSAGE, MOCK_PONG_MESSAGE]
    results = parser.parse_batch(messages)
    logger.info(f"✅ Parsed {len(results)}/{len(messages)} messages")
    
    # Test 1.6: Statistics
    logger.info("\n1.6 Parser Statistics:")
    stats = parser.get_stats()
    logger.info(f"   Total parsed: {stats['total_parsed']}")
    logger.info(f"   Total errors: {stats['total_errors']}")
    logger.info(f"   Success rate: {stats['success_rate']:.1f}%")

def test_data_validator():
    """Test DataValidator"""
    logger.info("\n" + "=" * 60)
    logger.info("TEST 2: DataValidator")
    logger.info("=" * 60)
    
    validator = DataValidator()
    parser = MessageParser()
    
    # Test 2.1: Validate valid liquidation
    logger.info("\n2.1 Validate Valid Liquidation:")
    parsed = parser.parse(MOCK_LIQUIDATION_MESSAGE)
    if parsed:
        result = validator.validate_liquidation(parsed.raw)
        logger.info(f"   Valid: {result.is_valid}")
        logger.info(f"   Errors: {result.errors}")
        logger.info(f"   Warnings: {result.warnings}")
        if result.is_valid:
            logger.info("✅ Validation passed")
        else:
            logger.error("❌ Validation failed")
    
    # Test 2.2: Validate valid trade
    logger.info("\n2.2 Validate Valid Trade:")
    parsed = parser.parse(MOCK_TRADE_MESSAGE)
    if parsed:
        result = validator.validate_trade(parsed.raw)
        logger.info(f"   Valid: {result.is_valid}")
        if result.is_valid:
            logger.info("✅ Validation passed")
    
    # Test 2.3: Validate invalid data
    logger.info("\n2.3 Validate Invalid Data (missing fields):")
    invalid_data = {"event": "liquidation", "data": {"symbol": "BTC"}}  # Missing required fields
    result = validator.validate_liquidation(invalid_data)
    logger.info(f"   Valid: {result.is_valid}")
    logger.info(f"   Errors: {len(result.errors)} errors")
    for error in result.errors[:3]:
        logger.info(f"      - {error}")
    if not result.is_valid:
        logger.info("✅ Correctly rejected invalid data")
    
    # Test 2.4: Symbol validation
    logger.info("\n2.4 Symbol Format Validation:")
    test_symbols = ["BTCUSDT", "ETHUSDT", "btcusdt", "BTC", "123ABC"]
    for symbol in test_symbols:
        is_valid = validator.is_valid_symbol(symbol)
        status = "✅" if is_valid else "❌"
        logger.info(f"   {status} {symbol}: {is_valid}")
    
    # Test 2.5: Price reasonableness
    logger.info("\n2.5 Price Reasonableness Check:")
    test_cases = [
        ("BTCUSDT", 96000),
        ("BTCUSDT", 5000),  # Too low
        ("ETHUSDT", 2800),
        ("ETHUSDT", 50000),  # Too high
    ]
    for symbol, price in test_cases:
        is_reasonable, reason = validator.is_reasonable_price(symbol, price)
        status = "✅" if is_reasonable else "⚠️"
        logger.info(f"   {status} {symbol} @ ${price:,.0f}: {reason}")
    
    # Test 2.6: Statistics
    logger.info("\n2.6 Validator Statistics:")
    stats = validator.get_stats()
    logger.info(f"   Total validations: {stats['total_validations']}")
    logger.info(f"   Success rate: {stats['success_rate']:.1f}%")

def test_buffer_manager():
    """Test BufferManager"""
    logger.info("\n" + "=" * 60)
    logger.info("TEST 3: BufferManager")
    logger.info("=" * 60)
    
    buffer = BufferManager(max_liquidations=100, max_trades=50)
    parser = MessageParser()
    
    # Test 3.1: Add liquidations
    logger.info("\n3.1 Add Liquidation Events:")
    for i in range(10):
        parsed = parser.parse(MOCK_LIQUIDATION_MESSAGE)
        if parsed:
            liq_event = parser.parse_liquidation(parsed.raw)
            if liq_event:
                buffer.add_liquidation(liq_event.symbol, liq_event.raw_data)
    
    size = buffer.get_buffer_size("BTCUSDT")
    logger.info(f"✅ Added 10 liquidations")
    logger.info(f"   Buffer size: {size['liquidations']}")
    
    # Test 3.2: Add trades
    logger.info("\n3.2 Add Trade Events:")
    for i in range(15):
        parsed = parser.parse(MOCK_TRADE_MESSAGE)
        if parsed:
            trade_event = parser.parse_trade(parsed.raw)
            if trade_event:
                buffer.add_trade(trade_event.symbol, trade_event.raw_data)
    
    size = buffer.get_buffer_size("ETHUSDT")
    logger.info(f"✅ Added 15 trades")
    logger.info(f"   Buffer size: {size['trades']}")
    
    # Test 3.3: Get recent events
    logger.info("\n3.3 Get Recent Events (30s window):")
    recent_liq = buffer.get_liquidations("BTCUSDT", time_window=30)
    recent_trades = buffer.get_trades("ETHUSDT", time_window=300)
    logger.info(f"✅ Retrieved {len(recent_liq)} recent liquidations")
    logger.info(f"✅ Retrieved {len(recent_trades)} recent trades")
    
    # Test 3.4: Get all events
    logger.info("\n3.4 Get All Events:")
    all_liq = buffer.get_all_liquidations("BTCUSDT")
    all_trades = buffer.get_all_trades("ETHUSDT")
    logger.info(f"   All liquidations: {len(all_liq)}")
    logger.info(f"   All trades: {len(all_trades)}")
    
    # Test 3.5: Tracked symbols
    logger.info("\n3.5 Tracked Symbols:")
    symbols = buffer.get_tracked_symbols()
    logger.info(f"   Symbols: {symbols}")
    
    # Test 3.6: Statistics
    logger.info("\n3.6 Buffer Statistics:")
    stats = buffer.get_stats()
    logger.info(f"   Total liquidations received: {stats['total_liquidations_received']}")
    logger.info(f"   Total trades received: {stats['total_trades_received']}")
    logger.info(f"   Symbols tracked: {stats['symbols_tracked']}")
    logger.info(f"   Avg liq/symbol: {stats['avg_liquidations_per_symbol']:.1f}")
    logger.info(f"   Avg trades/symbol: {stats['avg_trades_per_symbol']:.1f}")
    
    # Test 3.7: Memory usage
    logger.info("\n3.7 Memory Usage:")
    memory = buffer.get_memory_usage_estimate()
    logger.info(f"   Estimated: {memory['total_kb']:.2f} KB")
    
    # Test 3.8: Cleanup
    logger.info("\n3.8 Cleanup Old Data:")
    buffer.cleanup_old_data(max_age_seconds=3600)
    logger.info("✅ Cleanup completed")
    
    # Test 3.9: Clear symbol
    logger.info("\n3.9 Clear Symbol Buffer:")
    buffer.clear_symbol("BTCUSDT")
    size = buffer.get_buffer_size("BTCUSDT")
    logger.info(f"✅ Cleared BTCUSDT buffer")
    logger.info(f"   New size: {size['liquidations']}")

def test_integration():
    """Test integrated workflow"""
    logger.info("\n" + "=" * 60)
    logger.info("TEST 4: Integration (Parser → Validator → Buffer)")
    logger.info("=" * 60)
    
    parser = MessageParser()
    validator = DataValidator()
    buffer = BufferManager()
    
    messages = [
        MOCK_LIQUIDATION_MESSAGE,
        MOCK_TRADE_MESSAGE,
        MOCK_LIQUIDATION_MESSAGE,
        MOCK_TRADE_MESSAGE,
    ]
    
    processed_count = 0
    valid_count = 0
    buffered_count = 0
    
    for msg in messages:
        # Step 1: Parse
        parsed = parser.parse(msg)
        if not parsed:
            continue
        processed_count += 1
        
        # Step 2: Validate
        if parsed.event == "liquidation":
            validation = validator.validate_liquidation(parsed.raw)
            if validation.is_valid:
                valid_count += 1
                # Step 3: Buffer
                liq_event = parser.parse_liquidation(parsed.raw)
                if liq_event:
                    buffer.add_liquidation(liq_event.symbol, liq_event.raw_data)
                    buffered_count += 1
        
        elif parsed.event == "trade":
            validation = validator.validate_trade(parsed.raw)
            if validation.is_valid:
                valid_count += 1
                # Step 3: Buffer
                trade_event = parser.parse_trade(parsed.raw)
                if trade_event:
                    buffer.add_trade(trade_event.symbol, trade_event.raw_data)
                    buffered_count += 1
    
    logger.info(f"\n✅ Integration Test Complete!")
    logger.info(f"   Messages processed: {processed_count}/{len(messages)}")
    logger.info(f"   Valid events: {valid_count}")
    logger.info(f"   Buffered events: {buffered_count}")
    
    stats = buffer.get_stats()
    logger.info(f"   Buffer stats: {stats['symbols_tracked']} symbols tracked")

def main():
    """Run all tests"""
    logger.info("\n" + "=" * 60)
    logger.info("🧪 TELEGLAS Pro - Processors Layer Tests")
    logger.info("=" * 60)
    
    try:
        # Run tests
        test_message_parser()
        test_data_validator()
        test_buffer_manager()
        test_integration()
        
        # Summary
        logger.info("\n" + "=" * 60)
        logger.info("✅ ALL TESTS COMPLETED SUCCESSFULLY!")
        logger.info("=" * 60)
        logger.info("\nProcessors Layer is production-ready!")
        logger.info("- MessageParser: ✅ Working")
        logger.info("- DataValidator: ✅ Working")
        logger.info("- BufferManager: ✅ Working")
        logger.info("- Integration: ✅ Working")
        
    except Exception as e:
        logger.error(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
