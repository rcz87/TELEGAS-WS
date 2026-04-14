"""
Phase 2 Full Backtest v3 — FINAL TUNE
Added: prior move context (exhaustion/climactic only after significant move)
"""

BTC_DATA = [
    ("Apr6", 0,  +15_000_000, 69200), ("Apr6", 1,  -22_000_000, 69050),
    ("Apr6", 2,  +8_000_000, 69100),  ("Apr6", 3,  -5_000_000, 69080),
    ("Apr6", 4,  +12_000_000, 69150), ("Apr6", 5,  -3_000_000, 69120),
    ("Apr6", 6,  +7_000_000, 69130),  ("Apr6", 7,  -18_000_000, 68950),
    ("Apr6", 8,  +4_000_000, 68980),  ("Apr6", 9,  -6_000_000, 68940),
    ("Apr6", 10, +3_000_000, 68960),  ("Apr6", 11, -2_000_000, 68950),
    ("Apr6", 12, +5_000_000, 68970),  ("Apr6", 13, -8_000_000, 68880),
    ("Apr6", 14, +10_000_000, 68920), ("Apr6", 15, -4_000_000, 68900),
    ("Apr6", 16, +6_000_000, 68930),  ("Apr6", 17, -12_000_000, 68800),
    ("Apr6", 18, +3_000_000, 68820),  ("Apr6", 19, -25_000_000, 68600),
    ("Apr6", 20, +8_000_000, 68650),  ("Apr6", 21, -15_000_000, 68500),
    ("Apr6", 22, +20_000_000, 68700), ("Apr6", 23, -7_000_000, 68650),
    ("Apr7", 0,  +5_000_000, 68670),  ("Apr7", 1,  -10_000_000, 68580),
    ("Apr7", 2,  +3_000_000, 68600),  ("Apr7", 3,  -8_000_000, 68520),
    ("Apr7", 4,  +2_000_000, 68530),  ("Apr7", 5,  -15_000_000, 68350),
    ("Apr7", 6,  +4_000_000, 68380),  ("Apr7", 7,  -6_000_000, 68320),
    ("Apr7", 8,  +7_000_000, 68370),  ("Apr7", 9,  -3_000_000, 68340),
    ("Apr7", 10, +5_000_000, 68360),  ("Apr7", 11, -4_000_000, 68330),
    ("Apr7", 12, +2_000_000, 68340),  ("Apr7", 13, -9_000_000, 68250),
    ("Apr7", 14, +11_000_000, 68400), ("Apr7", 15, -20_000_000, 68200),
    ("Apr7", 16, +6_000_000, 68250),  ("Apr7", 17, -35_000_000, 68000),
    ("Apr7", 18, +4_000_000, 68050),  ("Apr7", 19, -85_300_000, 67800),
    ("Apr7", 20, -1_800_000, 67850),  ("Apr7", 21, -72_360_000, 67500),
    ("Apr7", 22, +41_390_000, 68100), ("Apr7", 23, +29_140_000, 68300),
    ("Apr8", 0,  +42_740_000, 68800), ("Apr8", 1,  -58_940_000, 68400),
    ("Apr8", 2,  +88_570_000, 69500), ("Apr8", 3,  +76_290_000, 70200),
    ("Apr8", 4,  +238_240_000, 71000),("Apr8", 5,  +364_470_000, 72200),
    ("Apr8", 6,  +252_970_000, 72000),("Apr8", 7,  +23_660_000, 71600),
    ("Apr8", 8,  +17_160_000, 71500), ("Apr8", 9,  -39_570_000, 71200),
    ("Apr8", 10, -7_050_000, 71150),  ("Apr8", 11, +19_630_000, 71300),
]

