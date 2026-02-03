# Logger - Centralized Logging System
# FIXED: Implemented singleton pattern to prevent duplicate handlers

"""
Logger Module

Responsibilities:
- Setup centralized logging with singleton pattern
- Configure log levels
- Configure log handlers (file, console)
- Log formatting
- Prevent duplicate handler registration

FIXES:
- Bug #1: Added singleton pattern to prevent duplicate handlers
- Added handler cleanup on exit
- Check for existing handlers before adding new ones
"""

import logging
import sys
import atexit
from pathlib import Path
from logging.handlers import RotatingFileHandler

# Global registry to track configured loggers
_configured_loggers = {}

def setup_logger(name: str = "teleglas", level: str = "INFO", log_file: str = None):
    """
    Setup logger with console and file handlers (singleton pattern)
    
    CRITICAL FIX: Returns existing logger if already configured to prevent
    duplicate handlers that cause log spam and disk space issues.
    
    Args:
        name: Logger name
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional log file path
        
    Returns:
        Configured logger instance (existing or new)
    """
    
    # CRITICAL FIX: Return existing logger if already configured
    if name in _configured_loggers:
        return _configured_loggers[name]
    
    # Create logger
    logger = logging.getLogger(name)
    
    # CRITICAL FIX: Check if handlers already exist
    if logger.handlers:
        _configured_loggers[name] = logger
        return logger
    
    logger.setLevel(getattr(logging, level.upper()))
    logger.propagate = False  # Prevent propagation to root logger
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)
    logger.addHandler(console_handler)
    
    # File handler with rotation (if specified)
    if log_file:
        # Ensure log directory exists
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Use RotatingFileHandler to prevent log files from growing too large
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10485760,  # 10MB
            backupCount=5,
            encoding='utf-8'
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.DEBUG)  # File gets all levels
        logger.addHandler(file_handler)
    
    # Register cleanup on exit
    def cleanup_handlers():
        """Close all handlers properly to prevent resource leaks."""
        for handler in logger.handlers[:]:
            try:
                handler.close()
                logger.removeHandler(handler)
            except Exception:
                pass  # Ignore errors during cleanup
    
    atexit.register(cleanup_handlers)
    
    # Register this logger as configured
    _configured_loggers[name] = logger
    
    return logger

def get_logger(name: str = "teleglas"):
    """
    Get existing logger or create new one if not exists.
    
    Args:
        name: Logger name
        
    Returns:
        Logger instance
    """
    if name in _configured_loggers:
        return _configured_loggers[name]
    return setup_logger(name)

# Create default logger (only once)
default_logger = setup_logger("teleglas", "INFO", "logs/teleglas.log")
