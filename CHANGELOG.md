# CHANGELOG — TELEGLAS Pro

## [2026-04-08] Pre-Send Gate — Close All Alert Backdoors

### Problem
6 alert paths kirim langsung ke Telegram bot Tier 3 (CONFIRMED) tanpa melewati noise filter, BTC check, atau confidence threshold. Contoh:
- ADA STEALTH SELL Grade B masuk Tier 3 padahal harusnya max Tier 2
- SEI LONG confidence 65%, BTC DIVERGENT, FR split masuk Tier 3 padahal harusnya BLOCKED

### Root Cause
`MovementDetector`, `TakerSignalDetector`, `CVD Divergence`, dan `Proactive Scanner` bypass semua filter dan kirim langsung ke `telegram_router.send_alert()`.

### Solution
Buat `_pre_send_gate()` — centralized filter yang diterapkan ke SEMUA alert path sebelum kirim ke Telegram.

#### Rules dalam `_pre_send_gate()`:
| Rule | Effect |
|------|--------|
| Noise filter | Anti-flip 30min, directional consistency 1hr, min confidence 65 |
| BTC DIVERGENT | -5 confidence + force max Tier 2 |
| FR split (exchange berlawanan sign) | -3 confidence |
| Grade B | Max Tier 2 (tidak boleh Tier 3) |
| Confidence < 72 | Max Tier 2 (Tier 3 hanya untuk confirmed >= 72) |
| Confidence < 65 (setelah penalty) | BLOCKED — tidak dikirim |

#### Path yang di-fix:

| # | Path | File | Sebelum | Sesudah |
|---|------|------|---------|---------|
| 1 | MovementDetector (Stealth/Flush/Absorption/FlowReversal/Quiet) | `main.py:645` | ZERO filter | `_pre_send_gate()` |
| 2 | REST CVD Flip/OI Spike/Whale | `main.py:1164` | Noise filter saja, hardcoded Tier 3 | + BTC/FR/conf tier routing |
| 3 | Taker Exhaustion/Climactic (Tier 1) | `main.py:1307` | ZERO filter | `_pre_send_gate()` |
| 4 | CVD Divergence (Tier 2) | `main.py:1345` | BTC label setelah kirim | `_pre_send_gate()` sebelum kirim |
| 5 | WebSocket StopHunt/OrderFlow | `main.py:1714` | Noise filter saja, hardcoded Tier 3 | + BTC/FR/conf tier routing |
| 6 | Proactive Scanner | `main.py:2045` | ZERO filter | `_pre_send_gate()` |

#### Changes di `movement_detector.py`:
- Tambah `_grade_confidence()`: Grade A=75, B=65, C=50
- Semua 5 alert dict sekarang include `"confidence"` field untuk pre-send gate

### Test Cases
- SEI LONG 65% + BTC DIVERGENT + FR split → 65 - 5 - 3 = 57% → **BLOCKED**
- ADA STEALTH SELL Grade B → max Tier 2 (bukan Tier 3)
- SOL CVD_FLIP LONG conf=72 + BTC ALIGNED + FR clean → Tier 3 (pass)
- HYPE WHALE_ACTIVITY LONG conf=75 + BTC DIVERGENT → 70% → Tier 2

---

## [2026-04-08] Data Quality Gate

### Commit: `5339f9d`
Block alerts yang tidak punya price data atau CVD data. Mencegah alert kosong/misleading.

---

## [2026-04-08] Per-Type REST Dedup Cooldowns

### Commit: `bf0c758`
- OI_SPIKE / WHALE: 30 min cooldown
- CVD_FLIP: 5 min cooldown
- Sebelumnya semua pakai cooldown yang sama.

---

## [2026-04-08] Divergence Abs Floor $500K

### Commit: `dbf1fb5`
Raise divergence absolute floor dari $10K ke $500K. Mencegah false trigger pada coin kecil seperti XRP.

---

## [2026-04-08] Trigger/Invalidation Bahasa Indonesia

### Commit: `8628e29`
- Trigger dan invalidation text di dashboard sekarang dalam Bahasa Indonesia
- RANGE regime sekarang actionable (bukan WAIT)

---

## [2026-04-08] Regime-is-King

### Commit: `d4e39d1`
AI analysis harus respect regime — tidak boleh LONG dalam DISTRIBUTION, tidak boleh SHORT dalam ACCUMULATION.

---

## [2026-04-07] Relative Thresholds

### Commit: `f78466e`
Convert semua detector thresholds ke relative (volume-scaled). Tidak lagi pakai absolute USD untuk CVD/taker filters.

---

## [2026-04-07] 3-Tier Alert System + Early Detection

### Commit: `d986696`
- Tier 3 (CONFIRMED): Signal proven, confidence >= 72
- Tier 2 (EARLY): Stealth accumulation, CVD divergence
- Tier 1 (WARNING): Taker exhaustion, climactic moves
- Phase 1: Noise filter (confidence gate, anti-flip, directional consistency)
- Phase 2: Taker exhaustion detector, climactic detector, stealth CVD divergence
- BTC macro label di setiap alert
- Channel routing 3 tier

---

## [2026-04-07] Stricter Alert Grading

### Commit: `6db35ab`
Grade A sekarang butuh absolute flow minimum (tidak cukup hanya CVD alignment). Mencegah Grade A pada flow yang terlalu kecil.

---

## [2026-04-07] AI Analysis Rebalance

### Commit: `356cf0f`
Rebalance AI analysis prompt — sebelumnya terlalu defensive (42% NEUTRAL rate). Sekarang lebih decisive.
