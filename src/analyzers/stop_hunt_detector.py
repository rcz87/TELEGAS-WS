# Stop Hunt Detector - Pre-Hunt Liquidity Scanner
# Detects OI spike + crowding + CVD alignment BEFORE the sweep happens

"""
Stop Hunt Detector Module — v2 (Pre-Hunt Scanner)

Replaces the old post-cascade detector with a predictive scanner that alerts
BEFORE the stop hunt happens, giving time to prepare.

Conditions (2 of 3 mandatory for alert):
1. OI SPIKE — OI increased ≥1% in last 30 minutes (new money entering)
2. CROWDING — L/S ratio >60% one side OR funding rate extreme (>0.03%)
3. CVD ALIGNED — SpotCVD and FuturesCVD not conflicting

Direction logic:
- Crowded LONG + OI spike → expect SHORT sweep (hunt longs) → prepare LONG after sweep
- Crowded SHORT + OI spike → expect LONG squeeze (hunt shorts) → prepare SHORT after sweep
- No clear crowding → use CVD direction as bias
"""

from typing import Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timezone

from ..utils.logger import setup_logger


@dataclass
class StopHuntSignal:
    """Pre-hunt setup signal data structure.

    Kept as StopHuntSignal for backward compatibility with signal_generator
    and message_formatter.
    """
    symbol: str
    total_volume: float          # OI USD value (for context)
    direction: str               # SHORT_HUNT or LONG_HUNT (who will get hunted)
    price_zone: Tuple[float, float]  # (current_price, 0) — no zone pre-hunt
    absorption_detected: bool    # True if CVD confirms (aligned)
    absorption_volume: float     # OI change in USD (absolute)
    confidence: float
    timestamp: str
    liquidation_count: int       # Crowding strength (long_pct or short_pct as int)
    directional_percentage: float  # OI change % (e.g., 0.023 = 2.3%)
    # New fields for pre-hunt metadata
    oi_spike_pct: float = 0.0
    crowded_side: str = "BALANCED"
    crowding_reason: str = ""
    funding_rate: float = 0.0
    long_pct: float = 50.0
    short_pct: float = 50.0
    cvd_aligned: bool = False
    conditions_met: int = 0


