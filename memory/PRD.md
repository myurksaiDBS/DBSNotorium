# Notarium — Product Requirements

## Product
Notarium is a professional notary public journal & compliance app built for **Domini Business Services**. Multi-tenant SaaS: each notary manages their own journal, clients, and intake portal under Domini's umbrella.

## Original Problem Statement
Design and build a log application for a notary public with:
- Journal entries, Client/signer database with search
- Document types tracking, PDF export of logs (legal/audit)
- Dashboard with monthly stats & revenue tracking
- JWT-based custom auth
- Digital signature pad (canvas)
- Professional/Legal vibe (navy, serif) branded for Domini Business Services

## User Personas
- **Notary** — daily journal use, PDF exports, signer intake, commission tracking
- **Firm Admin (Matthew)** — super-admin roster view across all notaries under Domini

## Tech Stack
- Frontend: React + Tailwind + Shadcn UI, react-router
- Backend: FastAPI + Motor (async Mongo), JWT auth via httpOnly cookies
- PDF: ReportLab • QR: qrcode • Email: Emergent-managed Resend
- Payments: **Stripe** (claimable sandbox, subscriptions)

## Implemented (chronological)
- Auth (JWT, register/login/logout, brute-force lockout, admin seed)
- Journal (CRUD entries + signature canvas)
- Clients + Document Types
- PDF exports with Domini watermark + custom notary seal
- Signer Intake portal (public pre-fill, PII-safe) + QR posters
- Commission expiration email alerts (daily cron via crons.yml, Resend)
- Entry Certificates (emailed PDFs, delivery log, resend button)
- State-specific renewal checklists (CA/TX/FL, auto-unlock at 30 days)
- Firm branding + multi-notary isolation, admin **Team Roster** view
- **Stripe Billing Per Notary** (Feb 2026): monthly $19 / yearly $190 (save $38), 14-day free trial, hard block after expiry, admin bypass, customer portal, secure origin_url allowlisting, webhook sync

## Key API endpoints
- Auth: `/api/auth/{register,login,logout,me,refresh,profile,change-password,seal}`
- Journal: `/api/entries`, `/api/entries/{id}/{certificate.pdf,email-certificate,email-history}`, `/api/entries/export/pdf`
- Signers: `/api/clients`, `/api/document-types`
- Intake: `/api/intakes`, `/api/public/intake/{token}`, `/api/intakes/{id}/qr-poster.pdf`
- Alerts: `/api/alerts/{status,test}`, `/api/cron/commission-alerts`
- Renewal: `/api/renewal-checklist`, `/api/renewal-checklist/toggle`
- Admin: `/api/admin/roster`
- **Billing**: `/api/billing/{status,checkout,session/{id},portal}`, `/api/stripe/webhook`

## Data Models (Mongo)
- `users` {email, password_hash, name, role, firm_name, seal_data_url, subscription_status, subscription_plan_key, stripe_customer_id, stripe_subscription_id, subscription_current_period_end, subscription_cancel_at_period_end, created_at}
- `entries`, `clients`, `document_types`, `signer_intakes`
- `certificate_email_log`, `cron_runs`
- `payment_transactions` {session_id, user_id, customer_id, lookup_key, amount, status, payment_status, stripe_subscription_id, created_at, updated_at}

## 3rd-Party Integrations
- **Emergent Resend** (email) — commission alerts, entry certificates
- **Stripe** (claimable sandbox, Flow A) — subscriptions, customer portal
  - Products: `notarium_pro_monthly`, `notarium_pro_yearly`
  - Trial: 14 days (`subscription_data.trial_period_days`)
  - Tax mode: `full` (SMP — US SaaS eligible)
  - Onboarding URL for Matthew: see `.env` `STRIPE_ONBOARDING_URL`

## Backlog / Roadmap
### P1
- **Firm Logo Upload** — notary uploads their firm's own logo (alongside Domini shield)
- **Invite by Email** — send registration link with firm pre-filled

### P2
- **Delivery Bounce Alerts** — morning ping if certificate email bounced
- **Custom Checklist Steps** — user-defined renewal checklist items
- **Multi-State Commissions** — hold CA+NV etc. with separate alerts

### Tech Debt
- Split `server.py` (2200+ lines) into modules: auth, billing, journal, intake, admin, pdf
- Migrate to async Stripe SDK (or worker thread) — currently blocking event loop
- Fix login throttling across proxy replicas (key on trusted client IP, not `request.client.host`)
- Explicit-origin credentialed CORS (currently `allow_origins=['*']`, `allow_credentials=False`)

## Test Reports
`/app/test_reports/iteration_{1..13}.json` — iteration_13 confirms 15/15 billing tests pass.
