# Panduan Penggunaan Dashboard TELEGLAS

URL: https://teleglas.guardiansofthetoken.org/dashboard/

---

## Layout Dashboard (Atas ke Bawah)

### 1. HEADER
Pojok kanan atas: status koneksi dan timestamp data terakhir.

| Status | Artinya |
|---|---|
| `LIVE` (hijau) | Data real-time, aman untuk analisa |
| `RECONNECTING` (kuning) | Sedang reconnect, data mungkin delay |
| `OFFLINE` (merah) | Tidak ada data — jangan ambil keputusan |

---

### 2. BTC MACRO TICKER
Bar horizontal paling atas — BTC sebagai barometer pasar global.

| Kondisi BTC | Artinya untuk Altcoin |
|---|---|
| BTC naik + OI naik | Risk-on, altcoin cenderung ikut naik |
| BTC turun + OI drop | Risk-off / deleveraging, hati-hati long altcoin |
| BTC sideways + OI flat | Range, cari setup di coin spesifik |

**Selalu lihat BTC dulu sebelum analisa coin manapun.**

---

### 3. COIN SELECTOR
Baris tombol coin: SOL, ETH, AVAX, SUI, HYPE, XRP, BNB.

- Klik tombol untuk switch coin
- Pakai **search box** untuk coin lain (ketik nama, hasil di-rank berdasarkan score tertinggi)
- Coin yang dipilih akan ditampilkan di semua section di bawahnya

---

### 4. COIN HEADER
Menampilkan informasi utama coin terpilih.

| Elemen | Cara Baca |
|---|---|
| Nama coin | Coin yang sedang dianalisa |
| Badge LONG (hijau) | Sistem condong bullish |
| Badge SHORT (merah) | Sistem condong bearish |
| Badge NEUTRAL (abu) | Belum ada bias jelas |
| Harga | Harga terakhir |
| % Change | Perubahan 24 jam |

**Badge bias bukan signal entry — hanya indikasi arah. Jangan entry hanya karena badge.**

---

### 5. TRADINGVIEW CHART
Chart Binance Perpetual full-size.

**Fungsi:** Melihat price action — support/resistance, trend line, pattern candle.

**Cara gabungkan dengan dashboard:**
- Dashboard kasih tahu *mengapa* harga bergerak (CVD, OI, whale)
- Chart kasih tahu *di mana* levelnya (support, resistance, range)
- Keduanya harus aligned sebelum entry

---

### 6A. REGIME + SNIPER DECISION

**Ini section paling penting di seluruh dashboard.**

#### REGIME — Kondisi pasar saat ini

| Regime | Artinya | Yang harus dilakukan |
|---|---|---|
| ACCUMULATION | Smart money mengumpulkan, harga sideways/naik pelan | Cari entry LONG saat trigger terpenuhi |
| DISTRIBUTION | Smart money distribusi, harga mulai lemah | Cari entry SHORT saat trigger terpenuhi |
| LIQUIDATION | Forced selling/buying, OI drop, harga flush | Ikuti arah flush, atau tunggu reversal |
| SQUEEZE | Funding extreme + posisi crowded, siap squeeze | Masuk melawan crowd saat trigger |
| RANGE | Tidak ada edge, CVD konflik, OI flat | **Jangan masuk — tunggu regime berubah** |

#### STATUS — Kesiapan setup

| Status | Grade | Aksi |
|---|---|---|
| READY | A | Setup layak eksekusi — tunggu trigger |
| WATCH | B | Setup menarik tapi belum lengkap — pantau terus |
| WAIT | C | Belum ada setup — sabar |
| AVOID | D | Kondisi buruk — **jangan masuk** |

#### CONFIDENCE + LONG/SHORT SCORE

- **Confidence**: Seberapa yakin sistem tentang regime (0-95%)
- **Long Score**: Skor total indikator yang mendukung LONG
- **Short Score**: Skor total indikator yang mendukung SHORT
- **Selisih besar** antara Long dan Short = bias kuat
- **Selisih kecil** = tidak ada edge

#### TRIGGER — Kapan masuk

Teks spesifik yang memberitahu kondisi apa yang harus terpenuhi. Contoh:
- *"SpotCVD sustained rise + OI break above recent high"*
- *"FutCVD RISING sustain + funding staying negative"*

**Aturan emas: JANGAN entry sebelum trigger terpenuhi, walaupun Grade A.**

#### INVALIDATION — Kapan keluar

Kondisi yang membatalkan setup. Contoh:
- *"SpotCVD reversal to FALLING + OI drop >1%"*

**Kalau invalidation terjadi setelah kamu entry = signal cut loss.**

#### VETO

Sistem punya safety check otomatis. Walaupun semua indikator Grade A, kalau ada kontradiksi logis, sistem auto-downgrade ke Grade B. Contoh veto:
- OI spike terlalu cepat untuk accumulation
- Price sudah extended terlalu jauh
- Funding tidak extreme untuk squeeze

