# Symbol Normalizer — Canonical symbol mapping
# Merges CoinGlass multi-exchange variants into single canonical symbol

"""
Symbol Normalizer

CoinGlass sends liquidation/trade data with exchange-specific symbol formats:
  Binance:    BTCUSDT
  OKX:        BTC-USDT-SWAP
  Bitget:     BTCUSDT_UMCBL
  Bybit:      BTC_USDT
  dYdX:       BTCPERP
  Coinbase:   BTC-USD
  Hyperliquid: BTCUSD

All should map to a single canonical symbol: BTCUSDT

This prevents:
- Duplicate buffers per coin
- Duplicate signal generation
- Duplicate Telegram alerts (5x BTC alerts instead of 1)
"""

import re

# Exchange-specific suffixes to strip
_EXCHANGE_SUFFIXES = (
    '_UMCBL',   # Bitget
    '_DMCBL',   # Bitget inverse
    '_CMCBL',   # Bitget coin-margin
    '_PERP',    # Some exchanges
    '-SWAP',    # OKX
)

# Known commodity mappings (XYZ:GOLD-USD → XAUUSDT)
_COMMODITY_MAP = {
    'GOLD': 'XAU',
    'SILVER': 'XAG',
    'BRENTOIL': 'BRENT',
    'SP500': 'SP500',
    'NASDAQ': 'NAS100',
}

# Pre-compiled regex for XYZ: prefix
_PREFIX_RE = re.compile(r'^[A-Z]{2,5}:')


def normalize_symbol(raw: str) -> str:
    """
    Normalize any CoinGlass symbol to canonical form.

    Examples:
        BTCUSDT          → BTCUSDT
        BTC-USDT         → BTCUSDT
        BTC-USDT-SWAP    → BTCUSDT
        BTCUSDT_UMCBL    → BTCUSDT
        BTC_USDT         → BTCUSDT
        BTCPERP          → BTCUSDT
        BTC-USD          → BTCUSDT
        BTCUSD           → BTCUSDT
        ETHPERP          → ETHUSDT
        XYZ:GOLD-USD     → XAUUSDT
        XYZ:SP500-USD    → SP500USDT
        1000PEPEUSDT     → 1000PEPEUSDT
        老子_USDT         → (returned as-is, non-latin)
    """
    if not raw or not isinstance(raw, str):
        return raw or ''

    s = raw.strip().upper()

    # Skip non-ASCII symbols (Chinese etc.) — return cleaned but not normalized
    if not s.isascii():
        return s.replace('-', '').replace('_', '')

    # 1. Remove exchange/vendor prefix (XYZ:, ABC:)
    if ':' in s:
        prefix, base = s.split(':', 1)
        # Check commodity mapping
        for commodity, canonical in _COMMODITY_MAP.items():
            if commodity in base:
                base = base.replace(commodity, canonical)
        s = base

    # 2. Remove exchange-specific suffixes
    for suffix in _EXCHANGE_SUFFIXES:
        if s.endswith(suffix):
            s = s[:-len(suffix)]
            break

    # 3. Handle PERP suffix (BTCPERP → BTC, then add USDT)
    if s.endswith('PERP'):
        s = s[:-4]

    # 4. Remove separators (- and _)
    s = s.replace('-', '').replace('_', '')

    # 5. Normalize quote currency to USDT
    #    BTCUSD → BTCUSDT, BTCUSDC → BTCUSDT, BTCBUSD → BTCUSDT
    #    But BTCUSDT stays BTCUSDT
    if s.endswith('USDT'):
        pass  # Already canonical
    elif s.endswith('USDC'):
        s = s[:-4] + 'USDT'
    elif s.endswith('BUSD'):
        s = s[:-4] + 'USDT'
    elif s.endswith('USD'):
        s = s[:-3] + 'USDT'
    else:
        # No recognized quote — append USDT if it looks like a base symbol
        # (e.g., after stripping PERP: "BTC" → "BTCUSDT")
        if len(s) <= 10 and s.isalnum():
            s = s + 'USDT'

    return s


def to_base_symbol(pair_symbol: str) -> str:
    """
    Extract base symbol from canonical pair.

    BTCUSDT → BTC, ETHUSDT → ETH, 1000PEPEUSDT → 1000PEPE
    """
    canonical = normalize_symbol(pair_symbol)
    for suffix in ('USDT', 'USDC', 'BUSD', 'USD'):
        if canonical.endswith(suffix):
            return canonical[:-len(suffix)]
    return canonical


def display_symbol(pair_symbol: str) -> str:
    """
    Clean symbol for display in Telegram alerts.

    BTCUSDT → BTC, ETHUSDT → ETH, XAUUSDT → XAU, SP500USDT → SP500
    """
    return to_base_symbol(pair_symbol)
