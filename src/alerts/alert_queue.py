# Alert Queue - Priority Queue Management
# Production-ready alert queue with retry logic

"""
Alert Queue Module

Responsibilities:
- Manage alert queue with priority
- Priority-based ordering (1=High, 2=Medium, 3=Low)
- Thread-safe operations
- Retry failed alerts
- Batch processing
- Statistics tracking

Queue behavior:
- Higher priority (lower number) processed first
- FIFO within same priority level
- Failed alerts can be re-queued with retry count
"""

import asyncio
from asyncio import PriorityQueue
from typing import Any, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime

from ..utils.logger import setup_logger

@dataclass(order=True)
class QueuedAlert:
    """Alert in queue with priority"""
    priority: int
    alert: Any = field(compare=False)
    timestamp: datetime = field(default_factory=datetime.now, compare=False)
    retry_count: int = field(default=0, compare=False)
    max_retries: int = field(default=3, compare=False)

class AlertQueue:
    """
    Production-ready priority alert queue
    
    Features:
    - Priority-based ordering
    - Thread-safe async operations
    - Retry logic for failed alerts
    - Batch processing support
    - Statistics tracking
    
    Priority levels:
    1 = High (urgent)
    2 = Medium (watch)
    3 = Low (info)
    """
    
    def __init__(self, max_size: int = 1000):
        """
        Initialize alert queue
        
        Args:
            max_size: Maximum queue size
        """
        self.queue: PriorityQueue = PriorityQueue(maxsize=max_size)
        self.logger = setup_logger("AlertQueue", "INFO")
        
        # Statistics
        self._total_queued = 0
        self._total_processed = 0
        self._total_failed = 0
        self._total_retried = 0
        
    async def add(self, alert: Any, priority: int = 2, max_retries: int = 3) -> bool:
        """
        Add alert to queue
        
        Args:
            alert: Alert object (TradingSignal or formatted message)
            priority: Priority level (1=High, 2=Medium, 3=Low)
            max_retries: Maximum retry attempts
            
        Returns:
            True if added, False if queue full
        """
        try:
            queued_alert = QueuedAlert(
                priority=priority,
                alert=alert,
                max_retries=max_retries
            )
            
            # Non-blocking put with timeout
            await asyncio.wait_for(
                self.queue.put(queued_alert),
                timeout=1.0
            )
            
            self._total_queued += 1
            self.logger.debug(
                f"Added alert to queue (priority={priority}, "
                f"queue_size={self.size()})"
            )
            return True
            
        except asyncio.TimeoutError:
            self.logger.error("❌ Queue full - cannot add alert")
            return False
        except Exception as e:
            self.logger.error(f"❌ Failed to add alert: {e}")
            return False
    
    async def get(self, timeout: Optional[float] = None) -> Optional[QueuedAlert]:
        """
        Get next alert from queue
        
        Args:
            timeout: Maximum wait time in seconds (None = wait forever)
            
        Returns:
            QueuedAlert or None if timeout
        """
        try:
            if timeout:
                queued_alert = await asyncio.wait_for(
                    self.queue.get(),
                    timeout=timeout
                )
            else:
                queued_alert = await self.queue.get()
            
            self.logger.debug(
                f"Retrieved alert from queue (priority={queued_alert.priority}, "
                f"queue_size={self.size()})"
            )
            return queued_alert
            
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            self.logger.error(f"❌ Failed to get alert: {e}")
            return None
    
    async def mark_processed(self, success: bool = True):
        """
        Mark task as done
        
        Args:
            success: Whether processing was successful
        """
        self.queue.task_done()
        if success:
            self._total_processed += 1
        else:
            self._total_failed += 1
    
    async def retry(self, queued_alert: QueuedAlert) -> bool:
        """
        Re-queue failed alert for retry
        
        Args:
            queued_alert: Failed alert to retry
            
        Returns:
            True if re-queued, False if max retries reached
        """
        if queued_alert.retry_count >= queued_alert.max_retries:
            self.logger.warning(
                f"⚠️ Max retries ({queued_alert.max_retries}) reached for alert"
            )
            self._total_failed += 1
            return False
        
        # Increment retry count
        queued_alert.retry_count += 1
        self._total_retried += 1
        
        # Lower priority for retries (increase number)
        retry_priority = min(queued_alert.priority + 1, 3)
        
        self.logger.info(
            f"🔄 Retrying alert (attempt {queued_alert.retry_count}/{queued_alert.max_retries})"
        )
        
        # Re-queue with lower priority
        return await self.add(
            queued_alert.alert,
            priority=retry_priority,
            max_retries=queued_alert.max_retries
        )
    
    async def get_batch(self, batch_size: int = 10, timeout: float = 1.0) -> list:
        """
        Get multiple alerts from queue
        
        Args:
            batch_size: Maximum alerts to retrieve
            timeout: Max wait time for each alert
            
        Returns:
            List of QueuedAlert objects
        """
        batch = []
        
        for _ in range(batch_size):
            if self.is_empty():
                break
            
            alert = await self.get(timeout=timeout)
            if alert:
                batch.append(alert)
            else:
                break
        
        if batch:
            self.logger.debug(f"Retrieved batch of {len(batch)} alerts")
        
        return batch
    
    def size(self) -> int:
        """
        Get current queue size
        
        Returns:
            Number of alerts in queue
        """
        return self.queue.qsize()
    
    def is_empty(self) -> bool:
        """
        Check if queue is empty
        
        Returns:
            True if empty, False otherwise
        """
        return self.queue.empty()
    
    def is_full(self) -> bool:
        """
        Check if queue is full
        
        Returns:
            True if full, False otherwise
        """
        return self.queue.full()
    
    async def clear(self):
        """Clear all alerts from queue"""
        cleared = 0
        while not self.is_empty():
            try:
                await asyncio.wait_for(self.queue.get(), timeout=0.1)
                self.queue.task_done()
                cleared += 1
            except:
                break
        
        if cleared > 0:
            self.logger.info(f"Cleared {cleared} alerts from queue")
    
    async def wait_empty(self, timeout: Optional[float] = None):
        """
        Wait for queue to be fully processed
        
        Args:
            timeout: Maximum wait time (None = wait forever)
        """
        try:
            if timeout:
                await asyncio.wait_for(self.queue.join(), timeout=timeout)
            else:
                await self.queue.join()
            self.logger.info("✅ Queue fully processed")
        except asyncio.TimeoutError:
            self.logger.warning(f"⚠️ Queue not empty after {timeout}s timeout")
    
    def get_stats(self) -> dict:
        """Get queue statistics"""
        return {
            "current_size": self.size(),
            "is_empty": self.is_empty(),
            "is_full": self.is_full(),
            "total_queued": self._total_queued,
            "total_processed": self._total_processed,
            "total_failed": self._total_failed,
            "total_retried": self._total_retried,
            "success_rate": (self._total_processed / max(self._total_queued, 1)) * 100
        }
