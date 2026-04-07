# REST Signal Detector — Generate signals from REST poller data
#
# Solves: pipeline only triggers from liquidation cascade (rare for altcoins).
# This detector runs after each REST poll cycle and checks for:
#   1. CVD Flip — SpotCVD changes direction (strongest signal)
#   2. OI Spike — significant OI change + price movement
#   3. Whale Activity — large net flow in one direction
#
# Produces signals compatible with existing pipeline (same as SignalGenerator output).
# Non-destructive: existing liquidation-based signals unchanged.

import time
from dataclasses import dataclass
from typing import Optional, List, Dict
from collections import defaultdict

from ..utils.logger import setup_logger


@dataclass
class RestSignal:
    """Signal generated from REST data (CVD/OI/whale)."""
    symbol: str
    signal_type: str      # CVD_FLIP, OI_SPIKE, WHALE_ACTIVITY, COMPOSITE
    direction: str        # LONG or SHORT
    confidence: float
    sources: List[str]    # which detectors contributed
    description: str
    metadata: dict


# Minimum CVD delta (USD) per tier to trigger flip signal
CVD_MIN_DELTA = {
    "t1": 500_000,   # BTC/ETH
    "t2": 100_000,   # Large cap
    "t3": 30_000,    # Mid cap
    "t4": 10_000,    # Small cap
}

# Minimum OI change % to trigger spike
OI_MIN_CHANGE_PCT = 1.5

# Minimum whale flow USD to trigger
WHALE_MIN_FLOW = 1_000_000
WHALE_MIN_DOMINANCE = 0.65

# Alignment bonus when multiple signals agree
ALIGNMENT_BONUS = 10
CONFLICT_PENALTY = 10


