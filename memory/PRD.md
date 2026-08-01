# Notarium — Product Requirements

## Product
Notarium is a professional notary public journal & compliance app built for **Domini Business Services**. Multi-tenant SaaS: each notary manages their own journal, clients, intake portal, subscription, and firm brand under Domini's umbrella.

## Original Problem Statement
Design and build a log application for a notary public with:
- Journal entries, Client/signer database with search
- Document types tracking, PDF export of logs (legal/audit)
- Dashboard with monthly stats & revenue tracking
- JWT-based custom auth
- Digital signature pad (canvas)
- Professional/Legal vibe (navy, serif) branded for Domini Business Services

## User Personas
- **Notary** — daily journal, PDF exports, signer intake, commission tracking, own subscription
- **Firm Admin (Matthew)** — super-admin roster view, invites colleagues, bypasses billing

## Tech Stack
- Frontend: React + Tailwind + Shadcn UI, react-router
- Backend: FastAPI + Motor (async Mongo), JWT auth via httpOnly cookies
- PDF: ReportLab • QR: qrcode • Image validation: Pillow
- Email: Emergent-managed Resend
- Payments: **Stripe** (claimable sandbox, subscriptions)

## Implemented (chronological highlights)
- Auth (JWT, register/login/logout, brute-force lockout, admin seed)
- Journal (CRUD entries + signature canvas)
- Clients + Document Types
- PDF exports with Domini watermark + custom notary seal
- Signer Intake portal (public pre-fill, PII-safe) + QR posters
- Commission expiration email alerts (daily cron, Resend)
- Entry Certificates (emailed PDFs, delivery log, resend button)
- State-specific renewal checklists (CA/TX/FL, auto-unlock at 30 days)
- Firm branding + multi-notary isolation, admin **Team Roster** view
- **Stripe Billing Per Notary** (Feb 2026): monthly $19 / yearly $190, 14-day free trial
- **Firm Logo Upload** (Feb 2026): PIL-validated on certificates + QR posters
- **Invite by Email** (Feb 2026): atomic invite claim + unique email index
- **Login Lockout Hardening** (Feb 2026): email-only identifier, atomic `$inc`
- **Firm Profile** (Feb 2026): rendered in single-entry certificate footer
- **Delivery Bounce Alerts** (Feb 2026): daily 8:30 UTC digest with one-click resend
- **Custom Checklist Steps** (Feb 2026): up to 20 personal steps, atomic 20-cap
- **Public Intake Branding** (Feb 2026): firm logo + name on public intake page
- **Firm Footer on Journal Export** (Feb 2026): multi-entry PDF also carries the footer
- **Shared Helpers Module** (Feb 2026): `/app/backend/deps.py` centralizes `send_email`, `draw_domini_stamp`, `firm_logo_flowable`, `LOGO_PATH`, `EMAIL_BASE_URL`. Removes duplication and unblocks the next round of route extractions (alerts, intake, journal, auth).
- **Journal Search Presets** (Feb 2026): notaries save named filters (`GET/POST/DELETE /api/journal-presets`) — chip row above the filter bar for one-click apply, "Name this filter"→"Save" mini-form appears whenever any filter is active, dedup by name, 20-preset cap. Applying a preset highlights it; changing any filter clears the highlight.
- **Modular Refactor Phases 1-2** (Feb 2026): billing → `routes/billing.py` (333 lines), invites → `routes/invites.py` (262 lines). server.py: 2800 → ~2340 lines (delta after deps.py extraction).

## Backend Layout
```
backend/
  server.py          # ~2340 lines — auth, journal, alerts, intake, admin, PDF, renewal-checklist, presets
  deps.py            # 114 lines — send_email, draw_domini_stamp, firm_logo_flowable, LOGO_PATH, EMAIL_BASE_URL
  routes/
    __init__.py
    billing.py       # 333 lines — Stripe subscriptions + webhook + require_active_subscription
    invites.py       # 262 lines — invite CRUD + public accept flow
```

## Key API Endpoints
- Auth: `/api/auth/{register,login,logout,me,refresh,profile,change-password,seal,firm-logo}`
- Journal: `/api/entries`, `/api/entries/{id}/{certificate.pdf,email-certificate,email-history}`, `/api/entries/export/pdf`
- Presets: `/api/journal-presets`, `/api/journal-presets/{id}`
- Signers: `/api/clients`, `/api/document-types`
- Intake: `/api/intakes`, `/api/public/intake/{token}`, `/api/intakes/{id}/qr-poster.pdf`
- Alerts: `/api/alerts/{status,test,bounces}`, `/api/cron/{commission-alerts,delivery-bounces,delivery-bounces/preview}`
- Renewal: `/api/renewal-checklist`, `/api/renewal-checklist/toggle`, `/api/renewal-checklist/custom`, `/api/renewal-checklist/custom/{id}`
- Admin: `/api/admin/roster`
- Invites: `/api/invites`, `/api/invites/{id}`, `/api/public/invite/{token}`, `/api/public/invite/{token}/accept`
- Billing: `/api/billing/{status,checkout,session/{id},portal}`, `/api/stripe/webhook`

## Data Models (Mongo)
- `users` {…, custom_checklist_items[], firm_*, subscription_*, …}
- `journal_presets` {user_id, name, q, type, start_date, end_date, created_at}
- `entries`, `clients`, `document_types`, `signer_intakes`
- `invites`, `login_attempts`, `certificate_email_log`, `cron_runs`, `payment_transactions`

## Backlog / Roadmap
### P1 — Continue route extraction now that deps.py is in place
- **alerts module** — commission-alerts + bounce-digest + `_render_commission_email`/`_render_bounce_digest_email` → `routes/alerts.py`, reusing `deps.send_email`
- **intake module** — public intake + QR poster + intake CRUD → `routes/intake.py`, reusing `deps.draw_domini_stamp`/`firm_logo_flowable`
- **journal module** — entries CRUD + certificate PDF + email-certificate → `routes/journal.py`
- **auth module** — session cookies + register/login/lockout + seal/firm-logo/profile → `routes/auth.py`

### P2 — Feature backlog
- **Multi-State Commissions** — hold CA+NV etc. with separate alerts and separate checklists
- **Bulk Signer Import** — CSV upload to seed the client database
- **Client Portal Follow-ups** — auto-remind signers whose intake link is still pending 24h before appointment
- **Preset Export** — PDF-export the currently-applied preset in one click (currently it exports whatever the visible filter is; a "Export this preset" pill on each chip would tighten the loop)

### Hardening
- Explicit-origin credentialed CORS (`allow_origins=['*']`, `allow_credentials=False`)
- Async Stripe SDK — move blocking Stripe calls to a threadpool
- Migrate legacy certificate_email_log rows to unified schema

## Test Reports
`/app/test_reports/iteration_{1..20}.json` — iteration_20 confirmed all fixes with 56/57 targeted + regression tests. This iteration verified via curl: helper promotion preserves journal + certificate PDF generation and `/api/alerts/status`; journal-preset CRUD works end-to-end including dup-name/empty-name/404-unknown validation.