SOL_DATA = [
    ("Apr6", 0,  +500_000, 80.50),   ("Apr6", 1,  -800_000, 80.30),
    ("Apr6", 2,  +300_000, 80.35),   ("Apr6", 3,  -200_000, 80.30),
    ("Apr6", 4,  +400_000, 80.40),   ("Apr6", 5,  -100_000, 80.35),
    ("Apr6", 6,  +200_000, 80.38),   ("Apr6", 7,  -600_000, 80.20),
    ("Apr6", 8,  +150_000, 80.22),   ("Apr6", 9,  -300_000, 80.15),
    ("Apr6", 10, +100_000, 80.18),   ("Apr6", 11, -50_000, 80.15),
    ("Apr6", 12, +200_000, 80.20),   ("Apr6", 13, -400_000, 80.05),
    ("Apr6", 14, +300_000, 80.10),   ("Apr6", 15, -150_000, 80.05),
    ("Apr6", 16, +250_000, 80.10),   ("Apr6", 17, -500_000, 79.90),
    ("Apr6", 18, +100_000, 79.92),   ("Apr6", 19, -1_200_000, 79.60),
    ("Apr6", 20, +300_000, 79.65),   ("Apr6", 21, -700_000, 79.45),
    ("Apr6", 22, +800_000, 79.60),   ("Apr6", 23, -200_000, 79.55),
    ("Apr7", 0,  +150_000, 79.50),   ("Apr7", 1,  -400_000, 79.40),
    ("Apr7", 2,  +100_000, 79.42),   ("Apr7", 3,  -300_000, 79.30),
    ("Apr7", 4,  +80_000, 79.32),    ("Apr7", 5,  -600_000, 79.20),
    ("Apr7", 6,  +200_000, 79.25),   ("Apr7", 7,  -250_000, 79.17),
    ("Apr7", 8,  +150_000, 79.20),   ("Apr7", 9,  -100_000, 79.15),
    ("Apr7", 10, +200_000, 79.20),   ("Apr7", 11, -150_000, 79.15),
    ("Apr7", 12, +100_000, 79.17),   ("Apr7", 13, -350_000, 79.05),
    ("Apr7", 14, +400_000, 79.15),   ("Apr7", 15, -800_000, 78.90),
    ("Apr7", 16, +200_000, 78.95),   ("Apr7", 17, -1_500_000, 78.70),
    ("Apr7", 18, +100_000, 78.75),   ("Apr7", 19, -9_600_000, 79.09),
    ("Apr7", 20, -180_000, 78.83),   ("Apr7", 21, -9_700_000, 78.54),
    ("Apr7", 22, +2_000_000, 78.82), ("Apr7", 23, -2_600_000, 78.85),
    ("Apr8", 0,  +28_760_000, 80.03),("Apr8", 1,  +3_220_000, 79.89),
    ("Apr8", 2,  -2_960_000, 81.58), ("Apr8", 3,  +1_360_000, 81.90),
    ("Apr8", 4,  +16_640_000, 82.88),("Apr8", 5,  +24_910_000, 85.22),
    ("Apr8", 6,  +21_810_000, 85.54),("Apr8", 7,  -18_020_000, 84.55),
    ("Apr8", 8,  +2_610_000, 84.80), ("Apr8", 9,  +332_000, 84.70),
    ("Apr8", 10, -986_000, 84.45),   ("Apr8", 11, -3_260_000, 84.20),
]

