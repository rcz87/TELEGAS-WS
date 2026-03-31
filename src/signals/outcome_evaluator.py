# Outcome Evaluator - Rich signal outcome evaluation
#
# Replaces simple WIN/LOSS binary with richer metrics:
#   - MFE (Maximum Favorable Excursion): best price reached in signal direction
#   - MAE (Maximum Adverse Excursion): worst price reached against signal
#   - time_to_resolution: how long until outcome was clear
#   - pnl_pct: actual P&L percentage
#   - excursion_ratio: MFE / MAE (higher = cleaner setup)
#   - outcome: WIN / LOSS / NEUTRAL / PARTIAL
#
# Used by signal_tracker to produce richer feedback for confidence_scorer.

from dataclasses import dataclass
from typing import Optional


@dataclass(slots=True)
class OutcomeResult:
    """Rich outcome evaluation result."""
    outcome: str              # WIN, LOSS, NEUTRAL, PARTIAL
    pnl_pct: float            # Actual P&L %
    mfe_pct: float            # Max Favorable Excursion % from entry
    mae_pct: float            # Max Adverse Excursion % from entry (always positive)
    excursion_ratio: float    # MFE / MAE (higher = cleaner setup, 0 if MAE=0)
    time_to_resolution: float # Seconds from signal to evaluation
    hit_target: bool          # Did price reach target at any point?
    hit_sl: bool              # Did price reach stop loss at any point?


def evaluate_outcome(
    direction: str,
    entry_price: float,
    exit_price: float,
    target_price: float,
    stop_loss: float,
    high_since_entry: Optional[float] = None,
    low_since_entry: Optional[float] = None,
    signal_timestamp: float = 0,
    eval_timestamp: float = 0,
) -> OutcomeResult:
    """
    Evaluate signal outcome with rich metrics.

    Args:
        direction: "LONG" or "SHORT"
        entry_price: Price at signal generation
        exit_price: Current price at evaluation time
        target_price: Target price level
        stop_loss: Stop loss level
        high_since_entry: Highest price since signal (for MFE/MAE on LONG)
        low_since_entry: Lowest price since signal (for MFE/MAE on SHORT)
        signal_timestamp: Unix time when signal was created
        eval_timestamp: Unix time of evaluation

    Returns:
        OutcomeResult with rich metrics
    """
    if entry_price <= 0:
        return OutcomeResult("NEUTRAL", 0, 0, 0, 0, 0, False, False)

    time_to_resolution = eval_timestamp - signal_timestamp if eval_timestamp and signal_timestamp else 0

    if direction == "LONG":
        pnl_pct = (exit_price - entry_price) / entry_price * 100

        # MFE: best price above entry
        best = high_since_entry if high_since_entry and high_since_entry > entry_price else exit_price
        mfe_pct = max((best - entry_price) / entry_price * 100, 0)

        # MAE: worst price below entry
        worst = low_since_entry if low_since_entry and low_since_entry < entry_price else exit_price
        mae_pct = max((entry_price - worst) / entry_price * 100, 0)

        hit_target = best >= target_price
        hit_sl = worst <= stop_loss if stop_loss > 0 else False

        # Outcome classification
        if exit_price >= target_price:
            outcome = "WIN"
        elif exit_price <= stop_loss and stop_loss > 0:
            outcome = "LOSS"
        else:
            halfway = entry_price + (target_price - entry_price) * 0.5
            if exit_price >= halfway:
                outcome = "PARTIAL"
            elif exit_price < entry_price:
                outcome = "LOSS"
            else:
                outcome = "NEUTRAL"

    else:  # SHORT
        pnl_pct = (entry_price - exit_price) / entry_price * 100

        # MFE: best price below entry (for short)
        best = low_since_entry if low_since_entry and low_since_entry < entry_price else exit_price
        mfe_pct = max((entry_price - best) / entry_price * 100, 0)

        # MAE: worst price above entry (for short)
        worst = high_since_entry if high_since_entry and high_since_entry > entry_price else exit_price
        mae_pct = max((worst - entry_price) / entry_price * 100, 0)

        hit_target = best <= target_price
        hit_sl = worst >= stop_loss if stop_loss > 0 else False

        if exit_price <= target_price:
            outcome = "WIN"
        elif exit_price >= stop_loss and stop_loss > 0:
            outcome = "LOSS"
        else:
            halfway = entry_price - (entry_price - target_price) * 0.5
            if exit_price <= halfway:
                outcome = "PARTIAL"
            elif exit_price > entry_price:
                outcome = "LOSS"
            else:
                outcome = "NEUTRAL"

    excursion_ratio = mfe_pct / mae_pct if mae_pct > 0 else (10.0 if mfe_pct > 0 else 0.0)

    return OutcomeResult(
        outcome=outcome,
        pnl_pct=pnl_pct,
        mfe_pct=mfe_pct,
        mae_pct=mae_pct,
        excursion_ratio=excursion_ratio,
        time_to_resolution=time_to_resolution,
        hit_target=hit_target,
        hit_sl=hit_sl,
    )