**Kalau ada VETO, hormati — sistem sudah cek kontradiksi yang mungkin kamu lewatkan.**

#### AI ANALYSIS

Tombol "ANALISA AI" memanggil Claude untuk analisa naratif. Berguna saat:
- Kamu bingung baca data
- Mau second opinion
- Mau penjelasan regime dalam bahasa manusia

---

### 6B. ANALYSIS GRID

#### ORDERFLOW INTELLIGENCE (2 kolom kiri) — Data Paling Leading

| Elemen | Cara Baca |
|---|---|
| Spot CVD | RISING = tekanan beli di spot. FALLING = tekanan jual. Ini paling organic |
| Futures CVD | RISING = tekanan beli di futures. FALLING = tekanan jual |
| Slope | Angka kemiringan — makin besar makin kuat trend-nya |
| Dominance bar | Spot vs Futures — siapa memimpin? Spot memimpin = lebih reliable |
| Tags | Ringkasan signal otomatis dalam chip warna |
| Insight | Narasi otomatis gabungan semua data flow |

**Kombinasi paling penting:**

| Spot CVD | Futures CVD | Artinya |
|---|---|---|
| RISING | RISING | **Strong buy** — keduanya beli |
| FALLING | FALLING | **Strong sell** — keduanya jual |
| RISING | FALLING | **Divergence** — hati-hati, sinyal konflik |
| FALLING | RISING | **Divergence** — hati-hati, sinyal konflik |
| FLAT | FLAT | Tidak ada tekanan — **tunggu** |

#### MARKET STRUCTURE (1 kolom kanan)

| Elemen | Cara Baca |
|---|---|
| Open Interest | OI naik = uang baru masuk. OI turun = posisi ditutup |
| OI Change % | Perubahan OI 1 jam — angka paling penting |
| Funding Rate | Positif = longs bayar shorts (crowded long). Negatif = sebaliknya |
| Orderbook | Bid vs Ask ratio — siapa yang lebih banyak pasang order |
| Positioning | Long/Short ratio — gauge visual |

**Tabel interpretasi OI + Price:**

| OI | Price | Label | Artinya |
|---|---|---|---|
| Naik | Naik | MOMENTUM | Posisi baru masuk searah trend — trend kuat |
| Naik | Turun | SHORT ADDING | Shorts agresif masuk — potensi squeeze jika salah |
| Turun | Naik | SHORT COVERING | Shorts tutup posisi — rally kurang reliable |
| Turun | Turun | DELEVERAGING | Forced liquidation — awas cascade |

#### TAKER + L/S
- **Taker Buy/Sell Volume**: Volume aggressive buyer vs seller
- **Long/Short Ratio**: Gauge visual dari exchange — crowded side = rawan squeeze

#### LIQUIDITY
- **Cluster di atas harga**: Level dimana SHORT akan ter-liquidasi (magnet naik)
- **Cluster di bawah harga**: Level dimana LONG akan ter-liquidasi (magnet turun)
- **Cluster yang lebih besar = magnet lebih kuat**

#### CVD HISTORY
Sparkline history Spot CVD dan Futures CVD. Lihat **trend**, bukan cuma angka sekarang. CVD yang baru saja flip dari FALLING ke RISING = signal paling kuat.

---

### 7. WHALE INTELLIGENCE

#### Tab ALERTS
Pergerakan posisi whale dari Hyperliquid.

| Badge | Artinya |
|---|---|
| MEGA (ungu) | Posisi > $10M — sangat penting |
| BIG (biru) | Posisi > $1M — penting |

**Cara pakai:**
- MEGA whale buka LONG + SpotCVD RISING = konfirmasi kuat untuk long
- Whale berlawanan dengan setup kamu = **pertimbangkan ulang**
- Banyak whale close posisi = anticipate volatility

#### Tab POSITIONS
Tabel posisi whale aktif: entry price, liquidation price, ukuran, arah.

**Yang paling penting**: Whale dengan posisi besar yang liquidation price-nya dekat harga sekarang = expect volatilitas besar di level itu. Bisa jadi target profit kamu atau danger zone.

---

### 7.5. SIGNAL INTELLIGENCE

| Elemen | Cara Baca |
|---|---|
| Primary Signal Card | Signal utama untuk coin terpilih — satu saja |
| Direction + Type | Contoh: "STOP_HUNT LONG" atau "DISTRIBUTION SHORT" |
| Confidence | Skor kualitas signal (70-99) |
| Effective Score | Confidence × freshness — turun seiring waktu |
| Status ACTIVE | Signal masih valid dan segar |
| Status WEAKENING | Signal hampir expired — verifikasi ulang |
| Status SUPERSEDED | Diganti signal lebih kuat — baca reason-nya |
| Freshness bar hijau | Signal baru, bisa dipercaya |
| Freshness bar kuning | Sudah aging, cek data live lagi |
| Freshness bar merah | Hampir expired, jangan entry baru |
| Expiry countdown | Berapa lama lagi signal ini valid |
| Recent history | Signal-signal sebelumnya yang sudah expired/diganti |
| All Active Signals | Semua coin yang punya signal aktif — klik untuk switch |