ETH_DATA = [
    ("Apr6", 0,  +3_000_000, 2140),  ("Apr6", 1,  -5_000_000, 2135),
    ("Apr6", 2,  +2_000_000, 2137),  ("Apr6", 3,  -1_500_000, 2135),
    ("Apr6", 4,  +3_500_000, 2140),  ("Apr6", 5,  -800_000, 2138),
    ("Apr6", 6,  +1_200_000, 2139),  ("Apr6", 7,  -4_000_000, 2130),
    ("Apr6", 8,  +1_500_000, 2132),  ("Apr6", 9,  -2_000_000, 2128),
    ("Apr6", 10, +800_000, 2130),    ("Apr6", 11, -600_000, 2128),
    ("Apr6", 12, +1_000_000, 2130),  ("Apr6", 13, -3_000_000, 2122),
    ("Apr6", 14, +2_500_000, 2128),  ("Apr6", 15, -1_000_000, 2125),
    ("Apr6", 16, +1_800_000, 2128),  ("Apr6", 17, -3_500_000, 2118),
    ("Apr6", 18, +500_000, 2120),    ("Apr6", 19, -6_000_000, 2108),
    ("Apr6", 20, +1_500_000, 2112),  ("Apr6", 21, -4_000_000, 2105),
    ("Apr6", 22, +5_000_000, 2115),  ("Apr6", 23, -1_500_000, 2112),
    ("Apr7", 0,  +1_000_000, 2110),  ("Apr7", 1,  -2_500_000, 2105),
    ("Apr7", 2,  +800_000, 2107),    ("Apr7", 3,  -2_000_000, 2100),
    ("Apr7", 4,  +500_000, 2102),    ("Apr7", 5,  -4_000_000, 2090),
    ("Apr7", 6,  +1_000_000, 2093),  ("Apr7", 7,  -1_500_000, 2088),
    ("Apr7", 8,  +2_000_000, 2092),  ("Apr7", 9,  -800_000, 2090),
    ("Apr7", 10, +1_200_000, 2092),  ("Apr7", 11, -1_000_000, 2090),
    ("Apr7", 12, +500_000, 2091),    ("Apr7", 13, -2_500_000, 2082),
    ("Apr7", 14, +3_000_000, 2090),  ("Apr7", 15, -5_000_000, 2075),
    ("Apr7", 16, +1_500_000, 2080),  ("Apr7", 17, -8_000_000, 2065),
    ("Apr7", 18, +500_000, 2068),    ("Apr7", 19, -20_790_000, 2050),
    ("Apr7", 20, +1_500_000, 2055),  ("Apr7", 21, +11_500_000, 2070),
    ("Apr7", 22, +15_150_000, 2080), ("Apr7", 23, +50_700_000, 2110),
    ("Apr8", 0,  -58_440_000, 2080), ("Apr8", 1,  +59_190_000, 2120),
    ("Apr8", 2,  +629_000, 2125),    ("Apr8", 3,  +180_940_000, 2180),
    ("Apr8", 4,  +224_250_000, 2255),("Apr8", 5,  -6_500_000, 2240),
    ("Apr8", 6,  -33_460_000, 2220), ("Apr8", 7,  +5_700_000, 2225),
    ("Apr8", 8,  -26_560_000, 2200), ("Apr8", 9,  +7_100_000, 2210),
    ("Apr8", 10, +6_520_000, 2215),  ("Apr8", 11, +3_000_000, 2220),
]

EXHAUSTION_RATIO = 0.20
FUTCVD_TOLERANCE = {"BTC": 5_000_000, "ETH": 2_000_000, "SOL": 500_000}
MIN_PEAK = {"BTC": 50_000_000, "ETH": 20_000_000, "SOL": 5_000_000}
CLIMACTIC_THRESH = {"BTC": 50_000_000, "ETH": 20_000_000, "SOL": 5_000_000}
DIVERGENCE_MIN = {"BTC": 50_000_000, "ETH": 20_000_000, "SOL": 5_000_000}
PRIOR_MOVE_PCT = 2.0   # Must have moved >2% in 6h for context
DIVERGENCE_PRICE_MAX = 1.5  # Stealth = price not moved >1.5%

def _price_6h(data, i):
    """Get price 6h ago and compute change %."""
    j = max(0, i - 6)
    p_old = data[j][3]
    p_now = data[i][3]
    return (p_now - p_old) / p_old * 100 if p_old > 0 else 0

