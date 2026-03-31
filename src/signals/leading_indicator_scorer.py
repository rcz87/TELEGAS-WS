# Leading Indicator Scorer — CVD + OI + Orderbook Primary Scoring
# Replaces lagging-only confidence with leading indicator weights

"""
Leading Indicator Scorer Module

Scoring hierarchy:
  LEADING (primary): SpotCVD, FutCVD, OI — these PREDICT price moves
  LAGGING (support): Funding, Taker, Momentum, Liquidations — these CONFIRM

Label thresholds:
  >= 80  → 🎯 EXECUTION READY
  60-79  → ⚡ HIGH CONFIDENCE
  40-59  → 💡 WATCH
  < 40   → 📊 MONITOR

Integration point in pipeline:
  analyzers → signal_generator → confidence_scorer → signal_validator
  → market_context_filter → [THIS SCORER] → message_formatter → telegram
"""

from dataclasses import dataclass, field
from typing import List, Optional

from ..utils.logger import setup_logger


# --- Scoring weights ---
WEIGHTS = {
    # LEADING — Primary
    "spot_cvd_flip":        35,
    "spot_cvd_sustained":   20,
    "fut_cvd_flip":         25,
    "fut_cvd_sustained":    15,
    "oi_spike":             20,
    "oi_sustained":         10,
    "ob_bids_dominant":     10,
    # LAGGING — Support
    "fr_favorable":          8,
    "taker_dominant":        8,
    "momentum":              5,
    "liquidation_trigger":   7,
}

LABEL_THRESHOLDS = [
    (80, "\U0001f3af", "EXECUTION READY"),
    (60, "\u26a1",     "HIGH CONFIDENCE"),
    (40, "\U0001f4a1", "WATCH"),
    (0,  "\U0001f4ca", "MONITOR"),
]


@dataclass
class ScoredIndicator:
    """Single indicator evaluation result"""
    name: str
    points: int
    max_points: int
    detail: str  # Human-readable detail for Telegram


@dataclass
class LeadingScore:
    """Complete scoring result"""
    total: float              # 0-99
    label_emoji: str          # 🎯/⚡/💡/📊
    label_text: str           # EXECUTION READY etc
    indicators: List[ScoredIndicator] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)  # COILING, CVD RECOVERY, etc.
    leading_subtotal: int = 0
    lagging_subtotal: int = 0


