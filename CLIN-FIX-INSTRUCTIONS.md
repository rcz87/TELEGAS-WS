# INSTRUKSI PERBAIKAN TELEGAS-WS

## ATURAN UTAMA - BACA INI DULU SEBELUM NGAPA-NGAPAIN

```
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
!! JANGAN TULIS ULANG FILE DARI NOL                       !!
!! JANGAN HAPUS FUNGSI YANG SUDAH ADA                     !!
!! JANGAN REFACTOR BESAR-BESARAN                          !!
!! JANGAN UBAH ARSITEKTUR ATAU STRUKTUR FOLDER            !!
!! JANGAN GANTI NAMA CLASS/FUNGSI/VARIABEL YANG SUDAH ADA !!
!! JANGAN TAMBAH DEPENDENCY BARU KECUALI DIMINTA          !!
!! GUNAKAN EDIT TOOL, BUKAN WRITE TOOL                    !!
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
```

---

## CARA KERJA

1. BACA dulu file yang mau diubah dengan Read tool
2. PAHAMI kode yang sudah ada
3. EDIT hanya bagian yang perlu diubah (gunakan Edit tool, BUKAN Write tool)
4. JANGAN sentuh kode lain di file yang sama jika tidak terkait
5. TEST setelah setiap perubahan
6. COMMIT setelah setiap task selesai (1 commit per task)

---

## TASK LIST (KERJAKAN SESUAI URUTAN)

---

### TASK 1: Tambah API Authentication di Dashboard
**File:** `src/dashboard/api.py`
**Apa yang dilakukan:** Tambah simple Bearer token auth
**JANGAN:** Ubah endpoint yang sudah ada, ubah response format, atau install OAuth library

**Langkah:**
1. Baca `src/dashboard/api.py`
2. Baca `config/config.yaml`
3. Tambahkan key baru di `config/config.yaml` di bawah section `dashboard`:
   ```yaml
   dashboard:
     # ... yang sudah ada jangan diubah ...
     api_token: "GANTI_DENGAN_TOKEN_RANDOM_YANG_PANJANG"
   ```
4. Di `src/dashboard/api.py`, tambahkan dependency function untuk auth:
   ```python
   from fastapi import Depends, Header

   async def verify_token(authorization: str = Header(None)):
       """Simple Bearer token auth."""
       if not API_TOKEN:
           return True  # Skip auth jika token tidak di-set di config
       if not authorization or not authorization.startswith("Bearer "):
           raise HTTPException(status_code=401, detail="Missing token")
       if authorization.replace("Bearer ", "") != API_TOKEN:
           raise HTTPException(status_code=403, detail="Invalid token")
       return True
   ```
5. Tambahkan `Depends(verify_token)` HANYA di endpoint yang mengubah data:
   - `POST /api/coins/add`
   - `DELETE /api/coins/{symbol}`
   - Endpoint GET biarkan public (read-only aman)
6. Load `API_TOKEN` dari config saat init, simpan di module-level variable

**JANGAN ubah:** Endpoint signatures, response models, WebSocket endpoint, CORS settings (itu task terpisah)

---

### TASK 2: Restrict CORS Origins
**File:** `src/dashboard/api.py`
**Apa yang dilakukan:** Ganti `allow_origins=["*"]` dengan origins yang spesifik

**Langkah:**
1. Baca `src/dashboard/api.py`
2. Baca `config/config.yaml`
3. Tambahkan di `config/config.yaml` di bawah section `dashboard`:
   ```yaml
   dashboard:
     # ... yang sudah ada jangan diubah ...
     cors_origins:
       - "http://localhost:3000"
       - "http://localhost:8080"
       - "http://31.97.107.243:8080"
   ```
4. Di `api.py`, ubah HANYA baris `allow_origins=["*"]` menjadi:
   ```python
   allow_origins=config.get("dashboard", {}).get("cors_origins", ["http://localhost:3000"]),
   ```

**JANGAN ubah:** Middleware lain, allow_methods, allow_headers

---

