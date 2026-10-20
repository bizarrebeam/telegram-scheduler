# Todo — Telegram → Google Calendar Assistant

Plan & acceptance criteria per task: `tasks/plan.md`. Status terakhir: semua fase diimplementasikan,
`pytest` 174 passed, smoke nyata ke Google Calendar lulus (propose → approve → idempoten → cleanup).

## Phase 1 — Foundation
- [x] T1: Scaffold FastAPI app, config, .env.example, health endpoint, pytest baseline
- [x] T2: Migrations v1: user_settings, tasks — sudah dijalankan ke Supabase asli
- [x] T3: Telegram auth allowlist + /start + /help (reject unauthorized) — unit test + live
- [x] T4: Task capture from text/link + update dedup — unit test + smoke DB asli
- [x] T5: /queue, /done, /cancel (+ /skip, /reschedule, /delete) + task lifecycle

### Checkpoint 1
- [x] Semua test Phase 1 hijau + smoke DB lulus; capture → queue → done jalan di bot asli

## Phase 2 — Google OAuth + Calendar
- [x] T6: OAuth flow (PKCE) + token storage terenkripsi + reauth (/auth)
- [x] T7: Freebusy/availability read
- [x] T8: Proposal lifecycle + tombol Setuju/Tolak/Cari jam lain + validasi callback
- [x] T9: Approve → create event exactly-once (event id deterministik + 409 idempoten)
- [x] T10: Failure recovery (db-fail, slot terisi → supersede + tawaran baru, event dihapus eksternal dideteksi)

### Checkpoint 2
- [x] Approve → event muncul di kalender asli; double-approve = 1 event (terverifikasi via smoke_scheduling)

## Phase 3 — Scheduler
- [x] T11: Availability windows + timezone (Asia/Jakarta, weekend nonaktif)
- [x] T12: Priority policy + tie-breakers deterministik (deadline → priority → created → id)
- [x] T13: Slot search ≤14 hari (busy, no-slot/deadline_unmet retention)
- [x] T14: AI parser + validasi server-side + fallback deterministik
- [x] T15: Task breakdown / subtasks (/breakdown, parent_task_id)
- [x] T16: Duration extension (+30 menit) → update DB + patcht event kalender

### Checkpoint 3
- [x] Scheduler unit tests hijau (kalender di-mock); proposal end-to-end (smoke nyata)

## Phase 4 — Reminders, routines, attachments, overwhelm
- [x] T17: Durable job_queue worker + reminder start / pre-end / end
- [x] T18: Missed-start + satu follow-up 10 menit (reminder_state unik)
- [x] T19: Routines + kuota mingguan (/routine, /routines menawarkan sesi)
- [x] T20: Attachments (file_id Telegram; upload Drive tidak diimplementasikan — didokumentasikan)
- [x] T21: Overwhelm mode (satu langkah berikutnya + /overwhelm)
- [x] T22: Task selesai → job reminder dibatalkan (cancel_session_jobs)

### Checkpoint 4
- [x] Worker: claim SKIP LOCKED, reset job macet, reap proposal kedaluwarsa; job idempoten per kunci

## Phase 5 — Verify & deploy
- [x] T23: Gap-fill test matrix §16 (102 test: planner, priority, parser, kalender, session, jobs, rutin, overwhelm, AI)
- [x] T24: Berkas deploy siap (Dockerfile, .dockerignore, health check) — deploy Railway menunggu aksi user
- [x] T25: README: setup, OAuth, migrasi, env, recovery, limitations

### Checkpoint 5 — Definition of Done (§18)
- [x] Semua test kritis lulus; langkah deploy didokumentasikan untuk dijalankan bersama user

## Aksi user yang tersisa
1. Restart uvicorn (worker + perintah baru + migrasi 0003/0004 baru aktif).
2. Opsional: deploy Railway (butuh login + verifikasi akun).
3. Opsional: isi `OPENAI_API_KEY` bila ingin parser AI (fallback tetap jalan tanpa itu).