class LeadingIndicatorScorer:
    """
    Scores signals using leading indicators from CoinGlass REST data.

    Uses market_context_buffer historical snapshots for:
    - CVD flip detection (negative → positive for LONG)
    - CVD sustained trend (3+ candles in direction)
    - CVD recovery detection (improving while still negative)
    - OI spike (>3% in 15 min)
    - OI sustained rise
    - Orderbook imbalance
    - COILING pattern (low vol + CVD+ + OI rising)

    Tier-aware:
    - CVD flip minimum value per tier (Tier 1: $1M, Tier 4: $10K)
    - OI minimum for signal validity
    - FR extreme auto-skip for Tier 3/4
    """

    def __init__(self, monitoring_config: dict = None):
        self.logger = setup_logger("LeadingScorer", "INFO")
        self._scores_calculated = 0

        # Tier config
        m = monitoring_config or {}
        self._tier1_symbols = set(m.get('tier1_symbols', ['BTCUSDT', 'ETHUSDT']))
        self._tier2_symbols = set(m.get('tier2_symbols', []))
        self._tier3_symbols = set(m.get('tier3_symbols', []))

        self._cvd_flip_min = {
            1: m.get('tier1_cvd_flip_min', 1_000_000),
            2: m.get('tier2_cvd_flip_min', 200_000),
            3: m.get('tier3_cvd_flip_min', 50_000),
            4: m.get('tier4_cvd_flip_min', 10_000),
        }
        self._oi_min = {
            1: m.get('tier1_oi_min', 500_000_000),
            2: m.get('tier2_oi_min', 50_000_000),
            3: m.get('tier3_oi_min', 10_000_000),
            4: m.get('tier4_oi_min', 1_000_000),
        }
        # FR extreme threshold normalized to per-hour (0.001 = 0.1%/hour)
        self._fr_extreme_per_hour = m.get('fr_extreme_threshold_per_hour', 0.001)

    def _get_tier(self, symbol_usdt: str) -> int:
        """Get tier number for a symbol (1-4)."""
        if symbol_usdt in self._tier1_symbols:
            return 1
        if symbol_usdt in self._tier2_symbols:
            return 2
        if symbol_usdt in self._tier3_symbols:
            return 3
        return 4

    def score(
        self,
        direction: str,
        market_context_buffer,
        base_symbol: str,
        signal_metadata: dict,
    ) -> LeadingScore:
        """
        Calculate leading indicator score for a signal.

        Args:
            direction: "LONG" or "SHORT"
            market_context_buffer: MarketContextBuffer instance
            base_symbol: CoinGlass base symbol (e.g. "BTC")
            signal_metadata: Signal metadata dict (for lagging indicators)

        Returns:
            LeadingScore with total, label, and indicator details
        """
        indicators = []
        notes = []
        is_long = direction == "LONG"

        # Determine tier for this symbol
        symbol_usdt = base_symbol + "USDT" if not base_symbol.endswith("USDT") else base_symbol
        tier = self._get_tier(symbol_usdt)
        cvd_min = self._cvd_flip_min.get(tier, 10_000)
        oi_min = self._oi_min.get(tier, 1_000_000)

        # FR extreme auto-skip for tier 3/4
        # Normalize FR to per-hour: most exchanges use 8h interval
        # CoinGlass returns raw interval rate, so divide by 8 for hourly
        mc = signal_metadata.get('market_context', {})
        fr_raw = abs(mc.get('funding_rate', 0))
        fr_per_hour = fr_raw / 8.0  # Assume 8h default interval
        if tier >= 3 and fr_per_hour >= self._fr_extreme_per_hour:
            self.logger.debug(
                f"{base_symbol} tier{tier}: FR {fr_raw*100:.4f}%/8h "
                f"= {fr_per_hour*100:.4f}%/h — extreme, skipped"
            )
            return LeadingScore(
                total=0, label_emoji="\U0001f4ca", label_text="MONITOR",
                notes=[f"\u26a0\ufe0f FR extreme ({fr_raw*100:.3f}%/8h) — auto skip"],
            )

        # OI minimum check
        oi_snap = market_context_buffer.get_latest_oi(base_symbol)
        if oi_snap and oi_snap.current_oi_usd < oi_min:
            self.logger.debug(
                f"{base_symbol} tier{tier}: OI ${oi_snap.current_oi_usd:,.0f} "
                f"below min ${oi_min:,.0f}"
            )
            # Don't skip entirely, but note it
            notes.append(f"\u26a0\ufe0f OI rendah (${oi_snap.current_oi_usd/1_000_000:.0f}M)")

        # --- LEADING INDICATORS ---

        # 1. SpotCVD flip / sustained
        spot_snap = market_context_buffer.get_latest_spot_cvd(base_symbol)
        if spot_snap and spot_snap.cvd_values:
            pts, detail = self._score_cvd(
                spot_snap.cvd_values, is_long, "SpotCVD",
                WEIGHTS["spot_cvd_flip"], WEIGHTS["spot_cvd_sustained"],
                cvd_min=cvd_min,
            )
            if pts > 0:
                max_pts = max(WEIGHTS["spot_cvd_flip"], WEIGHTS["spot_cvd_sustained"])
                indicators.append(ScoredIndicator("spot_cvd", pts, max_pts, detail))

            # CVD recovery detection
            recovery = self._detect_cvd_recovery(spot_snap.cvd_values, is_long)
            if recovery:
                notes.append(recovery)

        # 2. FuturesCVD flip / sustained
        fut_snap = market_context_buffer.get_latest_futures_cvd(base_symbol)
        if fut_snap and fut_snap.cvd_values:
            pts, detail = self._score_cvd(
                fut_snap.cvd_values, is_long, "FutCVD",
                WEIGHTS["fut_cvd_flip"], WEIGHTS["fut_cvd_sustained"],
                cvd_min=cvd_min,
            )
            if pts > 0:
                max_pts = max(WEIGHTS["fut_cvd_flip"], WEIGHTS["fut_cvd_sustained"])
                indicators.append(ScoredIndicator("fut_cvd", pts, max_pts, detail))

        # 3. OI spike / sustained
        oi_history = market_context_buffer.get_oi_history(base_symbol, n=6)
        if oi_history:
            pts, detail = self._score_oi(oi_history)
            if pts > 0:
                max_pts = max(WEIGHTS["oi_spike"], WEIGHTS["oi_sustained"])
                indicators.append(ScoredIndicator("oi", pts, max_pts, detail))

        # 4. Orderbook imbalance
        ob = market_context_buffer.get_latest_orderbook(base_symbol)
        if ob:
            pts, detail = self._score_orderbook(ob, is_long)
            if pts > 0:
                indicators.append(ScoredIndicator(
                    "orderbook", pts, WEIGHTS["ob_bids_dominant"], detail
                ))

        # COILING pattern detection
        coiling = self._detect_coiling(
            spot_snap, fut_snap, oi_history, signal_metadata, is_long
        )
        if coiling:
            notes.append(coiling)

        leading_subtotal = sum(i.points for i in indicators)

        # --- LAGGING INDICATORS ---

        # 5. Funding rate
        mc = signal_metadata.get('market_context', {})
        fr = mc.get('funding_rate', 0)
        if fr != 0:
            pts, detail = self._score_funding(fr, is_long)
            if pts > 0:
                indicators.append(ScoredIndicator(
                    "funding", pts, WEIGHTS["fr_favorable"], detail
                ))

        # 6. Taker buy/sell ratio
        of = signal_metadata.get('order_flow', {})
        buy_ratio = of.get('buy_ratio', 0.5)
        pts, detail = self._score_taker(buy_ratio, is_long)
        if pts > 0:
            indicators.append(ScoredIndicator(
                "taker", pts, WEIGHTS["taker_dominant"], detail
            ))

        # 7. Momentum (price direction)
        price_change = mc.get('price_change_24h_pct', 0)
        pts, detail = self._score_momentum(price_change, is_long)
        if pts > 0:
            indicators.append(ScoredIndicator(
                "momentum", pts, WEIGHTS["momentum"], detail
            ))

        # 8. Liquidation trigger (stop hunt)
        sh = signal_metadata.get('stop_hunt', {})
        pts, detail = self._score_liquidation(sh, is_long)
        if pts > 0:
            indicators.append(ScoredIndicator(
                "liquidation", pts, WEIGHTS["liquidation_trigger"], detail
            ))

        lagging_subtotal = sum(i.points for i in indicators) - leading_subtotal

        # --- TOTAL ---
        raw_total = sum(i.points for i in indicators)

        # COILING bonus: +10 points
        if any("COILING" in n for n in notes):
            raw_total += 10

        total = min(raw_total, 99.0)

        # Determine label
        label_emoji, label_text = "\U0001f4ca", "MONITOR"
        for threshold, emoji, text in LABEL_THRESHOLDS:
            if total >= threshold:
                label_emoji, label_text = emoji, text
                break

        self._scores_calculated += 1

        result = LeadingScore(
            total=total,
            label_emoji=label_emoji,
            label_text=label_text,
            indicators=indicators,
            notes=notes,
            leading_subtotal=leading_subtotal,
            lagging_subtotal=lagging_subtotal,
        )

        self.logger.info(
            f"{base_symbol} {direction}: {total:.0f}% {label_text} "
            f"(leading={leading_subtotal}, lagging={lagging_subtotal}, "
            f"indicators={len(indicators)})"
        )

        return result

    # --- CVD scoring ---

    def _score_cvd(
        self, cvd_values: list, is_long: bool, label: str,
        flip_weight: int, sustained_weight: int,
        cvd_min: float = 0,
    ) -> tuple:
        """Score CVD: flip detection takes priority over sustained.

        BUG FIX: flip requires 2+ candle confirmation, sustained requires
        3+ candle delta in same direction AND cumulative alignment.
        """
        if len(cvd_values) < 3:
            return 0, ""

        prev2 = cvd_values[-3]
        prev = cvd_values[-2]
        curr = cvd_values[-1]

        # Flip detection — requires 2-candle confirmation
        # Both prev→curr AND prev2→prev must show the new direction
        if is_long and prev < 0 and curr > 0 and abs(curr) >= cvd_min:
            # Confirmed if prev is already recovering (prev > prev2)
            flip_confirmed = prev > prev2
            if flip_confirmed:
                return flip_weight, f"{label} FLIP POSITIF confirmed {self._fmt(curr)}"
            else:
                # Single candle flip — reduced weight, label as "recovering"
                return flip_weight // 3, f"{label} RECOVERING (single candle) {self._fmt(curr)}"
        if not is_long and prev > 0 and curr < 0 and abs(curr) >= cvd_min:
            flip_confirmed = prev < prev2
            if flip_confirmed:
                return flip_weight, f"{label} FLIP NEGATIF confirmed {self._fmt(curr)}"
            else:
                return flip_weight // 3, f"{label} WEAKENING (single candle) {self._fmt(curr)}"

        # Sustained: 3+ candle deltas in same direction AND cumulative alignment
        recent = cvd_values[-3:]
        deltas = [recent[i] - recent[i - 1] for i in range(1, len(recent))]

        if is_long:
            # All 3 candles positive AND all deltas positive (actually rising)
            all_positive = all(v > 0 for v in recent)
            all_deltas_positive = all(d > 0 for d in deltas)

            if all_positive and all_deltas_positive:
                return sustained_weight, f"{label} RISING sustained {self._fmt(curr)}"
            elif all_positive:
                return sustained_weight // 2, f"{label} POSITIF steady {self._fmt(curr)}"
            elif curr > 0 and all_deltas_positive:
                # Cumulative positive + deltas rising = valid but weaker
                return sustained_weight // 3, f"{label} RECOVERING trend {self._fmt(curr)}"
        else:
            all_negative = all(v < 0 for v in recent)
            all_deltas_negative = all(d < 0 for d in deltas)

            if all_negative and all_deltas_negative:
                return sustained_weight, f"{label} FALLING sustained {self._fmt(curr)}"
            elif all_negative:
                return sustained_weight // 2, f"{label} NEGATIF steady {self._fmt(curr)}"
            elif curr < 0 and all_deltas_negative:
                return sustained_weight // 3, f"{label} WEAKENING trend {self._fmt(curr)}"

        return 0, ""

    def _detect_cvd_recovery(self, cvd_values: list, is_long: bool) -> Optional[str]:
        """Detect CVD recovery before flip (Upgrade 2)."""
        if len(cvd_values) < 6:
            return None

        recent = cvd_values[-6:]

        if is_long:
            all_negative = all(v < 0 for v in recent)
            improving = all(recent[i] > recent[i - 1] for i in range(1, len(recent)))
            if all_negative and improving and recent[0] != 0:
                pct = ((recent[-1] - recent[0]) / abs(recent[0])) * 100
                if pct > 30:
                    return f"\U0001f525 CVD RECOVERY +{pct:.0f}% \u2014 Potensi flip!"

            # Acceleration: already positive and accelerating
            if all(v > 0 for v in recent[-3:]):
                if recent[-1] > recent[-2] > recent[-3]:
                    return f"\U0001f680 CVD ACCELERATION \u2014 Entry zone!"
        else:
            all_positive = all(v > 0 for v in recent)
            declining = all(recent[i] < recent[i - 1] for i in range(1, len(recent)))
            if all_positive and declining and recent[0] != 0:
                pct = ((recent[0] - recent[-1]) / abs(recent[0])) * 100
                if pct > 30:
                    return f"\U0001f525 CVD RECOVERY (SHORT) -{pct:.0f}%"

            if all(v < 0 for v in recent[-3:]):
                if recent[-1] < recent[-2] < recent[-3]:
                    return f"\U0001f680 CVD ACCELERATION (SHORT)"

        return None

    # --- OI scoring ---

    def _score_oi(self, oi_history: list) -> tuple:
        """Score OI: spike (>3% in ~15min) or sustained rise."""
        if len(oi_history) < 2:
            return 0, ""

        curr_oi = oi_history[-1].current_oi_usd
        if curr_oi <= 0:
            return 0, ""

        # OI spike: compare current vs 3 snapshots back (~15 min at 5-min interval)
        lookback_idx = max(0, len(oi_history) - 4)
        prev_oi = oi_history[lookback_idx].current_oi_usd
        if prev_oi > 0:
            pct_change = (curr_oi - prev_oi) / prev_oi * 100
            if pct_change >= 3.0:
                return WEIGHTS["oi_spike"], f"OI spike {pct_change:+.1f}% dalam 15min"
            if pct_change >= 1.5:
                return WEIGHTS["oi_spike"] // 2, f"OI naik {pct_change:+.1f}% 15min"

        # OI sustained: 3+ snapshots all rising
        if len(oi_history) >= 3:
            recent = oi_history[-3:]
            rising = all(
                recent[i].current_oi_usd > recent[i - 1].current_oi_usd
                for i in range(1, len(recent))
            )
            if rising:
                oi_pct = oi_history[-1].oi_change_pct
                return WEIGHTS["oi_sustained"], f"OI rising sustained ({oi_pct:+.1f}%)"

        return 0, ""

    # --- Orderbook scoring ---

    def _score_orderbook(self, ob, is_long: bool) -> tuple:
        """Score orderbook imbalance."""
        if is_long and ob.dominant_side == "BIDS":
            return WEIGHTS["ob_bids_dominant"], "Bids > Asks (buyers dominan)"
        if not is_long and ob.dominant_side == "ASKS":
            return WEIGHTS["ob_bids_dominant"], "Asks > Bids (sellers dominan)"
        return 0, ""

    # --- COILING pattern detection (Upgrade 3) ---

    def _detect_coiling(
        self, spot_snap, fut_snap, oi_history, metadata, is_long
    ) -> Optional[str]:
        """
        Detect COILING: Low volume + CVD positive + OI rising = breakout imminent.
        """
        # Check CVD positive
        spot_ok = False
        if spot_snap:
            if is_long and spot_snap.cvd_latest > 0:
                spot_ok = True
            elif not is_long and spot_snap.cvd_latest < 0:
                spot_ok = True

        fut_ok = False
        if fut_snap:
            if is_long and fut_snap.cvd_latest > 0:
                fut_ok = True
            elif not is_long and fut_snap.cvd_latest < 0:
                fut_ok = True

        # Check OI rising
        oi_rising = False
        if oi_history and len(oi_history) >= 2:
            oi_rising = oi_history[-1].current_oi_usd > oi_history[-2].current_oi_usd

        # Check low volume from order_flow metadata
        of = metadata.get('order_flow', {})
        total_trades = of.get('total_trades', 0)
        # Low trade count = potential coiling (threshold varies, use <20 as heuristic)
        low_volume = 0 < total_trades < 20

        if spot_ok and fut_ok and oi_rising and low_volume:
            return (
                "\U0001f525 COILING \u2014 Low vol + CVD"
                + ("+" if is_long else "-")
                + " + OI rising = Breakout imminent"
            )

        return None

    # --- Lagging indicator scoring ---

    def _score_funding(self, fr: float, is_long: bool) -> tuple:
        """Score funding rate alignment."""
        if is_long and fr < -0.0001:
            return WEIGHTS["fr_favorable"], "Short bayar mahal"
        if not is_long and fr > 0.0001:
            return WEIGHTS["fr_favorable"], "Long bayar mahal"
        return 0, ""

    def _score_taker(self, buy_ratio: float, is_long: bool) -> tuple:
        """Score taker buy/sell dominance."""
        if is_long and buy_ratio > 0.60:
            return WEIGHTS["taker_dominant"], "Tekanan beli kuat"
        if not is_long and buy_ratio < 0.40:
            return WEIGHTS["taker_dominant"], "Tekanan jual kuat"
        # Partial credit
        if is_long and buy_ratio > 0.55:
            return WEIGHTS["taker_dominant"] // 2, "Tekanan beli moderat"
        if not is_long and buy_ratio < 0.45:
            return WEIGHTS["taker_dominant"] // 2, "Tekanan jual moderat"
        return 0, ""

    def _score_momentum(self, price_change_24h: float, is_long: bool) -> tuple:
        """Score price momentum."""
        if is_long and price_change_24h > 1.0:
            return WEIGHTS["momentum"], f"Momentum positif ({price_change_24h:+.1f}%)"
        if not is_long and price_change_24h < -1.0:
            return WEIGHTS["momentum"], f"Momentum negatif ({price_change_24h:+.1f}%)"
        return 0, ""

    def _score_liquidation(self, stop_hunt: dict, is_long: bool) -> tuple:
        """Score liquidation trigger."""
        if not stop_hunt:
            return 0, ""
        direction = stop_hunt.get('direction', '')
        vol = stop_hunt.get('total_volume', 0)
        if is_long and direction == "SHORT_HUNT" and vol > 0:
            return WEIGHTS["liquidation_trigger"], f"Short liq ${vol / 1000:.0f}K"
        if not is_long and direction == "LONG_HUNT" and vol > 0:
            return WEIGHTS["liquidation_trigger"], f"Long liq ${vol / 1000:.0f}K"
        return 0, ""

    # --- Bias override (Upgrade 4) ---

    @staticmethod
    def should_override_bias(
        leading_subtotal: int,
        filter_assessment: str,
        signal_direction: str,
    ) -> tuple:
        """
        Check if leading indicators should override unfavorable market bias.

        Returns:
            (should_override: bool, note: str)
        """
        if filter_assessment != "UNFAVORABLE":
            return False, ""

        if leading_subtotal >= 60:
            return True, (
                f"\u26a1 Counter-trend {signal_direction} \u2014 "
                f"Leading indicators override bearish bias"
            )
        return False, ""

    # --- Helpers ---

    @staticmethod
    def _fmt(v: float) -> str:
        if abs(v) >= 1_000_000:
            return f"{v / 1_000_000:+.1f}M"
        elif abs(v) >= 1_000:
            return f"{v / 1_000:+.0f}K"
        return f"{v:+.0f}"

    def get_stats(self) -> dict:
        return {"scores_calculated": self._scores_calculated}
