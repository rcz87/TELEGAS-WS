# Prometheus Metrics - Exposes system stats for Grafana/Prometheus scraping

"""
Metrics Module

Reads from existing system_state and sub-module get_stats() dicts.
Does NOT modify any existing logic — purely observational.

Usage:
    from src.utils.metrics import metrics
    metrics.update_from_stats(system_state["stats"])
    metrics.update_from_module_stats(module_stats_dict)
"""

from prometheus_client import Counter, Gauge, Histogram, CollectorRegistry, generate_latest

# Dedicated registry (avoids conflicts with default process metrics)
registry = CollectorRegistry()

# ── Core pipeline counters ──────────────────────────────────────────────

messages_received = Counter(
    "teleglas_messages_received_total",
    "Total WebSocket messages received",
    registry=registry,
)
messages_processed = Counter(
    "teleglas_messages_processed_total",
    "Total messages processed through pipeline",
    registry=registry,
)
liquidations_processed = Counter(
    "teleglas_liquidations_processed_total",
    "Total liquidation events processed",
    registry=registry,
)
trades_processed = Counter(
    "teleglas_trades_processed_total",
    "Total trade events processed",
    registry=registry,
)
signals_generated = Counter(
    "teleglas_signals_generated_total",
    "Total trading signals generated",
    registry=registry,
)
alerts_sent = Counter(
    "teleglas_alerts_sent_total",
    "Total Telegram alerts sent",
    registry=registry,
)
errors_total = Counter(
    "teleglas_errors_total",
    "Total errors across all components",
    registry=registry,
)

# ── System gauges ───────────────────────────────────────────────────────

uptime_seconds = Gauge(
    "teleglas_uptime_seconds",
    "System uptime in seconds",
    registry=registry,
)
coins_tracked = Gauge(
    "teleglas_coins_tracked",
    "Number of coins currently tracked",
    registry=registry,
)
active_websocket_connections = Gauge(
    "teleglas_websocket_connections_active",
    "Active WebSocket dashboard connections",
    registry=registry,
)

# ── REST poller gauges ──────────────────────────────────────────────────

rest_polls_completed = Counter(
    "teleglas_rest_polls_completed_total",
    "Total REST API poll cycles completed",
    registry=registry,
)
rest_errors = Counter(
    "teleglas_rest_errors_total",
    "Total REST API errors",
    registry=registry,
)
rest_oi_fetches = Counter(
    "teleglas_rest_oi_fetches_total",
    "Total OI data fetches",
    registry=registry,
)
rest_funding_fetches = Counter(
    "teleglas_rest_funding_fetches_total",
    "Total funding rate fetches",
    registry=registry,
)
rest_cvd_fetches = Counter(
    "teleglas_rest_cvd_fetches_total",
    "Total CVD fetches (spot + futures)",
    registry=registry,
)
rest_whale_fetches = Counter(
    "teleglas_rest_whale_fetches_total",
    "Total whale alert fetches",
    registry=registry,
)

# ── Signal quality gauges ───────────────────────────────────────────────

signal_win_rate = Gauge(
    "teleglas_signal_win_rate",
    "Overall signal win rate (0-100)",
    registry=registry,
)
signal_pending = Gauge(
    "teleglas_signal_pending",
    "Signals awaiting outcome evaluation",
    registry=registry,
)
signal_completed = Gauge(
    "teleglas_signal_completed",
    "Signals with recorded outcomes",
    registry=registry,
)

# ── Validator gauges ────────────────────────────────────────────────────

validator_approved = Counter(
    "teleglas_validator_approved_total",
    "Total signals approved by validator",
    registry=registry,
)
validator_rejected = Counter(
    "teleglas_validator_rejected_total",
    "Total signals rejected by validator",
    registry=registry,
)

# ── Market context filter gauges ────────────────────────────────────────

filter_favorable = Counter(
    "teleglas_filter_favorable_total",
    "Signals with favorable market context",
    registry=registry,
)
filter_unfavorable = Counter(
    "teleglas_filter_unfavorable_total",
    "Signals with unfavorable market context (blocked)",
    registry=registry,
)
filter_cvd_vetoed = Counter(
    "teleglas_filter_cvd_vetoed_total",
    "Signals vetoed by CVD check",
    registry=registry,
)
filter_whale_vetoed = Counter(
    "teleglas_filter_whale_vetoed_total",
    "Signals vetoed by whale check",
    registry=registry,
)

# ── Buffer gauges ───────────────────────────────────────────────────────

