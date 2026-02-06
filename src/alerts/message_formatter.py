# Message Formatter - Format Telegram Messages
# Production-ready message formatting for Telegram

"""
Message Formatter Module

Responsibilities:
- Format TradingSignal into readable Telegram messages
- Priority-based formatting with emojis
- Markdown formatting for Telegram
- Visual indicators (progress bars, charts)
- Clean, concise, actionable format

Telegram Markdown:
- *bold* for emphasis
- `code` for values
- Multiple line breaks for structure
"""

from typing import Any
from datetime import datetime

from ..utils.logger import setup_logger

class MessageFormatter:
    """
    Production-ready message formatter for Telegram
    
    Formats trading signals into clean, readable,
    actionable Telegram messages with proper markdown.
    
    Features:
    - Priority-based formatting
    - Visual indicators
    - Clear structure
    - Emoji indicators
    """
    
    def __init__(self):
        """Initialize message formatter"""
        self.logger = setup_logger("MessageFormatter", "INFO")
        self._messages_formatted = 0
        
    def format_signal(self, signal: Any) -> str:
        """
        Format trading signal based on type
        
        Args:
            signal: TradingSignal object
            
        Returns:
            Formatted Telegram message string
        """
        try:
            # Get priority emoji
            priority_emoji = self.get_priority_emoji(signal.priority)
            
            # Route to specific formatter
            if signal.signal_type == "STOP_HUNT":
                message = self.format_stop_hunt(signal)
            elif signal.signal_type in ["ACCUMULATION", "DISTRIBUTION"]:
                message = self.format_order_flow(signal)
            elif signal.signal_type == "EVENT":
                message = self.format_event(signal)
            else:
                message = self.format_generic(signal)
            
            self._messages_formatted += 1
            return message
            
        except Exception as e:
            self.logger.error(f"Formatting failed: {e}")
            return self.format_error(signal)
    
    def format_stop_hunt(self, signal: Any) -> str:
        """
        Format stop hunt signal
        
        Example output:
        🔴 STOP HUNT DETECTED - BTCUSDT
        
        📊 *Liquidations*: $3.5M cleared
        • Direction: SHORT_HUNT (longs stopped)
        • Count: 175 liquidations
        • Zone: $95,800 - $96,200
        
        🐋 *Whale Absorption*: $800K
        ✅ Strong buying after cascade
        
        💡 *TRADING SETUP*
        Entry: $96,000 - $96,200
        Stop Loss: $95,650 (below hunt zone)
        Target 1: $97,000 (+1.0%)
        Target 2: $97,800 (+1.8%)
        
        🎯 Confidence: 85%
        ⏰ 12:05:23 UTC
        """
        try:
            metadata = signal.metadata.get('stop_hunt', {})
            
            # Priority emoji
            priority_emoji = self.get_priority_emoji(signal.priority)
            
            # Direction
            direction = "SHORT_HUNT (longs stopped)" if "SHORT" in str(metadata.get('direction', '')) else "LONG_HUNT (shorts stopped)"
            
            # Price zone - calculate levels from actual liquidation data
            price_zone = metadata.get('price_zone', (0, 0))
            zone_spread = abs(price_zone[1] - price_zone[0]) if price_zone[1] > 0 else 0
            is_short_hunt = "SHORT" in str(metadata.get('direction', ''))

            if is_short_hunt:
                # Longs got stopped, price akan naik → LONG entry
                entry_low = price_zone[1]
                entry_high = price_zone[1] + (zone_spread * 0.1)
                sl = price_zone[0] - (zone_spread * 0.3)
                risk = max(entry_low - sl, 1)
                target1 = entry_low + (risk * 2)
                target2 = entry_low + (risk * 3)
            else:
                # Shorts got stopped, price akan turun → SHORT entry
                entry_high = price_zone[0]
                entry_low = price_zone[0] - (zone_spread * 0.1)
                sl = price_zone[1] + (zone_spread * 0.3)
                risk = max(sl - entry_high, 1)
                target1 = entry_high - (risk * 2)
                target2 = entry_high - (risk * 3)

            entry_mid = (entry_low + entry_high) / 2 if entry_low > 0 else 1
            t1_pct = abs(target1 - entry_mid) / entry_mid * 100
            t2_pct = abs(target2 - entry_mid) / entry_mid * 100
            risk_pct = risk / entry_mid * 100

            # Absorption context
            absorption_vol = metadata.get('absorption_volume', 0)
            total_vol = metadata.get('total_volume', 1)
            absorption_pct = (absorption_vol / total_vol * 100) if total_vol > 0 else 0

            # Track record from metadata (populated by signal_tracker)
            track_line = ""
            track = metadata.get('track_record', {})
            if track.get('total', 0) > 0:
                track_line = f"\n📈 *Track Record*: {track['wins']}W/{track['losses']}L ({track['win_rate']:.0f}%)"

            message = f"""{priority_emoji} *STOP HUNT DETECTED* - {signal.symbol}

📊 *Liquidations*: ${metadata.get('total_volume', 0)/1_000_000:.1f}M cleared
• Direction: {direction}
• Count: {metadata.get('liquidation_count', 0)} liquidations
• Zone: ${price_zone[0]:,.0f} - ${price_zone[1]:,.0f}

🐋 *Whale Absorption*: ${absorption_vol/1_000:.0f}K ({absorption_pct:.0f}% of cascade)

💡 *TRADING SETUP* {'📈 LONG' if is_short_hunt else '📉 SHORT'}
Entry: ${entry_low:,.0f} - ${entry_high:,.0f}
Stop Loss: ${sl:,.0f} ({risk_pct:.1f}% risk)
Target 1: ${target1:,.0f} ({'+' if is_short_hunt else '-'}{t1_pct:.1f}%) R:R 1:2
Target 2: ${target2:,.0f} ({'+' if is_short_hunt else '-'}{t2_pct:.1f}%) R:R 1:3{track_line}

🎯 Confidence: {signal.confidence:.0f}%
⏰ {datetime.now().strftime('%H:%M:%S')} UTC"""
            
            return message
            
        except Exception as e:
            self.logger.error(f"Stop hunt formatting failed: {e}")
            return self.format_generic(signal)
    
    def format_order_flow(self, signal: Any) -> str:
        """
        Format order flow signal (accumulation/distribution)
        
        Example output:
        🟢 ETHUSDT - WHALE ACCUMULATION
        
        📈 *5min Analysis*
        
        Buy Volume: $2.2M (72%)
        ████████████████░░░░░░░░
        
        Sell Volume: $850K (28%)
        ████████░░░░░░░░░░░░░░░
        
        🐋 *Whale Activity*
        • Large Buys: 15 orders >$10K
        • Large Sells: 5 orders >$10K
        
        📊 Net Delta: +$1.35M (BULLISH)
        
        💡 Signal: Strong accumulation
        
        🎯 Confidence: 78%
        ⏰ 12:05:23 UTC
        """
        try:
            metadata = signal.metadata.get('order_flow', {})
            
            # Priority and type emoji
            if signal.signal_type == "ACCUMULATION":
                type_emoji = "🟢"
                signal_desc = "WHALE ACCUMULATION"
                delta_label = "BULLISH"
            else:
                type_emoji = "🔴"
                signal_desc = "WHALE DISTRIBUTION"
                delta_label = "BEARISH"
            
            buy_ratio = metadata.get('buy_ratio', 0.5)
            buy_pct = buy_ratio * 100
            sell_pct = (1 - buy_ratio) * 100
            
            # Get actual buy/sell volumes
            buy_vol = metadata.get('buy_volume', 0)
            sell_vol = metadata.get('sell_volume', 0)
            net_delta = metadata.get('net_delta', 0)

            # Progress bars
            buy_bar = self.create_progress_bar(buy_pct, 20)
            sell_bar = self.create_progress_bar(sell_pct, 20)

            message = f"""{type_emoji} *{signal.symbol}* - {signal_desc}

📈 *5min Analysis*

Buy Volume: ${buy_vol/1000:.0f}K ({buy_pct:.0f}%)
{buy_bar}

Sell Volume: ${sell_vol/1000:.0f}K ({sell_pct:.0f}%)
{sell_bar}

🐋 *Whale Activity*
• Large Buys: {metadata.get('large_buys', 0)} orders >$10K
• Large Sells: {metadata.get('large_sells', 0)} orders >$10K

📊 Net Delta: ${net_delta/1000:+.0f}K ({delta_label})

💡 Signal: Strong {'accumulation' if signal.signal_type == 'ACCUMULATION' else 'distribution'}

🎯 Confidence: {signal.confidence:.0f}%
⏰ {datetime.now().strftime('%H:%M:%S')} UTC"""
            
            return message
            
        except Exception as e:
            self.logger.error(f"Order flow formatting failed: {e}")
            return self.format_generic(signal)
    
    def format_event(self, signal: Any) -> str:
        """
        Format event signal
        
        Example output:
        ⚡ BTCUSDT - MARKET EVENT
        
        🔔 Liquidation Cascade
        $8.0M in 30 seconds
        
        🔔 Whale Accumulation
        12 large buy orders detected
        
        💡 Multiple events detected
        
        🎯 Confidence: 85%
        ⏰ 12:05:23 UTC
        """
        try:
            events = signal.metadata.get('events', [])
            
            event_lines = []
            for event in events[:3]:  # Max 3 events
                event_lines.append(f"🔔 {event.get('type', 'Unknown').replace('_', ' ').title()}")
                event_lines.append(event.get('description', ''))
                event_lines.append("")
            
            message = f"""⚡ *{signal.symbol}* - MARKET EVENTS

{chr(10).join(event_lines)}
💡 {len(events)} event{'s' if len(events) > 1 else ''} detected

🎯 Confidence: {signal.confidence:.0f}%
⏰ {datetime.now().strftime('%H:%M:%S')} UTC"""
            
            return message
            
        except Exception as e:
            self.logger.error(f"Event formatting failed: {e}")
            return self.format_generic(signal)
    
    def format_generic(self, signal: Any) -> str:
        """Generic fallback formatter"""
        priority_emoji = self.get_priority_emoji(signal.priority)
        
        return f"""{priority_emoji} *{signal.symbol}* - {signal.signal_type}

Direction: {signal.direction}
Sources: {', '.join(signal.sources)}

🎯 Confidence: {signal.confidence:.0f}%
⏰ {datetime.now().strftime('%H:%M:%S')} UTC"""
    
    def format_error(self, signal: Any) -> str:
        """Format error message"""
        return f"""⚠️ *Signal Formatting Error*

Symbol: {getattr(signal, 'symbol', 'Unknown')}
Type: {getattr(signal, 'signal_type', 'Unknown')}

Please check logs for details."""
    
    def get_priority_emoji(self, priority: int) -> str:
        """
        Get emoji based on priority
        
        Args:
            priority: Priority level (1-3)
            
        Returns:
            Emoji string
        """
        if priority == 1:
            return "🔴"  # Urgent
        elif priority == 2:
            return "🟡"  # Watch
        else:
            return "🔵"  # Info
    
    def create_progress_bar(self, percentage: float, length: int = 20) -> str:
        """
        Create visual progress bar
        
        Args:
            percentage: Percentage value (0-100)
            length: Total length of bar
            
        Returns:
            Progress bar string
        """
        filled = int(length * percentage / 100)
        filled = max(0, min(filled, length))
        return "█" * filled + "░" * (length - filled)
    
    def get_stats(self) -> dict:
        """Get formatter statistics"""
        return {
            "messages_formatted": self._messages_formatted
        }
