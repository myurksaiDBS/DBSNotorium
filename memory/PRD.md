# Notarium — Product Requirements

## Product
Notarium is a professional notary public journal & compliance app built for **Domini Business Services**. Multi-tenant SaaS: each notary manages their own journal, clients, intake portal, subscription, and firm brand under Domini's umbrella.

## User Personas
- **Notary** — daily journal, PDF exports, signer intake, commission tracking, own subscription
- **Firm Admin (Matthew)** — super-admin roster view, invites colleagues, bypasses billing

## Tech Stack
- Frontend: React + Tailwind + Shadcn UI, react-router
- Backend: FastAPI + Motor (async Mongo), JWT auth via httpOnly cookies
- PDF: ReportLab • QR: qrcode • Image validation: Pillow
- Email: Emergent-managed Resend
- Payments: **Stripe** (claimable sandbox, subscriptions)

## Backend Layout (post-Phase-3 partial split)
```
backend/
  server.py          # 1993 lines — auth, journal, intake, admin, PDF renderers, renewal-checklist, presets
  deps.py            # 114 lines — send_email, draw_domini_stamp, firm_logo_flowable, LOGO_PATH, EMAIL_BASE_URL
  routes/
    __init__.py
    billing.py       # 333 lines — Stripe subscriptions + webhook + require_active_subscription
    invites.py       # 262 lines — invite CRUD + public accept flow (atomic claim)
    alerts.py        # 324 lines — commission-alerts + bounce-digest cron routes + email renderers
```
server.py went from 2800 → 1993 lines across Phases 1-3 (billing → invites → deps → alerts).

## Implemented (chronological highlights)
- Auth (JWT, register/login/logout, brute-force lockout, admin seed)
- Journal (CRUD entries + signature canvas)
- Clients + Document Types
- PDF exports with Domini watermark + notary seal (both single-entry cert and multi-entry journal)
- Signer Intake portal + QR posters + firm branding on public page
- Commission expiration email alerts (daily cron)
- Entry Certificates (emailed PDFs, delivery log, resend button)
- Renewal Checklists (CA/TX/FL state-specific + custom user steps, atomic 20-cap)
- Firm branding + multi-notary isolation + admin Team Roster
- Stripe Billing Per Notary (14-day trial, monthly $19 / yearly $190, hard block after expiry)
- Firm Logo + Firm Profile (address/phone/website/license) — rendered in cert + journal PDF footers
- Invite by Email (7-day expiry, atomic accept race)
- Login Lockout Hardening (email-only identifier, atomic `$inc`)
- Delivery Bounce Digest (8:30 UTC cron + preview + one-click resend)
- Public Intake Branding (firm logo + name + website)
- Shared Helpers Module (`deps.py`)
- Journal Search Presets (save named filters, 20-preset cap, dedup by name)
- **Preset Export (Feb 2026)**: each saved-filter chip now has a Download button that PDF-exports THAT preset's date range with the preset name in the filename.
- **Alerts Module Extraction (Feb 2026)**: commission-alerts + bounce-digest + email renderers moved to `routes/alerts.py`. server.py shrank from 2338 → 1993 lines.

## Key API Endpoints
- Auth: `/api/auth/{register,login,logout,me,refresh,profile,change-password,seal,firm-logo}`
- Journal: `/api/entries`, `/api/entries/{id}/{certificate.pdf,email-certificate,email-history}`, `/api/entries/export/pdf`
- Presets: `/api/journal-presets`, `/api/journal-presets/{id}`
- Signers: `/api/clients`, `/api/document-types`
- Intake: `/api/intakes`, `/api/public/intake/{token}`, `/api/intakes/{id}/qr-poster.pdf`
- Alerts (extracted): `/api/alerts/{status,test,bounces}`, `/api/cron/{commission-alerts,delivery-bounces,delivery-bounces/preview}`
- Renewal: `/api/renewal-checklist`, `/api/renewal-checklist/toggle`, `/api/renewal-checklist/custom`, `/api/renewal-checklist/custom/{id}`
- Admin: `/api/admin/roster`
- Invites (extracted): `/api/invites`, `/api/invites/{id}`, `/api/public/invite/{token}`, `/api/public/invite/{token}/accept`
- Billing (extracted): `/api/billing/{status,checkout,session/{id},portal}`, `/api/stripe/webhook`

## Data Models (Mongo)
- `users` {…, custom_checklist_items[], firm_*, subscription_*, alert dedup fields …}
- `journal_presets` {user_id, name, q, type, start_date, end_date, created_at}
- `entries`, `clients`, `document_types`, `signer_intakes`
- `invites`, `login_attempts`, `certificate_email_log`, `cron_runs`, `payment_transactions`

## Backlog / Roadmap
### P1 — Continue route extraction
- **intake module** — public intake + QR poster + intake CRUD → `routes/intake.py` (still in server.py; entangled with `_render_intake_poster_pdf`)
- **journal module** — entries CRUD + certificate PDF + presets → `routes/journal.py`
- **auth module** — session cookies + register/login/lockout + seal/firm-logo/profile → `routes/auth.py`

### P2 — Feature backlog
- **Multi-State Commissions** — hold CA+NV etc. with separate alerts and separate checklists
- **Bulk Signer Import** — CSV upload to seed the client database
- **Client Portal Follow-ups** — auto-remind signers whose intake link is still pending 24h before appointment
- **Preset Sharing** — copy a preset link so another notary can adopt the same filter

### Hardening
- Explicit-origin credentialed CORS (currently `allow_origins=['*']`, `allow_credentials=False`)
- Async Stripe SDK — move blocking Stripe calls to a threadpool
- Migrate legacy certificate_email_log rows to unified schema then drop the fallback code paths

## Verification (this iteration)
- `import server` OK after alerts extraction
- `GET /api/alerts/status` → 200 with commission days_left field
- `GET /api/alerts/bounces` → 200
- `POST /api/cron/commission-alerts` (no auth) → 401
- `POST /api/cron/delivery-bounces` (no auth) → 401
- Journal export PDF: 38061 bytes valid `%PDF`
- Single-entry certificate PDF: 41730 bytes valid `%PDF`
- Preset export button verified via UI wire (calls `/api/entries/export/pdf?start_date=…&end_date=…` using the preset's saved dates, downloads as `notary-journal-<preset-slug>.pdf`)

## Test Reports
`/app/test_reports/iteration_{1..20}.json`
