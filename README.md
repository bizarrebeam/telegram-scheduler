# Telegram → Google Calendar Personal Task Assistant

Asisten jadwal pribadi (single user). Kirim tugas lewat Telegram; backend memparsing,
menghitung durasi, mencari slot kosong di jam ketersediaanmu, mengirim satu proposal,
lalu membuat event Google Calendar setelah kamu tekan **Setuju**.

- Telegram = antarmuka (capture, reminder, tombol approve)
- Google Calendar = jadwal + notifikasi event
- Supabase Postgres = penyimpanan
- Railway = hosting (web + worker dalam satu proses)

## Isi repo

```
app/
  main.py              FastAPI + lifespan (polling Telegram + worker)
  config.py            Settings dari .env (pydantic-settings)
  db.py                Koneksi psycopg (+ dict row)
  parsing/basic.py     Parser deterministik (durasi, extension, overwhelm)
  parsing/ai.py        Parser AI (opsional) + validasi + fallback
  scheduler/           availability.py, priority.py, planner.py (pure, tanpa I/O)
  services/            tasks, proposals, sessions, session_actions, routines,
                       jobs, reminders, history, attachments, overwhelm, settings
  google/              oauth.py (PKCE), token_store.py (Fernet), calendar.py
  telegram_bot/        bot.py (handlers), ui.py (format + keyboard), auth.py
  workers/runner.py    Loop job_queue (SKIP LOCKED)
  web/oauth_routes.py  /oauth/google/callback
migrations/            0001..0006 (.sql)
scripts/               migrate.py, smoke_tasks.py, smoke_scheduling.py
tests/                 pytest (174 test, tanpa DB/network)
```

## Setup lokal

1. `python -m venv .venv` lalu `./.venv/Scripts/python.exe -m pip install -r requirements.txt`
2. Salin `.env.example` → `.env`, isi semua variabel (jangan pernah commit/paste `.env`).
   - `TELEGRAM_BOT_TOKEN` dari @BotFather; `TELEGRAM_ALLOWED_USER_IDS` id numerik dari @userinfobot.
   - `DATABASE_URL`: pakai **session pooler** Supabase
     (`postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require`).
     Host `db.<ref>.supabase.co` sering IPv6-only → error `getaddrinfo failed`.
   - `TOKEN_ENCRYPTION_KEY`: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
3. Terapkan migrasi: `./.venv/Scripts/python.exe -m scripts.migrate`
4. Jalankan: `./.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000 --host 127.0.0.1`
5. Telegram: `/start`, lalu `/auth` → buka link → Approve. Redirect URI harus
   `http://localhost:8000/oauth/google/callback` (juga didaftarkan di Google Cloud Console).

> Hanya jalankan SATU instance bot. Dua instance bersamaan → `telegram.error.Conflict`
> (`terminated by other getUpdates request`); matikan semua `python.exe` dulu.

## Perintah bot

| Perintah | Fungsi |
|---|---|
| kirim teks/foto | simpan tugas (+ lampiran), AI/fallback parse, langsung tawarkan slot |
| kirim foto/PDF **sebagai reply** ke pesan tugas | lampiran menempel ke tugas itu (bukan tugas baru) |
| `/files <id>` | bot kirim ulang foto/PDF lampiran tugas |
| `+30` / `tambah 20 menit` | tambah durasi sesi berjalan (kalender ikut di-update) |
| `bingung` / `kewalahan` | mode overwhelm: satu langkah berikutnya |
| `/today` | langkah berikutnya + agenda hari ini |
| `/queue` | tugas belum dijadwalkan |
| `/plan [id]` | carikan slot (tanpa id = tugas terbaru) |
| `/done <id>` `/skip <id>` `/cancel <id>` `/delete <id>` | lifecycle tugas |
| `/reschedule <id>` | batalkan sesi, cari slot baru |
| `/breakdown <id> \| langkah 1 ; langkah 2` | pecah tugas jadi subtask |
| `/overwhelm` | rekomendasi satu langkah |
| `/routines` `/routine <judul> \| <menit> \| <per pekan>` | rutinitas + kuota mingguan |
| `/deadline <id> <tanggal>` | tambah/ubah deadline all-day (butuh approval) |
| `/duration <id> <menit>` | ubah estimasi durasi persiapan |
| `/auth` | hubungkan / ganti akun Google |

