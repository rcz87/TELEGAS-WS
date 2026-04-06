"""
Movement Detector v2 — multi-condition alerts with grade system.

Reads DIRECTLY from MarketContextBuffer and BufferManager (no duplicate data).
Patterns: STEALTH_BUY/SELL, QUIET_TO_MOVE, FLUSH_SWEEP, ABSORPTION_REVERSAL, FLOW_REVERSAL.

Upgrade v2:
- SpotCVD + FutCVD in every alert (WAJIB)
- OI state context
- Consecutive candle count
- Grade A/B/C system (multi-condition)
- Actionable output per grade

Data flow:
    REST Poller (5m) → MarketContextBuffer (snapshots)
    WebSocket → BufferManager (liquidations)
        ↓
    MovementDetector.scan() reads from both buffers
        ↓
    Returns list of alert messages → alert_queue
"""

import time
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

WATCHLIST = {"BTC", "ETH", "SOL", "BNB", "XRP", "AVAX", "DOGE", "SUI", "LINK", "ADA"}

# Per-coin thresholds scaled by market cap.
_THRESHOLDS = {
    "BTC": {
        "cvd_quiet": 5_000_000,
        "cvd_spike": 50_000_000,
        "flush_cvd": -30_000_000,
        "absorption_cvd": 30_000_000,
        "flush_liq_min": 500_000,
        "oi_drop_pct": -0.3,
        "price_drop_pct": -0.3,
    },
    "ETH": {
        "cvd_quiet": 2_000_000,
        "cvd_spike": 20_000_000,
        "flush_cvd": -15_000_000,
        "absorption_cvd": 15_000_000,
        "flush_liq_min": 200_000,
        "oi_drop_pct": -0.3,
        "price_drop_pct": -0.4,
    },
    "SOL": {
        "cvd_quiet": 500_000,
        "cvd_spike": 5_000_000,
        "flush_cvd": -5_000_000,
        "absorption_cvd": 5_000_000,
        "flush_liq_min": 50_000,
        "oi_drop_pct": -0.4,
        "price_drop_pct": -0.5,
    },
    "BNB": {
        "cvd_quiet": 500_000,
        "cvd_spike": 5_000_000,
        "flush_cvd": -5_000_000,
        "absorption_cvd": 5_000_000,
        "flush_liq_min": 50_000,
        "oi_drop_pct": -0.4,
        "price_drop_pct": -0.5,
    },
    "XRP": {
        "cvd_quiet": 300_000,
        "cvd_spike": 3_000_000,
        "flush_cvd": -3_000_000,
        "absorption_cvd": 3_000_000,
        "flush_liq_min": 30_000,
        "oi_drop_pct": -0.4,
        "price_drop_pct": -0.5,
    },
}

_DEFAULT_THRESHOLDS = {
    "cvd_quiet": 200_000,
    "cvd_spike": 2_000_000,
    "flush_cvd": -2_000_000,
    "absorption_cvd": 2_000_000,
    "flush_liq_min": 20_000,
    "oi_drop_pct": -0.5,
    "price_drop_pct": -0.5,
}

_WIB = timezone(timedelta(hours=7))


def _fmt(value: float) -> str:
    """Format USD value with K/M suffix."""
    av = abs(value)
    if av >= 1e9:
        return f"${value / 1e9:,.1f}B"
    if av >= 1e6:
        return f"${value / 1e6:,.1f}M"
    if av >= 1e3:
        return f"${value / 1e3:,.0f}K"
    return f"${value:,.0f}"


def _fmts(value: float) -> str:
    """Format signed USD value with +/- and K/M suffix."""
    sign = "+" if value >= 0 else ""
    av = abs(value)
    if av >= 1e9:
        return f"{sign}{value / 1e9:.1f}B"
    if av >= 1e6:
        return f"{sign}{value / 1e6:.1f}M"
    if av >= 1e3:
        return f"{sign}{value / 1e3:.0f}K"
    return f"{sign}{value:,.0f}"


