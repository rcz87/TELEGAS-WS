# Message Formatter - Data-First Alert Format
# Shows raw market data prominently, system bias as reference

"""
Message Formatter Module — Data-First Format

Every alert shows raw market data first:
- SpotCVD, FutCVD, OI, OBDelta
- Funding Rate per exchange
- Whale positions (Hyperliquid)
- Price action + volume + liquidations

System bias (LONG/SHORT + confidence) at bottom as reference only.
User decides entry based on data, not system conclusion.
"""

from typing import Any
from datetime import datetime, timezone, timedelta

from ..utils.logger import setup_logger
from ..utils.symbol_normalizer import display_symbol


class MessageFormatter:
    """
    Data-first message formatter for Telegram.

    Shows raw market data prominently with system bias
    as a small reference at the bottom.
    """

    # WIB = UTC+7
    _WIB = timezone(timedelta(hours=7))

    def __init__(self):
        """Initialize message formatter"""
        self.logger = setup_logger("MessageFormatter", "INFO")
        self._messages_formatted = 0

    def format_signal(self, signal: Any) -> str:
        """
        Format any trading signal using unified data-first format.

        Args:
            signal: TradingSignal object

        Returns:
            Formatted Telegram message string
        """
        try:
            message = self.format_data_first(signal)
            self._messages_formatted += 1
            return message
        except Exception as e:
            self.logger.error(f"Formatting failed: {e}")
            return self.format_error(signal)

    def format_data_first(self, signal: Any) -> str:
        """
        Unified data-first formatter for all signal types.

        Sections:
        1. Header: symbol | price | time
        2. Trigger: signal type + key detail
        3. Order Flow: SpotCVD, FutCVD, OI, OBDelta
        4. Funding Rate: per-exchange rates
        5. Whale: Hyperliquid positions if significant
        6. Price Action: price, volume, liq 24h
        7. Bias: system direction + confidence
        8. DYOR disclaimer
        """
        ctx = signal.metadata.get('market_context', {})

        # Build sections
        header = self._format_header(signal, ctx)
        trigger = self._format_trigger_line(signal)
        order_flow = self._format_order_flow_section(ctx)
        funding = self._format_funding_section(ctx)
        whale = self._format_whale_section(ctx)
        leading = self._format_leading_section(signal)
        price_action = self._format_price_action_section(ctx, signal)
        bias = self._format_bias_line(signal)

        # Assemble — skip empty sections instead of showing N/A
        hidden = sum(1 for s in [order_flow, funding] if not s)
        sections = [header, trigger]
        if order_flow:
            sections.append(order_flow)
        if funding:
            sections.append(funding)
        if whale:
            sections.append(whale)
        if leading:
            sections.append(leading)
        if price_action:
            sections.append(price_action)
        if hidden > 0:
            sections.append("\U0001f4e1 _Market context limited_")
        sections.append(bias)

        return "\n\n".join(sections)

    def _format_header(self, signal: Any, ctx: dict) -> str:
        """Header: symbol | price | time WIB"""
        symbol_clean = display_symbol(signal.symbol)
        price = ctx.get('current_price', 0)
        price_str = self.format_price(price) if price > 0 else "N/A"
        time_str = datetime.now(self._WIB).strftime('%H:%M:%S')
        return f"\u26a1 *{symbol_clean}* | {price_str} | {time_str} WIB"

    def _format_trigger_line(self, signal: Any) -> str:
        """Trigger line with signal type + full numerical detail"""
        sig_type = signal.signal_type
        metadata = signal.metadata

        if sig_type == "STOP_HUNT":
            sh = metadata.get('stop_hunt', {})
            vol = sh.get('total_volume', 0)
            direction = sh.get('direction', 'UNKNOWN')
            absorption = sh.get('absorption_volume', 0)
            dir_pct = sh.get('directional_percentage', 0) * 100
            liq_count = sh.get('liquidation_count', 0)
            lines = [f"\U0001f514 Trigger: STOP HUNT | {direction}"]
            lines.append(f"Liquidasi: {self._fmt_large_usd(vol)} ({liq_count} events, {dir_pct:.0f}% one-sided)")
            lines.append(f"Absorpsi : {self._fmt_large_usd(absorption)}" + (" \u2705" if absorption > 0 else " \u274c"))
            return "\n".join(lines)

        elif sig_type in ("ACCUMULATION", "DISTRIBUTION"):
            of = metadata.get('order_flow', {})
            net = of.get('net_delta', 0)
            buy_ratio = of.get('buy_ratio', 0) * 100
            large_buys = of.get('large_buys', 0)
            large_sells = of.get('large_sells', 0)
            total_vol = of.get('total_volume', 0)
            lines = [f"\U0001f514 *Trigger: {sig_type}*"]
            lines.append(f"Volume   : {self._fmt_large_usd(total_vol)} | Net delta: {self._fmt_value(net)}")
            lines.append(f"Buy ratio: {buy_ratio:.0f}% | Whale orders: {large_buys}B/{large_sells}S")
            return "\n".join(lines)

        elif sig_type == "EVENT":
            events = metadata.get('events', [])
            count = len(events)
            descs = [e.get('type', 'unknown').replace('_', ' ').title() for e in events[:3]]
            lines = [f"\U0001f514 *Trigger: EVENT* ({count} event{'s' if count > 1 else ''})"]
            for d in descs:
                lines.append(f"\u2022 {d}")
            return "\n".join(lines)

        else:
            return f"\U0001f514 *Trigger: {sig_type}*"

    def _format_order_flow_section(self, ctx: dict) -> str:
        """Order Flow section: SpotCVD, FutCVD, OI, OBDelta with full numbers."""
        has_spot = ctx.get('spot_cvd_direction', 'UNKNOWN') != 'UNKNOWN'
        has_fut = ctx.get('futures_cvd_direction', 'UNKNOWN') != 'UNKNOWN'
        has_oi = ctx.get('oi_usd', 0) > 0
        has_ob = ctx.get('orderbook_dominant', 'UNKNOWN') != 'UNKNOWN'

        if not any([has_spot, has_fut, has_oi, has_ob]):
            return ""

        lines = ["\U0001f4ca *ORDER FLOW*"]

        if has_spot:
            spot_dir = ctx['spot_cvd_direction']
            chg = ctx.get('spot_cvd_change', 0)
            chg_arrow = "\u25b2" if chg > 0 else "\u25bc" if chg < 0 else "\u2192"
            lines.append(f"SpotCVD  : {self._fmt_value(ctx.get('spot_cvd_latest', 0))} | \u039460m: {chg_arrow}{self._fmt_value(chg)} | {spot_dir}")

        if has_fut:
            fut_dir = ctx['futures_cvd_direction']
            chg = ctx.get('futures_cvd_change', 0)
            chg_arrow = "\u25b2" if chg > 0 else "\u25bc" if chg < 0 else "\u2192"
            lines.append(f"FutCVD   : {self._fmt_value(ctx.get('futures_cvd_latest', 0))} | \u039460m: {chg_arrow}{self._fmt_value(chg)} | {fut_dir}")

        # CVD alignment label
        cvd_align = ctx.get('cvd_alignment', 'NEUTRAL')
        if has_spot or has_fut:
            lines.append(f"CVD sync : {cvd_align}")

        if has_oi:
            oi_usd = ctx['oi_usd']
            oi_change = ctx.get('oi_change_1h_pct', 0)
            oi_align = ctx.get('oi_alignment', 'NEUTRAL')
            oi_arrow = "\u25b2" if oi_change > 0 else "\u25bc" if oi_change < 0 else "\u25b6"
            lines.append(f"OI       : {self._fmt_large_usd(oi_usd)} {oi_arrow} {oi_change:+.1f}% 1h [{oi_align}]")

        if has_ob:
            ob_dominant = ctx['orderbook_dominant']
            bid_vol = ctx.get('orderbook_bid_vol', 0)
            ask_vol = ctx.get('orderbook_ask_vol', 0)
            total_ob = bid_vol + ask_vol
            bid_pct = (bid_vol / total_ob * 100) if total_ob > 0 else 50
            lines.append(f"OBDelta  : Bid {self._fmt_large_usd(bid_vol)} ({bid_pct:.0f}%) / Ask {self._fmt_large_usd(ask_vol)} ({100-bid_pct:.0f}%)")

        return "\n".join(lines)

    def _format_funding_section(self, ctx: dict) -> str:
        """Funding Rate section: per-exchange rates with alignment. Returns '' if no data."""
        per_exchange = ctx.get('funding_per_exchange', {})
        fr = ctx.get('funding_rate', 0)
        funding_align = ctx.get('funding_alignment', 'NEUTRAL')

        if not per_exchange and fr == 0:
            return ""

        # Sanity check: FR should be between -1% and +1% per interval
        # Anything outside this is bogus data (API error or format mismatch)
        def is_sane_fr(rate):
            return abs(rate) < 0.01  # < 1%

        lines = ["\U0001f4b8 *FUNDING RATE*"]
        if per_exchange:
            sane_rates = {k: v for k, v in per_exchange.items() if is_sane_fr(v)}
            if sane_rates:
                sorted_rates = sorted(sane_rates.items(), key=lambda x: abs(x[1]), reverse=True)
                for exchange, rate in sorted_rates[:5]:
                    lines.append(f"{exchange:9s}: {rate * 100:+.4f}%")
            elif is_sane_fr(fr):
                lines.append(f"Avg      : {fr * 100:+.4f}%")
            else:
                lines.append(f"Avg      : N/A (data unavailable)")
        elif is_sane_fr(fr):
            lines.append(f"Avg      : {fr * 100:+.4f}%")
        else:
            lines.append(f"Avg      : N/A (data unavailable)")
        lines.append(f"Alignment: {funding_align}")

        return "\n".join(lines)

    def _format_whale_section(self, ctx: dict) -> str:
        """Whale section: Hyperliquid positions if significant (>= $1M)"""
        whale_conflicting = ctx.get('whale_conflicting', False)
        whale_val = ctx.get('whale_largest_value_usd', 0)
        whale_dir = ctx.get('whale_largest_direction', '')
        whale_entry = ctx.get('whale_entry_price', 0)
        whale_liq = ctx.get('whale_liq_price', 0)
        whale_align = ctx.get('whale_alignment', 'NEUTRAL')

        # Show whale section if any significant position exists
        if whale_val < 1_000_000:
            return ""

        lines = ["\U0001f40b *WHALE (Hyperliquid)*"]

        dir_label = whale_dir.upper() if whale_dir else "?"
        val_str = self._fmt_large_usd(whale_val)

        detail = f"{dir_label} {val_str}"
        if whale_entry > 0:
            detail += f" @ {self.format_price(whale_entry)}"
        if whale_liq > 0:
            detail += f" | Liq: {self.format_price(whale_liq)}"
        detail += f" [{whale_align}]"

        lines.append(detail)

        return "\n".join(lines)

    def _format_price_action_section(self, ctx: dict, signal: Any) -> str:
        """Price Action section: price, volume, liq 24h. Returns '' if no data."""
        price = ctx.get('current_price', 0)
        volume = ctx.get('volume_24h', 0)
        liq_24h = ctx.get('liquidation_24h_volume', 0)

        if price == 0 and volume == 0 and liq_24h == 0:
            return ""

        lines = ["\U0001f4c8 *PRICE ACTION*"]

        # Price + 24h change (omit % if from WebSocket since we don't have 24h data)
        if price > 0:
            change_24h = ctx.get('price_change_24h_pct', 0)
            if change_24h != 0:
                lines.append(f"Harga    : {self.format_price(price)} ({change_24h:+.1f}% 24h)")
            else:
                lines.append(f"Harga    : {self.format_price(price)}")

        # Volume (only show if available from REST)
        if volume > 0:
            lines.append(f"Volume   : {self._fmt_large_usd(volume)}")

        # Liquidation 24h
        uptime = ctx.get('uptime_hours', 0)
        if liq_24h > 0:
            lines.append(f"Liq 24h  : {self._fmt_large_usd(liq_24h)}")
        elif uptime < 1:
            lines.append("Liq 24h  : warmup")
        else:
            lines.append("Liq 24h  : $0")

        return "\n".join(lines)

    def _format_leading_section(self, signal: Any) -> str:
        """Leading indicators section with details. Returns '' if no data."""
        ls = signal.metadata.get('leading_score', {})
        indicators = ls.get('indicators', [])
        notes = ls.get('notes', [])

        if not indicators and not notes:
            return ""

        lines = ["\U0001f525 *LEADING SIGNALS*"]
        for ind in indicators:
            if ind.get('detail'):
                lines.append(f"\u2726 {ind['detail']}")
        for note in notes:
            lines.append(f"\u2726 {note}")

        return "\n".join(lines)

    def _format_bias_line(self, signal: Any) -> str:
        """System bias with leading label + progress bar + context verdict + DYOR"""
        direction = signal.direction.upper() if signal.direction else "NEUTRAL"
        confidence = signal.confidence
        ctx = signal.metadata.get('market_context', {})

        if direction in ("LONG", "BULLISH"):
            dir_emoji = "\U0001f4c8"
            dir_label = "LONG"
        elif direction in ("SHORT", "BEARISH"):
            dir_emoji = "\U0001f4c9"
            dir_label = "SHORT"
        else:
            dir_emoji = "\u2796"
            dir_label = "NEUTRAL"

        # Use leading score label if available
        ls = signal.metadata.get('leading_score', {})
        label_emoji = ls.get('label_emoji', '\U0001f3af')
        label_text = ls.get('label_text', '')

        # Progress bar
        bar = self.create_progress_bar(confidence, length=10)

        if label_text:
            header = f"{label_emoji} {dir_label} {dir_emoji} \u2014 {label_text}"
        else:
            header = f"\U0001f3af {dir_label} {dir_emoji}"

        # Market context verdict
        assessment = ctx.get('combined_assessment', '')
        assessment_line = ""
        if assessment:
            assess_emoji = {
                "FAVORABLE": "\u2705",
                "NEUTRAL": "\u2796",
                "UNFAVORABLE": "\u274c",
            }.get(assessment, "")
            assessment_line = f"\nContext  : {assess_emoji} {assessment}"

        return f"{header}\n{bar} {confidence:.0f}%{assessment_line}\n\u26a0\ufe0f DYOR \u2014 verifikasi sebelum entry"

    # --- Utility helpers ---

    @staticmethod
    def _fmt_value(v: float) -> str:
        """Format value with K/M suffix and sign"""
        if abs(v) >= 1_000_000:
            return f"{v/1_000_000:+.1f}M"
        elif abs(v) >= 1_000:
            return f"{v/1_000:+.0f}K"
        else:
            return f"{v:+.0f}"

    @staticmethod
    def _fmt_large_usd(v: float) -> str:
        """Format large USD value (no sign)"""
        if v >= 1_000_000_000:
            return f"${v/1_000_000_000:.1f}B"
        elif v >= 1_000_000:
            return f"${v/1_000_000:.1f}M"
        elif v >= 1_000:
            return f"${v/1_000:.0f}K"
        else:
            return f"${v:.0f}"

    @staticmethod
    def _dir_arrow(direction: str) -> str:
        """Direction to arrow symbol"""
        return {
            "RISING": "\u25b2",
            "FALLING": "\u25bc",
            "FLAT": "\u25b6",
        }.get(direction, "?")

    # --- Kept methods ---

    def format_generic(self, signal: Any) -> str:
        """Generic fallback formatter"""
        priority_emoji = self.get_priority_emoji(signal.priority)

        return f"""{priority_emoji} *{signal.symbol}* - {signal.signal_type}

Direction: {signal.direction}
Sources: {', '.join(signal.sources)}

\U0001f3af Confidence: {signal.confidence:.0f}%
\u23f0 {datetime.now(self._WIB).strftime('%H:%M:%S')} WIB"""

    def format_error(self, signal: Any) -> str:
        """Format error message"""
        return f"""\u26a0\ufe0f *Signal Formatting Error*

Symbol: {getattr(signal, 'symbol', 'Unknown')}
Type: {getattr(signal, 'signal_type', 'Unknown')}

Please check logs for details."""

    @staticmethod
    def format_price(price: float) -> str:
        """
        Format price smartly based on magnitude.

        BTC $96,200  |  ETH $3,450  |  DOGE $0.1823  |  PEPE $0.00001182
        """
        abs_price = abs(price)
        if abs_price == 0:
            return "$0"
        elif abs_price >= 1000:
            return f"${price:,.0f}"
        elif abs_price >= 1:
            return f"${price:,.2f}"
        elif abs_price >= 0.01:
            return f"${price:.4f}"
        elif abs_price >= 0.0001:
            return f"${price:.6f}"
        else:
            return f"${price:.8f}"

    def get_priority_emoji(self, priority: int) -> str:
        """Get emoji based on priority (1=urgent, 2=watch, 3=info)"""
        if priority == 1:
            return "\U0001f534"  # red circle
        elif priority == 2:
            return "\U0001f7e1"  # yellow circle
        else:
            return "\U0001f535"  # blue circle

    def create_progress_bar(self, percentage: float, length: int = 20) -> str:
        """Create visual progress bar"""
        filled = int(length * percentage / 100)
        filled = max(0, min(filled, length))
        return "\u2588" * filled + "\u2591" * (length - filled)

    def get_stats(self) -> dict:
        """Get formatter statistics"""
        return {
            "messages_formatted": self._messages_formatted
        }
