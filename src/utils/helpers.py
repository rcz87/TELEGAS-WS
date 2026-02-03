# Helpers - Utility Functions
# TODO: Implement helper functions

"""
Helpers Module

Common utility functions used across the application
"""

from datetime import datetime, timezone
from typing import Any, Dict

def get_current_timestamp() -> str:
    """Get current UTC timestamp"""
    return datetime.now(timezone.utc).isoformat()

def format_volume(volume: float) -> str:
    """Format volume for display (e.g., $2.8M)"""
    if volume >= 1_000_000:
        return f"${volume/1_000_000:.1f}M"
    elif volume >= 1_000:
        return f"${volume/1_000:.1f}K"
    else:
        return f"${volume:.2f}"

def format_price(price: float) -> str:
    """Format price for display"""
    return f"${price:,.2f}"

def calculate_percentage(part: float, total: float) -> float:
    """Calculate percentage"""
    if total == 0:
        return 0
    return (part / total) * 100

def safe_get(data: Dict, key: str, default: Any = None) -> Any:
    """Safely get value from dict"""
    return data.get(key, default)
