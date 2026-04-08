"""
Taker Signal Detector — Tier 1A (Exhaustion) + Tier 1B (Climactic) + Combo

Detects early reversal signals from taker flow patterns:
  1A: Taker sell/buy drops to <20% of 6h peak → seller/buyer exhausted
  1B: Current taker candle is the biggest in 6h → climactic dump/pump
  Combo: Both 1A + 1B within 2h window → higher conviction

Data source: CVDSnapshot.taker_buy_vol / taker_sell_vol from MarketContextBuffer
"""

import time
import logging
from collections import deque
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

_WIB = timezone(timedelta(hours=7))

# RELATIVE THRESHOLDS — scaled to coin's own taker activity
# Min peak = average_taker_1h * 3 (computed per coin from buffer)
# Climactic = same (must be bigger than 3x avg to be "climactic")
MIN_PEAK_MULTIPLIER = 3.0   # peak must be 3x avg taker to count
CLIMACTIC_MULTIPLIER = 3.0  # climactic candle must be 3x avg

EXHAUSTION_RATIO = 0.20  # current < 20% of 6h max = exhaustion
# FutCVD tolerance = average_taker_1h * 0.1 (10% of avg = noise floor)
FUTCVD_TOLERANCE_RATIO = 0.1
EXHAUSTION_MIN_HISTORY = 12  # need at least 12 snapshots (~1h) of history
COOLDOWN_SECONDS = 3600  # 60 min per symbol per pattern
COMBO_WINDOW = 7200  # 2 hours for combo detection


def _fmt(v: float) -> str:
    av = abs(v)
    if av >= 1e9:
        return f"${v / 1e9:,.1f}B"
    if av >= 1e6:
        return f"${v / 1e6:,.1f}M"
    if av >= 1e3:
        return f"${v / 1e3:,.0f}K"
    return f"${v:,.0f}"


def _fmts(v: float) -> str:
    sign = "+" if v >= 0 else ""
    av = abs(v)
    if av >= 1e6:
        return f"{sign}{v / 1e6:.1f}M"
    if av >= 1e3:
        return f"{sign}{v / 1e3:.0f}K"
    return f"{sign}{v:,.0f}"


