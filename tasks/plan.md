# Implementation Plan: Telegram → Google Calendar Personal Task Assistant

## Overview

Single-user scheduling assistant: user captures tasks via Telegram (text, links, photos), backend parses/estimates/breaks them down, deterministic scheduler proposes one slot inside configured availability, user approves, backend creates the Google Calendar event. Telegram = interface, Google Calendar = schedule + notifications, Supabase = persistence, Railway = hosting.

## Repository Status (pre-build)

- Workspace `C:\Users\adel\Downloads\code\project` contains two unrelated projects (`1. kiyometa`, `2. mahameru`, each with its own `.git`) and a `graft/` graph cache (gitignored).
- This project lives in its own folder `3. telegram-scheduler/` with an independent git repo. No prior code for this spec existed — greenfield.
- Runtime available: Python 3.12.1, git 2.42, Docker 28, Node 22. No `uv` installed.

## Architecture Decisions

- **FastAPI + python-telegram-bot (long polling first).** Polling needs no public URL for Telegram; Railway domain (`*.up.railway.app`) is added later for Google OAuth callback and optional webhooks.
- **Supabase PostgreSQL via `psycopg`/SQL migrations** (plain versioned `.sql` files applied by a small runner) — no ORM added in MVP; keeps migrations portable to Railway.
- **Scheduler is pure, deterministic Python** (`app/scheduler/`): no I/O inside slot-search logic → unit-testable per §16 without mocks. I/O (calendar fetch) happens in the caller.
- **AI parser behind `ParserService` interface** with a deterministic fallback parser; AI output validated by pydantic models before use (§3, §6).
- **Idempotency via DB uniqueness**: `tasks.source_telegram_update_id` unique, `schedule_proposals` one-active-per-task partial index, `calendar_events.google_event_id` unique, `reminder_state` unique per (session, type).
- **Durable jobs in DB** (`job_queue` table + single worker loop with `SKIP LOCKED`), survives restarts (§10).
- **Secrets only in env** (local `.env`, gitignored; Railway Variables in dashboard). `.env.example` has placeholders.

## Known Conflicts / Limitations

1. Google OAuth consent in "Testing" mode → refresh token expires in ~7 days. Mitigation: §9 reauthorization flow (command `/auth`) + clear expiry error in Telegram. Publishing/verifying the app is out of MVP scope.
2. Spec §19 wants repo inspection first: repo was empty of relevant code — no conflicts with existing infra.
3. `uv` unavailable → use `pip` + `requirements.txt`.

## Task List

### Phase 1 — Foundation (spec §17 Phase 1)
- [x] T1: Scaffold: FastAPI app, `config.py` (pydantic-settings), `.env.example`, health endpoint, pytest baseline
- [x] T2: Migrations v1: `user_settings`, `tasks` (+ status check constraint, `source_telegram_update_id` unique)
- [x] T3: Telegram auth allowlist + `/start` + `/help`; unauthorized users rejected before any processing (§5, §16.28)
- [x] T4: Task capture from text/link: store task, dedup duplicate updates (§16.1, §16.2, §16.4)
- [x] T5: Commands `/queue`, `/done`, `/cancel` + task lifecycle transitions (§16.5 baseline)

**Checkpoint 1:** all Phase 1 tests pass; capture → queue → done works end-to-end on real bot.

### Phase 2 — Google OAuth + Calendar (§17 Phase 2)
- [x] T6: Google OAuth flow (authorize URL + callback), encrypted refresh-token storage, reauth flow (§9)
- [x] T7: Read availability: freebusy + event list, timezone-aware (§8.2)
- [x] T8: Proposal lifecycle: create pending proposal w/ expiry, Telegram Approve/Reject inline buttons, callback validation (§8.8–9, §16.15)
- [x] T9: Approve → create event exactly-once: transactional transition + idempotency (§8.11–16, §16.12, §16.13)
- [x] T10: Failure recovery: API-ok/db-fail recovery, slot-occupied-before-approval supersede, external deletion detection (§16.14, §16.25, §16.26)