buffer_symbols_tracked = Gauge(
    "teleglas_buffer_symbols_tracked",
    "Unique symbols in buffer manager",
    registry=registry,
)
buffer_liquidations = Gauge(
    "teleglas_buffer_liquidations_count",
    "Current liquidation records in buffers",
    registry=registry,
)
buffer_trades = Gauge(
    "teleglas_buffer_trades_count",
    "Current trade records in buffers",
    registry=registry,
)

# ── Context buffer gauges ───────────────────────────────────────────────

context_oi_symbols = Gauge(
    "teleglas_context_oi_symbols",
    "Symbols with OI data tracked",
    registry=registry,
)
context_cvd_symbols = Gauge(
    "teleglas_context_cvd_symbols",
    "Symbols with CVD data tracked",
    registry=registry,
)
context_whale_positions = Gauge(
    "teleglas_context_whale_positions",
    "Total whale positions tracked",
    registry=registry,
)


# ── Snapshot updater ────────────────────────────────────────────────────

# Track previous counter values to compute increments
_prev = {}


def _inc_counter(counter, key, new_value):
    """Increment a Prometheus Counter by the delta since last update."""
    prev = _prev.get(key, 0)
    delta = new_value - prev
    if delta > 0:
        counter.inc(delta)
    _prev[key] = new_value


def update_from_stats(stats: dict):
    """Update core metrics from system_state['stats'] dict."""
    _inc_counter(messages_received, "messages_received", stats.get("messages_received", 0))
    _inc_counter(messages_processed, "messages_processed", stats.get("messages_processed", 0))
    _inc_counter(liquidations_processed, "liquidations_processed", stats.get("liquidations_processed", 0))
    _inc_counter(trades_processed, "trades_processed", stats.get("trades_processed", 0))
    _inc_counter(signals_generated, "signals_generated", stats.get("signals_generated", 0))
    _inc_counter(alerts_sent, "alerts_sent", stats.get("alerts_sent", 0))
    _inc_counter(errors_total, "errors", stats.get("errors", 0))
    uptime_seconds.set(stats.get("uptime_seconds", 0))


def update_from_module_stats(module_stats: dict):
    """Update metrics from aggregated sub-module stats dict."""

    # REST poller
    rp = module_stats.get("rest_poller", {})
    _inc_counter(rest_polls_completed, "rp_polls", rp.get("polls_completed", 0))
    _inc_counter(rest_errors, "rp_errors", rp.get("errors", 0))
    _inc_counter(rest_oi_fetches, "rp_oi", rp.get("oi_fetches", 0))
    _inc_counter(rest_funding_fetches, "rp_funding", rp.get("funding_fetches", 0))
    _inc_counter(rest_cvd_fetches, "rp_cvd",
                 rp.get("spot_cvd_fetches", 0) + rp.get("futures_cvd_fetches", 0))
    _inc_counter(rest_whale_fetches, "rp_whale", rp.get("whale_fetches", 0))

    # Signal tracker
    tr = module_stats.get("tracker", {})
    signal_win_rate.set(tr.get("overall_win_rate", 0))
    signal_pending.set(tr.get("pending", 0))
    signal_completed.set(tr.get("completed", 0))

    # Validator
    va = module_stats.get("validator", {})
    _inc_counter(validator_approved, "va_approved", va.get("total_approved", 0))
    _inc_counter(validator_rejected, "va_rejected", va.get("total_rejected", 0))

    # Market context filter
    mf = module_stats.get("market_context_filter", {})
    _inc_counter(filter_favorable, "mf_favorable", mf.get("favorable", 0))
    _inc_counter(filter_unfavorable, "mf_unfavorable", mf.get("unfavorable", 0))
    _inc_counter(filter_cvd_vetoed, "mf_cvd_vetoed", mf.get("cvd_vetoed", 0))
    _inc_counter(filter_whale_vetoed, "mf_whale_vetoed", mf.get("whale_vetoed", 0))

    # Buffer manager
    bm = module_stats.get("buffer_manager", {})
    buffer_symbols_tracked.set(bm.get("symbols_tracked", 0))
    buffer_liquidations.set(bm.get("liquidations_in_buffers", 0))
    buffer_trades.set(bm.get("trades_in_buffers", 0))

    # Market context buffer
    cb = module_stats.get("market_context_buffer", {})
    context_oi_symbols.set(cb.get("oi_symbols_tracked", 0))
    context_cvd_symbols.set(cb.get("cvd_symbols_tracked", 0))
    context_whale_positions.set(cb.get("total_whale_positions", 0))


def generate_metrics() -> bytes:
    """Generate Prometheus text exposition format."""
    return generate_latest(registry)
