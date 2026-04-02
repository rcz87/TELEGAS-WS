# Market Context Filter - OI & Funding Rate Signal Filter
# Evaluates whether market context supports a trading signal

"""
Market Context Filter Module

Responsibilities:
- Evaluate signals against OI and funding rate context
- Return PASS/FAIL with detailed reasoning
- Adjust confidence score based on context strength
- Provide context data for message enrichment

Filter modes:
- "normal": Block UNFAVORABLE, pass FAVORABLE + NEUTRAL
- "strict": Only pass FAVORABLE
- "permissive": Pass all, only adjust confidence

Insertion point in pipeline:
  analyzers -> signal_generator -> confidence_scorer
  -> signal_validator -> [THIS FILTER] -> message_formatter -> telegram
"""

from dataclasses import dataclass
from typing import Optional

from ..utils.logger import setup_logger
from ..utils.symbol_normalizer import to_base_symbol as _to_base


@dataclass
class FilterResult:
    """Result of market context filter evaluation"""
    passed: bool                    # True = send to Telegram, False = dashboard only
    assessment: str                 # "FAVORABLE", "NEUTRAL", "UNFAVORABLE"
    reason: str                     # Human-readable explanation
    confidence_adjustment: float    # Suggested confidence adjustment (-10 to +5)
    market_context: Optional[object] = None  # MarketContext for message enrichment