### TASK 3: Hapus Dead Code (Stub Classes)
**File:** `src/connection/heartbeat_manager.py` dan `src/connection/subscription_manager.py`
**Apa yang dilakukan:** Hapus isi stub yang tidak dipakai, tapi JANGAN hapus file-nya

**Langkah:**
1. Baca kedua file
2. Cari di SELURUH codebase apakah ada yang import dari file ini:
   ```
   grep -r "heartbeat_manager" src/
   grep -r "subscription_manager" src/
   grep -r "HeartbeatManager" src/
   grep -r "SubscriptionManager" src/
   ```
3. Jika TIDAK ada yang import:
   - Kosongkan class body, ganti dengan docstring yang menjelaskan kenapa stub:
   ```python
   """
   Stub module - not used.
   Heartbeat handled by websockets library automatically.
   Subscription handled directly in main.py on_connect().
   Kept for potential future use.
   """
   ```
4. Jika ADA yang import: JANGAN SENTUH file ini. Laporkan saja.

**JANGAN:** Hapus file, ubah `__init__.py`, ubah import di file lain

---

### TASK 4: Track Fire-and-Forget Tasks
**File:** `main.py`
**Apa yang dilakukan:** Simpan referensi asyncio tasks agar tidak hilang

**Langkah:**
1. Baca `main.py`
2. Cari semua `asyncio.create_task(self.analyze_and_alert`
3. Di `__init__` dari class utama, tambahkan:
   ```python
   self._analysis_tasks: set = set()
   ```
4. Ganti setiap:
   ```python
   asyncio.create_task(self.analyze_and_alert(symbol))
   ```
   Menjadi:
   ```python
   task = asyncio.create_task(self.analyze_and_alert(symbol))
   self._analysis_tasks.add(task)
   task.add_done_callback(self._analysis_tasks.discard)
   ```

**JANGAN ubah:** Logic di dalam `analyze_and_alert()`, urutan pemanggilan, parameter apapun

---

### TASK 5: Tambah Alert Saat Buffer Overflow
**File:** `src/processors/buffer_manager.py`
**Apa yang dilakukan:** Log warning saat buffer mulai penuh (>80% capacity)

**Langkah:**
1. Baca `src/processors/buffer_manager.py`
2. Di method `add_liquidation` dan `add_trade`, SETELAH baris yang increment `_dropped_liquidations` atau `_dropped_trades`, tambahkan:
   ```python
   if self._dropped_liquidations % 100 == 1:  # Log setiap 100 drops, bukan setiap kali
       self.logger.warning(
           f"Buffer overflow for {symbol}: {self._dropped_liquidations} liquidations dropped total"
       )
   ```
   Dan sama untuk trades.

**JANGAN ubah:** maxlen, deque logic, method signatures, return values

---

### TASK 6: Pindahkan Magic Numbers ke Config
**File:** `src/processors/buffer_manager.py`, `src/analyzers/stop_hunt_detector.py`, `config/config.yaml`
**Apa yang dilakukan:** Pindahkan hardcoded numbers ke config, tapi PAKAI NILAI DEFAULT YANG SAMA

**Langkah:**
1. Baca `config/config.yaml`
2. Tambahkan section baru (JANGAN ubah yang sudah ada):
   ```yaml
   buffers:
     max_liquidations_per_symbol: 1000
     max_trades_per_symbol: 500

   detection:
     stop_hunt_threshold_usd: 2000000
     absorption_threshold_usd: 100000
     analysis_window_seconds: 30
   ```
3. Di `buffer_manager.py`, ubah constructor agar terima config values dengan DEFAULT yang sama:
   ```python
   def __init__(self, max_liquidations=1000, max_trades=500):
   ```
   Pastikan default value SAMA PERSIS dengan yang hardcoded sekarang.
4. Di `stop_hunt_detector.py`, sama - terima dari config tapi default SAMA:
   ```python
   def __init__(self, threshold=2000000, absorption_threshold=100000, window=30):
   ```
5. Di `main.py`, pass config values saat instantiate. Baca dari config, fallback ke default.

**KRITIS: Jika tidak ada config, behavior harus IDENTIK dengan sekarang. Jangan ubah default values.**

---

