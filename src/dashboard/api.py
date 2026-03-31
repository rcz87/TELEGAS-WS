# Bridge module — keeps main.py imports working
# main.py uses: from src.dashboard import api as dashboard_api
# The original api.py (Alpine.js dashboard) is in _legacy/
# The active browser dashboard is server.py (port 8082)
#
# This file re-exports everything main.py needs from the legacy API
# so the signal pipeline (WebSocket → Analyzer → Dashboard) keeps working.

import sys
from pathlib import Path

# Add _legacy to path so we can import the original api module
_legacy_dir = Path(__file__).parent / "_legacy"
if str(_legacy_dir) not in sys.path:
    sys.path.insert(0, str(_legacy_dir))

# Re-export everything from the legacy api.py
from api import *  # noqa: F401, F403
from api import (  # explicit re-exports for clarity
    app,
    system_state,
    state_lock,
    update_stats,
    update_order_flow,
    add_signal,
    initialize_coins,
    get_monitored_coins,
    get_pending_subscriptions,
    broadcast_update,
    verify_token,
    active_connections,
    set_lifecycle,
    set_calibration,
)
