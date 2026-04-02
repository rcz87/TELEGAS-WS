"""
MovementDetector — Detects stealth accumulation/distribution patterns.

Scans for coins where large one-sided volume is happening without
corresponding price movement (quiet moves, flushes, absorption).
Sends alerts when stealth buying or selling is detected.
"""

import time
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Watchlist — only scan these coins for movement detection
WATCHLIST = {
    "BTC", "ETH", "SOL", "BNB", "XRP", "AVAX", "DOGE", "SUI",
    "LINK", "ADA", "DOT", "NEAR", "APT", "MATIC", "ARB", "OP",
    "HYPE", "TRUMP", "WIF", "PEPE", "BONK", "FET", "INJ", "TIA",
    "SEI", "JUP", "RENDER", "STX", "AAVE", "MKR",
}


def _fmt_usd(val: float) -> str:
    """Format USD value with K/M suffix."""
    if abs(val) >= 1_000_000:
        return f"${val / 1_000_000:.1f}M"
    elif abs(val) >= 1_000:
        return f"${val / 1_000:.0f}K"
    return f"${val:.0f}"


def _fmt_usd_signed(val: float) -> str:
    """Format signed USD value with K/M suffix."""
    sign = "+" if val >= 0 else "-"
    abs_val = abs(val)
    if abs_val >= 1_000_000:
        return f"{sign}${abs_val / 1_000_000:.1f}M"
    elif abs_val >= 1_000:
        return f"{sign}${abs_val / 1_000:.0f}K"
    return f"{sign}${abs_val:.0f}"