**Cara pakai:**
1. Lihat All Active Signals — pilih coin dengan effective score tertinggi
2. Baca primary signal: arah, confidence, freshness
3. Kalau freshness kuning/merah — jangan entry, tunggu signal baru
4. Kalau SUPERSEDED — baca reason, pahami kenapa bias berubah

---

### 8. FR EXTREMES
Perbandingan funding rate antar exchange.

- Satu exchange FR jauh lebih tinggi = crowding di exchange itu
- Spread besar antar exchange = peluang arbitrage
- Semua exchange FR extreme = pasar sangat crowded, rawan squeeze

---

## Workflow Analisa — 9 Langkah

```
Step 1: Cek BTC ticker
        → Risk-on atau risk-off? Tentukan agresivitas.

Step 2: Pilih coin
        → Lihat All Active Signals, pilih effective score tertinggi.
        → Atau browse coin selector.

Step 3: Baca regime
        → ACCUMULATION / DISTRIBUTION / LIQUIDATION / SQUEEZE / RANGE?
        → Status READY / WATCH / WAIT / AVOID?
        → Kalau RANGE atau AVOID → skip, cari coin lain.

Step 4: Baca orderflow
        → SpotCVD + FuturesCVD aligned?
        → Keduanya RISING = strong buy. Keduanya FALLING = strong sell.
        → Divergence = skip.

Step 5: Baca market structure
        → OI mendukung? (naik untuk long, turun untuk short-liq)
        → Funding rate melawan crowd? (bagus untuk squeeze)
        → Orderbook bid dominant? (bagus untuk long)

Step 6: Cek whale
        → Ada MEGA whale yang confirm bias?
        → Ada whale berlawanan? → hati-hati.

Step 7: Baca trigger
        → Trigger condition dari regime card sudah terpenuhi?
        → Kalau belum → TUNGGU. Jangan paksa entry.

Step 8: Cek signal lifecycle
        → Ada primary signal ACTIVE dengan freshness hijau?
        → Confidence di atas 80?
        → Kalau ya + semua di atas aligned → entry.

Step 9: Set invalidation
        → Pakai invalidation condition dari regime card.
        → Kalau kondisi itu terjadi setelah entry → cut loss.
```

---

## Aturan Keras

| Jangan | Kenapa |
|---|---|
| Entry hanya karena badge LONG/SHORT | Itu bias, bukan signal — perlu trigger |
| Entry saat status WAIT atau AVOID | Tidak ada edge — gambling |
| Abaikan VETO | Sistem sudah cek kontradiksi |
| Entry saat CVD spot dan futures berlawanan | Divergence = sinyal konflik |
| Entry signal freshness merah | Signal hampir expired, data sudah basi |
| Abaikan whale berlawanan | Smart money tahu sesuatu yang kamu tidak tahu |
| Hold saat invalidation terpenuhi | Setup sudah batal — protect capital |
| Entry tanpa cek BTC macro | Altcoin ikut BTC, selalu cek dulu |

---

## Contoh Analisa Lengkap

**Situasi**: Kamu buka dashboard, pilih SOL.

1. **BTC ticker**: BTC +1.2%, OI naik 0.5% → risk-on ringan ✓
2. **Regime**: ACCUMULATION, Grade A, Status READY ✓
3. **CVD**: SpotCVD RISING (slope 0.04), FutCVD RISING (slope 0.02) → aligned ✓
4. **OI**: +1.8% 1h, interpretasi MOMENTUM → mendukung ✓
5. **Funding**: -0.008% → negatif ringan, longs tidak crowded ✓
6. **Orderbook**: Bid 58% / Ask 42% → bid dominant ✓
7. **Whale**: 1 MEGA whale LONG $12M SOL baru buka → konfirmasi kuat ✓
8. **Trigger**: "SpotCVD sustained rise + OI break above recent high" → SpotCVD sudah RISING sustained, OI naik → trigger terpenuhi ✓
9. **Signal**: STOP_HUNT LONG, confidence 85%, freshness hijau (3 menit) ✓
10. **Invalidation**: "SpotCVD reversal to FALLING + OI drop >1%" → set alert di level ini

**Keputusan**: LONG SOL, invalidation clear.

---

**Situasi**: Kamu buka dashboard, pilih HYPE.

1. **BTC ticker**: BTC -0.3%, OI flat → neutral
2. **Regime**: RANGE, Grade C, Status WAIT
3. **CVD**: SpotCVD RISING, FutCVD FALLING → divergence

**Keputusan**: SKIP — regime RANGE + CVD divergence = tidak ada edge. Cari coin lain.