class StopHuntDetector:
    """
    Pre-Hunt Liquidity Scanner

    Scans for conditions that typically precede a stop hunt:
    1. OI spike (new positions = new stops being placed)
    2. Crowding (one side dominant = stops concentrated)
    3. CVD alignment (flow direction confirms setup)

    Alerts BEFORE the sweep so the trader can prepare and watch the heatmap.
    """

    def __init__(self, buffer_manager, threshold: float = 2000000,
                 absorption_threshold: float = 100000,
                 absorption_min_order_usd: float = 5000,
                 monitoring_config: dict = None):
        self.buffer_manager = buffer_manager
        self.market_context_buffer = None  # Set by main.py
        self.logger = setup_logger("StopHuntDetector", "INFO")
        self._detections = 0

        # Tier thresholds for OI spike significance
        monitoring = monitoring_config or {}
        self._tier1_symbols = set(monitoring.get('tier1_symbols', ['BTCUSDT', 'ETHUSDT']))
        self._tier2_symbols = set(monitoring.get('tier2_symbols', []))
        self._tier3_symbols = set(monitoring.get('tier3_symbols', []))

        # OI spike thresholds per tier (minimum % for VALID)
        self._oi_thresholds = {
            1: 0.8,   # BTC/ETH: 0.8% is significant
            2: 1.0,   # Large alts: 1.0%
            3: 1.5,   # Mid alts: 1.5%
            4: 2.0,   # Small: 2.0%
        }

        # Cooldown tracking
        self._last_alert: dict = {}  # symbol -> timestamp
        monitoring = monitoring_config or {}
        self._tier1_cooldown = monitoring.get('tier1_cooldown', 7200)   # 2h
        self._tier2_cooldown = monitoring.get('tier2_cooldown', 3600)   # 1h
        self._tier3_cooldown = monitoring.get('tier3_cooldown', 2700)   # 45m
        self._tier4_cooldown = monitoring.get('tier4_cooldown', 1800)   # 30m

    def _get_tier(self, symbol: str) -> int:
        if symbol in self._tier1_symbols:
            return 1
        elif symbol in self._tier2_symbols:
            return 2
        elif symbol in self._tier3_symbols:
            return 3
        return 4

    def _get_cooldown(self, symbol: str) -> int:
        tier = self._get_tier(symbol)
        return {1: self._tier1_cooldown, 2: self._tier2_cooldown,
                3: self._tier3_cooldown, 4: self._tier4_cooldown}[tier]

    def get_threshold_for_symbol(self, symbol: str) -> tuple:
        """Backward compat — returns (oi_threshold_pct, 0) for this scanner."""
        tier = self._get_tier(symbol)
        return (self._oi_thresholds.get(tier, 2.0), 0)

    async def analyze(self, symbol: str, cascade_window: int = 30,
                      absorption_window: int = 30) -> Optional[StopHuntSignal]:
        """
        Scan for pre-hunt setup conditions.

        Returns StopHuntSignal if 2+ conditions are met, None otherwise.
        """
        try:
            if not self.market_context_buffer:
                return None

            # Cooldown check
            now = datetime.now(timezone.utc).timestamp()
            last = self._last_alert.get(symbol, 0)
            cooldown = self._get_cooldown(symbol)
            if now - last < cooldown:
                return None

            # Strip USDT suffix for buffer lookups
            base = symbol.replace("USDT", "")

            # ── Condition 1: OI Spike ──
            oi_snap = self.market_context_buffer.get_latest_oi(base)
            if not oi_snap:
                return None

            oi_change_pct = oi_snap.oi_change_pct  # Already 30m window from 5m candles
            tier = self._get_tier(symbol)
            oi_threshold = self._oi_thresholds.get(tier, 2.0)

            oi_spike = abs(oi_change_pct) >= oi_threshold
            oi_strong = abs(oi_change_pct) >= oi_threshold * 2

            if not oi_spike:
                return None  # OI is mandatory — no spike, no alert

            # ── Condition 2: Crowding ──
            ls = self.market_context_buffer.get_latest_long_short(base)
            funding = self.market_context_buffer.get_latest_funding(base)

            long_pct = ls.long_pct if ls else 50.0
            short_pct = ls.short_pct if ls else 50.0
            fr = funding.current_rate if funding else 0.0

            crowded_side = "BALANCED"
            crowding_reason = ""
            crowding_met = False

            # L/S ratio crowding
            if long_pct >= 60:
                crowded_side = "LONG"
                crowding_reason = f"L/S {long_pct:.1f}% long"
                crowding_met = True
            elif short_pct >= 60:
                crowded_side = "SHORT"
                crowding_reason = f"L/S {short_pct:.1f}% short"
                crowding_met = True

            # Funding rate extreme (supplements or replaces L/S)
            if abs(fr) >= 0.0003:  # ≥0.03%
                fr_side = "LONG" if fr > 0 else "SHORT"
                if not crowding_met:
                    crowded_side = fr_side
                    crowding_reason = f"FR {fr*100:+.4f}% extreme"
                    crowding_met = True
                elif crowded_side == fr_side:
                    crowding_reason += f" + FR {fr*100:+.4f}%"
                # If FR opposes L/S crowding, mixed signal — still count as crowded

            # ── Condition 3: CVD Alignment ──
            spot_cvd = self.market_context_buffer.get_latest_spot_cvd(base)
            fut_cvd = self.market_context_buffer.get_latest_futures_cvd(base)

            spot_dir = spot_cvd.cvd_direction if spot_cvd else "UNKNOWN"
            fut_dir = fut_cvd.cvd_direction if fut_cvd else "UNKNOWN"

            cvd_aligned = False
            if spot_dir != "UNKNOWN" and fut_dir != "UNKNOWN":
                # Both going same direction = aligned
                if spot_dir == fut_dir and spot_dir != "FLAT":
                    cvd_aligned = True
                # Spot moving, futures flat = partial (still count)
                elif spot_dir != "FLAT" and fut_dir == "FLAT":
                    cvd_aligned = True

            # ── Decision: need OI spike (mandatory) + at least 1 more ──
            conditions = sum([oi_spike, crowding_met, cvd_aligned])

            if conditions < 2:
                return None

            # ── Direction ──
            # Crowded LONG → expect SHORT sweep → prepare LONG after
            # Crowded SHORT → expect LONG squeeze → prepare SHORT after
            if crowded_side == "LONG":
                direction = "SHORT_HUNT"  # Longs will get hunted
            elif crowded_side == "SHORT":
                direction = "LONG_HUNT"   # Shorts will get hunted
            else:
                # No clear crowding — use CVD as bias
                if spot_dir == "RISING":
                    direction = "LONG_HUNT"  # Momentum up, shorts at risk
                elif spot_dir == "FALLING":
                    direction = "SHORT_HUNT"  # Momentum down, longs at risk
                else:
                    direction = "SHORT_HUNT"  # Default conservative

            # ── Confidence ──
            confidence = self._calculate_confidence(
                oi_change_pct=oi_change_pct,
                oi_threshold=oi_threshold,
                crowding_met=crowding_met,
                crowded_pct=max(long_pct, short_pct),
                cvd_aligned=cvd_aligned,
                fr=fr,
                conditions=conditions,
            )

            # Get price for context
            price_snap = self.market_context_buffer.get_latest_price(base)
            current_price = price_snap.price if price_snap else 0.0
            oi_change_usd = abs(oi_change_pct / 100 * oi_snap.current_oi_usd)

            signal = StopHuntSignal(
                symbol=symbol,
                total_volume=oi_snap.current_oi_usd,
                direction=direction,
                price_zone=(current_price, 0.0),
                absorption_detected=cvd_aligned,
                absorption_volume=oi_change_usd,
                confidence=confidence,
                timestamp=datetime.now(timezone.utc).isoformat(),
                liquidation_count=int(max(long_pct, short_pct)),
                directional_percentage=abs(oi_change_pct) / 100,
                oi_spike_pct=oi_change_pct,
                crowded_side=crowded_side,
                crowding_reason=crowding_reason,
                funding_rate=fr,
                long_pct=long_pct,
                short_pct=short_pct,
                cvd_aligned=cvd_aligned,
                conditions_met=conditions,
            )

            self._last_alert[symbol] = now
            self._detections += 1
            self.logger.info(
                f"🎯 Pre-hunt setup: {symbol} {direction} — "
                f"OI {oi_change_pct:+.2f}% | {crowding_reason or 'no crowd'} | "
                f"CVD {'aligned' if cvd_aligned else 'not aligned'} | "
                f"{conditions}/3 conditions | conf={confidence:.0f}%"
            )

            return signal

        except Exception as e:
            self.logger.error(f"Analysis failed for {symbol}: {e}")
            return None

    def _calculate_confidence(
        self,
        oi_change_pct: float,
        oi_threshold: float,
        crowding_met: bool,
        crowded_pct: float,
        cvd_aligned: bool,
        fr: float,
        conditions: int,
    ) -> float:
        """
        Calculate confidence score for pre-hunt setup.

        Base 50% + bonuses from each condition's strength.
        """
        confidence = 50.0

        # OI spike strength (10-25 points)
        oi_ratio = abs(oi_change_pct) / max(oi_threshold, 0.1)
        if oi_ratio >= 3.0:
            confidence += 25
        elif oi_ratio >= 2.0:
            confidence += 20
        elif oi_ratio >= 1.5:
            confidence += 15
        else:
            confidence += 10

        # Crowding strength (0-20 points)
        if crowding_met:
            if crowded_pct >= 70:
                confidence += 20
            elif crowded_pct >= 65:
                confidence += 15
            elif crowded_pct >= 60:
                confidence += 10

            # FR extreme bonus
            if abs(fr) >= 0.0005:  # ≥0.05%
                confidence += 5

        # CVD alignment (0-10 points)
        if cvd_aligned:
            confidence += 10

        # 3/3 conditions bonus
        if conditions == 3:
            confidence += 5

        return min(confidence, 99.0)

    def get_stats(self) -> dict:
        return {
            "total_detections": self._detections,
            "mode": "pre-hunt-scanner",
        }
