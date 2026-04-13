# Regime Detection + Sniper Decision — Python port of frontend detectRegime/computeAdaptiveScore
#
# Mirrors the JavaScript logic in index.html exactly so that backend tg_state.json
# contains the same regime/grade/status/trigger data the dashboard computes client-side.
# This enables Telegram alerts, AI analysis, and external consumers to use regime data
# without needing the browser.

import math


def detect_regime(d: dict) -> dict:
    """Detect market regime from coin state dict.

    Returns: {regime, conf, scores: {ACCUMULATION, DISTRIBUTION, LIQUIDATION, SQUEEZE, RANGE}}
    """
    if not d or not d.get("price"):
        return {"regime": "RANGE", "conf": 0,
                "scores": {"ACCUMULATION": 0, "DISTRIBUTION": 0, "LIQUIDATION": 0, "SQUEEZE": 0, "RANGE": 50}}

    acc = dist = liq = sqz = rng = 0
    spot_dir = d.get("spot_cvd_dir", "FLAT")
    fut_dir = d.get("fut_cvd_dir", "FLAT")
    spot_slope = abs(d.get("spot_cvd_slope", 0))
    fut_slope = abs(d.get("fut_cvd_slope", 0))
    oi_chg_raw = d.get("oi_change_1h", 0)
    oi_interp = d.get("oi_interp", "")
    fr = d.get("funding_rate", 0)
    fr_abs = abs(fr)
    chg24 = abs(d.get("change_24h", 0))
    ob_bid = d.get("ob_bid", 0)
    ob_ask = d.get("ob_ask", 0)
    ob_total = ob_bid + ob_ask or 1
    ob_ratio = ob_bid / ob_total
    taker_buy = d.get("taker_buy_vol", 0)
    taker_sell = d.get("taker_sell_vol", 0)
    taker_total = taker_buy + taker_sell or 1
    taker_buy_pct = taker_buy / taker_total
    long_s = d.get("long_score", 0)
    short_s = d.get("short_score", 0)
    cvd_conflict = spot_dir != fut_dir and spot_dir != "FLAT" and fut_dir != "FLAT"

    # OI tier normalization
    oi_usd = d.get("oi_usd", 0)
    oi_mult = 1.0 if oi_usd >= 10e9 else 0.8 if oi_usd >= 1e9 else 0.6 if oi_usd >= 200e6 else 0.4
    oi_chg = oi_chg_raw * oi_mult

    # Price structure
    if chg24 < 1.5:
        acc += 15; rng += 15
    elif chg24 < 3:
        acc += 5; dist += 5
    elif chg24 > 5:
        liq += 15; sqz += 10
    if chg24 > 8:
        liq += 10

    # OI trend
    if 0.3 < oi_chg < 2:
        acc += 20; sqz += 10
    elif oi_chg >= 2:
        sqz += 15; liq += 5; acc -= 5
    elif oi_chg < -1:
        liq += 25; dist += 15
    elif oi_chg < -0.3:
        dist += 10; liq += 10
    else:
        rng += 20

    if oi_interp == "SHORT ADDING":
        sqz += 10; acc -= 5
    if oi_interp == "SHORT COVERING":
        sqz += 5; liq += 5
    if oi_interp == "MOMENTUM VALID":
        acc += 10
    if oi_interp == "DELEVERAGING":
        liq += 10; dist += 5

    # Spot CVD
    if spot_dir == "RISING":
        acc += 20
        if spot_slope > 0.2:
            acc += 5
    elif spot_dir == "FALLING":
        dist += 20; liq += 15
        if spot_slope > 0.2:
            dist += 5; liq += 5
    else:
        rng += 10

    # Futures CVD
    if fut_dir == "RISING":
        acc += 10; sqz += 15
        if fut_slope > 0.25:
            sqz += 5
    elif fut_dir == "FALLING":
        dist += 15; liq += 10
        if fut_slope > 0.25:
            liq += 5
    else:
        rng += 10

    # Funding
    if -0.02 <= fr < -0.005:
        acc += 10
    elif fr < -0.02:
        sqz += 20
    elif 0.005 < fr <= 0.015:
        dist += 10
    elif fr > 0.015:
        sqz += 15; dist += 5
    else:
        rng += 10

    # Orderbook
    if ob_ratio > 0.55:
        acc += 10
    elif ob_ratio < 0.45:
        dist += 10
    else:
        rng += 10

    # Taker
    if taker_buy_pct > 0.6 and oi_chg < -0.5:
        liq += 15
    elif taker_buy_pct < 0.4 and oi_chg < -0.5:
        liq += 15
    if taker_buy_pct > 0.6:
        acc += 5
    if taker_buy_pct < 0.4:
        dist += 5; liq += 5

    # Positioning
    if long_s > 50:
        liq += 10; sqz += 5
    if short_s > 50:
        sqz += 10

    # CVD conflict
    if cvd_conflict:
        rng += 25; acc -= 10; dist -= 10

    # No spike check (accumulation bonus)
    if chg24 < 2 and spot_slope < 0.3 and fut_slope < 0.3:
        acc += 5

    scores = {
        "ACCUMULATION": max(0, acc),
        "DISTRIBUTION": max(0, dist),
        "LIQUIDATION": max(0, liq),
        "SQUEEZE": max(0, sqz),
        "RANGE": max(0, rng),
    }
    regime = max(scores, key=scores.get)
    max_score = scores[regime]
    total_all = sum(scores.values()) or 1
    conf = min(95, round(max_score / total_all * 100 + max_score * 0.3))

    return {"regime": regime, "conf": conf, "scores": scores}