class MovementDetector:
    """Detects stealth buying/selling — large volume with small price move."""

    # Thresholds
    SELL_BUY_RATIO = 1.8       # Sell volume must be 1.8x buy volume (or vice versa)
    MIN_TOTAL_VOL = 50_000     # Minimum total volume to consider ($50K)
    MAX_PRICE_CHANGE = 0.3     # Price change must be small (<=0.3%) for stealth
    COOLDOWN_SECONDS = 1800    # 30 min cooldown per coin per type
    FLUSH_RATIO = 2.5          # Higher ratio for flush detection
    FLUSH_MIN_PRICE_CHANGE = 0.5  # Flush needs visible price drop

    def __init__(self):
        self._cooldowns: dict[str, float] = {}   # "COIN_TYPE" -> timestamp
        self._last_flush_items: dict[str, float] = {}
        self._last_stealth_direction: dict[str, str] = {}
        self._last_stealth_time: dict[str, float] = {}
        logger.info(f"MovementDetector initialized — watchlist: {len(WATCHLIST)} coins")

    def _on_cooldown(self, coin: str, alert_type: str) -> bool:
        """Check if coin+type is on cooldown."""
        key = f"{coin}_{alert_type}"
        last = self._cooldowns.get(key, 0)
        return (time.time() - last) < self.COOLDOWN_SECONDS

    def _set_cooldown(self, coin: str, alert_type: str):
        """Set cooldown for coin+type."""
        key = f"{coin}_{alert_type}"
        self._cooldowns[key] = time.time()

    def scan(self, coin: str, taker_data: dict, price_data: dict) -> list[dict]:
        """
        Scan a single coin for stealth movement patterns.

        Args:
            coin: Base symbol (e.g. "BTC")
            taker_data: Dict with 'buy_vol' and 'sell_vol' (USD)
            price_data: Dict with 'price_change_pct' and 'p_last'

        Returns:
            List of alert dicts (may be empty)
        """
        if coin not in WATCHLIST:
            return []

        alerts = []

        total_buy = taker_data.get("buy_vol", 0)
        total_sell = taker_data.get("sell_vol", 0)
        total_vol = total_buy + total_sell
        price_change_pct = price_data.get("price_change_pct", 0)
        p_last = price_data.get("p_last", 0)

        if total_vol < self.MIN_TOTAL_VOL:
            return []

        # Determine dominant side
        if total_buy > 0 and total_sell > 0:
            buy_ratio = total_buy / total_sell
            sell_ratio = total_sell / total_buy
        else:
            return []

        now = datetime.now()
        time_str = now.strftime("%H:%M")
        current_side = None
        prev_side = self._last_stealth_direction.get(coin)

        # ─── FLUSH detection (big sell + price dropping) ───
        if sell_ratio >= self.FLUSH_RATIO and abs(price_change_pct) >= self.FLUSH_MIN_PRICE_CHANGE and price_change_pct < 0:
            if not self._on_cooldown(coin, "FLUSH"):
                net_flow = total_buy - total_sell
                sell_pct = total_sell / total_vol * 100

                msg = (
                    f"\U0001f6a8 {coin} FLUSH DETECTED  |  ${p_last:,.2f}  |  {time_str} WIB\n"
                    f"\n"
                    f"Trigger: MOVEMENT DETECTOR  |  FLUSH\n"
                    f"\n"
                    f"Penjualan agresif dengan harga turun signifikan.\n"
                    f"Sell: {_fmt_usd(total_sell)} vs Buy: {_fmt_usd(total_buy)}\n"
                    f"Net flow: {_fmt_usd_signed(net_flow)} ({sell_pct:.0f}% sell dominant)\n"
                    f"Harga: ${p_last:,.2f} ({price_change_pct:+.1f}%)\n"
                    f"\n"
                    f"\U0001f4a5 FLUSH \u2014 liquidation cascade possible\n"
                    f"\n"
                    f"DYOR \u2014 verifikasi sebelum entry"
                )

                self._set_cooldown(coin, "FLUSH")
                self._last_flush_items[coin] = time.time()

                logger.info(
                    f"FLUSH {coin} sell={_fmt_usd(total_sell)} "
                    f"buy={_fmt_usd(total_buy)} ratio={sell_ratio:.1f}x "
                    f"price_chg={price_change_pct:+.2f}%"
                )

                alerts.append({
                    "type": "FLUSH",
                    "coin": coin,
                    "direction": "SHORT",
                    "message": msg,
                    "priority": 1,
                })

        # ─── STEALTH BUY detection (big buy volume, flat price) ───
        elif buy_ratio >= self.SELL_BUY_RATIO and abs(price_change_pct) <= self.MAX_PRICE_CHANGE:
            current_side = "BUY"
            if not self._on_cooldown(coin, "STEALTH_BUY"):
                buy_pct = total_buy / total_vol * 100
                net_flow = total_buy - total_sell

                msg = (
                    f"\U0001f6d2 {coin} ADA PEMBELI BESAR!  |  ${p_last:,.2f}  |  {time_str} WIB\n"
                    f"\n"
                    f"Trigger: MOVEMENT DETECTOR  |  STEALTH BUY\n"
                    f"\n"
                    f"Harga belum naik tapi ada yang beli BANYAK.\n"
                    f"Buy: {_fmt_usd(total_buy)} vs Sell: {_fmt_usd(total_sell)}\n"
                    f"Net flow: {_fmt_usd_signed(net_flow)} ({buy_pct:.0f}% buy dominant)\n"
                    f"Harga: ${p_last:,.2f} (masih FLAT {price_change_pct:+.1f}%)\n"
                    f"\n"
                    f"\U0001f449 PANTAU \u2014 begitu seller habis, harga bisa naik\n"
                    f"\n"
                    f"DYOR \u2014 verifikasi sebelum entry"
                )

                self._set_cooldown(coin, "STEALTH_BUY")
                self._last_stealth_direction[coin] = "BUY"
                self._last_stealth_time[coin] = time.time()

                logger.info(
                    f"STEALTH_BUY {coin} buy={_fmt_usd(total_buy)} "
                    f"sell={_fmt_usd(total_sell)} ratio={buy_ratio:.1f}x "
                    f"price_chg={price_change_pct:+.2f}%"
                )

                alerts.append({
                    "type": "STEALTH_BUY",
                    "coin": coin,
                    "direction": "LONG",
                    "message": msg,
                    "priority": 2,
                })

        # ─── STEALTH SELL detection (big sell volume, flat price) ───
        elif sell_ratio >= self.SELL_BUY_RATIO and abs(price_change_pct) <= self.MAX_PRICE_CHANGE:
            current_side = "SELL"
            if not self._on_cooldown(coin, "STEALTH_SELL"):
                sell_pct = total_sell / total_vol * 100
                net_flow = total_buy - total_sell

                msg = (
                    f"\U0001f515 {coin} ADA PENJUAL BESAR!  |  ${p_last:,.2f}  |  {time_str} WIB\n"
                    f"\n"
                    f"Trigger: MOVEMENT DETECTOR  |  STEALTH SELL\n"
                    f"\n"
                    f"Harga belum turun tapi ada yang jual BANYAK.\n"
                    f"Sell: {_fmt_usd(total_sell)} vs Buy: {_fmt_usd(total_buy)}\n"
                    f"Net flow: {_fmt_usd_signed(net_flow)} ({sell_pct:.0f}% sell dominant)\n"
                    f"Harga: ${p_last:,.2f} (masih FLAT {price_change_pct:+.1f}%)\n"
                    f"\n"
                    f"\U0001f449 PANTAU \u2014 begitu buyer habis, harga bisa jatuh\n"
                    f"\n"
                    f"DYOR \u2014 verifikasi sebelum entry"
                )

                self._set_cooldown(coin, "STEALTH_SELL")
                self._last_stealth_direction[coin] = "SELL"
                self._last_stealth_time[coin] = time.time()

                logger.info(
                    f"STEALTH_SELL {coin} sell={_fmt_usd(total_sell)} "
                    f"buy={_fmt_usd(total_buy)} ratio={total_sell/total_buy:.1f}x "
                    f"price_chg={price_change_pct:+.2f}%"
                )

                alerts.append({
                    "type": "STEALTH_SELL",
                    "coin": coin,
                    "direction": "SHORT",
                    "message": msg,
                    "priority": 2,
                })

        # If no stealth detected now but was active before, clear tracking
        if current_side is None and prev_side:
            self._last_stealth_direction.pop(coin, None)
            self._last_stealth_time.pop(coin, None)

        return alerts

    # ─────────────────────────────────────────────────────────
    # Stats
    # ─────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Return detector state for debugging."""
        now = time.time()
        active_cooldowns = {
            k: round(self.COOLDOWN_SECONDS - (now - v))
            for k, v in self._cooldowns.items()
            if now - v < self.COOLDOWN_SECONDS
        }
        recent_flushes = {
            k: round((now - v) / 60, 1)
            for k, v in self._last_flush_items.items()
            if now - v < 10800  # 3 hours
        }
        return {
            "watchlist": sorted(WATCHLIST),
            "active_cooldowns": active_cooldowns,
            "recent_flushes_min_ago": recent_flushes,
        }
