"""
Backtest Phase 2 Detectors — SOL 7 Apr 19:00 to 8 Apr 03:00 WIB

Uses historical data from investigation report + CoinGlass confirmation.
Simulates: Tier 1A (Exhaustion), Tier 1B (Climactic), Tier 2 (Stealth Divergence)
"""

# ── Historical Taker Net Data (FutCVD delta per hour = taker net proxy) ──
# From investigation report Section 3b + CoinGlass confirmed data
# Each entry: (hour_wib, taker_net_usd, fut_cvd_delta, spot_cvd_delta, price, oi_change_pct)

HOURLY_DATA = [
    # hour,  taker_net,    fut_delta,     spot_delta,    price,  oi_chg%
    (19,    -9_600_000,   -9_629_068,    +452_496,      79.09,  0.0),   # base
    (20,      -180_000,     -179_643,    -1_208,        78.83, -0.2),   # SELL DROP 98%
    (21,    -9_700_000,   -9_733_134,    -194_494,      78.54, +0.6),   # CLIMACTIC SELL
    (22,    +2_000_000,   +1_997_554,    -148_263,      78.82, +1.4),   # FutCVD FLIP #1
    (23,    -2_600_000,   -2_560_530,    +134_883,      78.85, -0.6),   # retest
    (0,    +32_200_000,  +32_219_439,   +1_408_306,     80.03, -0.8),   # MEGA FLIP
    (1,     +2_700_000,   +2_697_983,    -461_261,      79.89, -1.0),   # sustained
    (2,     -1_600_000,   -1_613_161,   +1_158_313,     81.58, +1.2),   # CVD_FLIP trigger
    (3,       +197_000,     +196_615,    +257_518,      81.90, +1.0),   # hold
]

# Thresholds for SOL (from taker_signal_detector.py)
CLIMACTIC_THRESHOLD = 5_000_000  # $5M
EXHAUSTION_RATIO = 0.20  # 20%
FUTCVD_TOLERANCE = 500_000  # $500K for SOL (tuned)
DIVERGENCE_MIN_TOTAL = 5_000_000  # $5M for SOL (tuned)

print("=" * 80)
print("BACKTEST PHASE 2 — SOL 7 Apr 19:00 → 8 Apr 03:00 WIB")
print("=" * 80)

# ══════════════════════════════════════════════════════════════════════
# TIER 1A: TAKER EXHAUSTION SIMULATION
# ══════════════════════════════════════════════════════════════════════
print("\n" + "─" * 60)
print("TIER 1A: TAKER EXHAUSTION")
print("Rule: taker_net_current < 20% of max_sell_6h")
print(f"Filter: FutCVD delta >= -${FUTCVD_TOLERANCE/1e3:.0f}K (tolerance), OI change > -1.0%")
print("─" * 60)

# Track rolling 6h max sell (most negative taker_net)
taker_history = []
exhaustion_triggers = []

for i, (hour, taker_net, fut_delta, spot_delta, price, oi_chg) in enumerate(HOURLY_DATA):
    taker_history.append(taker_net)

    # Need at least 2 data points
    if len(taker_history) < 2:
        continue

    max_sell = min(taker_history)  # most negative

    if max_sell >= 0:
        continue  # no significant sell in history

    # Calculate ratio
    if taker_net < 0:
        ratio = abs(taker_net) / abs(max_sell)
    else:
        ratio = 0.0  # seller completely gone

    # Check exhaustion
    if ratio < EXHAUSTION_RATIO:
        # Qualifying filters
        filter_fut = fut_delta >= -FUTCVD_TOLERANCE
        filter_oi = oi_chg > -1.0

        h_str = f"{hour:02d}:00"
        status = "✅ TRIGGER" if (filter_fut and filter_oi) else "❌ FILTERED"

        if filter_fut and filter_oi:
            exhaustion_triggers.append((hour, price, ratio, taker_net, max_sell))

        print(f"\n  {h_str} WIB | ${price:.2f}")
        print(f"  Taker net  : ${taker_net:+,.0f}")
        print(f"  Max sell 6h: ${max_sell:+,.0f}")
        print(f"  Ratio      : {ratio:.1%} {'← EXHAUSTED' if ratio < EXHAUSTION_RATIO else ''}")
        print(f"  FutCVD Δ   : ${fut_delta:+,.0f} {'✅' if filter_fut else '❌ accelerating sell (> tolerance)'}")
        print(f"  OI 1h      : {oi_chg:+.1f}% {'✅' if filter_oi else '❌ crashing'}")
        print(f"  → {status}")