### TASK 7: Tambah WebSocket Authentication di Dashboard
**File:** `src/dashboard/api.py`
**Apa yang dilakukan:** Tambah optional token check di WebSocket connection

**Langkah:**
1. Baca `src/dashboard/api.py`, fokus ke `websocket_endpoint`
2. Tambahkan token check di awal WebSocket handler:
   ```python
   @app.websocket("/ws")
   async def websocket_endpoint(websocket: WebSocket):
       # Optional auth check
       if API_TOKEN:
           token = websocket.query_params.get("token", "")
           if token != API_TOKEN:
               await websocket.close(code=4003, reason="Invalid token")
               return
       await websocket.accept()
       # ... sisa kode JANGAN DIUBAH ...
   ```

**JANGAN ubah:** WebSocket message handling, broadcast logic, client management

---

### TASK 8: Tambah Rate Limiting di REST API
**File:** `src/dashboard/api.py`, `requirements.txt`
**Apa yang dilakukan:** Tambah simple rate limiting tanpa dependency baru

**Langkah:**
1. Baca `src/dashboard/api.py`
2. JANGAN install slowapi atau dependency baru
3. Buat simple in-memory rate limiter di file yang sama:
   ```python
   from collections import defaultdict
   import time

   _rate_limit_store: dict = defaultdict(list)
   _RATE_LIMIT = 30  # requests per minute

   async def check_rate_limit(request: Request):
       client_ip = request.client.host if request.client else "unknown"
       now = time.time()
       # Cleanup old entries
       _rate_limit_store[client_ip] = [
           t for t in _rate_limit_store[client_ip] if now - t < 60
       ]
       if len(_rate_limit_store[client_ip]) >= _RATE_LIMIT:
           raise HTTPException(status_code=429, detail="Too many requests")
       _rate_limit_store[client_ip].append(now)
   ```
4. Tambahkan `Depends(check_rate_limit)` di endpoint POST dan DELETE saja

**JANGAN:** Install package baru, ubah endpoint logic, ubah response format

---

## SETELAH SEMUA TASK SELESAI

1. Jalankan semua test yang ada:
   ```bash
   cd /home/user/TELEGAS-WS
   python -m pytest scripts/ -v 2>/dev/null || python scripts/test_processors.py && python scripts/test_analyzers.py && python scripts/test_signals.py && python scripts/test_alerts.py
   ```
2. Cek syntax semua file yang diubah:
   ```bash
   python -m py_compile src/dashboard/api.py
   python -m py_compile src/processors/buffer_manager.py
   python -m py_compile src/analyzers/stop_hunt_detector.py
   python -m py_compile main.py
   ```
3. Pastikan aplikasi bisa start:
   ```bash
   timeout 10 python main.py --dry-run 2>&1 || echo "OK jika exit dengan timeout"
   ```

---

## CHECKLIST FINAL

Sebelum commit terakhir, pastikan:
- [ ] Tidak ada file baru yang tidak perlu
- [ ] Tidak ada fungsi yang hilang/terhapus
- [ ] Semua import masih valid
- [ ] Config lama tidak berubah (hanya ditambah)
- [ ] Default values SAMA dengan sebelumnya
- [ ] Test lama masih pass
- [ ] Tidak ada `print()` statement yang tertinggal (gunakan logger)

---

## YANG TIDAK BOLEH DILAKUKAN (DAFTAR LARANGAN)

1. JANGAN buat file `src/middleware/auth.py` atau file baru lainnya kecuali diminta
2. JANGAN refactor `main.py` jadi file-file kecil (itu task terpisah, bukan sekarang)
3. JANGAN upgrade dependency versions di requirements.txt
4. JANGAN ubah logging format atau level
5. JANGAN tambah database/SQLite/Redis untuk state persistence (bukan scope ini)
6. JANGAN ubah Telegram message format
7. JANGAN ubah signal detection logic/thresholds (hanya pindahkan ke config)
8. JANGAN hapus komentar atau docstring yang sudah ada
9. JANGAN tambah type hints ke kode yang tidak kamu ubah
10. JANGAN "improve" kode yang tidak terkait task di atas
