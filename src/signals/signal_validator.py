# Signal Validator - Anti-Spam & Quality Control
# Production-ready signal validation with comprehensive filtering

"""
Signal Validator Module

Responsibilities:
- Prevent duplicate signals (same symbol+type within 5min)
- Rate limiting (max signals per hour)
- Minimum confidence threshold enforcement
- Cooldown period management
- Quality control

Algorithm:
1. Check minimum confidence threshold
2. Check if signal is duplicate
3. Check cooldown period for symbol+type
4. Check rate limit
5. If all pass, approve and add cooldown
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import hashlib

from ..utils.logger import setup_logger

class SignalValidator:
    """
    Production-ready signal validator
    
    Prevents spam and ensures signal quality through:
    - Duplicate detection
    - Rate limiting
    - Confidence thresholds
    - Cooldown management
    
    Features:
    - Anti-spam protection
    - Quality control
    - Statistics tracking
    - Configurable limits
    """
    
    def __init__(
        self, 
        max_signals_per_hour: int = 50, 
        min_confidence: float = 65.0,
        cooldown_minutes: int = 5
    ):
        """
        Initialize signal validator
        
        Args:
            max_signals_per_hour: Maximum signals allowed per hour
            min_confidence: Minimum confidence threshold
            cooldown_minutes: Cooldown period between same signals
        """
        self.max_signals_per_hour = max_signals_per_hour
        self.min_confidence = min_confidence
        self.cooldown_minutes = cooldown_minutes
        
        self.logger = setup_logger("SignalValidator", "INFO")
        
        # Track recent signals for rate limiting
        self.recent_signals: List[datetime] = []
        
        # Track cooldowns per signal key
        self.signal_cooldowns: Dict[str, datetime] = {}
        
        # Track signal hashes to prevent exact duplicates
        self.recent_hashes: Dict[str, datetime] = {}
        
        # Statistics
        self._total_validated = 0
        self._total_approved = 0
        self._total_rejected = 0
        self._rejection_reasons: Dict[str, int] = {
            "low_confidence": 0,
            "duplicate": 0,
            "cooldown": 0,
            "rate_limit": 0
        }
        
    def validate(self, signal) -> Tuple[bool, Optional[str]]:
        """
        Validate if signal should be sent
        
        Validation rules:
        1. Confidence >= minimum threshold
        2. Not exact duplicate
        3. Not in cooldown period
        4. Not rate limited
        
        Args:
            signal: TradingSignal to validate
            
        Returns:
            (is_valid, rejection_reason) tuple
        """
        self._total_validated += 1
        
        try:
            # Rule 1: Check confidence threshold
            if signal.confidence < self.min_confidence:
                self._total_rejected += 1
                self._rejection_reasons["low_confidence"] += 1
                reason = f"Confidence {signal.confidence:.1f}% below threshold {self.min_confidence}%"
                self.logger.debug(f"❌ Rejected: {reason}")
                return (False, reason)
            
            # Rule 2: Check for exact duplicate
            signal_hash = self.generate_signal_hash(signal)
            if self.is_duplicate(signal_hash):
                self._total_rejected += 1
                self._rejection_reasons["duplicate"] += 1
                reason = "Exact duplicate signal"
                self.logger.debug(f"❌ Rejected: {reason}")
                return (False, reason)
            
            # Rule 3: Check cooldown period
            signal_key = self.generate_signal_key(signal)
            if self.is_in_cooldown(signal_key):
                self._total_rejected += 1
                self._rejection_reasons["cooldown"] += 1
                cooldown_until = self.signal_cooldowns[signal_key]
                remaining = (cooldown_until - datetime.now()).total_seconds() / 60
                reason = f"In cooldown (remaining: {remaining:.1f} min)"
                self.logger.debug(f"❌ Rejected: {reason}")
                return (False, reason)
            
            # Rule 4: Check rate limit
            if self.is_rate_limited():
                self._total_rejected += 1
                self._rejection_reasons["rate_limit"] += 1
                reason = f"Rate limit exceeded ({self.max_signals_per_hour}/hour)"
                self.logger.warning(f"⚠️ Rejected: {reason}")
                return (False, reason)
            
            # All checks passed - approve signal
            self._approve_signal(signal, signal_key, signal_hash)
            self._total_approved += 1
            
            self.logger.info(
                f"✅ Signal approved: {signal.symbol} {signal.signal_type} "
                f"({signal.confidence:.1f}%)"
            )
            
            return (True, None)
            
        except Exception as e:
            self.logger.error(f"Validation error: {e}")
            return (False, f"Validation error: {str(e)}")
    
    def generate_signal_key(self, signal) -> str:
        """
        Generate unique key for signal cooldown tracking
        
        Key format: symbol_signaltype_direction
        e.g., "BTCUSDT_STOP_HUNT_LONG"
        
        Args:
            signal: TradingSignal
            
        Returns:
            Signal key string
        """
        return f"{signal.symbol}_{signal.signal_type}_{signal.direction}"
    
    def generate_signal_hash(self, signal) -> str:
        """
        Generate hash of signal to detect exact duplicates
        
        Includes: symbol, type, direction, confidence (rounded)
        
        Args:
            signal: TradingSignal
            
        Returns:
            Signal hash string
        """
        # Bucket confidence into 5% bands for consistent dedup
        # e.g. 73.2% and 76.8% both → 75, so treated as same signal
        confidence_band = round(signal.confidence / 5) * 5
        hash_input = f"{signal.symbol}_{signal.signal_type}_{signal.direction}_{confidence_band}"
        return hashlib.md5(hash_input.encode()).hexdigest()
    
    def is_duplicate(self, signal_hash: str) -> bool:
        """
        Check if signal hash was recently seen
        
        Args:
            signal_hash: Hash of signal
            
        Returns:
            True if duplicate, False otherwise
        """
        # Clean old hashes (older than 10 minutes)
        cutoff = datetime.now() - timedelta(minutes=10)
        self.recent_hashes = {
            h: t for h, t in self.recent_hashes.items()
            if t > cutoff
        }
        
        # Check if hash exists
        if signal_hash in self.recent_hashes:
            return True
        
        return False
    
    def is_in_cooldown(self, signal_key: str) -> bool:
        """
        Check if signal key is in cooldown period
        
        Args:
            signal_key: Key identifying signal type for symbol
            
        Returns:
            True if in cooldown, False otherwise
        """
        # Clean expired cooldowns
        now = datetime.now()
        self.signal_cooldowns = {
            k: t for k, t in self.signal_cooldowns.items()
            if t > now
        }
        
        cooldown_until = self.signal_cooldowns.get(signal_key)
        if cooldown_until and now < cooldown_until:
            return True
        
        return False
    
    def is_rate_limited(self) -> bool:
        """
        Check if rate limit is exceeded
        
        Returns:
            True if rate limited, False otherwise
        """
        # Clean signals older than 1 hour
        cutoff = datetime.now() - timedelta(hours=1)
        self.recent_signals = [
            t for t in self.recent_signals
            if t > cutoff
        ]
        
        # Check count
        if len(self.recent_signals) >= self.max_signals_per_hour:
            return True
        
        return False
    
    def _approve_signal(self, signal, signal_key: str, signal_hash: str):
        """
        Approve signal and update tracking
        
        Args:
            signal: Approved signal
            signal_key: Signal key
            signal_hash: Signal hash
        """
        now = datetime.now()
        
        # Add to rate limit tracking
        self.recent_signals.append(now)
        
        # Add cooldown
        self.signal_cooldowns[signal_key] = now + timedelta(minutes=self.cooldown_minutes)
        
        # Add hash
        self.recent_hashes[signal_hash] = now
    
    def get_remaining_quota(self) -> int:
        """
        Get remaining signal quota for current hour
        
        Returns:
            Number of signals remaining
        """
        # Clean old signals
        cutoff = datetime.now() - timedelta(hours=1)
        self.recent_signals = [
            t for t in self.recent_signals
            if t > cutoff
        ]
        
        return max(0, self.max_signals_per_hour - len(self.recent_signals))
    
    def get_cooldown_remaining(self, symbol: str, signal_type: str, direction: str) -> Optional[float]:
        """
        Get remaining cooldown time for specific signal
        
        Args:
            symbol: Trading symbol
            signal_type: Signal type
            direction: Signal direction
            
        Returns:
            Remaining minutes in cooldown, or None if not in cooldown
        """
        signal_key = f"{symbol}_{signal_type}_{direction}"
        cooldown_until = self.signal_cooldowns.get(signal_key)
        
        if not cooldown_until:
            return None
        
        now = datetime.now()
        if now >= cooldown_until:
            return None
        
        remaining = (cooldown_until - now).total_seconds() / 60
        return remaining
    
    def reset_cooldown(self, symbol: str, signal_type: str, direction: str):
        """
        Manually reset cooldown for specific signal
        
        Args:
            symbol: Trading symbol
            signal_type: Signal type
            direction: Signal direction
        """
        signal_key = f"{symbol}_{signal_type}_{direction}"
        if signal_key in self.signal_cooldowns:
            del self.signal_cooldowns[signal_key]
            self.logger.info(f"Reset cooldown for {signal_key}")
    
    def reset_all_cooldowns(self):
        """Reset all cooldowns"""
        self.signal_cooldowns.clear()
        self.logger.info("Reset all cooldowns")
    
    def get_stats(self) -> dict:
        """Get validator statistics"""
        return {
            "total_validated": self._total_validated,
            "total_approved": self._total_approved,
            "total_rejected": self._total_rejected,
            "approval_rate": self._total_approved / max(self._total_validated, 1),
            "rejection_reasons": self._rejection_reasons.copy(),
            "current_quota_remaining": self.get_remaining_quota(),
            "active_cooldowns": len(self.signal_cooldowns),
            "settings": {
                "max_signals_per_hour": self.max_signals_per_hour,
                "min_confidence": self.min_confidence,
                "cooldown_minutes": self.cooldown_minutes
            }
        }