class TakerSignalDetector:
    """
    Detects Tier 1 early reversal signals from taker flow patterns.

    Reads taker_buy_vol / taker_sell_vol from CVDSnapshot history
    stored in MarketContextBuffer.
    """

    def __init__(self):
        self._cooldowns: dict[str, float] = {}  # "SYMBOL_PATTERN" → timestamp
        self._combo_history: dict[str, list] = {}  # symbol → [{pattern, ts, direction}]

    def _on_cooldown(self, symbol: str, pattern: str) -> bool:
        key = f"{symbol}_{pattern}"
        last = self._cooldowns.get(key, 0)
        return (time.time() - last) < COOLDOWN_SECONDS

    def _set_cooldown(self, symbol: str, pattern: str):
        self._cooldowns[f"{symbol}_{pattern}"] = time.time()

    @staticmethod
    def _avg_taker_abs(taker_nets: list) -> float:
        """Average absolute taker net across buffer — represents this coin's typical activity."""
        if not taker_nets:
            return 0
        return sum(abs(t) for t in taker_nets) / len(taker_nets)

    def _record_combo(self, symbol: str, pattern: str, direction: str):
        if symbol not in self._combo_history:
            self._combo_history[symbol] = []
        self._combo_history[symbol].append({
            "pattern": pattern,
            "ts": time.time(),
            "direction": direction,
        })
        # Cleanup old entries
        cutoff = time.time() - COMBO_WINDOW
        self._combo_history[symbol] = [
            e for e in self._combo_history[symbol] if e["ts"] > cutoff
        ]

    def _check_combo(self, symbol: str, current_pattern: str, direction: str) -> bool:
        """Check if complementary pattern fired within 2h window."""
        history = self._combo_history.get(symbol, [])
        complement = "CLIMACTIC" if "EXHAUSTION" in current_pattern else "EXHAUSTION"
        cutoff = time.time() - COMBO_WINDOW
        for entry in history:
            if entry["ts"] > cutoff and complement in entry["pattern"] and entry["direction"] == direction:
                return True
        return False

    def scan(self, market_context_buffer) -> list[dict]:
        """
        Scan all symbols in buffer for taker exhaustion and climactic signals.

        Returns list of alert dicts: {type, symbol, direction, tier, message, ...}
        """
        alerts = []
        # Scan symbols that have spot CVD history (which contains taker data)
        with market_context_buffer._lock:
            symbols = list(market_context_buffer._spot_cvd_buffers.keys())

        for symbol in symbols:
            try:
                coin_alerts = self._check_symbol(symbol, market_context_buffer)
                alerts.extend(coin_alerts)
            except Exception as e:
                logger.error(f"TakerSignalDetector error {symbol}: {e}")

        return alerts

    def _check_symbol(self, symbol: str, mcb) -> list[dict]:
        """Check one symbol for exhaustion and climactic signals."""
        spot_hist = mcb.get_spot_cvd_history(symbol, n=72)
        if len(spot_hist) < EXHAUSTION_MIN_HISTORY:
            return []

        # Extract taker net per snapshot: buy - sell
        taker_nets = []
        for snap in spot_hist:
            net = snap.taker_buy_vol - snap.taker_sell_vol
            taker_nets.append(net)

        if not taker_nets:
            return []

        # Get supporting data
        fut_hist = mcb.get_futures_cvd_history(symbol, n=6)
        oi_snap = mcb.get_latest_oi(symbol)
        price_snap = mcb.get_latest_price(symbol)

        oi_change = oi_snap.oi_change_pct if oi_snap else 0.0
        current_price = price_snap.price if price_snap else 0.0

        # Price change over 6h for prior move context
        price_hist = mcb.get_price_history(symbol, limit=72)
        price_chg_6h = 0.0
        if price_hist and len(price_hist) >= 6:
            p_old = price_hist[-6].price if price_hist[-6].price > 0 else price_hist[0].price
            if p_old > 0:
                price_chg_6h = (current_price - p_old) / p_old * 100

        # FutCVD delta (latest vs previous)
        fut_delta = 0.0
        if fut_hist and len(fut_hist) >= 2:
            fut_delta = fut_hist[-1].cvd_latest - fut_hist[-2].cvd_latest

        current_taker = taker_nets[-1]
        max_sell = min(taker_nets)  # most negative
        max_buy = max(taker_nets)  # most positive

        alerts = []
        PRIOR_MOVE_PCT = 2.0  # Must have moved >2% for context

        # RELATIVE thresholds: based on this coin's avg taker activity
        avg_taker = self._avg_taker_abs(taker_nets)
        if avg_taker <= 0:
            return []  # no taker data = nothing to detect

        min_peak = avg_taker * MIN_PEAK_MULTIPLIER      # e.g. SOL avg $3M → min $9M
        threshold = avg_taker * CLIMACTIC_MULTIPLIER     # climactic must be 3x avg
        tolerance = avg_taker * FUTCVD_TOLERANCE_RATIO   # 10% of avg = noise
        if not self._on_cooldown(symbol, "SELL_EXHAUSTION") and max_sell < -min_peak and price_chg_6h < -PRIOR_MOVE_PCT:
            sell_ratio = abs(current_taker) / abs(max_sell) if current_taker < 0 else 0.0
            # Also trigger if current is slightly positive (seller completely gone)
            if current_taker >= 0:
                sell_ratio = 0.0  # effectively 0% of max sell

            if sell_ratio < EXHAUSTION_RATIO:
                # Qualifying: FutCVD not accelerating sell (with tolerance) + OI not crashing
                if fut_delta >= -tolerance and oi_change > -1.0:
                    is_combo = self._check_combo(symbol, "SELL_EXHAUSTION", "LONG")
                    self._record_combo(symbol, "SELL_EXHAUSTION", "LONG")
                    self._set_cooldown(symbol, "SELL_EXHAUSTION")

                    alert = self._format_exhaustion(
                        symbol, "SELL", current_taker, max_sell, sell_ratio,
                        fut_delta, oi_change, current_price, is_combo
                    )
                    alerts.append(alert)

        # --- TIER 1A: BUY EXHAUSTION (requires prior pump >2%) ---
        if not self._on_cooldown(symbol, "BUY_EXHAUSTION") and max_buy > min_peak and price_chg_6h > PRIOR_MOVE_PCT:
            buy_ratio = abs(current_taker) / abs(max_buy) if current_taker > 0 else 0.0
            if current_taker <= 0:
                buy_ratio = 0.0

            if buy_ratio < EXHAUSTION_RATIO:
                if fut_delta <= tolerance and oi_change > -1.0:
                    is_combo = self._check_combo(symbol, "BUY_EXHAUSTION", "SHORT")
                    self._record_combo(symbol, "BUY_EXHAUSTION", "SHORT")
                    self._set_cooldown(symbol, "BUY_EXHAUSTION")

                    alert = self._format_exhaustion(
                        symbol, "BUY", current_taker, max_buy, buy_ratio,
                        fut_delta, oi_change, current_price, is_combo
                    )
                    alerts.append(alert)

        # --- TIER 1B: CLIMACTIC SELL (requires prior dump >2%) ---
        if not self._on_cooldown(symbol, "CLIMACTIC_SELL"):
            if current_taker == max_sell and current_taker < -threshold and price_chg_6h < -PRIOR_MOVE_PCT:
                # FutCVD filter: climactic candle BY DEFINITION has large FutCVD
                # Only block if FutCVD is 1.5x MORE aggressive (separate wave, not same candle)
                if fut_delta >= -(abs(current_taker) * 1.5) and oi_change > -1.0:
                    is_combo = self._check_combo(symbol, "CLIMACTIC_SELL", "LONG")
                    self._record_combo(symbol, "CLIMACTIC_SELL", "LONG")
                    self._set_cooldown(symbol, "CLIMACTIC_SELL")

                    alert = self._format_climactic(
                        symbol, "SELL", current_taker, taker_nets,
                        fut_delta, oi_change, current_price, is_combo
                    )
                    alerts.append(alert)

        # --- TIER 1B: CLIMACTIC BUY ---
        # NOTE: Climactic BUY during a pump = continuation, NOT reversal.
        # Only trigger if price has been FALLING (buy exhaustion context).
        # For now, disabled — climactic SELL → LONG is the proven pattern.
        # Climactic BUY → SHORT is too prone to false positives mid-pump.

        return alerts

    def _format_exhaustion(self, symbol, side, current, peak, ratio,
                           fut_delta, oi_change, price, is_combo) -> dict:
        time_str = datetime.now(_WIB).strftime("%H:%M:%S")
        price_str = f"${price:,.4f}" if price > 0 else "N/A"

        if side == "SELL":
            direction = "LONG"
            title = "SELL EXHAUSTION"
            desc = "Seller HABIS — prepare for reversal"
        else:
            direction = "SHORT"
            title = "BUY EXHAUSTION"
            desc = "Buyer HABIS — prepare for reversal"

        combo_line = ""
        if is_combo:
            combo_line = "\n⚡ DOUBLE SIGNAL — EXHAUSTION + CLIMACTIC dalam 2 jam = HIGHER CONVICTION\n"

        msg = (
            f"🟡 TIER 1 — {title}\n"
            f"{symbol} | {price_str} | {time_str} WIB\n"
            f"\n"
            f"{desc}\n"
            f"{combo_line}"
            f"\n"
            f"TAKER:\n"
            f"Current  : {_fmts(current)}\n"
            f"Max 6h   : {_fmts(peak)}\n"
            f"Ratio    : {ratio * 100:.1f}% ← EXHAUSTED\n"
            f"\n"
            f"FILTERS:\n"
            f"FutCVD Δ : {_fmts(fut_delta)} ✅\n"
            f"OI 1h    : {oi_change:+.1f}% ✅\n"
            f"\n"
            f"ACTION: PREPARE — belum entry, monitor FutCVD flip"
        )

        logger.info(f"TIER1A {title} {symbol} ratio={ratio:.1%} combo={is_combo}")

        return {
            "type": f"TIER1A_{title.replace(' ', '_')}",
            "symbol": symbol,
            "direction": direction,
            "tier": 1,
            "message": msg,
            "is_combo": is_combo,
        }

    def _format_climactic(self, symbol, side, current, taker_nets,
                          fut_delta, oi_change, price, is_combo) -> dict:
        time_str = datetime.now(_WIB).strftime("%H:%M:%S")
        price_str = f"${price:,.4f}" if price > 0 else "N/A"

        if side == "SELL":
            direction = "LONG"
            title = "CLIMACTIC SELL"
            desc = "Dump TERBESAR 6 jam — potential bottom"
        else:
            direction = "SHORT"
            title = "CLIMACTIC BUY"
            desc = "Pump TERBESAR 6 jam — potential top"

        # Find previous max for context
        sorted_nets = sorted(taker_nets)
        if side == "SELL":
            prev_max = sorted_nets[1] if len(sorted_nets) > 1 else 0
        else:
            sorted_nets_desc = sorted(taker_nets, reverse=True)
            prev_max = sorted_nets_desc[1] if len(sorted_nets_desc) > 1 else 0

        combo_line = ""
        if is_combo:
            combo_line = "\n⚡ DOUBLE SIGNAL — EXHAUSTION + CLIMACTIC dalam 2 jam = HIGHER CONVICTION\n"

        msg = (
            f"🔴 TIER 1 — {title}\n"
            f"{symbol} | {price_str} | {time_str} WIB\n"
            f"\n"
            f"{desc}\n"
            f"{combo_line}"
            f"\n"
            f"TAKER:\n"
            f"Current  : {_fmts(current)} ← MAX {'SELL' if side == 'SELL' else 'BUY'} 6H\n"
            f"Prev max : {_fmts(prev_max)}\n"
            f"\n"
            f"FILTERS:\n"
            f"FutCVD Δ : {_fmts(fut_delta)} ✅\n"
            f"OI 1h    : {oi_change:+.1f}% ✅\n"
            f"\n"
            f"ACTION: PREPARE — ini bisa {'bottom' if side == 'SELL' else 'top'}, monitor exhaustion"
        )

        logger.info(f"TIER1B {title} {symbol} taker={_fmts(current)} combo={is_combo}")

        return {
            "type": f"TIER1B_{title.replace(' ', '_')}",
            "symbol": symbol,
            "direction": direction,
            "tier": 1,
            "message": msg,
            "is_combo": is_combo,
        }
