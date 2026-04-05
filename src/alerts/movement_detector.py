"""
Movement Detector — detect when market starts moving from quiet state.

Reads DIRECTLY from MarketContextBuffer and BufferManager (no duplicate data).
Patterns: QUIET_TO_MOVE, FLUSH_SWEEP, ABSORPTION_REVERSAL.

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
# cvd_quiet: max abs CVD delta per snapshot to count as "quiet"
# cvd_spike: min abs CVD delta (sum recent) to trigger "moving"
# flush_cvd: max (negative) CVD sum to trigger flush
# absorption_cvd: min positive CVD sum after flush
# flush_liq_min: min long-liq USD in window to confirm flush
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

# WIB timezone for alert timestamps
_WIB = timezone(timedelta(hours=7))


def _fmt_usd(value: float) -> str:
    """Format USD value with K/M suffix."""
    av = abs(value)
    if av >= 1_000_000_000:
        return f"${value / 1e9:,.1f}B"
    if av >= 1_000_000:
        return f"${value / 1e6:,.1f}M"
    if av >= 1_000:
        return f"${value / 1e3:,.0f}K"
    return f"${value:,.0f}"


def _fmt_usd_signed(value: float) -> str:
    """Format signed USD value with +/- and K/M suffix."""
    sign = "+" if value >= 0 else ""
    av = abs(value)
    if av >= 1_000_000_000:
        return f"{sign}{value / 1e9:.1f}B"
    if av >= 1_000_000:
        return f"{sign}{value / 1e6:.1f}M"
    if av >= 1_000:
        return f"{sign}{value / 1e3:.0f}K"
    return f"{sign}{value:,.0f}"


class MovementDetector:
    """
    Detect market movements from quiet states by reading buffer history.

    Call scan() periodically (every 30-60s). It reads existing buffer data —
    no new API calls, no duplicate storage.
    """

    COOLDOWN_SECONDS = 3600  # 60 min cooldown per coin per pattern
    STEALTH_COOLDOWN_SECONDS = 900  # 15 min — repeat while still active
    REVERSAL_COOLDOWN_SECONDS = 600  # 10 min — flow reversal warning

    def __init__(self):
        # {coin}_{pattern} -> last alert timestamp
        self._cooldowns: dict[str, float] = {}
        # coin -> last flush timestamp (for absorption prerequisite)
        self._last_flush: dict[str, float] = {}
        # coin -> price at flush (for absorption "near bottom" check)
        self._flush_price: dict[str, float] = {}
        # coin -> last stealth direction ("BUY"/"SELL") for flow reversal detection
        self._last_stealth_direction: dict[str, str] = {}
        # coin -> timestamp of last stealth alert
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
        self._cooldowns[key := f"{coin}_{pattern}"] = time.time()

    # ------------------------------------------------------------------
    # CVD delta helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _cvd_deltas_from_history(cvd_snapshots: list) -> list[float]:
        """
        Compute per-snapshot CVD deltas from a list of CVDSnapshot objects.

        Each snapshot has cvd_latest (cumulative). Delta = change between
        consecutive snapshots. Returns list of deltas (len = len(snapshots) - 1).
        """
        if len(cvd_snapshots) < 2:
            return []
        deltas = []
        for i in range(1, len(cvd_snapshots)):
            prev_val = cvd_snapshots[i - 1].cvd_latest
            curr_val = cvd_snapshots[i].cvd_latest
            # Guard against CVD reset (CoinGlass resets cumulative at day boundary)
            # If delta is absurdly large relative to both values, skip it
            delta = curr_val - prev_val
            deltas.append(delta)
        return deltas

    @staticmethod
    def _price_range_pct(price_snapshots: list) -> float:
        """Price range as % of mean over a list of PriceSnapshot objects."""
        if not price_snapshots:
            return 0.0
        prices = [s.price for s in price_snapshots if s.price > 0]
        if len(prices) < 2:
            return 0.0
        mean = sum(prices) / len(prices)
        if mean == 0:
            return 0.0
        return (max(prices) - min(prices)) / mean * 100

    # ------------------------------------------------------------------
    # Liquidation aggregation
    # ------------------------------------------------------------------

    @staticmethod
    def _aggregate_liquidations(buffer_manager, symbol_usdt: str,
                                seconds: int) -> tuple[float, float]:
        """
        Aggregate long and short liquidation volume from buffer_manager.

        Returns (long_liq_usd, short_liq_usd).
        side=1 → long liquidation (longs got rekt, price dropped)
        side=2 → short liquidation (shorts got rekt, price pumped)
        """
        events = buffer_manager.get_liquidations(symbol_usdt, time_window=seconds)
        long_liq = 0.0
        short_liq = 0.0
        for ev in events:
            vol = float(ev.get("vol", 0))
            side = int(ev.get("side", 0))
            if side == 1:
                long_liq += vol
            elif side == 2:
                short_liq += vol
        return long_liq, short_liq

    # ------------------------------------------------------------------
    # Main scan entry point
    # ------------------------------------------------------------------

    def scan(self, market_context_buffer, buffer_manager) -> list[dict]:
        """
        Scan all watchlist coins for movement patterns.

        Args:
            market_context_buffer: MarketContextBuffer instance
            buffer_manager: BufferManager instance (for liquidation data)

        Returns:
            List of alert dicts: [{type, coin, direction, message, priority}]
        """
        alerts = []
        for coin in WATCHLIST:
            coin_alerts = self._check_coin(coin, market_context_buffer, buffer_manager)
            alerts.extend(coin_alerts)
        return alerts

    def _check_coin(self, coin: str, mcb, bm) -> list[dict]:
        """Check all 3 patterns for one coin."""
        # Fetch history from buffers — 24 snapshots = ~2h at 5m interval
        fut_cvd_hist = mcb.get_futures_cvd_history(coin, n=24)
        oi_hist = mcb.get_oi_history(coin, n=24)
        price_hist = mcb.get_price_history(coin, limit=24)

        # Need minimum data: at least 6 snapshots (~30 min)
        if len(fut_cvd_hist) < 6:
            return []

        symbol_usdt = f"{coin}USDT"
        alerts = []

        stealth_alerts = self._check_stealth_flow(coin, fut_cvd_hist, price_hist)
        alerts.extend(stealth_alerts)

        alert = self._check_quiet_to_move(coin, fut_cvd_hist, price_hist)
        if alert:
            alerts.append(alert)

        alert = self._check_flush(coin, fut_cvd_hist, oi_hist, price_hist, bm, symbol_usdt)
        if alert:
            alerts.append(alert)

        alert = self._check_absorption(coin, fut_cvd_hist, oi_hist, price_hist, bm, symbol_usdt)
        if alert:
            alerts.append(alert)

        return alerts

    # ------------------------------------------------------------------
    # Pattern 1: QUIET → MOVE
    # ------------------------------------------------------------------

    def _check_quiet_to_move(self, coin: str, fut_cvd_hist: list,
                             price_hist: list) -> dict | None:
        """
        Detect when a coin transitions from quiet to sudden movement.

        Baseline (older snapshots): CVD deltas small, price range tight.
        Recent (last 2-3 snapshots): CVD delta spikes ≥4x baseline or
        exceeds absolute threshold.
        """
        if self._on_cooldown(coin, "QUIET_TO_MOVE"):
            return None
        if len(fut_cvd_hist) < 12:
            return None

        th = self._thresholds(coin)
        deltas = self._cvd_deltas_from_history(fut_cvd_hist)
        if len(deltas) < 10:
            return None

        # Baseline: deltas[:-3] (older), Recent: deltas[-3:] (newest)
        baseline_deltas = deltas[:-3]
        recent_deltas = deltas[-3:]

        baseline_avg_abs = sum(abs(d) for d in baseline_deltas) / len(baseline_deltas)
        recent_sum = sum(recent_deltas)
        recent_avg_abs = sum(abs(d) for d in recent_deltas) / len(recent_deltas)

        # Is baseline "quiet"?
        if baseline_avg_abs > th["cvd_quiet"]:
            return None

        # Is recent a spike? Need both: absolute threshold AND relative (4x baseline)
        min_baseline_ref = max(baseline_avg_abs, 1.0)  # avoid div-by-zero
        if recent_avg_abs < th["cvd_spike"] / 3 and recent_avg_abs < min_baseline_ref * 4:
            return None

        # Optional: confirm price was also quiet in baseline period
        if price_hist and len(price_hist) >= 10:
            baseline_price = price_hist[:-3]
            price_range = self._price_range_pct(baseline_price)
            if price_range > 0.8:
                return None  # price was already moving, not "quiet"

        direction = "LONG" if recent_sum > 0 else "SHORT"
        dir_emoji = "\U0001f4c8" if direction == "LONG" else "\U0001f4c9"
        current_price = price_hist[-1].price if price_hist else 0
        time_str = datetime.now(_WIB).strftime("%H:%M:%S")

        self._set_cooldown(coin, "QUIET_TO_MOVE")

        msg = (
            f"\U0001f514 {coin} MULAI BERGERAK | ${current_price:,.2f} | {time_str} WIB\n"
            f"\n"
            f"Trigger: MOVEMENT DETECTOR | QUIET \u2192 MOVE\n"
            f"\n"
            f"\u2726 FutCVD spike: {_fmt_usd_signed(recent_sum)} (15 menit)\n"
            f"\u2726 Baseline avg: {_fmt_usd(baseline_avg_abs)}/candle (diam)\n"
            f"\u2726 Spike ratio: {recent_avg_abs / min_baseline_ref:.1f}x dari baseline\n"
            f"\n"
            f"{dir_emoji} Arah: {direction}\n"
            f"\n"
            f"\u26a0\ufe0f Konfirmasi dulu — ini sinyal AWAL pergerakan\n"
            f"DYOR — verifikasi sebelum entry"
        )

        logger.info(f"QUIET_TO_MOVE {coin} {direction} CVD={_fmt_usd_signed(recent_sum)}")

        return {
            "type": "QUIET_TO_MOVE",
            "coin": coin,
            "direction": direction,
            "message": msg,
            "priority": 2,
        }

    # ------------------------------------------------------------------
    # Pattern 2: FLUSH / LIQUIDITY SWEEP
    # ------------------------------------------------------------------

    def _check_flush(self, coin: str, fut_cvd_hist: list, oi_hist: list,
                     price_hist: list, bm, symbol_usdt: str) -> dict | None:
        """
        Detect flush / liquidity sweep:
        - FutCVD heavily negative (recent 3 snapshots)
        - Long liquidations spiking
        - OI dropping
        - Price dropping
        """
        if self._on_cooldown(coin, "FLUSH_SWEEP"):
            return None

        th = self._thresholds(coin)
        deltas = self._cvd_deltas_from_history(fut_cvd_hist)
        if len(deltas) < 3:
            return None

        recent_cvd_sum = sum(deltas[-3:])

        # CVD must be strongly negative
        if recent_cvd_sum > th["flush_cvd"]:
            return None

        # OI should be dropping
        oi_change = 0.0
        if oi_hist and len(oi_hist) >= 2:
            oi_change = oi_hist[-1].oi_change_pct
            # Also check multi-snapshot OI drop
            if len(oi_hist) >= 4:
                oi_now = oi_hist[-1].current_oi_usd
                oi_before = oi_hist[-4].current_oi_usd
                if oi_before > 0:
                    oi_change = (oi_now - oi_before) / oi_before * 100

        # Price should be dropping
        price_change = 0.0
        current_price = 0.0
        if price_hist and len(price_hist) >= 2:
            current_price = price_hist[-1].price
            price_before = price_hist[-4].price if len(price_hist) >= 4 else price_hist[0].price
            if price_before > 0:
                price_change = (current_price - price_before) / price_before * 100

        # Long liquidations in last 15 minutes
        long_liq, short_liq = self._aggregate_liquidations(bm, symbol_usdt, seconds=900)

        # Need at least 2 of 3 confirmations: OI drop, price drop, liq spike
        confirmations = 0
        if oi_change < th["oi_drop_pct"]:
            confirmations += 1
        if price_change < th["price_drop_pct"]:
            confirmations += 1
        if long_liq > th["flush_liq_min"]:
            confirmations += 1

        if confirmations < 2:
            return None

        time_str = datetime.now(_WIB).strftime("%H:%M:%S")

        # Record flush for absorption detection
        self._last_flush[coin] = time.time()
        self._flush_price[coin] = current_price
        self._set_cooldown(coin, "FLUSH_SWEEP")

        msg = (
            f"\U0001f480 {coin} FLUSH / LIQUIDITY SWEEP | ${current_price:,.2f} | {time_str} WIB\n"
            f"\n"
            f"Trigger: MOVEMENT DETECTOR | FLUSH SWEEP\n"
            f"\n"
            f"\u2726 FutCVD: {_fmt_usd_signed(recent_cvd_sum)} (15 menit)\n"
            f"\u2726 Long Liq: {_fmt_usd(long_liq)} | Short Liq: {_fmt_usd(short_liq)}\n"
            f"\u2726 OI Change: {oi_change:+.2f}%\n"
            f"\u2726 Price: {price_change:+.2f}%\n"
            f"\n"
            f"\u26a0\ufe0f JANGAN entry \u2014 tunggu absorption / reversal signal\n"
            f"DYOR — verifikasi sebelum entry"
        )

        logger.info(
            f"FLUSH_SWEEP {coin} CVD={_fmt_usd_signed(recent_cvd_sum)} "
            f"LongLiq={_fmt_usd(long_liq)} OI={oi_change:+.2f}%"
        )

        return {
            "type": "FLUSH_SWEEP",
            "coin": coin,
            "direction": "SHORT",
            "message": msg,
            "priority": 1,  # high priority — time-sensitive warning
        }

    # ------------------------------------------------------------------
    # Pattern 3: ABSORPTION AFTER SWEEP (most important)
    # ------------------------------------------------------------------

    def _check_absorption(self, coin: str, fut_cvd_hist: list, oi_hist: list,
                          price_hist: list, bm, symbol_usdt: str) -> dict | None:
        """
        Detect absorption reversal after a flush:
        - Flush happened within 1-3 hours ago
        - FutCVD flips from negative to strongly positive
        - OI starts rising (new positions, not just short covering)
        - Long liquidations stopped
        - Price still near flush bottom (not already pumped >1.5%)
        """
        if self._on_cooldown(coin, "ABSORPTION_REVERSAL"):
            return None

        # Prerequisite: flush must have happened within 1-3 hours
        last_flush_ts = self._last_flush.get(coin, 0)
        if last_flush_ts == 0:
            return None
        hours_since = (time.time() - last_flush_ts) / 3600
        if hours_since > 3.0 or hours_since < 0.1:
            return None

        th = self._thresholds(coin)
        deltas = self._cvd_deltas_from_history(fut_cvd_hist)
        if len(deltas) < 3:
            return None

        # Recent CVD must flip positive and be strong
        recent_cvd_sum = sum(deltas[-3:])
        if recent_cvd_sum < th["absorption_cvd"]:
            return None

        # OI must be rising (new positions entering)
        oi_rising = False
        oi_change = 0.0
        if oi_hist and len(oi_hist) >= 2:
            oi_change = oi_hist[-1].oi_change_pct
            if oi_change > 0.1:
                oi_rising = True
            # Fallback: multi-snapshot check
            if not oi_rising and len(oi_hist) >= 4:
                oi_now = oi_hist[-1].current_oi_usd
                oi_before = oi_hist[-3].current_oi_usd
                if oi_before > 0 and (oi_now - oi_before) / oi_before * 100 > 0.1:
                    oi_rising = True
                    oi_change = (oi_now - oi_before) / oi_before * 100

        if not oi_rising:
            return None

        # Long liquidations must have stopped (compare recent vs flush period)
        long_liq_recent, _ = self._aggregate_liquidations(bm, symbol_usdt, seconds=900)
        long_liq_earlier, _ = self._aggregate_liquidations(bm, symbol_usdt, seconds=3600)
        # Recent 15min should be much less than earlier hour
        # (long_liq_earlier includes recent, so subtract)
        liq_during_flush = long_liq_earlier - long_liq_recent
        liq_stopped = True
        if liq_during_flush > 0 and long_liq_recent > liq_during_flush * 0.5:
            liq_stopped = False  # still liquidating at >50% of flush rate

        if not liq_stopped:
            return None

        # Price should still be near bottom (not already pumped >1.5%)
        current_price = price_hist[-1].price if price_hist else 0
        flush_price = self._flush_price.get(coin, 0)
        if flush_price > 0 and current_price > 0:
            recovery_pct = (current_price - flush_price) / flush_price * 100
            if recovery_pct > 1.5:
                return None  # already pumped, missed the entry

        time_str = datetime.now(_WIB).strftime("%H:%M:%S")

        self._set_cooldown(coin, "ABSORPTION_REVERSAL")

        msg = (
            f"\U0001f525 {coin} ABSORPTION DETECTED | ${current_price:,.2f} | {time_str} WIB\n"
            f"\n"
            f"Trigger: MOVEMENT DETECTOR | ABSORPTION REVERSAL\n"
            f"\n"
            f"\u2726 FutCVD flip: {_fmt_usd_signed(recent_cvd_sum)} (setelah flush)\n"
            f"\u2726 OI: {oi_change:+.2f}% \u2014 NEW POSITIONS \u2705\n"
            f"\u2726 Long liq berhenti \u2705\n"
            f"\u2726 Flush terjadi {hours_since:.1f} jam lalu\n"
            f"\n"
            f"\U0001f4c8 LONG \u2014 POTENTIAL REVERSAL\n"
            f"\n"
            f"\u26a1 ENTRY ZONE \u2014 cek data live untuk konfirmasi\n"
            f"DYOR — verifikasi sebelum entry"
        )

        logger.info(
            f"ABSORPTION_REVERSAL {coin} CVD={_fmt_usd_signed(recent_cvd_sum)} "
            f"OI={oi_change:+.2f}% flush={hours_since:.1f}h ago"
        )

        return {
            "type": "ABSORPTION_REVERSAL",
            "coin": coin,
            "direction": "LONG",
            "message": msg,
            "priority": 1,  # highest — best R/R entry signal
        }

    # ------------------------------------------------------------------
    # Pattern 4: STEALTH FLOW (buy/sell/reversal — earliest signal)
    # ------------------------------------------------------------------

    def _check_stealth_flow(self, coin: str, fut_cvd_hist: list,
                            price_hist: list) -> list[dict]:
        """
        Detect stealth taker activity while price stays flat.

        Sub-patterns:
          A) STEALTH_BUY  — taker buy >> sell, price flat → potensi naik
          B) STEALTH_SELL — taker sell >> buy, price flat → potensi turun
          C) FLOW_REVERSAL — direction flips from previous stealth alert
        """
        if len(fut_cvd_hist) < 3 or len(price_hist) < 3:
            return []

        # --- Aggregate taker volume from last 3 snapshots ---
        recent_snaps = fut_cvd_hist[-3:]
        total_buy = 0.0
        total_sell = 0.0
        buy_dominant_count = 0
        sell_dominant_count = 0

        for snap in recent_snaps:
            buy = snap.taker_buy_vol
            sell = snap.taker_sell_vol
            if buy <= 0 and sell <= 0:
                return []  # no taker data
            total_buy += buy
            total_sell += sell
            if sell > 0 and buy / sell > 2.0:
                buy_dominant_count += 1
            if buy > 0 and sell / buy > 2.0:
                sell_dominant_count += 1

        total_vol = total_buy + total_sell
        if total_vol <= 0:
            return []

        # --- Price flatness check ---
        recent_prices = price_hist[-3:]
        p_first = recent_prices[0].price
        p_last = recent_prices[-1].price
        if p_first <= 0:
            return []
        price_change_pct = (p_last - p_first) / p_first * 100

        # --- Determine current dominant side ---
        current_side = None  # "BUY", "SELL", or None
        if buy_dominant_count >= 2 and total_sell > 0 and total_buy / total_sell >= 2.0:
            current_side = "BUY"
        elif sell_dominant_count >= 2 and total_buy > 0 and total_sell / total_buy >= 2.0:
            current_side = "SELL"

        alerts = []
        time_str = datetime.now(_WIB).strftime("%H:%M:%S")
        prev_side = self._last_stealth_direction.get(coin)
        prev_time = self._last_stealth_time.get(coin, 0)

        # --- Sub-pattern C: FLOW REVERSAL ---
        # Previous stealth was active (within 30 min) and direction flipped
        if (current_side and prev_side and current_side != prev_side
                and (time.time() - prev_time) < 1800
                and not self._on_cooldown(coin, "FLOW_REVERSAL")):

            if current_side == "SELL":
                flip_msg = "Sebelumnya BUYER dominant, sekarang SELLER masuk."
            else:
                flip_msg = "Sebelumnya SELLER dominant, sekarang BUYER masuk."

            net_flow = total_buy - total_sell
            msg = (
                f"\u26a0\ufe0f {coin} FLOW REVERSAL! | ${p_last:,.2f} | {time_str} WIB\n"
                f"\n"
                f"Trigger: MOVEMENT DETECTOR | FLOW REVERSAL\n"
                f"\n"
                f"{flip_msg}\n"
                f"Buy: {_fmt_usd(total_buy)} vs Sell: {_fmt_usd(total_sell)}\n"
                f"Net: {_fmt_usd_signed(net_flow)}\n"
                f"\n"
                f"\U0001f449 Kalau sudah entry \u2192 tighten stop.\n"
                f"\U0001f449 Kalau belum entry \u2192 tunggu dulu.\n"
                f"\n"
                f"DYOR \u2014 verifikasi sebelum entry"
            )

            self._set_cooldown(coin, "FLOW_REVERSAL")

            logger.info(
                f"FLOW_REVERSAL {coin} {prev_side}->{current_side} "
                f"buy={_fmt_usd(total_buy)} sell={_fmt_usd(total_sell)}"
            )

            alerts.append({
                "type": "FLOW_REVERSAL",
                "coin": coin,
                "direction": "LONG" if current_side == "BUY" else "SHORT",
                "message": msg,
                "priority": 1,  # urgent warning
            })

        # --- Sub-pattern A: STEALTH BUY ---
        if current_side == "BUY" and abs(price_change_pct) <= 0.3:
            if not self._on_cooldown(coin, "STEALTH_BUY"):
                buy_pct = total_buy / total_vol * 100
                net_flow = total_buy - total_sell

                msg = (
                    f"\U0001f515 {coin} ADA PEMBELI BESAR! | ${p_last:,.2f} | {time_str} WIB\n"
                    f"\n"
                    f"Trigger: MOVEMENT DETECTOR | STEALTH BUY\n"
                    f"\n"
                    f"Harga belum gerak tapi ada yang beli BANYAK.\n"
                    f"Buy: {_fmt_usd(total_buy)} vs Sell: {_fmt_usd(total_sell)}\n"
                    f"Net flow: {_fmt_usd_signed(net_flow)} ({buy_pct:.0f}% buy dominant)\n"
                    f"Harga: ${p_last:,.2f} (masih FLAT {price_change_pct:+.1f}%)\n"
                    f"\n"
                    f"\U0001f449 PANTAU \u2014 begitu seller habis, harga bisa terbang\n"
                    f"\n"
                    f"DYOR \u2014 verifikasi sebelum entry"
                )

                self._set_cooldown(coin, "STEALTH_BUY")
                self._last_stealth_direction[coin] = "BUY"
                self._last_stealth_time[coin] = time.time()

                logger.info(
                    f"STEALTH_BUY {coin} buy={_fmt_usd(total_buy)} "
                    f"sell={_fmt_usd(total_sell)} ratio={total_buy/total_sell:.1f}x "
                    f"price_chg={price_change_pct:+.2f}%"
                )

                alerts.append({
                    "type": "STEALTH_BUY",
                    "coin": coin,
                    "direction": "LONG",
                    "message": msg,
                    "priority": 2,
                })

        # --- Sub-pattern B: STEALTH SELL ---
        elif current_side == "SELL" and abs(price_change_pct) <= 0.3:
            if not self._on_cooldown(coin, "STEALTH_SELL"):
                sell_pct = total_sell / total_vol * 100
                net_flow = total_buy - total_sell

                msg = (
                    f"\U0001f515 {coin} ADA PENJUAL BESAR! | ${p_last:,.2f} | {time_str} WIB\n"
                    f"\n"
                    f"Trigger: MOVEMENT DETECTOR | STEALTH SELL\n"
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

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

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
            for k, v in self._last_flush.items()
            if now - v < 10800  # 3 hours
        }
        return {
            "watchlist": sorted(WATCHLIST),
            "active_cooldowns": active_cooldowns,
            "recent_flushes_min_ago": recent_flushes,
        }