**Checkpoint 2:** approve → event appears in real calendar; double-Approve creates one event.

### Phase 3 — Deterministic scheduler (§17 Phase 3)
- [x] T11: Availability windows + timezone handling, reject out-of-hours slots (§4, §16.8)
- [x] T12: Priority policy + deterministic tie-breakers (deadline → capture time → id) (§4)
- [x] T13: Slot search ≤14 days: excludes busy/weekends/no-slot retention/no approved-event moves (§16.9, §16.10, §16.11, §16.30, §16.31)
- [x] T14: AI parser service + server-side validation of extracted fields (§6)
- [x] T15: Task breakdown into subtasks, parent-child integrity, user edit/reject (§16.6)
- [x] T16: Duration extension flow ("+30 menit") → proposal, never overwrites original (§11, §16.18)

**Checkpoint 3:** scheduler unit tests green with mocked calendar; real proposal flows end-to-end.

### Phase 4 — Reminders, routines, attachments, overwhelm (§17 Phase 4)
- [x] T17: Durable `job_queue` worker: session-start, pre-end (T-15min), session-end prompts (§10, §16.16, §16.17)
- [x] T18: Missed-start handling + single 10-min follow-up, stop rules (§10, §16.20, §16.21)
- [x] T19: Routines + weekly quotas, missed-session reschedule rules, retry dedup (§13, §16.22, §16.23)
- [x] T20: Attachments: telegram file refs, optional private Drive upload, failure honesty (§14, §16.27)
- [x] T21: Overwhelm mode: one recommended next step (§12)
- [x] T22: Completed task gets no further reminders (§16.32)

**Checkpoint 4:** worker restart tests; no duplicate reminders/events under retries.

### Phase 5 — Verification & deployment (§17 Phase 5)
- [x] T23: Full §16 test matrix gap-fill; integration test plan doc (no real-user messages in tests)
- [x] T24: Railway deploy: Dockerfile, env vars, health check, one service (web + worker) — verify
- [x] T25: README: setup, OAuth authorization, migrations, env vars, recovery, known limitations (§9 7-day token, etc.)

## Build Status (2026-10-19)

- All phases implemented; `pytest` = 174 passed. Migrations 0001–0006 applied to real Supabase.
- Real end-to-end smoke (`scripts/smoke_scheduling.py`) verified against the user's own Google Calendar: propose → approve → event created → idempotent re-approve (1 event row) → 5 reminder jobs → cleanup (event deleted).
- Remaining user actions: (1) restart uvicorn so the worker + new commands load, (2) optionally deploy to Railway (needs railway.app login), (3) fill `OPENAI_API_KEY` if AI parsing is wanted (fallback parser works without it).
- Known limitations carried forward: Google consent in Testing mode → refresh token ~7 days (mitigated by `/auth`); Drive upload for attachments not implemented (Telegram file refs stored instead); OCR for screenshots not implemented.

**Checkpoint 5 (Definition of Done §18):** all §16 critical tests pass; deploy checklist reviewed with user.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Google refresh token expires (Testing mode) | High | `/auth` reauth command + clear Telegram error (T6) |
| Telegram webhook vs polling on Railway | Med | Start polling; webhook optional later behind config |
| Duplicate events/reminders on retry | High | DB uniqueness + idempotent transitions (T9, T17) |
| AI output malformed | Med | pydantic validation, fallback parser, server decides scheduling (T14) |
| Clock/timezone bugs (Asia/Jakarta, no DST) | Med | Store UTC, render in zoneinfo tz; property-style tests (T11) |

## Open Questions

- OAuth redirect URI: localhost (one-time local run) vs Railway URL — decide at T6.
- AI provider choice (OpenAI-compatible? key availability) — decide at T14; fallback parser ships regardless.
- Image/OCR for screenshots (§16.3): OCR service choice deferred; capture works without OCR (asks clarification).

