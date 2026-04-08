# Telegram Router — 3-Tier Alert Routing
# Routes alerts to different Telegram bots based on signal tier/confidence

"""
Telegram Router Module

3-tier alert system:
  Tier 3 (CONFIRMED) — Existing bot, proven signals (confidence >= 72, passed all filters)
  Tier 2 (EARLY)     — Stealth accumulation, CVD divergence (Phase 2 detectors)
  Tier 1 (WARNING)   — Exhaustion, climactic sell/buy (Phase 2 detectors)

Each tier has its own Telegram bot to avoid rate-limit interference.
"""

import asyncio
from typing import Optional
from .telegram_bot import TelegramBot
from ..utils.logger import setup_logger


class TelegramRouter:
    """
    Routes alerts to the correct Telegram bot based on tier.

    Tier 3 = confirmed signals (existing bot)
    Tier 2 = early signals (radar bot)
    Tier 1 = warnings (sniper bot)
    """

    TIER_CONFIRMED = 3
    TIER_EARLY = 2
    TIER_WARNING = 1

    def __init__(self, telegram_config: dict, rate_limit_delay: float = 3.0):
        self.logger = setup_logger("TelegramRouter", "INFO")
        self.bots: dict[int, Optional[TelegramBot]] = {}

        # Tier 3 — Confirmed (existing bot, required)
        if telegram_config.get('enabled'):
            self.bots[self.TIER_CONFIRMED] = TelegramBot(
                bot_token=telegram_config['bot_token'],
                chat_id=telegram_config['chat_id'],
                rate_limit_delay=rate_limit_delay,
            )
            self.logger.info("Tier 3 (CONFIRMED) bot initialized")

        # Tier 2 — Early (optional)
        t2_token = telegram_config.get('tier2_bot_token', '')
        t2_chat = telegram_config.get('tier2_chat_id', '')
        if t2_token and t2_chat:
            self.bots[self.TIER_EARLY] = TelegramBot(
                bot_token=t2_token,
                chat_id=t2_chat,
                rate_limit_delay=rate_limit_delay,
            )
            self.logger.info("Tier 2 (EARLY) bot initialized")

        # Tier 1 — Warning (optional)
        t1_token = telegram_config.get('tier1_bot_token', '')
        t1_chat = telegram_config.get('tier1_chat_id', '')
        if t1_token and t1_chat:
            self.bots[self.TIER_WARNING] = TelegramBot(
                bot_token=t1_token,
                chat_id=t1_chat,
                rate_limit_delay=rate_limit_delay,
            )
            self.logger.info("Tier 1 (WARNING) bot initialized")

        self.logger.info(f"TelegramRouter ready — {len(self.bots)} tier(s) active")

    async def send_alert(self, message: str, tier: int = TIER_CONFIRMED) -> bool:
        """
        Send alert to the appropriate tier bot.

        Falls back to Tier 3 bot if requested tier not configured.
        """
        bot = self.bots.get(tier) or self.bots.get(self.TIER_CONFIRMED)
        if not bot:
            self.logger.warning(f"No bot available for tier {tier}")
            return False

        tier_label = {1: "WARNING", 2: "EARLY", 3: "CONFIRMED"}.get(tier, "?")
        self.logger.debug(f"Routing to Tier {tier} ({tier_label})")
        return await bot.send_alert(message)

    async def send_to_all(self, message: str) -> dict[int, bool]:
        """Send message to all configured tier bots (e.g. system status)."""
        results = {}
        for tier, bot in self.bots.items():
            results[tier] = await bot.send_alert(message)
        return results

    async def test_connection(self) -> dict[int, bool]:
        """Test connection for all tier bots."""
        results = {}
        for tier, bot in self.bots.items():
            results[tier] = await bot.test_connection()
        return results

    async def close(self):
        """Close all bot sessions."""
        for bot in self.bots.values():
            await bot.close()

    def get_stats(self) -> dict:
        """Get combined stats from all bots."""
        stats = {}
        for tier, bot in self.bots.items():
            tier_label = {1: "warning", 2: "early", 3: "confirmed"}.get(tier, f"tier{tier}")
            stats[tier_label] = bot.get_stats()
        return stats

    @property
    def enabled(self) -> bool:
        return len(self.bots) > 0

    # Backward compatibility: expose Tier 3 bot as default
    @property
    def telegram_bot(self) -> Optional[TelegramBot]:
        return self.bots.get(self.TIER_CONFIRMED)
