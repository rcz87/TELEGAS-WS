#!/usr/bin/env python3
"""
Watch specific coins from TELEGAS-WS live state.
Usage: python3 watch_coins.py SOL BTC ETH AVAX
"""

import json
import sys
import time
import os
from datetime import datetime

STATE_FILE = "data/live_state.json"
REFRESH = 5  # seconds

def clear():
    os.system('clear')

def fmt_price(p):
    if p == 0: return "N/A"
    if p >= 1000: return f"${p:,.0f}"
    if p >= 1: return f"${p:.2f}"
    if p >= 0.01: return f"${p:.4f}"
    return f"${p:.6f}"

def fmt_usd(v):
    if v >= 1e9: return f"${v/1e9:.1f}B"
    if v >= 1e6: return f"${v/1e6:.1f}M"
    if v >= 1e3: return f"${v/1e3:.0f}K"
    return f"${v:.0f}"

def fmt_cvd(v):
    if abs(v) >= 1e6: return f"{v/1e6:+.1f}M"
    if abs(v) >= 1e3: return f"{v/1e3:+.0f}K"
    return f"{v:+.0f}"

def show(coins_filter):
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        print("Waiting for data...")
        return

    clear()
    ts = state.get("timestamp", "?")
    up = state.get("uptime_min", 0)

    print(f"{'='*70}")
    print(f"  TELEGLAS LIVE  |  {ts}  |  Uptime: {up:.0f}m")
    print(f"  Watching: {', '.join(coins_filter)}")
    print(f"{'='*70}")
    print()

    coins = state.get("coins", {})

    for sym in coins_filter:
        d = coins.get(sym)
        if not d:
            print(f"  {sym}: no data yet")
            print()
            continue

        price = d.get("price", 0)
        chg = d.get("change_24h", 0)
        bias = d.get("bias", "?")
        l_score = d.get("long_score", 0)
        s_score = d.get("short_score", 0)
        spot_cvd = d.get("spot_cvd", 0)
        spot_dir = d.get("spot_cvd_dir", "?")
        spot_slope = d.get("spot_cvd_slope", 0)
        fut_cvd = d.get("fut_cvd", 0)
        fut_dir = d.get("fut_cvd_dir", "?")
        oi = d.get("oi_usd", 0)
        oi_chg = d.get("oi_change_1h", 0)
        fr = d.get("funding_rate", 0) * 100
        ob_bid = d.get("ob_bid", 0)
        ob_ask = d.get("ob_ask", 0)
        ob_dom = d.get("ob_dominant", "?")

        # Bias indicator
        if bias == "LONG":
            bias_icon = "▲ LONG"
        elif bias == "SHORT":
            bias_icon = "▼ SHORT"
        else:
            bias_icon = "─ NEUTRAL"

        print(f"  ┌─ {sym} ── {fmt_price(price)} ({chg:+.1f}%) ── {bias_icon} L:{l_score:.0f}% S:{s_score:.0f}%")
        print(f"  │  SpotCVD : {fmt_cvd(spot_cvd)} {spot_dir} (slope:{fmt_cvd(spot_slope)}/5m)")
        print(f"  │  FutCVD  : {fmt_cvd(fut_cvd)} {fut_dir}")
        print(f"  │  OI      : {fmt_usd(oi)} ({oi_chg:+.1f}%)")
        print(f"  │  FR      : {fr:+.4f}%")
        print(f"  │  OB      : Bid {fmt_usd(ob_bid)} / Ask {fmt_usd(ob_ask)} [{ob_dom}]")
        print(f"  └{'─'*50}")
        print()

    print(f"  Refresh: {REFRESH}s | Ctrl+C to quit")

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 watch_coins.py SOL BTC ETH AVAX")
        print("       python3 watch_coins.py ALL")
        sys.exit(1)

    coins_filter = [c.upper() for c in sys.argv[1:]]

    if "ALL" in coins_filter:
        # Show all coins sorted by score
        coins_filter = None

    while True:
        try:
            if coins_filter is None:
                # ALL mode: read and sort
                with open(STATE_FILE) as f:
                    state = json.load(f)
                all_coins = state.get("coins", {})
                ranked = sorted(all_coins.keys(),
                    key=lambda s: max(all_coins[s].get("long_score",0), all_coins[s].get("short_score",0)),
                    reverse=True)
                show(ranked)
            else:
                show(coins_filter)
        except KeyboardInterrupt:
            print("\nBye!")
            break
        except Exception as e:
            print(f"Error: {e}")

        time.sleep(REFRESH)

if __name__ == "__main__":
    main()