if not exhaustion_triggers:
    print("\n  No triggers found.")

# ══════════════════════════════════════════════════════════════════════
# TIER 1B: CLIMACTIC CANDLE SIMULATION
# ══════════════════════════════════════════════════════════════════════
print("\n" + "─" * 60)
print("TIER 1B: CLIMACTIC CANDLE")
print(f"Rule: taker_net IS max_sell/buy in 6h AND > ${CLIMACTIC_THRESHOLD/1e6:.0f}M")
print("Filter: FutCVD delta >= -(taker*1.5), OI change > -1.0%  [TUNED from 0.5→1.5]")
print("─" * 60)

taker_history2 = []
climactic_triggers = []

for i, (hour, taker_net, fut_delta, spot_delta, price, oi_chg) in enumerate(HOURLY_DATA):
    taker_history2.append(taker_net)

    if len(taker_history2) < 2:
        continue

    max_sell = min(taker_history2)
    max_buy = max(taker_history2)

    # CLIMACTIC SELL: current = biggest sell AND above threshold
    if taker_net == max_sell and taker_net < -CLIMACTIC_THRESHOLD:
        filter_fut = fut_delta >= -(abs(taker_net) * 1.5)
        filter_oi = oi_chg > -1.0

        h_str = f"{hour:02d}:00"
        status = "✅ TRIGGER" if (filter_fut and filter_oi) else "❌ FILTERED"

        if filter_fut and filter_oi:
            climactic_triggers.append((hour, price, "SELL", taker_net))

        print(f"\n  {h_str} WIB | ${price:.2f} | CLIMACTIC SELL")
        print(f"  Taker net  : ${taker_net:+,.0f} ← MAX SELL")
        print(f"  Threshold  : ${CLIMACTIC_THRESHOLD/1e6:.0f}M → {'PASS' if abs(taker_net) > CLIMACTIC_THRESHOLD else 'FAIL'}")
        print(f"  FutCVD Δ   : ${fut_delta:+,.0f} {'✅' if filter_fut else '❌'}")
        print(f"  OI 1h      : {oi_chg:+.1f}% {'✅' if filter_oi else '❌'}")
        print(f"  → {status}")

    # CLIMACTIC BUY: current = biggest buy AND above threshold
    if taker_net == max_buy and taker_net > CLIMACTIC_THRESHOLD:
        filter_fut = fut_delta <= abs(taker_net) * 1.5
        filter_oi = oi_chg > -1.0

        h_str = f"{hour:02d}:00"
        status = "✅ TRIGGER" if (filter_fut and filter_oi) else "❌ FILTERED"

        if filter_fut and filter_oi:
            climactic_triggers.append((hour, price, "BUY", taker_net))

        print(f"\n  {h_str} WIB | ${price:.2f} | CLIMACTIC BUY")
        print(f"  Taker net  : ${taker_net:+,.0f} ← MAX BUY")
        print(f"  Threshold  : ${CLIMACTIC_THRESHOLD/1e6:.0f}M → {'PASS' if abs(taker_net) > CLIMACTIC_THRESHOLD else 'FAIL'}")
        print(f"  FutCVD Δ   : ${fut_delta:+,.0f} {'✅' if filter_fut else '❌'}")
        print(f"  OI 1h      : {oi_chg:+.1f}% {'✅' if filter_oi else '❌'}")
        print(f"  → {status}")

if not climactic_triggers:
    print("\n  No triggers found.")

# ══════════════════════════════════════════════════════════════════════
# TIER 2: STEALTH CVD DIVERGENCE SIMULATION
# ══════════════════════════════════════════════════════════════════════
print("\n" + "─" * 60)
print("TIER 2: STEALTH CVD DIVERGENCE")
print("Rule: FutCVD positive streak >= 2 AND SpotCVD NOT rising/positive")
print(f"      AND FutCVD total >= ${DIVERGENCE_MIN_TOTAL/1e6:.0f}M  [TUNED: min total filter]")
print("OR: Mega delta (single candle > 5x avg)")
print("─" * 60)