def compute_sniper_decision(d: dict, regime: str, regime_conf: int) -> dict:
    """Compute adaptive sniper decision for a coin given its regime.

    Returns: {bias, grade, status, confidence, long_raw, short_raw, note,
              trigger, invalidation, vetoed, veto_reason}
    """
    if not d or not d.get("price"):
        return {"bias": "NEUTRAL", "grade": "--", "status": "WAIT", "confidence": 0,
                "long_raw": 0, "short_raw": 0, "note": "No data",
                "trigger": "--", "invalidation": "--", "vetoed": False, "veto_reason": ""}

    spot_dir = d.get("spot_cvd_dir", "FLAT")
    fut_dir = d.get("fut_cvd_dir", "FLAT")
    spot_slope = d.get("spot_cvd_slope", 0)
    fut_slope = d.get("fut_cvd_slope", 0)
    oi_chg = d.get("oi_change_1h", 0)
    oi_interp = d.get("oi_interp", "")
    fr = d.get("funding_rate", 0)
    fr_abs = abs(fr)
    ob_bid = d.get("ob_bid", 0)
    ob_ask = d.get("ob_ask", 0)
    ob_total = ob_bid + ob_ask or 1
    ob_ratio = ob_bid / ob_total
    taker_buy = d.get("taker_buy_vol", 0)
    taker_sell = d.get("taker_sell_vol", 0)
    taker_total = taker_buy + taker_sell or 1
    taker_buy_pct = taker_buy / taker_total
    long_s = d.get("long_score", 0)
    short_s = d.get("short_score", 0)
    chg24 = d.get("change_24h", 0)

    # Regime-specific weights
    WEIGHTS = {
        "ACCUMULATION": {"cvd": 30, "oi": 25, "ob": 15, "taker": 10, "fr": 10, "pos": 10},
        "DISTRIBUTION": {"cvd": 30, "oi": 20, "ob": 15, "taker": 15, "pos": 10, "fr": 10},
        "LIQUIDATION":  {"oi": 30, "cvd": 25, "taker": 20, "pos": 15, "liq": 10, "fr": 5, "ob": 5},
        "SQUEEZE":      {"pos": 25, "fr": 20, "futcvd": 20, "oi": 15, "liq": 10, "spotcvd": 5, "ob": 5},
        "RANGE":        {"conflict": 30, "oi": 20, "cvd": 20, "taker": 10, "liq": 10, "fr": 5, "ob": 5},
    }
    W = WEIGHTS.get(regime, WEIGHTS["RANGE"])

    long_raw = 0.0
    short_raw = 0.0

    # CVD scoring
    cvd_w = W.get("cvd", 0)
    if cvd_w:
        if spot_dir == "RISING":
            long_raw += cvd_w * 0.5 * (0.5 + min(abs(spot_slope), 0.5))
        elif spot_dir == "FALLING":
            short_raw += cvd_w * 0.5 * (0.5 + min(abs(spot_slope), 0.5))
        if fut_dir == "RISING":
            long_raw += cvd_w * 0.5 * (0.5 + min(abs(fut_slope), 0.5))
        elif fut_dir == "FALLING":
            short_raw += cvd_w * 0.5 * (0.5 + min(abs(fut_slope), 0.5))

    # Separate fut/spot CVD weights for squeeze
    futcvd_w = W.get("futcvd", 0)
    if futcvd_w:
        if fut_dir == "RISING":
            long_raw += futcvd_w * (0.5 + min(abs(fut_slope), 0.5))
        elif fut_dir == "FALLING":
            short_raw += futcvd_w * (0.5 + min(abs(fut_slope), 0.5))
    spotcvd_w = W.get("spotcvd", 0)
    if spotcvd_w:
        if spot_dir == "RISING":
            long_raw += spotcvd_w * (0.5 + min(abs(spot_slope), 0.5))
        elif spot_dir == "FALLING":
            short_raw += spotcvd_w * (0.5 + min(abs(spot_slope), 0.5))

    # OI scoring (tier-normalized)
    oi_usd = d.get("oi_usd", 0)
    oi_tier_mult = 1.0 if oi_usd >= 10e9 else 0.8 if oi_usd >= 1e9 else 0.6 if oi_usd >= 200e6 else 0.4
    oi_norm = oi_chg * oi_tier_mult
    oi_w = W.get("oi", 0)
    if oi_w:
        if regime == "LIQUIDATION":
            if oi_norm < -1:
                short_raw += oi_w * 0.8
            elif oi_norm < -0.3:
                short_raw += oi_w * 0.4
            elif oi_norm > 0.5:
                long_raw += oi_w * 0.3
        elif regime == "ACCUMULATION":
            if 0.3 < oi_norm < 2:
                long_raw += oi_w * 0.7
            elif oi_norm > 2:
                long_raw += oi_w * 0.3
        else:
            if oi_norm > 0.5:
                long_raw += oi_w * 0.5
            elif oi_norm < -0.5:
                short_raw += oi_w * 0.5
        if oi_interp == "MOMENTUM VALID":
            long_raw += oi_w * 0.2
        if oi_interp == "SHORT ADDING":
            long_raw += oi_w * 0.15
        if oi_interp == "DELEVERAGING":
            short_raw += oi_w * 0.2

    # Orderbook scoring
    ob_w = W.get("ob", 0)
    if ob_w:
        if ob_ratio > 0.6:
            long_raw += ob_w * 0.8
        elif ob_ratio > 0.55:
            long_raw += ob_w * 0.4
        elif ob_ratio < 0.4:
            short_raw += ob_w * 0.8
        elif ob_ratio < 0.45:
            short_raw += ob_w * 0.4

    # Taker scoring
    tk_w = W.get("taker", 0)
    if tk_w:
        if taker_buy_pct > 0.6:
            long_raw += tk_w * 0.7
        elif taker_buy_pct > 0.55:
            long_raw += tk_w * 0.3
        elif taker_buy_pct < 0.4:
            short_raw += tk_w * 0.7
        elif taker_buy_pct < 0.45:
            short_raw += tk_w * 0.3

    # Funding scoring
    fr_w = W.get("fr", 0)
    if fr_w:
        if regime == "SQUEEZE":
            if fr < -0.02:
                long_raw += fr_w * 0.9
            elif fr < -0.005:
                long_raw += fr_w * 0.5
            elif fr > 0.02:
                short_raw += fr_w * 0.9
            elif fr > 0.005:
                short_raw += fr_w * 0.5
        else:
            if fr < -0.01:
                long_raw += fr_w * 0.6
            elif fr > 0.015:
                short_raw += fr_w * 0.6
            elif fr > 0.005:
                short_raw += fr_w * 0.3

    # Positioning scoring
    pos_w = W.get("pos", 0)
    if pos_w:
        if regime == "SQUEEZE":
            if short_s > 50:
                long_raw += pos_w * 0.8
            elif long_s > 50:
                short_raw += pos_w * 0.8
        else:
            if long_s > 60:
                short_raw += pos_w * 0.6
            elif short_s > 60:
                long_raw += pos_w * 0.6
            elif long_s > 40:
                long_raw += pos_w * 0.2
            elif short_s > 40:
                short_raw += pos_w * 0.2

    # Conflict penalty (range)
    conflict_w = W.get("conflict", 0)
    if conflict_w:
        cvd_conflict = spot_dir != fut_dir and spot_dir != "FLAT" and fut_dir != "FLAT"
        if cvd_conflict:
            long_raw -= conflict_w * 0.3
            short_raw -= conflict_w * 0.3

    # Clamp
    long_raw = max(0, round(long_raw))
    short_raw = max(0, round(short_raw))

    # Net score + bias
    net_score = long_raw - short_raw
    bias = "LONG" if net_score > 3 else "SHORT" if net_score < -3 else "NEUTRAL"

    # Confidence (sigmoid-inspired curve)
    align_bonus = 8 if (spot_dir == fut_dir and spot_dir != "FLAT") else 0
    spot_chg = d.get("spot_cvd_change", 0)
    fut_chg = d.get("fut_cvd_change", 0)
    chg_bonus = 5 if ((spot_chg > 0 and fut_chg > 0) or (spot_chg < 0 and fut_chg < 0)) else 0
    raw_conf = abs(net_score) * 1.2 + regime_conf * 0.35 + align_bonus + chg_bonus
    confidence = round(95 / (1 + math.exp(-0.06 * (raw_conf - 50)))) + 2
    confidence = max(20, min(95, confidence))

    # Grade
    if confidence >= 78:
        grade = "A"
    elif confidence >= 62:
        grade = "B"
    elif confidence >= 45:
        grade = "C"
    else:
        grade = "D"

    # Veto rules — require 2+ contradictions
    veto_count = 0
    veto_reasons = []

    if regime == "ACCUMULATION" and grade in ("A", "B"):
        if oi_chg > 5:
            veto_count += 1; veto_reasons.append("OI spike >5% — too aggressive for accumulation")
        if abs(chg24) > 6:
            veto_count += 1; veto_reasons.append("Price moved >6% — extended, not accumulation")
        if fr_abs > 0.05:
            veto_count += 1; veto_reasons.append("Funding extreme — crowded positioning")
        if spot_dir == "FALLING" and fut_dir == "FALLING":
            veto_count += 1; veto_reasons.append("Both CVDs falling — contradicts accumulation")

    if regime == "DISTRIBUTION" and grade in ("A", "B"):
        if spot_dir == "RISING" and fut_dir == "RISING":
            veto_count += 1; veto_reasons.append("Both CVDs rising — contradicts distribution")
        if oi_chg > 1:
            veto_count += 1; veto_reasons.append("OI rising >1% — new money entering, not distributing")
        if ob_ratio > 0.6:
            veto_count += 1; veto_reasons.append("Bid dominant >60% — buy pressure contradicts")

    if regime == "LIQUIDATION" and grade in ("A", "B"):
        if 0 < oi_chg < 0.2:
            veto_count += 1; veto_reasons.append("OI stabilizing — liquidation may be over")
        if abs(chg24) > 10:
            veto_count += 1; veto_reasons.append("Price flushed >10% — late entry risk")
        if spot_dir == "RISING" and fut_dir == "RISING":
            veto_count += 1; veto_reasons.append("CVDs recovering — reversal forming")

    if regime == "SQUEEZE" and grade in ("A", "B"):
        if fr_abs < 0.005:
            veto_count += 1; veto_reasons.append("Funding not extreme enough for squeeze")
        if long_s < 25 and short_s < 25:
            veto_count += 1; veto_reasons.append("No crowded side detected")
        if oi_chg < -0.5:
            veto_count += 1; veto_reasons.append("OI declining — no fuel for squeeze")

    vetoed = veto_count >= 2
    veto_reason = " | ".join(veto_reasons)
    if vetoed:
        grade = "B" if grade == "A" else "C"
        confidence = min(confidence, 62)

    # Status
    if grade == "A":
        status = "READY"
    elif grade == "B":
        status = "WATCH"
    elif grade == "C":
        status = "WAIT"
    else:
        status = "AVOID"

    # Range = no edge
    if regime == "RANGE":
        if grade in ("A", "B"):
            grade = "C"
            status = "WAIT"
        confidence = min(confidence, 50)

    # Phase note
    if regime == "ACCUMULATION":
        if bias == "LONG":
            spot_part = "Spot accumulation active" if spot_dir == "RISING" else "Spot building slowly"
            oi_part = "building" if oi_chg > 0 else "flat"
            fr_part = "funding supports longs" if fr < 0 else "funding neutral"
            note = f"{spot_part}. OI {oi_part}, {fr_part}."
        else:
            note = "Accumulation structure but no clear long signal yet. Watch for SpotCVD confirmation."
    elif regime == "DISTRIBUTION":
        if bias == "SHORT":
            spot_part = "spot weakening" if spot_dir == "FALLING" else "spot stalling"
            oi_part = "OI unwinding" if oi_chg < 0 else "sellers passive"
            note = f"Distribution in progress — {spot_part}, {oi_part}."
        else:
            note = "Distribution structure detected but short signal weak. Wait for breakdown."
    elif regime == "LIQUIDATION":
        oi_part = "dropping fast" if oi_chg < -1 else "declining"
        cont = "Continuation likely." if bias == "SHORT" else "Watch for reversal signs."
        note = f"Forced liquidation active — OI {oi_part}. {cont}"
    elif regime == "SQUEEZE":
        sq_dir = "LONG (short squeeze)" if fr < 0 else "SHORT (long squeeze)"
        crowd = "shorts" if short_s > 40 else "longs"
        fr_dir = "negative" if fr < 0 else "positive"
        note = f"Squeeze setup {sq_dir}. Funding {fr_dir} extreme, {crowd} crowded."
    else:
        note = "Market choppy — CVD conflicting, OI flat. No clear edge. Wait for regime shift."

    if vetoed:
        note += f" VETO ({veto_count} flags): {veto_reason}"
    elif veto_count == 1:
        note += f" WARNING: {veto_reasons[0]}"

    # Trigger & Invalidation
    if regime == "ACCUMULATION":
        trigger = "SpotCVD terus naik + OI break di atas high terakhir" if bias == "LONG" else "Tunggu SpotCVD flip ke RISING"
        invalidation = "SpotCVD balik FALLING + OI turun >1% = akumulasi gagal"
    elif regime == "DISTRIBUTION":
        trigger = "SpotCVD breakdown + ask makin besar" if bias == "SHORT" else "Tunggu CVD aligned bearish"
        invalidation = "SpotCVD flip RISING + bid kuat masuk = distribusi gagal"
    elif regime == "LIQUIDATION":
        if bias == "SHORT":
            trigger = "OI masih turun + taker sell dominan"
            invalidation = "OI stabil + SpotCVD flip RISING = liquidasi selesai"
        else:
            trigger = "OI stabil + SpotCVD flip untuk reversal long"
            invalidation = "OI turun lagi + CVD gagal = lanjut turun"
    elif regime == "SQUEEZE":
        trigger = "FutCVD RISING bertahan + funding tetap negatif" if fr < 0 else "FutCVD FALLING bertahan + funding tetap positif"
        invalidation = "Funding normal kembali + posisi crowded mulai keluar teratur"
    else:
        trigger = "Belum ada arah jelas — tunggu CVD aligned atau OI bergerak"
        invalidation = "Jika sudah punya posisi — pasang SL ketat, regime bisa berubah kapan saja"

    return {
        "bias": bias,
        "grade": grade,
        "status": status,
        "confidence": confidence,
        "long_raw": long_raw,
        "short_raw": short_raw,
        "note": note,
        "trigger": trigger,
        "invalidation": invalidation,
        "vetoed": vetoed,
        "veto_reason": veto_reason,
    }
