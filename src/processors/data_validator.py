# Data Validator - Validate Incoming Data
# Production-ready data validator with comprehensive checks

"""
Data Validator Module

Responsibilities:
- Validate required fields present
- Validate data types
- Validate value ranges
- Schema validation
- Business logic validation
"""

from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass

from ..utils.logger import setup_logger

@dataclass
class ValidationResult:
    """Validation result"""
    is_valid: bool
    errors: List[str]
    warnings: List[str]

class DataValidator:
    """
    Production-ready data validator
    
    Features:
    - Schema validation
    - Type checking
    - Range validation
    - Required field checking
    - Business logic validation
    """
    
    def __init__(self):
        """Initialize validator"""
        self.logger = setup_logger("DataValidator", "INFO")
        self._validation_count = 0
        self._error_count = 0
        
        # Define schemas
        # Note: CoinGlass sends price/vol as strings ("96000.50"), so accept str too
        self.liquidation_schema = {
            "symbol": {"type": str, "required": True},
            "exchange": {"type": str, "required": True},
            "price": {"type": (int, float, str), "required": True, "min": 0},
            "side": {"type": int, "required": True, "values": [1, 2]},
            "vol": {"type": (int, float, str), "required": True, "min": 0},
            "time": {"type": int, "required": True, "min": 0}
        }

        self.trade_schema = {
            "symbol": {"type": str, "required": True},
            "exchange": {"type": str, "required": True},
            "price": {"type": (int, float, str), "required": True, "min": 0},
            "side": {"type": int, "required": True, "values": [1, 2]},
            "vol": {"type": (int, float, str), "required": True, "min": 0},
            "time": {"type": int, "required": True, "min": 0}
        }
    
    def validate(self, data: Dict[str, Any], schema: Dict) -> ValidationResult:
        """
        Validate data against schema
        
        Args:
            data: Data to validate
            schema: Expected schema definition
            
        Returns:
            ValidationResult with is_valid, errors, warnings
        """
        self._validation_count += 1
        errors = []
        warnings = []
        
        try:
            # Check required fields
            for field, rules in schema.items():
                if rules.get("required", False):
                    if field not in data:
                        errors.append(f"Required field '{field}' is missing")
                        continue
                    
                    value = data[field]
                    
                    # Check type
                    expected_type = rules.get("type")
                    if expected_type and not isinstance(value, expected_type):
                        errors.append(
                            f"Field '{field}' has wrong type. "
                            f"Expected {expected_type}, got {type(value)}"
                        )
                        continue
                    
                    # Check allowed values
                    if "values" in rules:
                        if value not in rules["values"]:
                            errors.append(
                                f"Field '{field}' has invalid value. "
                                f"Expected one of {rules['values']}, got {value}"
                            )
                    
                    # Check min value
                    if "min" in rules:
                        try:
                            if float(value) < rules["min"]:
                                errors.append(
                                    f"Field '{field}' is below minimum. "
                                    f"Min: {rules['min']}, got {value}"
                                )
                        except (ValueError, TypeError):
                            errors.append(f"Field '{field}' cannot be compared to min value")
                    
                    # Check max value
                    if "max" in rules:
                        try:
                            if float(value) > rules["max"]:
                                warnings.append(
                                    f"Field '{field}' exceeds maximum. "
                                    f"Max: {rules['max']}, got {value}"
                                )
                        except (ValueError, TypeError):
                            pass
            
            is_valid = len(errors) == 0
            
            if not is_valid:
                self._error_count += 1
                self.logger.warning(f"Validation failed: {errors}")
            
            return ValidationResult(
                is_valid=is_valid,
                errors=errors,
                warnings=warnings
            )
            
        except Exception as e:
            self._error_count += 1
            self.logger.error(f"Validation error: {e}")
            return ValidationResult(
                is_valid=False,
                errors=[f"Validation exception: {str(e)}"],
                warnings=[]
            )
    
    def validate_liquidation(self, data: dict) -> ValidationResult:
        """
        Validate liquidation event data

        Checks:
        - Required fields present
        - Correct data types
        - Value ranges
        - Business logic

        Args:
            data: Liquidation data dictionary (flat or nested with "data" key)

        Returns:
            ValidationResult
        """
        # Support both flat format {"symbol":...} and nested {"data":{"symbol":...}}
        liquidation_data = data["data"] if "data" in data and isinstance(data["data"], dict) else data
        result = self.validate(liquidation_data, self.liquidation_schema)
        
        # Additional business logic validation
        if result.is_valid:
            # Check symbol format
            symbol = liquidation_data.get("symbol", "")
            if not symbol.isupper() or len(symbol) < 3:
                result.warnings.append(f"Unusual symbol format: {symbol}")
            
            # Check volume is reasonable
            volume = float(liquidation_data.get("vol", 0))
            if volume > 100_000_000:  # $100M
                result.warnings.append(f"Very large liquidation: ${volume:,.0f}")
            elif volume < 1000:  # $1K
                result.warnings.append(f"Very small liquidation: ${volume:,.2f}")
            
            # Check price is reasonable
            price = float(liquidation_data.get("price", 0))
            if price > 1_000_000:  # $1M per coin
                result.warnings.append(f"Unusually high price: ${price:,.2f}")
        
        return result
    
    def validate_trade(self, data: dict) -> ValidationResult:
        """
        Validate trade event data

        Checks:
        - Required fields present
        - Correct data types
        - Value ranges
        - Business logic

        Args:
            data: Trade data dictionary (flat or nested with "data" key)

        Returns:
            ValidationResult
        """
        # Support both flat format {"symbol":...} and nested {"data":{"symbol":...}}
        trade_data = data["data"] if "data" in data and isinstance(data["data"], dict) else data
        result = self.validate(trade_data, self.trade_schema)
        
        # Additional business logic validation
        if result.is_valid:
            # Check symbol format
            symbol = trade_data.get("symbol", "")
            if not symbol.isupper() or len(symbol) < 3:
                result.warnings.append(f"Unusual symbol format: {symbol}")
            
            # Check volume is reasonable
            volume = float(trade_data.get("vol", 0))
            if volume > 50_000_000:  # $50M
                result.warnings.append(f"Very large trade: ${volume:,.0f}")
            
            # Check price is reasonable
            price = float(trade_data.get("price", 0))
            if price > 1_000_000:  # $1M per coin
                result.warnings.append(f"Unusually high price: ${price:,.2f}")
        
        return result
    
    def validate_batch(self, data_list: List[dict], data_type: str = "liquidation") -> List[ValidationResult]:
        """
        Validate multiple data items
        
        Args:
            data_list: List of data dictionaries
            data_type: Type of data ("liquidation" or "trade")
            
        Returns:
            List of ValidationResult objects
        """
        results = []
        
        for data in data_list:
            if data_type == "liquidation":
                result = self.validate_liquidation(data)
            elif data_type == "trade":
                result = self.validate_trade(data)
            else:
                result = ValidationResult(
                    is_valid=False,
                    errors=[f"Unknown data type: {data_type}"],
                    warnings=[]
                )
            
            results.append(result)
        
        return results
    
    def is_valid_symbol(self, symbol: str) -> bool:
        """
        Check if symbol format is valid
        
        Args:
            symbol: Trading symbol (e.g., "BTCUSDT")
            
        Returns:
            True if valid, False otherwise
        """
        if not symbol or not isinstance(symbol, str):
            return False
        
        # Should be uppercase
        if not symbol.isupper():
            return False
        
        # Should be at least 6 characters (e.g., BTCUSD)
        if len(symbol) < 6:
            return False
        
        # Should contain only letters and digits (e.g. 1000PEPEUSDT)
        if not symbol.isalnum():
            return False
        
        return True
    
    def is_valid_exchange(self, exchange: str) -> bool:
        """
        Check if exchange name is valid
        
        Args:
            exchange: Exchange name
            
        Returns:
            True if valid, False otherwise
        """
        if not exchange or not isinstance(exchange, str):
            return False
        
        # Known exchanges
        known_exchanges = [
            "Binance", "OKX", "Bybit", "Bitget", "dYdX",
            "BitMEX", "Kraken", "Huobi", "Coinbase"
        ]
        
        return exchange in known_exchanges
    
    def is_reasonable_price(self, symbol: str, price: float) -> Tuple[bool, str]:
        """
        Check if price is reasonable for given symbol
        
        Args:
            symbol: Trading symbol
            price: Price value
            
        Returns:
            (is_reasonable, reason)
        """
        # Basic checks
        if price <= 0:
            return False, "Price must be positive"
        
        if price > 1_000_000:
            return False, f"Price too high: ${price:,.2f}"
        
        # Symbol-specific checks (rough estimates)
        if "BTC" in symbol:
            if price < 10_000 or price > 200_000:
                return False, f"BTC price out of range: ${price:,.2f}"
        
        elif "ETH" in symbol:
            if price < 500 or price > 10_000:
                return False, f"ETH price out of range: ${price:,.2f}"
        
        return True, "OK"
    
    def get_stats(self) -> dict:
        """Get validator statistics"""
        return {
            "total_validations": self._validation_count,
            "total_errors": self._error_count,
            "success_rate": (self._validation_count - self._error_count) / max(self._validation_count, 1) * 100
        }