fut_deltas_all = [d[2] for d in HOURLY_DATA]  # fut_cvd_delta
spot_deltas_all = [d[3] for d in HOURLY_DATA]  # spot_cvd_delta
divergence_triggers = []

# Running SpotCVD cumulative for level tracking
spot_cum = HOURLY_DATA[0][3]  # start from first spot value

for i in range(1, len(HOURLY_DATA)):
    hour, taker_net, fut_delta, spot_delta, price, oi_chg = HOURLY_DATA[i]
    spot_cum += spot_delta

    # Determine SpotCVD direction
    if spot_delta > 100_000:
        spot_dir = "RISING"
    elif spot_delta < -100_000:
        spot_dir = "FALLING"
    else:
        spot_dir = "FLAT"

    # Count FutCVD positive streak ending at current candle
    pos_streak = 0
    for j in range(i, 0, -1):
        if HOURLY_DATA[j][2] > 0:
            pos_streak += 1
        else:
            break

    neg_streak = 0
    for j in range(i, 0, -1):
        if HOURLY_DATA[j][2] < 0:
            neg_streak += 1
        else:
            break

    # Mega delta check
    if i >= 3:
        recent_deltas = [abs(HOURLY_DATA[k][2]) for k in range(max(0, i-6), i)]
        avg_delta = sum(recent_deltas) / len(recent_deltas) if recent_deltas else 1
        mega = abs(fut_delta) > avg_delta * 5 and avg_delta > 0
        mega_ratio = abs(fut_delta) / avg_delta if avg_delta > 0 else 0
    else:
        mega = False
        mega_ratio = 0

    h_str = f"{hour:02d}:00"

    # STEALTH ACCUMULATION: FutCVD rising, SpotCVD not
    if pos_streak >= 2 or (mega and fut_delta > 0):
        if spot_dir != "RISING" or spot_cum < 0:
            streak = pos_streak if pos_streak >= 2 else 1
            fut_total = sum(HOURLY_DATA[j][2] for j in range(max(1, i - streak + 1), i + 1))
            passes_min = abs(fut_total) >= DIVERGENCE_MIN_TOTAL or mega
            if passes_min:
                divergence_triggers.append((hour, price, "ACCUMULATION", pos_streak, mega))
                print(f"\n  {h_str} WIB | ${price:.2f} | STEALTH ACCUMULATION")
                print(f"  FutCVD Δ   : ${fut_delta:+,.0f} | streak: {pos_streak} candle positive")
                print(f"  FutCVD tot : ${fut_total:+,.0f} (min ${DIVERGENCE_MIN_TOTAL/1e6:.0f}M) ✅")
                print(f"  SpotCVD    : ${spot_cum:+,.0f} ({spot_dir}) ← NOT aligned")
                if mega:
                    print(f"  ⚡ MEGA DELTA: {mega_ratio:.1f}x avg → immediate trigger")
                print(f"  → ✅ TRIGGER")
            else:
                print(f"\n  {h_str} WIB | ${price:.2f} | STEALTH ACCUMULATION")
                print(f"  FutCVD Δ   : ${fut_delta:+,.0f} | streak: {pos_streak} candle positive")
                print(f"  FutCVD tot : ${fut_total:+,.0f} (min ${DIVERGENCE_MIN_TOTAL/1e6:.0f}M) ❌ TOO SMALL")
                print(f"  → ❌ FILTERED (min total)")

    # STEALTH DISTRIBUTION: FutCVD falling, SpotCVD not
    if neg_streak >= 2 or (mega and fut_delta < 0):
        if spot_dir != "FALLING" or spot_cum > 0:
            streak = neg_streak if neg_streak >= 2 else 1
            fut_total = sum(HOURLY_DATA[j][2] for j in range(max(1, i - streak + 1), i + 1))
            passes_min = abs(fut_total) >= DIVERGENCE_MIN_TOTAL or mega
            if passes_min:
                divergence_triggers.append((hour, price, "DISTRIBUTION", neg_streak, mega))
                print(f"\n  {h_str} WIB | ${price:.2f} | STEALTH DISTRIBUTION")
                print(f"  FutCVD Δ   : ${fut_delta:+,.0f} | streak: {neg_streak} candle negative")
                print(f"  FutCVD tot : ${fut_total:+,.0f} (min ${DIVERGENCE_MIN_TOTAL/1e6:.0f}M) ✅")
                print(f"  SpotCVD    : ${spot_cum:+,.0f} ({spot_dir}) ← NOT aligned")
                if mega:
                    print(f"  ⚡ MEGA DELTA: {mega_ratio:.1f}x avg → immediate trigger")
                print(f"  → ✅ TRIGGER")
            else:
                print(f"\n  {h_str} WIB | ${price:.2f} | STEALTH DISTRIBUTION")
                print(f"  FutCVD Δ   : ${fut_delta:+,.0f} | streak: {neg_streak} candle negative")
                print(f"  FutCVD tot : ${fut_total:+,.0f} (min ${DIVERGENCE_MIN_TOTAL/1e6:.0f}M) ❌ TOO SMALL")
                print(f"  → ❌ FILTERED (min total)")

