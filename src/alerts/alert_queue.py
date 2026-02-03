# Alert Queue - Manage Alert Queue
# TODO: Implement alert queue

"""
Alert Queue Module

Responsibilities:
- Manage alert queue
- Priority handling (URGENT > WATCH > INFO)
- FIFO with priority override
"""

import asyncio
from queue import PriorityQueue
from typing import Any, Tuple

class AlertQueue:
    """
    Manages alert queue with priority
    """
    
    def __init__(self):
        self.queue: PriorityQueue = PriorityQueue()
        self.priority_map = {
            "URGENT": 1,
            "WATCH": 2,
            "INFO": 3
        }
        
    def add(self, priority: str, alert: Any):
        """
        Add alert to queue
        
        Args:
            priority: URGENT, WATCH, or INFO
            alert: Alert data
        """
        priority_value = self.priority_map.get(priority, 3)
        self.queue.put((priority_value, alert))
    
    def get(self) -> Tuple[int, Any]:
        """Get next alert from queue"""
        return self.queue.get()
    
    def size(self) -> int:
        """Get queue size"""
        return self.queue.qsize()
    
    def is_empty(self) -> bool:
        """Check if queue is empty"""
        return self.queue.empty()