def _cvd_deltas(cvd_snapshots: list) -> list[float]:
    """Compute per-snapshot CVD deltas from consecutive snapshots."""
    if len(cvd_snapshots) < 2:
        return []
    return [cvd_snapshots[i].cvd_latest - cvd_snapshots[i - 1].cvd_latest
            for i in range(1, len(cvd_snapshots))]


def _consecutive_candles(deltas: list, direction: str) -> int:
    """Count consecutive candles in same direction from the end."""
    count = 0
    for d in reversed(deltas):
        if direction == "SELL" and d < 0:
            count += 1
        elif direction == "BUY" and d > 0:
            count += 1
        else:
            break
    return count


def _oi_state(oi_hist: list) -> tuple[str, float]:
    """Return OI interpretation and change %."""
    if not oi_hist or len(oi_hist) < 2:
        return "N/A", 0.0
    oi_change = oi_hist[-1].oi_change_pct
    if len(oi_hist) >= 4:
        oi_now = oi_hist[-1].current_oi_usd
        oi_before = oi_hist[-4].current_oi_usd
        if oi_before > 0:
            oi_change = (oi_now - oi_before) / oi_before * 100
    if oi_change > 0.3:
        return "NAIK", oi_change
    elif oi_change < -0.3:
        return "TURUN", oi_change
    return "FLAT", oi_change


def _build_cvd_block(spot_cvd_hist: list, fut_cvd_hist: list,
                     direction: str) -> tuple[str, bool, bool]:
    """
    Build CVD section text and return (text, spot_confirms, fut_confirms).
    direction = "BUY" or "SELL"
    """
    lines = []
    spot_confirms = False
    fut_confirms = False

    # SpotCVD
    if spot_cvd_hist and len(spot_cvd_hist) >= 2:
        s = spot_cvd_hist[-1]
        spot_val = s.cvd_latest
        spot_dir = s.cvd_direction
        spot_deltas = _cvd_deltas(spot_cvd_hist)
        spot_consec = _consecutive_candles(spot_deltas, direction)
        spot_sign = "POSITIF" if spot_val >= 0 else "NEGATIF"
        lines.append(f"SpotCVD : {_fmts(spot_val)} ({spot_sign}) {spot_dir} | {spot_consec} candle {direction.lower()}")
        if direction == "SELL" and (spot_dir == "FALLING" or spot_val < 0):
            spot_confirms = True
        elif direction == "BUY" and (spot_dir == "RISING" or spot_val > 0):
            spot_confirms = True
    else:
        lines.append("SpotCVD : N/A (data belum tersedia)")

    # FutCVD
    if fut_cvd_hist and len(fut_cvd_hist) >= 2:
        f = fut_cvd_hist[-1]
        fut_val = f.cvd_latest
        fut_dir = f.cvd_direction
        fut_deltas = _cvd_deltas(fut_cvd_hist)
        fut_consec = _consecutive_candles(fut_deltas, direction)
        fut_sign = "POSITIF" if fut_val >= 0 else "NEGATIF"
        lines.append(f"FutCVD  : {_fmts(fut_val)} ({fut_sign}) {fut_dir} | {fut_consec} candle {direction.lower()}")
        if direction == "SELL" and (fut_dir == "FALLING" or fut_val < 0):
            fut_confirms = True
        elif direction == "BUY" and (fut_dir == "RISING" or fut_val > 0):
            fut_confirms = True
    else:
        lines.append("FutCVD  : N/A")

    # Alignment
    if spot_confirms and fut_confirms:
        lines.append("Aligned : YES (keduanya konfirmasi)")
    elif spot_confirms or fut_confirms:
        lines.append("Aligned : PARTIAL (1/2 konfirmasi)")
    else:
        lines.append("Aligned : NO (belum konfirmasi)")

    return "\n".join(lines), spot_confirms, fut_confirms