class RestSignalDetector:
    """Detect signals from REST-polled data (CVD, OI, whale)."""

    def __init__(self, market_context_buffer, monitoring_config: dict = None):
        self.buffer = market_context_buffer
        self.logger = setup_logger("RestSignalDetector", "INFO")
        self._monitoring = monitoring_config or {}
        self._tier1 = set(self._monitoring.get("tier1_symbols", ["BTCUSDT", "ETHUSDT"]))
        self._tier2 = set(self._monitoring.get("tier2_symbols", []))
        self._tier3 = set(self._monitoring.get("tier3_symbols", []))
        self._signals_generated = 0

        # Track previous CVD direction per symbol for flip detection
        self._prev_spot_dir: Dict[str, str] = {}
        self._prev_fut_dir: Dict[str, str] = {}

        # Track previous whale net to detect changes (avoid static repeats)
        self._prev_whale_net: Dict[str, float] = {}  # symbol → last net_long - net_short

    def _get_tier(self, symbol: str) -> str:
        if symbol in self._tier1:
            return "t1"
        elif symbol in self._tier2:
            return "t2"
        elif symbol in self._tier3:
            return "t3"
        return "t4"

    # ── CVD Flip Detection ──────────────────────────────────────────

    def check_cvd_flip(self, symbol: str) -> Optional[RestSignal]:
        """Check if SpotCVD just flipped direction (requires 2-snapshot confirmation)."""
        spot_history = self.buffer.get_spot_cvd_history(symbol, n=3)
        fut_history = self.buffer.get_futures_cvd_history(symbol, n=3)

        if not spot_history or len(spot_history) < 2:
            return None

        current = spot_history[-1]
        previous = spot_history[-2]

        curr_dir = current.cvd_direction
        prev_dir = self._prev_spot_dir.get(symbol, previous.cvd_direction)

        # Update tracking
        self._prev_spot_dir[symbol] = curr_dir

        # No flip
        if curr_dir == prev_dir or curr_dir == "FLAT" or prev_dir == "FLAT":
            return None

        # 2-snapshot confirmation: need previous snapshot also trending in new direction
        # If we have 3 snapshots, check that middle was already transitioning
        if len(spot_history) >= 3:
            mid = spot_history[-2]
            oldest = spot_history[-3]
            # Confirmed: both recent snapshots show new direction trend
            mid_trending = (mid.cvd_latest > oldest.cvd_latest) if curr_dir == "RISING" else (mid.cvd_latest < oldest.cvd_latest)
            if not mid_trending:
                return None  # Single-snapshot flip — not confirmed

        # Check minimum delta
        tier = self._get_tier(symbol)
        min_delta = CVD_MIN_DELTA.get(tier, 10_000)
        prev_val = previous.cvd_latest if previous else 0
        delta = abs(current.cvd_latest - prev_val)

        if delta < min_delta:
            return None

        # CVD VETO: for LONG signal, cumulative CVD must not be deeply negative
        if curr_dir == "RISING" and current.cvd_latest < 0:
            # CVD recovering but still negative — don't trigger LONG
            return None

        # Check futures alignment
        fut_dir = fut_history[-1].cvd_direction if fut_history else "FLAT"
        both_aligned = curr_dir == fut_dir and fut_dir != "FLAT"

        direction = "LONG" if curr_dir == "RISING" else "SHORT"
        confidence = 72 if both_aligned else 58

        self.logger.info(
            f"CVD FLIP {symbol}: {prev_dir}→{curr_dir} "
            f"delta=${delta:,.0f} aligned={both_aligned} conf={confidence}"
        )

        return RestSignal(
            symbol=symbol,
            signal_type="CVD_FLIP",
            direction=direction,
            confidence=confidence,
            sources=["SpotCVD"],
            description=f"SpotCVD flipped {prev_dir}→{curr_dir}" +
                        (f", FutCVD aligned {fut_dir}" if both_aligned else ""),
            metadata={
                "spot_cvd_direction": curr_dir,
                "spot_cvd_prev_direction": prev_dir,
                "spot_cvd_latest": current.cvd_latest,
                "spot_cvd_slope": current.cvd_slope,
                "futures_cvd_direction": fut_dir,
                "both_aligned": both_aligned,
                "delta_usd": delta,
            },
        )

    # ── OI Spike Detection ──────────────────────────────────────────

    def check_oi_spike(self, symbol: str) -> Optional[RestSignal]:
        """Check for significant OI change + price movement."""
        oi_history = self.buffer.get_oi_history(symbol, n=5)
        price_history = self.buffer.get_price_history(symbol, limit=2) if hasattr(self.buffer, 'get_price_history') else [self.buffer.get_latest_price(symbol)] if self.buffer.get_latest_price(symbol) else []

        if not oi_history or len(oi_history) < 2:
            return None
        if not price_history:
            return None

        current_oi = oi_history[-1]
        baseline_oi = oi_history[0]

        oi_change_pct = current_oi.oi_change_pct
        if abs(oi_change_pct) < OI_MIN_CHANGE_PCT:
            return None

        # Get price change
        price = price_history[-1]
        price_change = price.change_24h_pct if hasattr(price, 'change_24h_pct') else 0

        # Classify
        if oi_change_pct > OI_MIN_CHANGE_PCT and price_change > 0.3:
            interp = "MOMENTUM"
            direction = "LONG"
            confidence = 65
        elif oi_change_pct > OI_MIN_CHANGE_PCT and price_change < -0.3:
            interp = "SHORT_ADDING"
            direction = "SHORT"
            confidence = 60
        elif oi_change_pct < -OI_MIN_CHANGE_PCT and price_change > 0.3:
            interp = "SHORT_COVERING"
            direction = "LONG"
            confidence = 55
        elif oi_change_pct < -OI_MIN_CHANGE_PCT and price_change < -0.3:
            interp = "DELEVERAGING"
            direction = "SHORT"
            confidence = 60
        else:
            return None

        self.logger.info(
            f"OI SPIKE {symbol}: {interp} OI={oi_change_pct:+.1f}% "
            f"price={price_change:+.1f}% conf={confidence}"
        )

        return RestSignal(
            symbol=symbol,
            signal_type="OI_SPIKE",
            direction=direction,
            confidence=confidence,
            sources=["OpenInterest"],
            description=f"OI {oi_change_pct:+.1f}% ({interp}), price {price_change:+.1f}%",
            metadata={
                "oi_change_pct": oi_change_pct,
                "oi_usd": current_oi.current_oi_usd,
                "interpretation": interp,
                "price_change_pct": price_change,
            },
        )

    # ── Whale Activity Detection ────────────────────────────────────

    def check_whale_activity(self, symbol: str) -> Optional[RestSignal]:
        """Check for significant whale net flow. Only triggers on CHANGED state."""
        whale_positions = self.buffer.get_whale_positions(symbol)
        if not whale_positions:
            return None

        net_long = 0
        net_short = 0
        count = 0

        for wp in whale_positions:
            val = abs(wp.position_value_usd) if hasattr(wp, 'position_value_usd') else 0
            if val < 100_000:
                continue
            count += 1
            if wp.direction == "LONG":
                net_long += val
            else:
                net_short += val

        total_flow = net_long + net_short
        if total_flow < WHALE_MIN_FLOW:
            return None

        # Check if whale state actually changed since last check
        current_net = net_long - net_short
        prev_net = self._prev_whale_net.get(symbol, None)
        self._prev_whale_net[symbol] = current_net

        if prev_net is not None:
            # Only trigger if net changed significantly (>10% or direction flipped)
            prev_dir = "LONG" if prev_net > 0 else "SHORT"
            curr_dir = "LONG" if current_net > 0 else "SHORT"
            net_change_pct = abs(current_net - prev_net) / max(abs(prev_net), 1) * 100
            if prev_dir == curr_dir and net_change_pct < 10:
                return None  # Same whale state, skip

        dominance = max(net_long, net_short) / total_flow
        if dominance < WHALE_MIN_DOMINANCE:
            return None

        direction = "LONG" if net_long > net_short else "SHORT"

        # Dynamic confidence based on multiple factors (not fixed 85)
        confidence = 50.0

        # 1. Dominance: 65%→+5, 80%→+10, 95%→+15
        confidence += min(15, (dominance - 0.6) * 37.5)

        # 2. Size: >$2M→+5, >$5M→+10, >$10M→+15
        dominant_flow = max(net_long, net_short)
        if dominant_flow >= 10_000_000:
            confidence += 15
        elif dominant_flow >= 5_000_000:
            confidence += 10
        elif dominant_flow >= 2_000_000:
            confidence += 5

        # 3. Whale count: multiple whales same direction = stronger
        if count >= 5:
            confidence += 8
        elif count >= 3:
            confidence += 5
        elif count >= 2:
            confidence += 2

        # 4. CVD alignment: whale + flow = strong, whale vs flow = weak
        spot_cvd = self.buffer.get_latest_spot_cvd(symbol)
        cvd_aligned = False
        cvd_opposing = False
        if spot_cvd:
            if direction == "LONG" and spot_cvd.cvd_direction == "RISING":
                cvd_aligned = True
                confidence += 8
            elif direction == "SHORT" and spot_cvd.cvd_direction == "FALLING":
                cvd_aligned = True
                confidence += 8
            elif direction == "LONG" and spot_cvd.cvd_direction == "FALLING":
                cvd_opposing = True
                confidence -= 12
            elif direction == "SHORT" and spot_cvd.cvd_direction == "RISING":
                cvd_opposing = True
                confidence -= 12

        # 5. OI confirmation: OI rising with whale = positioning, not just hedging
        oi_snap = self.buffer.get_latest_oi(symbol) if hasattr(self.buffer, 'get_latest_oi') else None
        if oi_snap and oi_snap.oi_change_pct > 0.5:
            confidence += 5

        confidence = max(55, min(92, round(confidence)))

        self.logger.info(
            f"WHALE {symbol}: {direction} net_long=${net_long:,.0f} "
            f"net_short=${net_short:,.0f} dominance={dominance:.0%} "
            f"count={count} cvd={'OK' if cvd_aligned else 'OPPOSE' if cvd_opposing else 'N/A'} "
            f"conf={confidence}"
        )

        return RestSignal(
            symbol=symbol,
            signal_type="WHALE_ACTIVITY",
            direction=direction,
            confidence=confidence,
            sources=["WhaleAlert"],
            description=f"Whale net {direction} ${dominant_flow:,.0f} ({dominance:.0%} dominant, {count} whales)",
            metadata={
                "net_long_usd": net_long,
                "net_short_usd": net_short,
                "total_flow_usd": total_flow,
                "dominance": dominance,
                "whale_count": count,
                "cvd_aligned": cvd_aligned,
                "cvd_opposing": cvd_opposing,
            },
        )

    # ── Composite Evaluation ────────────────────────────────────────

    def evaluate(self, symbol: str) -> Optional[RestSignal]:
        """
        Run all detectors and produce a composite signal if any trigger.

        Returns the strongest signal, with alignment bonus if multiple agree.
        """
        signals = []

        cvd = self.check_cvd_flip(symbol)
        if cvd:
            signals.append(cvd)

        oi = self.check_oi_spike(symbol)
        if oi:
            signals.append(oi)

        whale = self.check_whale_activity(symbol)
        if whale:
            signals.append(whale)

        if not signals:
            return None

        # Group by direction
        long_signals = [s for s in signals if s.direction == "LONG"]
        short_signals = [s for s in signals if s.direction == "SHORT"]

        dominant = "LONG" if len(long_signals) >= len(short_signals) else "SHORT"
        aligned = long_signals if dominant == "LONG" else short_signals
        conflicting = short_signals if dominant == "LONG" else long_signals

        # Base confidence = best signal
        best = max(aligned, key=lambda s: s.confidence)
        confidence = best.confidence

        # Alignment bonus
        if len(aligned) >= 2:
            confidence += ALIGNMENT_BONUS
        # Conflict penalty
        if conflicting:
            confidence -= CONFLICT_PENALTY

        confidence = max(55, min(95, confidence))

        # Determine composite type
        if len(aligned) >= 2:
            source_types = {s.signal_type for s in aligned}
            if "CVD_FLIP" in source_types and "OI_SPIKE" in source_types:
                signal_type = "ACCUMULATION" if dominant == "LONG" else "DISTRIBUTION"
            elif "WHALE_ACTIVITY" in source_types and "CVD_FLIP" in source_types:
                signal_type = "SMART_MONEY_BUY" if dominant == "LONG" else "SMART_MONEY_SELL"
            else:
                signal_type = best.signal_type
        else:
            signal_type = best.signal_type

        # Build composite metadata
        meta = {"sources": {s.signal_type: s.metadata for s in aligned}}
        if conflicting:
            meta["conflicting"] = [s.signal_type for s in conflicting]

        description = " + ".join(s.description for s in aligned)

        self._signals_generated += 1

        self.logger.info(
            f"REST SIGNAL {symbol}: {signal_type} {dominant} "
            f"conf={confidence} sources={[s.signal_type for s in aligned]}"
        )

        return RestSignal(
            symbol=symbol,
            signal_type=signal_type,
            direction=dominant,
            confidence=confidence,
            sources=[s.signal_type for s in aligned],
            description=description,
            metadata=meta,
        )

    def get_stats(self) -> dict:
        return {"signals_generated": self._signals_generated}
