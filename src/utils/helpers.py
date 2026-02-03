# Helpers - Utility Functions
# General utility functions used across the application

"""
Helpers Module

Provides utility functions for:
- Timestamp formatting
- Data transformation
- Common calculations
"""

from datetime import datetime, timezone
from typing import Any, Dict

def format_timestamp(timestamp_ms: int) -> str:
    """
    Convert millisecond timestamp to readable string
    
    Args:
        timestamp_ms: Unix timestamp in milliseconds
        
    Returns:
        Formatted datetime string (YYYY-MM-DD HH:MM:SS UTC)
    """
    dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    return dt.strftime('%Y-%m-%d %H:%M:%S UTC')

def format_usd(amount: float) -> str:
    """
    Format USD amount with commas and 2 decimal places
    
    Args:
        amount: USD amount
        
    Returns:
        Formatted string (e.g., "$1,234,567.89")
    """
    return f"${amount:,.2f}"

def format_percentage(value: float, decimals: int = 2) -> str:
    """
    Format value as percentage
    
    Args:
        value: Value to format (0.65 = 65%)
        decimals: Number of decimal places
        
    Returns:
        Formatted string (e.g., "65.00%")
    """
    return f"{value * 100:.{decimals}f}%"

def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """
    Safe division that returns default on division by zero
    
    Args:
        numerator: Top number
        denominator: Bottom number
        default: Value to return if denominator is 0
        
    Returns:
        Result of division or default
    """
    if denominator == 0:
        return default
    return numerator / denominator

def calculate_percentage_change(old_value: float, new_value: float) -> float:
    """
    Calculate percentage change between two values
    
    Args:
        old_value: Original value
        new_value: New value
        
    Returns:
        Percentage change (0.15 = 15% increase)
    """
    if old_value == 0:
        return 0.0
    return (new_value - old_value) / old_value
