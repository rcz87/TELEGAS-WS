# Setup Classifier - Converts signal + context into a setup_key for granular learning
#
# Instead of learning per signal_type only (STOP_HUNT, ACCUMULATION, etc.),
# we classify each signal into a specific setup like:
#   STOP_HUNT|LONG|tier2|oi_rising|fr_neutral|session_ny|events_1
#
# This lets confidence_scorer learn which *specific setups* have edge,
# not just which signal types.

from datetime import datetime, timezone
from typing import Optional


# Trading session windows (UTC hours)
_SESSIONS = [
    ("asia",    (0, 8)),    # 00:00-08:00 UTC (Asia)
    ("london",  (8, 13)),   # 08:00-13:00 UTC (London)
    ("ny",      (13, 21)),  # 13:00-21:00 UTC (New York)
    ("late",    (21, 24)),  # 21:00-00:00 UTC (Late/low liquidity)
]


def _get_session() -> str:
    """Get current trading session based on UTC hour."""
    hour = datetime.now(timezone.utc).hour
    for name, (start, end) in _SESSIONS:
        if start <= hour < end:
            return name
    return "late"


def _get_tier(symbol: str, monitoring_config: dict) -> str:
    """Determine symbol tier from monitoring config."""
    tier1 = set(monitoring_config.get("tier1_symbols", []))
    tier2 = set(monitoring_config.get("tier2_symbols", []))
    tier3 = set(monitoring_config.get("tier3_symbols", []))

    if symbol in tier1:
        return "t1"
    elif symbol in tier2:
        return "t2"
    elif symbol in tier3:
        return "t3"
    else:
        return "t4"


def _classify_oi(metadata: dict) -> str:
    """Classify OI alignment from market context metadata."""
    ctx = metadata.get("market_context", {})
    oi_change = ctx.get("oi_change_1h_pct", 0)
    if oi_change > 2:
        return "oi_spike"
    elif oi_change > 0.5:
        return "oi_rising"
    elif oi_change < -2:
        return "oi_drop"
    elif oi_change < -0.5:
        return "oi_falling"
    return "oi_flat"


def _classify_funding(metadata: dict) -> str:
    """Classify funding rate from market context metadata."""
    ctx = metadata.get("market_context", {})
    fr = ctx.get("funding_rate", 0)
    if fr > 0.0005:
        return "fr_long_crowded"
    elif fr < -0.0005:
        return "fr_short_crowded"
    return "fr_neutral"


def _classify_cvd(metadata: dict) -> str:
    """Classify CVD alignment from market context metadata."""
    ctx = metadata.get("market_context", {})
    spot_dir = ctx.get("spot_cvd_direction", "UNKNOWN")
    fut_dir = ctx.get("futures_cvd_direction", "UNKNOWN")

    if spot_dir == fut_dir and spot_dir in ("RISING", "FALLING"):
        return f"cvd_{spot_dir.lower()}_aligned"
    elif spot_dir != fut_dir and spot_dir in ("RISING", "FALLING") and fut_dir in ("RISING", "FALLING"):
        return "cvd_divergent"
    return "cvd_mixed"


def _count_events(metadata: dict) -> int:
    """Count event signals contributing to this signal."""
    events = metadata.get("events", [])
    return len(events) if isinstance(events, list) else 0


def classify_setup(
    signal_type: str,
    direction: str,
    symbol: str,
    metadata: dict,
    monitoring_config: dict = None,
) -> str:
    """
    Build a setup_key from signal type + context.

    Returns a pipe-delimited string like:
        STOP_HUNT|LONG|t2|oi_rising|fr_neutral|cvd_rising_aligned|asia|ev1

    This key is used by confidence_scorer for per-setup learning.
    """
    monitoring_config = monitoring_config or {}

    parts = [
        signal_type,
        direction,
        _get_tier(symbol, monitoring_config),
        _classify_oi(metadata),
        _classify_funding(metadata),
        _classify_cvd(metadata),
        _get_session(),
        f"ev{_count_events(metadata)}",
    ]

    return "|".join(parts)


def setup_key_to_signal_type(setup_key: str) -> str:
    """Extract signal_type from a setup_key (first component)."""
    return setup_key.split("|")[0] if setup_key else "UNKNOWN"