class MarketContextFilter:
    """
    Evaluates whether market context (OI + funding rate) supports a signal.

    Behavior:
    - FAVORABLE: Pass + boost confidence +5
    - NEUTRAL: Pass + no change (or small adjustment from sub-factors)
    - UNFAVORABLE: Block Telegram (dashboard only) + confidence -10

    Features:
    - Three filter modes (strict/normal/permissive)
    - Graceful pass-through when no data available
    - Symbol pair-to-base mapping (BTCUSDT -> BTC)
    - Statistics tracking
    """

    def __init__(
        self,
        market_context_buffer,
        mode: str = "normal",
        enable_confidence_adjust: bool = True,
    ):
        """
        Initialize market context filter.

        Args:
            market_context_buffer: MarketContextBuffer instance
            mode: "strict", "normal", or "permissive"
            enable_confidence_adjust: Whether to adjust confidence based on context
        """
        self.buffer = market_context_buffer
        self.mode = mode
        self.enable_confidence_adjust = enable_confidence_adjust
        self.logger = setup_logger("MarketContextFilter", "INFO")
        self._stats = {
            "total_evaluated": 0,
            "passed": 0,
            "filtered": 0,
            "favorable": 0,
            "neutral": 0,
            "unfavorable": 0,
            "no_data": 0,
            "cvd_vetoed": 0,
            "whale_vetoed": 0,
        }

    @staticmethod
    def to_base_symbol(pair_symbol: str) -> str:
        """
        Convert trading pair to CoinGlass base symbol.

        BTCUSDT -> BTC, BTC-USDT-SWAP -> BTC, BTCUSDT_UMCBL -> BTC
        """
        return _to_base(pair_symbol)

    def evaluate(self, signal) -> FilterResult:
        """
        Evaluate a TradingSignal against market context.

        Args:
            signal: TradingSignal with .symbol, .direction, .confidence

        Returns:
            FilterResult with pass/fail decision and context data
        """
        self._stats["total_evaluated"] += 1

        base_symbol = self.to_base_symbol(signal.symbol)
        context = self.buffer.evaluate_context(base_symbol, signal.direction)

        if context is None:
            # No OI/funding data available yet — pass through
            self._stats["no_data"] += 1
            self._stats["passed"] += 1
            return FilterResult(
                passed=True,
                assessment="NEUTRAL",
                reason="No OI/funding data available yet",
                confidence_adjustment=0,
                market_context=None,
            )

        assessment = context.combined_assessment
        self._stats[assessment.lower()] += 1

        # Determine confidence adjustment
        conf_adj = 0.0
        if self.enable_confidence_adjust:
            if assessment == "FAVORABLE":
                conf_adj = +5.0
            elif assessment == "UNFAVORABLE":
                conf_adj = -10.0
            else:
                # Partial adjustments from individual factors
                if context.funding_alignment == "FAVORABLE":
                    conf_adj = +2.0
                elif context.oi_alignment == "CONFIRMATION":
                    conf_adj = +2.0
                elif context.oi_alignment == "SQUEEZE_RISK":
                    conf_adj = -3.0

            # CVD adjustments (additive, on top of base)
            cvd_align = getattr(context, 'cvd_alignment', 'NEUTRAL')
            if cvd_align == "VETO":
                conf_adj = min(conf_adj, 0) - 15.0
                self._stats["cvd_vetoed"] += 1
            elif cvd_align == "CONFIRMS":
                conf_adj += 5.0
            elif cvd_align == "PARTIAL":
                conf_adj += 2.0

            # Whale adjustments
            whale_align = getattr(context, 'whale_alignment', 'NEUTRAL')
            if whale_align == "VETO":
                conf_adj = min(conf_adj, 0) - 15.0
                self._stats["whale_vetoed"] += 1
            elif whale_align == "CAUTION":
                # Halve any positive adjustment, then subtract 5
                if conf_adj > 0:
                    conf_adj = conf_adj / 2.0
                conf_adj -= 5.0

        # Determine pass/fail based on mode
        if self.mode == "strict":
            passed = assessment == "FAVORABLE"
        elif self.mode == "permissive":
            passed = True
        else:  # "normal"
            passed = assessment != "UNFAVORABLE"

        if passed:
            self._stats["passed"] += 1
        else:
            self._stats["filtered"] += 1

        reason = self._build_reason(context, signal.direction)

        cvd_align = getattr(context, 'cvd_alignment', 'NEUTRAL')
        whale_align = getattr(context, 'whale_alignment', 'NEUTRAL')
        self.logger.info(
            f"{'PASS' if passed else 'BLOCK'} {signal.symbol} {signal.direction}: "
            f"{assessment} (funding={context.funding_alignment}, "
            f"OI={context.oi_alignment}, CVD={cvd_align}, "
            f"whale={whale_align}) adj={conf_adj:+.0f}"
        )

        return FilterResult(
            passed=passed,
            assessment=assessment,
            reason=reason,
            confidence_adjustment=conf_adj,
            market_context=context,
        )

    def _build_reason(self, context, direction: str) -> str:
        """Build human-readable filter reason."""
        parts = []

        rate = getattr(context, 'current_funding_rate', 0) or 0
        rate_pct = rate * 100
        funding_align = getattr(context, 'funding_alignment', 'NEUTRAL')
        if funding_align == "FAVORABLE":
            parts.append(
                f"Funding {rate_pct:+.4f}% favors {direction} "
                f"(counter-side crowded)"
            )
        elif funding_align == "CAUTION":
            parts.append(
                f"Funding {rate_pct:+.4f}% suggests {direction} side crowded"
            )
        else:
            parts.append(f"Funding {rate_pct:+.4f}% neutral")

        oi_chg = getattr(context, 'oi_change_1h_pct', 0) or 0
        oi_align = getattr(context, 'oi_alignment', 'NEUTRAL')
        if oi_align == "CONFIRMATION":
            parts.append(f"OI +{oi_chg:.1f}% 1h (building)")
        elif oi_align == "WEAK":
            parts.append(f"OI {oi_chg:.1f}% 1h (closing)")
        elif oi_align == "SQUEEZE_RISK":
            parts.append(f"OI +{oi_chg:.1f}% 1h (squeeze risk)")
        else:
            parts.append(f"OI {oi_chg:+.1f}% 1h (stable)")

        # CVD info
        cvd_align = getattr(context, 'cvd_alignment', 'NEUTRAL')
        if cvd_align != "NEUTRAL":
            spot_dir = getattr(context, 'spot_cvd_direction', 'UNKNOWN')
            fut_dir = getattr(context, 'futures_cvd_direction', 'UNKNOWN')
            parts.append(f"CVD spot={spot_dir} fut={fut_dir} [{cvd_align}]")

        # Whale info
        whale_align = getattr(context, 'whale_alignment', 'NEUTRAL')
        if whale_align != "NEUTRAL":
            whale_val = getattr(context, 'whale_largest_value_usd', 0)
            whale_dir = getattr(context, 'whale_largest_direction', '')
            parts.append(
                f"Whale ${whale_val/1_000_000:.1f}M {whale_dir} [{whale_align}]"
            )

        return " | ".join(parts)

    def get_stats(self) -> dict:
        """Get filter statistics."""
        return dict(self._stats)
