# Logger - Centralized Logging System
# TODO: Implement logging configuration

"""
Logger Module

Responsibilities:
- Setup centralized logging
- Configure log levels
- Configure log handlers (file, console)
- Log formatting
"""

import logging
import sys
from pathlib import Path

def setup_logger(name: str, level: str = "INFO", log_file: str = None):
    """
    Setup logger with console and file handlers
    
    Args:
        name: Logger name
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional log file path
        
    Returns:
        Configured logger instance
    """
    
    # Create logger
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper()))
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # File handler (optional)
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    return logger

# Create default logger
default_logger = setup_logger("teleglas", "INFO", "logs/teleglas.log")