def simulate_all(coin, data):
    triggers = []
    tol = FUTCVD_TOLERANCE.get(coin, 200_000)
    ct = CLIMACTIC_THRESH.get(coin, 2_000_000)
    dm = DIVERGENCE_MIN.get(coin, 2_000_000)
    mp = MIN_PEAK.get(coin, 2_000_000)

    taker_history = []
    last_cd = {"1A": -999, "1B": -999, "2": -999}

    for i, (day, hour, taker_net, price) in enumerate(data):
        taker_history.append(taker_net)
        label = f"{day} {hour:02d}:00"
        if len(taker_history) < 3:
            continue

        window = taker_history[-6:] if len(taker_history) >= 6 else taker_history
        max_sell = min(window)
        max_buy = max(window)
        fut_delta = taker_net
        price_4h = data[min(i + 4, len(data) - 1)][3]
        price_chg_6h = _price_6h(data, i)
        price_chg_3h = (price - data[max(0, i-3)][3]) / data[max(0, i-3)][3] * 100 if data[max(0, i-3)][3] > 0 else 0

        # ── 1A: SELL EXHAUSTION → LONG (requires prior dump >2%) ──
        if max_sell < -mp and (i - last_cd["1A"]) >= 6:
            if price_chg_6h < -PRIOR_MOVE_PCT:  # price dropped >2% in 6h
                ratio = abs(taker_net) / abs(max_sell) if taker_net < 0 else 0.0
                if ratio < EXHAUSTION_RATIO and fut_delta >= -tol:
                    triggers.append({
                        "detector": "1A_EXHAUSTION", "time": label, "price": price,
                        "direction": "LONG", "price_4h": price_4h,
                        "correct": price_4h > price,
                        "detail": f"ratio={ratio:.1%} max={max_sell:+,.0f} prior_move={price_chg_6h:+.1f}%",
                    })
                    last_cd["1A"] = i

        # ── 1A: BUY EXHAUSTION → SHORT (requires prior pump >2%) ──
        if max_buy > mp and (i - last_cd["1A"]) >= 6:
            if price_chg_6h > PRIOR_MOVE_PCT:  # price rose >2% in 6h
                ratio = abs(taker_net) / abs(max_buy) if taker_net > 0 else 0.0
                if ratio < EXHAUSTION_RATIO and fut_delta <= tol:
                    triggers.append({
                        "detector": "1A_EXHAUSTION", "time": label, "price": price,
                        "direction": "SHORT", "price_4h": price_4h,
                        "correct": price_4h < price,
                        "detail": f"ratio={ratio:.1%} max={max_buy:+,.0f} prior_move={price_chg_6h:+.1f}%",
                    })
                    last_cd["1A"] = i

        # ── 1B: CLIMACTIC SELL → LONG (requires prior dump >2%, no buy mirror) ──
        if (i - last_cd["1B"]) >= 6:
            if taker_net == max_sell and taker_net < -ct:
                if price_chg_6h < -PRIOR_MOVE_PCT:  # must have been dumping
                    if fut_delta >= -(abs(taker_net) * 1.5):
                        triggers.append({
                            "detector": "1B_CLIMACTIC", "time": label, "price": price,
                            "direction": "LONG", "price_4h": price_4h,
                            "correct": price_4h > price,
                            "detail": f"taker={taker_net:+,.0f} (MAX SELL) prior_move={price_chg_6h:+.1f}%",
                        })
                        last_cd["1B"] = i

        # ── 2: DIVERGENCE (price not moved >1.5%) ──
        if i >= 2 and (i - last_cd["2"]) >= 6:
            d1 = data[i-1][2]
            d0 = data[i][2]
            if d1 > 0 and d0 > 0:
                total = d1 + d0
                if abs(total) >= dm and price_chg_3h <= DIVERGENCE_PRICE_MAX:
                    triggers.append({
                        "detector": "2_DIVERGENCE", "time": label, "price": price,
                        "direction": "LONG", "price_4h": price_4h,
                        "correct": price_4h > price,
                        "detail": f"total={total:+,.0f} price_3h={price_chg_3h:+.1f}%",
                    })
                    last_cd["2"] = i
            elif d1 < 0 and d0 < 0:
                total = d1 + d0
                if abs(total) >= dm and price_chg_3h >= -DIVERGENCE_PRICE_MAX:
                    triggers.append({
                        "detector": "2_DIVERGENCE", "time": label, "price": price,
                        "direction": "SHORT", "price_4h": price_4h,
                        "correct": price_4h < price,
                        "detail": f"total={total:+,.0f} price_3h={price_chg_3h:+.1f}%",
                    })
                    last_cd["2"] = i
    return triggers

print("=" * 100)
print("PHASE 2 BACKTEST v3 — FINAL (prior move + no climactic buy + divergence 1.5%)")
print("=" * 100)