# ══════════════════════════════════════════════════════════════════════
# COMBO CHECK
# ══════════════════════════════════════════════════════════════════════
print("\n" + "─" * 60)
print("COMBO CHECK: Exhaustion + Climactic within 2h")
print("─" * 60)

combo_found = False
for ex_h, ex_p, _, _, _ in exhaustion_triggers:
    for cl_h, cl_p, cl_side, _ in climactic_triggers:
        gap = abs(ex_h - cl_h)
        if gap <= 2:
            print(f"\n  ⚡ COMBO DETECTED!")
            print(f"  Exhaustion : {ex_h:02d}:00 @ ${ex_p:.2f}")
            print(f"  Climactic  : {cl_h:02d}:00 @ ${cl_p:.2f}")
            print(f"  Gap        : {gap}h ← within 2h window")
            print(f"  Conviction : HIGHER")
            combo_found = True

if not combo_found:
    print("  No combo found.")

# ══════════════════════════════════════════════════════════════════════
# COMPARISON TABLE
# ══════════════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print("COMPARISON: Phase 2 vs Actual Alert")
print("=" * 80)

actual_alert_hour = 2.25  # 02:15 WIB
actual_alert_price = 80.55

print(f"\n  {'Detector':<30} {'Time WIB':<12} {'Price':<10} {'vs Alert':<15} {'Leverage 10x'}")
print(f"  {'─'*30} {'─'*12} {'─'*10} {'─'*15} {'─'*12}")

all_triggers = []
for h, p, ratio, tn, ms in exhaustion_triggers:
    all_triggers.append(("TIER 1A Exhaustion", h, p))
for h, p, side, tn in climactic_triggers:
    all_triggers.append((f"TIER 1B Climactic {side}", h, p))
for h, p, pattern, streak, mega in divergence_triggers:
    label = f"TIER 2 Stealth {'(MEGA)' if mega else ''}"
    all_triggers.append((label, h, p))

all_triggers.append(("TIER 3 CVD_FLIP (actual)", 2.25, actual_alert_price))
all_triggers.sort(key=lambda x: x[1])

for name, h, p in all_triggers:
    if h == actual_alert_hour:
        h_str = "02:15"
    else:
        h_str = f"{int(h):02d}:00"

    diff = actual_alert_price - p
    diff_pct = diff / p * 100 if p > 0 else 0
    lev_pct = diff_pct * 10

    if name == "TIER 3 CVD_FLIP (actual)":
        print(f"  {name:<30} {h_str:<12} ${p:<9.2f} {'BASELINE':<15} {'BASELINE'}")
    else:
        earlier = (actual_alert_hour - h)
        if earlier < 0:
            earlier += 24
        print(f"  {name:<30} {h_str:<12} ${p:<9.2f} ${diff:+.2f} ({earlier:.0f}h early)  +{lev_pct:.0f}% extra")

# SOL peak
peak_price = 87.19
print(f"\n  SOL Peak: ${peak_price} (06:00 WIB)")
print(f"\n  Entry comparison (to peak ${peak_price}):")
for name, h, p in all_triggers:
    move = (peak_price - p) / p * 100
    lev = move * 10
    if h == actual_alert_hour:
        h_str = "02:15"
    else:
        h_str = f"{int(h):02d}:00"
    print(f"    {name:<30} ${p:.2f} → ${peak_price} = +{move:.1f}% ({lev:.0f}% @ 10x)")