Tombol inline: **Setuju / Tolak / Cari jam lain** pada proposal; **Selesai / +30 menit /
Jadwalkan ulang / Lewati / Mulai sekarang** pada reminder sesi.

## Alur jadwal (deterministik)

1. Capture → `tasks` (idempoten via `source_telegram_update_id`).
2. `planner.find_slot`: window ketersediaan (08:00–21:00 Asia/Jakarta, setiap hari
   termasuk Sabtu/Minggu), langkah 15 menit, horizon 14 hari, gap minimal 30 menit
   antar sesi tugas, hindari busy (freeBusy + sesi rencana sendiri), patuhi deadline,
   tidak pernah menjadwalkan ke masa lalu.
3. `schedule_proposals` (pending, kedaluwarsa 30 menit, satu pending per task) → tombol.
4. Setuju → event Google dengan **id deterministik = uuid sesi** (retry aman; 409 = sudah ada),
   `calendar_events` unik, `task_sessions.status=planned`, job reminder di-enqueue.
5. Reminder: mulai sesi, T-15 menit, akhir sesi, missed-start +10 menit, satu follow-up.
   Selesai/lewati/reschedule → job reminder dibatalkan (tidak ada reminder setelah selesai).
6. Deadline: frasa seperti `sampai 25 Oktober` disimpan sebagai `deadline_date`, lalu
   diusulkan sebagai event **all-day** terpisah (`DEADLINE: ...`). Approve deadline dan
   approve sesi persiapan adalah dua keputusan berbeda; sesi kerja tetap memakai window
   08:00–21:00 + gap 30 menit dan tidak pernah setelah deadline.

Prioritas: deadline terdekat → tanpa deadline di belakang → `priority` (1 = terpenting) →
`created_at` → id. Tie-break deterministik.

## Verifikasi

- `./.venv/Scripts/python.exe -m pytest` — 174 test (tanpa DB/network).
- `./.venv/Scripts/python.exe -m scripts.smoke_tasks` — CRUD tugas ke Supabase asli.
- `./.venv/Scripts/python.exe -m scripts.smoke_scheduling` — propose → approve → event
  Google nyata → idempotensi → cleanup (membuat & menghapus 1 event bertanda `[SMOKE]`).

## Deploy Railway

1. Push repo ke GitHub, buat project di railway.app (login + verifikasi akun), **Deploy from repo**.
2. Railway mendeteksi `Dockerfile`. Set Variables = isi `.env` (tanpa `DATABASE_URL` lokal).
3. Health check: `/health`. Satu service menjalankan web + worker + polling Telegram.
4. Ganti `GOOGLE_REDIRECT_URI` ke `https://<domain>.up.railway.app/oauth/google/callback`
   dan tambahkan URL itu di Google Cloud Console → Credentials → Authorized redirect URIs.
5. Setelah deploy, jalankan `/auth` sekali lagi (state OAuth disimpan in-memory, jadi
   link harus dibuat dari instance yang sedang berjalan).

## Batasan yang diketahui

- Google consent masih mode **Testing** → refresh token kedaluwarsa ~7 hari. Mitigasi: `/auth`.
- Upload lampiran ke Google Drive dan OCR screenshot belum diimplementasikan; lampiran
  disimpan sebagai `file_id` Telegram dan dikirim ulang lewat `/files <task_id>`
  (batas Telegram: file yang bisa dikirim ulang ≤20 MB).
- State OAuth (`_auth_states`) in-memory: restart server menghanguskan link `/auth` yang belum dipakai.
- Worker hanya satu proses (memakai `FOR UPDATE SKIP LOCKED`); jangan scale ke >1 replica.