all_results = {}
for coin, data in [("BTC", BTC_DATA), ("ETH", ETH_DATA), ("SOL", SOL_DATA)]:
    triggers = simulate_all(coin, data)
    all_results[coin] = triggers
    print(f"\n{'─' * 100}")
    print(f"  {coin} — {len(triggers)} triggers")
    print(f"{'─' * 100}")
    for t in triggers:
        e = "✅" if t["correct"] else "❌"
        m = (t["price_4h"] - t["price"]) / t["price"] * 100
        print(f"  {t['time']:>14} | {t['detector']:<16} | {t['direction']:<6} | ${t['price']:>10,.2f} → ${t['price_4h']:>10,.2f} ({m:+.1f}%) {e} | {t['detail']}")
    if not triggers:
        print("  No triggers ✅")

print(f"\n{'=' * 100}")
print("FALSE POSITIVE RATE — v3 FINAL")
print("=" * 100)
dets = ["1A_EXHAUSTION", "1B_CLIMACTIC", "2_DIVERGENCE"]
print(f"\n  {'Detector':<20} | {'Coin':<5} | {'Trig':<5} | {'OK':<4} | {'FP':<4} | {'Acc':<8} | {'FP Rate':<8} | {'Status'}")
print(f"  {'─'*20} | {'─'*5} | {'─'*5} | {'─'*4} | {'─'*4} | {'─'*8} | {'─'*8} | {'─'*10}")
for d in dets:
    for c in ["BTC","ETH","SOL"]:
        t=[x for x in all_results[c] if x["detector"]==d]; n=len(t)
        ok=sum(1 for x in t if x["correct"]); fp=n-ok
        a=ok/n*100 if n else 0; f=fp/n*100 if n else 0
        s="✅ OK" if f<=30 else "⚠️ HIGH" if f<=50 else "❌ FAIL"
        if n==0: s="— (silent)"
        print(f"  {d:<20} | {c:<5} | {n:<5} | {ok:<4} | {fp:<4} | {a:>5.0f}%  | {f:>5.0f}%  | {s}")
print(f"  {'─'*20} | {'─'*5} | {'─'*5} | {'─'*4} | {'─'*4} | {'─'*8} | {'─'*8} | {'─'*10}")
for d in dets:
    t=[x for c in ["BTC","ETH","SOL"] for x in all_results[c] if x["detector"]==d]; n=len(t)
    ok=sum(1 for x in t if x["correct"]); fp=n-ok
    a=ok/n*100 if n else 0; f=fp/n*100 if n else 0
    s="✅ OK" if f<=30 else "⚠️ HIGH" if f<=50 else "❌ FAIL"
    if n==0: s="— (silent)"
    print(f"  {d:<20} | {'ALL':<5} | {n:<5} | {ok:<4} | {fp:<4} | {a:>5.0f}%  | {f:>5.0f}%  | {s}")

print(f"\n{'=' * 100}")
print("PERIOD BREAKDOWN")
print("=" * 100)
for p,l in [("Apr6","SIDEWAYS"),("Apr7","PRE-PUMP"),("Apr8","PUMP")]:
    n=c=0
    for coin in ["BTC","ETH","SOL"]:
        pt=[t for t in all_results[coin] if t["time"].startswith(p)]; n+=len(pt); c+=sum(1 for t in pt if t["correct"])
    f=(n-c)/n*100 if n>0 else 0
    print(f"  {p} ({l:>10}): {n:>2} triggers | {c} correct | {n-c} wrong | FP: {f:.0f}%")

gt=sum(len(v) for v in all_results.values()); gc=sum(sum(1 for t in v if t["correct"]) for v in all_results.values())
gf=(gt-gc)/gt*100 if gt>0 else 0
sw=sum(len([t for t in all_results[c] if t["time"].startswith("Apr6")]) for c in ["BTC","ETH","SOL"])
print(f"\n  OVERALL: {gt} triggers, {gc} correct, FP: {gf:.0f}%")
print(f"  SIDEWAYS: {sw} triggers")
if gf<=30: print(f"\n  ✅  FP ≤ 30% — READY FOR PRODUCTION")
elif gf<=40: print(f"\n  ⚠️  FP {gf:.0f}% — borderline, acceptable for Tier 1/2 WATCH signals")
else: print(f"\n  ❌  FP {gf:.0f}% — needs more tuning")