def _grade_label(grade: str) -> str:
    """Return grade emoji + text."""
    if grade == "A":
        return "GRADE A — ACTIONABLE"
    elif grade == "B":
        return "GRADE B — WATCH"
    return "GRADE C — INFO"


def _action_text(grade: str, direction: str, spot_confirms: bool) -> str:
    """Return specific action text based on grade."""
    d = "LONG" if direction == "BUY" else "SHORT"
    if grade == "A":
        return f"{d} setup valid — semua data aligned. Verifikasi level entry."
    elif grade == "B":
        if not spot_confirms:
            return f"SpotCVD belum konfirmasi — TUNGGU sebelum entry {d}."
        return f"Data belum lengkap aligned — pantau, siapkan order."
    return f"Hanya 1 metrik triggered — abaikan atau pantau ringan."


class MovementDetector:
    """
    Detect market movements with multi-condition grading.

    Grade A: SpotCVD + FutCVD + Taker aligned → ACTIONABLE
    Grade B: FutCVD + Taker aligned, SpotCVD belum → WATCH
    Grade C: Single metric only → INFO (not sent to Telegram)
    """

    COOLDOWN_SECONDS = 3600
    STEALTH_COOLDOWN_SECONDS = 900
    REVERSAL_COOLDOWN_SECONDS = 600

    def __init__(self):
        self._cooldowns: dict[str, float] = {}
        self._last_flush: dict[str, float] = {}
        self._flush_price: dict[str, float] = {}
        self._last_stealth_direction: dict[str, str] = {}
        self._last_stealth_time: dict[str, float] = {}

    def _thresholds(self, coin: str) -> dict:
        return _THRESHOLDS.get(coin, _DEFAULT_THRESHOLDS)

    def _on_cooldown(self, coin: str, pattern: str) -> bool:
        key = f"{coin}_{pattern}"
        last = self._cooldowns.get(key, 0)
        if pattern in ("STEALTH_BUY", "STEALTH_SELL"):
            cd = self.STEALTH_COOLDOWN_SECONDS
        elif pattern == "FLOW_REVERSAL":
            cd = self.REVERSAL_COOLDOWN_SECONDS
        else:
            cd = self.COOLDOWN_SECONDS
        return (time.time() - last) < cd

    def _set_cooldown(self, coin: str, pattern: str):
        self._cooldowns[f"{coin}_{pattern}"] = time.time()

    @staticmethod
    def _price_range_pct(price_snapshots: list) -> float:
        if not price_snapshots:
            return 0.0
        prices = [s.price for s in price_snapshots if s.price > 0]
        if len(prices) < 2:
            return 0.0
        mean = sum(prices) / len(prices)
        return (max(prices) - min(prices)) / mean * 100 if mean else 0.0

    @staticmethod
    def _aggregate_liquidations(bm, symbol_usdt: str,
                                seconds: int) -> tuple[float, float]:
        events = bm.get_liquidations(symbol_usdt, time_window=seconds)
        long_liq = short_liq = 0.0
        for ev in events:
            vol = float(ev.get("vol", 0))
            side = int(ev.get("side", 0))
            if side == 1:
                long_liq += vol
            elif side == 2:
                short_liq += vol
        return long_liq, short_liq

    # ------------------------------------------------------------------
    # Main scan
    # ------------------------------------------------------------------

    def scan(self, market_context_buffer, buffer_manager) -> list[dict]:
        alerts = []
        for coin in WATCHLIST:
            try:
                coin_alerts = self._check_coin(coin, market_context_buffer, buffer_manager)
                alerts.extend(coin_alerts)
            except Exception as e:
                logger.error(f"MovementDetector error {coin}: {e}")
        return alerts

    def _check_coin(self, coin: str, mcb, bm) -> list[dict]:
        fut_cvd_hist = mcb.get_futures_cvd_history(coin, n=24)
        spot_cvd_hist = mcb.get_spot_cvd_history(coin, n=24)
        oi_hist = mcb.get_oi_history(coin, n=24)
        price_hist = mcb.get_price_history(coin, limit=24)

        if len(fut_cvd_hist) < 6:
            return []

        symbol_usdt = f"{coin}USDT"
        alerts = []

        stealth = self._check_stealth_flow(
            coin, fut_cvd_hist, spot_cvd_hist, oi_hist, price_hist)
        alerts.extend(stealth)

        alert = self._check_quiet_to_move(
            coin, fut_cvd_hist, spot_cvd_hist, oi_hist, price_hist)
        if alert:
            alerts.append(alert)

        alert = self._check_flush(
            coin, fut_cvd_hist, spot_cvd_hist, oi_hist, price_hist, bm, symbol_usdt)
        if alert:
            alerts.append(alert)

        alert = self._check_absorption(
            coin, fut_cvd_hist, spot_cvd_hist, oi_hist, price_hist, bm, symbol_usdt)
        if alert:
            alerts.append(alert)

        return alerts

    # ------------------------------------------------------------------
    # Pattern 1: QUIET → MOVE
    # ------------------------------------------------------------------

    def _check_quiet_to_move(self, coin, fut_cvd_hist, spot_cvd_hist,
                             oi_hist, price_hist) -> dict | None:
        if self._on_cooldown(coin, "QUIET_TO_MOVE"):
            return None
        if len(fut_cvd_hist) < 12:
            return None

        th = self._thresholds(coin)
        deltas = _cvd_deltas(fut_cvd_hist)
        if len(deltas) < 10:
            return None

        baseline_deltas = deltas[:-3]
        recent_deltas = deltas[-3:]
        baseline_avg_abs = sum(abs(d) for d in baseline_deltas) / len(baseline_deltas)
        recent_sum = sum(recent_deltas)
        recent_avg_abs = sum(abs(d) for d in recent_deltas) / len(recent_deltas)

        if baseline_avg_abs > th["cvd_quiet"]:
            return None

        min_baseline_ref = max(baseline_avg_abs, 1.0)
        if recent_avg_abs < th["cvd_spike"] / 3 and recent_avg_abs < min_baseline_ref * 4:
            return None

        if price_hist and len(price_hist) >= 10:
            if self._price_range_pct(price_hist[:-3]) > 0.8:
                return None

        direction = "BUY" if recent_sum > 0 else "SELL"
        d_label = "LONG" if direction == "BUY" else "SHORT"
        current_price = price_hist[-1].price if price_hist else 0
        time_str = datetime.now(_WIB).strftime("%H:%M")

        # CVD + OI context
        cvd_block, spot_ok, fut_ok = _build_cvd_block(
            spot_cvd_hist, fut_cvd_hist, direction)
        oi_label, oi_pct = _oi_state(oi_hist)

        # Grade
        conditions = sum([spot_ok, fut_ok, oi_label == "NAIK" if direction == "BUY" else oi_label == "TURUN"])
        grade = "A" if conditions >= 2 else "B" if conditions >= 1 else "C"

        if grade == "C":
            return None  # Don't send Grade C

        self._set_cooldown(coin, "QUIET_TO_MOVE")

        msg = (
            f"{coin} MULAI BERGERAK | ${current_price:,.2f} | {time_str} WIB\n"
            f"\n"
            f"{_grade_label(grade)}\n"
            f"Trigger: QUIET > MOVE | {d_label}\n"
            f"\n"
            f"FLOW:\n"
            f"FutCVD spike: {_fmts(recent_sum)} (15 menit)\n"
            f"Baseline: {_fmt(baseline_avg_abs)}/candle\n"
            f"Spike: {recent_avg_abs / min_baseline_ref:.1f}x dari baseline\n"
            f"\n"
            f"CVD:\n"
            f"{cvd_block}\n"
            f"\n"
            f"OI: {oi_label} ({oi_pct:+.2f}%)\n"
            f"\n"
            f"ACTION:\n"
            f"{_action_text(grade, direction, spot_ok)}\n"
            f"\n"
            f"DYOR — verifikasi sebelum entry"
        )

        logger.info(f"QUIET_TO_MOVE {coin} {d_label} grade={grade}")

        return {
            "type": "QUIET_TO_MOVE",
            "coin": coin,
            "direction": d_label,
            "grade": grade,
            "message": msg,
            "priority": 1 if grade == "A" else 2,
        }

    # ------------------------------------------------------------------
    # Pattern 2: FLUSH / LIQUIDITY SWEEP
    # ------------------------------------------------------------------

    def _check_flush(self, coin, fut_cvd_hist, spot_cvd_hist, oi_hist,
                     price_hist, bm, symbol_usdt) -> dict | None:
        if self._on_cooldown(coin, "FLUSH_SWEEP"):
            return None

        th = self._thresholds(coin)
        deltas = _cvd_deltas(fut_cvd_hist)
        if len(deltas) < 3:
            return None

        recent_cvd_sum = sum(deltas[-3:])
        if recent_cvd_sum > th["flush_cvd"]:
            return None

        oi_label, oi_pct = _oi_state(oi_hist)
        price_change = 0.0
        current_price = 0.0
        if price_hist and len(price_hist) >= 2:
            current_price = price_hist[-1].price
            price_before = price_hist[-4].price if len(price_hist) >= 4 else price_hist[0].price
            if price_before > 0:
                price_change = (current_price - price_before) / price_before * 100

        long_liq, short_liq = self._aggregate_liquidations(bm, symbol_usdt, 900)

        confirmations = 0
        if oi_pct < th["oi_drop_pct"]:
            confirmations += 1
        if price_change < th["price_drop_pct"]:
            confirmations += 1
        if long_liq > th["flush_liq_min"]:
            confirmations += 1
        if confirmations < 2:
            return None

        time_str = datetime.now(_WIB).strftime("%H:%M")
        cvd_block, spot_ok, fut_ok = _build_cvd_block(
            spot_cvd_hist, fut_cvd_hist, "SELL")
        sell_candles = _consecutive_candles(deltas, "SELL")

        self._last_flush[coin] = time.time()
        self._flush_price[coin] = current_price
        self._set_cooldown(coin, "FLUSH_SWEEP")

        msg = (
            f"{coin} FLUSH / LIQUIDITY SWEEP | ${current_price:,.2f} | {time_str} WIB\n"
            f"\n"
            f"GRADE A — WARNING\n"
            f"Trigger: FLUSH SWEEP\n"
            f"\n"
            f"FLOW:\n"
            f"FutCVD: {_fmts(recent_cvd_sum)} (15 menit)\n"
            f"Long Liq: {_fmt(long_liq)} | Short Liq: {_fmt(short_liq)}\n"
            f"Price: {price_change:+.2f}%\n"
            f"Sell candle berturut: {sell_candles}x\n"
            f"\n"
            f"CVD:\n"
            f"{cvd_block}\n"
            f"\n"
            f"OI: {oi_label} ({oi_pct:+.2f}%)"
            f"{' — shorts masuk fresh (berbahaya)' if oi_label == 'NAIK' else ' — longs exit' if oi_label == 'TURUN' else ''}\n"
            f"\n"
            f"ACTION:\n"
            f"JANGAN entry — tunggu absorption / reversal signal\n"
            f"\n"
            f"DYOR — verifikasi sebelum entry"
        )

        logger.info(
            f"FLUSH_SWEEP {coin} CVD={_fmts(recent_cvd_sum)} "
            f"LongLiq={_fmt(long_liq)} OI={oi_pct:+.2f}%"
        )

        return {
            "type": "FLUSH_SWEEP",
            "coin": coin,
            "direction": "SHORT",
            "grade": "A",
            "message": msg,
            "priority": 1,
        }

    # ------------------------------------------------------------------
    # Pattern 3: ABSORPTION AFTER SWEEP
    # ------------------------------------------------------------------

    def _check_absorption(self, coin, fut_cvd_hist, spot_cvd_hist, oi_hist,
                          price_hist, bm, symbol_usdt) -> dict | None:
        if self._on_cooldown(coin, "ABSORPTION_REVERSAL"):
            return None

        last_flush_ts = self._last_flush.get(coin, 0)
        if last_flush_ts == 0:
            return None
        hours_since = (time.time() - last_flush_ts) / 3600
        if hours_since > 3.0 or hours_since < 0.1:
            return None

        th = self._thresholds(coin)
        deltas = _cvd_deltas(fut_cvd_hist)
        if len(deltas) < 3:
            return None

        recent_cvd_sum = sum(deltas[-3:])
        if recent_cvd_sum < th["absorption_cvd"]:
            return None

        oi_label, oi_pct = _oi_state(oi_hist)
        if oi_label != "NAIK":
            return None

        long_liq_recent, _ = self._aggregate_liquidations(bm, symbol_usdt, 900)
        long_liq_earlier, _ = self._aggregate_liquidations(bm, symbol_usdt, 3600)
        liq_during_flush = long_liq_earlier - long_liq_recent
        if liq_during_flush > 0 and long_liq_recent > liq_during_flush * 0.5:
            return None

        current_price = price_hist[-1].price if price_hist else 0
        flush_price = self._flush_price.get(coin, 0)
        if flush_price > 0 and current_price > 0:
            if (current_price - flush_price) / flush_price * 100 > 1.5:
                return None

        time_str = datetime.now(_WIB).strftime("%H:%M")
        cvd_block, spot_ok, fut_ok = _build_cvd_block(
            spot_cvd_hist, fut_cvd_hist, "BUY")
        buy_candles = _consecutive_candles(deltas, "BUY")

        grade = "A" if spot_ok and fut_ok else "B"
        self._set_cooldown(coin, "ABSORPTION_REVERSAL")

        msg = (
            f"{coin} ABSORPTION DETECTED | ${current_price:,.2f} | {time_str} WIB\n"
            f"\n"
            f"{_grade_label(grade)}\n"
            f"Trigger: ABSORPTION REVERSAL\n"
            f"\n"
            f"FLOW:\n"
            f"FutCVD flip: {_fmts(recent_cvd_sum)} (setelah flush)\n"
            f"Buy candle berturut: {buy_candles}x\n"
            f"Flush: {hours_since:.1f} jam lalu\n"
            f"Long liq berhenti\n"
            f"\n"
            f"CVD:\n"
            f"{cvd_block}\n"
            f"\n"
            f"OI: {oi_label} ({oi_pct:+.2f}%) — NEW POSITIONS\n"
            f"\n"
            f"ACTION:\n"
            f"{_action_text(grade, 'BUY', spot_ok)}\n"
            f"\n"
            f"DYOR — verifikasi sebelum entry"
        )

        logger.info(
            f"ABSORPTION_REVERSAL {coin} grade={grade} "
            f"CVD={_fmts(recent_cvd_sum)} OI={oi_pct:+.2f}%"
        )

        return {
            "type": "ABSORPTION_REVERSAL",
            "coin": coin,
            "direction": "LONG",
            "grade": grade,
            "message": msg,
            "priority": 1,
        }

    # ------------------------------------------------------------------
    # Pattern 4: STEALTH FLOW (buy/sell/reversal)
    # ------------------------------------------------------------------

    def _check_stealth_flow(self, coin, fut_cvd_hist, spot_cvd_hist,
                            oi_hist, price_hist) -> list[dict]:
        if len(fut_cvd_hist) < 3 or len(price_hist) < 3:
            return []

        recent_snaps = fut_cvd_hist[-3:]
        total_buy = total_sell = 0.0
        buy_dom_count = sell_dom_count = 0

        for snap in recent_snaps:
            buy, sell = snap.taker_buy_vol, snap.taker_sell_vol
            if buy <= 0 and sell <= 0:
                return []
            total_buy += buy
            total_sell += sell
            if sell > 0 and buy / sell > 2.0:
                buy_dom_count += 1
            if buy > 0 and sell / buy > 2.0:
                sell_dom_count += 1

        total_vol = total_buy + total_sell
        if total_vol <= 0:
            return []

        recent_prices = price_hist[-3:]
        p_first, p_last = recent_prices[0].price, recent_prices[-1].price
        if p_first <= 0:
            return []
        price_change_pct = (p_last - p_first) / p_first * 100

        current_side = None
        if buy_dom_count >= 2 and total_sell > 0 and total_buy / total_sell >= 2.0:
            current_side = "BUY"
        elif sell_dom_count >= 2 and total_buy > 0 and total_sell / total_buy >= 2.0:
            current_side = "SELL"

        alerts = []
        time_str = datetime.now(_WIB).strftime("%H:%M")
        prev_side = self._last_stealth_direction.get(coin)
        prev_time = self._last_stealth_time.get(coin, 0)

        # --- FLOW REVERSAL ---
        if (current_side and prev_side and current_side != prev_side
                and (time.time() - prev_time) < 1800
                and not self._on_cooldown(coin, "FLOW_REVERSAL")):

            net_flow = total_buy - total_sell
            cvd_block, spot_ok, fut_ok = _build_cvd_block(
                spot_cvd_hist, fut_cvd_hist, current_side)
            oi_label, oi_pct = _oi_state(oi_hist)

            flip_msg = ("BUYER > SELLER" if current_side == "SELL"
                        else "SELLER > BUYER")

            self._set_cooldown(coin, "FLOW_REVERSAL")

            msg = (
                f"{coin} FLOW REVERSAL | ${p_last:,.2f} | {time_str} WIB\n"
                f"\n"
                f"GRADE A — WARNING\n"
                f"Trigger: FLOW REVERSAL ({flip_msg})\n"
                f"\n"
                f"FLOW:\n"
                f"Buy: {_fmt(total_buy)} vs Sell: {_fmt(total_sell)}\n"
                f"Net: {_fmts(net_flow)}\n"
                f"\n"
                f"CVD:\n"
                f"{cvd_block}\n"
                f"\n"
                f"OI: {oi_label} ({oi_pct:+.2f}%)\n"
                f"\n"
                f"ACTION:\n"
                f"Sudah entry > tighten stop.\n"
                f"Belum entry > tunggu dulu.\n"
                f"\n"
                f"DYOR — verifikasi sebelum entry"
            )

            logger.info(f"FLOW_REVERSAL {coin} {prev_side}>{current_side}")

            alerts.append({
                "type": "FLOW_REVERSAL",
                "coin": coin,
                "direction": "LONG" if current_side == "BUY" else "SHORT",
                "grade": "A",
                "message": msg,
                "priority": 1,
            })

        # --- STEALTH BUY / SELL ---
        if current_side and abs(price_change_pct) <= 0.3:
            pattern = f"STEALTH_{current_side}"
            if not self._on_cooldown(coin, pattern):
                net_flow = total_buy - total_sell
                dom_pct = (total_buy if current_side == "BUY" else total_sell) / total_vol * 100

                # CVD + OI context
                cvd_block, spot_ok, fut_ok = _build_cvd_block(
                    spot_cvd_hist, fut_cvd_hist, current_side)
                oi_label, oi_pct = _oi_state(oi_hist)

                # Consecutive candle count
                fut_deltas = _cvd_deltas(fut_cvd_hist)
                consec = _consecutive_candles(fut_deltas, current_side)

                # OI interpretation for this direction
                if current_side == "SELL":
                    oi_interp = "shorts masuk fresh (berbahaya)" if oi_label == "NAIK" else \
                                "longs exit (kurang berbahaya)" if oi_label == "TURUN" else ""
                else:
                    oi_interp = "longs masuk fresh (bullish)" if oi_label == "NAIK" else \
                                "shorts covering" if oi_label == "TURUN" else ""

                # Grade: A = spot+fut+taker, B = fut+taker, C = taker only
                conditions = sum([spot_ok, fut_ok])
                grade = "A" if conditions == 2 else "B" if conditions == 1 else "C"

                if grade == "C":
                    # Don't send Grade C, but still track direction
                    self._last_stealth_direction[coin] = current_side
                    self._last_stealth_time[coin] = time.time()
                else:
                    d_label = "LONG" if current_side == "BUY" else "SHORT"
                    action_label = "PEMBELI" if current_side == "BUY" else "PENJUAL"
                    trend_msg = ("seller habis, harga bisa terbang" if current_side == "BUY"
                                 else "buyer habis, harga bisa jatuh")

                    self._set_cooldown(coin, pattern)
                    self._last_stealth_direction[coin] = current_side
                    self._last_stealth_time[coin] = time.time()

                    msg = (
                        f"{coin} — STEALTH {current_side} DETECTED | ${p_last:,.2f} | {time_str} WIB\n"
                        f"\n"
                        f"{_grade_label(grade)}\n"
                        f"Trigger: STEALTH {current_side} | {d_label}\n"
                        f"\n"
                        f"FLOW:\n"
                        f"{'Sell' if current_side == 'SELL' else 'Buy'}: {_fmt(total_sell if current_side == 'SELL' else total_buy)}"
                        f" | {'Buy' if current_side == 'SELL' else 'Sell'}: {_fmt(total_buy if current_side == 'SELL' else total_sell)}"
                        f" | Net: {_fmts(net_flow)} ({dom_pct:.0f}% {current_side.lower()})\n"
                        f"Candle {current_side.lower()} berturut: {consec}x\n"
                        f"Harga: ${p_last:,.2f} (FLAT {price_change_pct:+.1f}%)\n"
                        f"\n"
                        f"CVD:\n"
                        f"{cvd_block}\n"
                        f"\n"
                        f"OI: {oi_label} ({oi_pct:+.2f}%)"
                        f"{f' — {oi_interp}' if oi_interp else ''}\n"
                        f"\n"
                        f"ACTION:\n"
                        f"{_action_text(grade, current_side, spot_ok)}\n"
                        f"\n"
                        f"DYOR — verifikasi sebelum entry"
                    )

                    logger.info(
                        f"STEALTH_{current_side} {coin} grade={grade} "
                        f"ratio={total_buy/total_sell if current_side == 'BUY' and total_sell > 0 else total_sell/total_buy if total_buy > 0 else 0:.1f}x "
                        f"spot_ok={spot_ok} fut_ok={fut_ok} consec={consec}"
                    )

                    alerts.append({
                        "type": pattern,
                        "coin": coin,
                        "direction": d_label,
                        "grade": grade,
                        "message": msg,
                        "priority": 1 if grade == "A" else 2,
                    })

        if current_side is None and prev_side:
            self._last_stealth_direction.pop(coin, None)
            self._last_stealth_time.pop(coin, None)

        return alerts

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        now = time.time()
        active_cooldowns = {
            k: round(self.COOLDOWN_SECONDS - (now - v))
            for k, v in self._cooldowns.items()
            if now - v < self.COOLDOWN_SECONDS
        }
        recent_flushes = {
            k: round((now - v) / 60, 1)
            for k, v in self._last_flush.items()
            if now - v < 10800
        }
        return {
            "watchlist": sorted(WATCHLIST),
            "active_cooldowns": active_cooldowns,
            "recent_flushes_min_ago": recent_flushes,
        }
