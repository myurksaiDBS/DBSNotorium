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
- **Notary** — daily journal use, PDF exports, signer intake, commission tracking, own subscription
- **Firm Admin (Matthew)** — super-admin roster view, invites colleagues, bypasses billing

## Tech Stack
- Frontend: React + Tailwind + Shadcn UI, react-router
- Backend: FastAPI + Motor (async Mongo), JWT auth via httpOnly cookies
- PDF: ReportLab • QR: qrcode • Image validation: Pillow
- Email: Emergent-managed Resend
- Payments: **Stripe** (claimable sandbox, subscriptions)

## Implemented (chronological)
- Auth (JWT, register/login/logout, brute-force lockout, admin seed)
- Journal (CRUD entries + signature canvas)
- Clients + Document Types
- PDF exports with Domini watermark + custom notary seal
- Signer Intake portal (public pre-fill, PII-safe) + QR posters
- Commission expiration email alerts (daily cron, Resend)
- Entry Certificates (emailed PDFs, delivery log, resend button)
- State-specific renewal checklists (CA/TX/FL, auto-unlock at 30 days)
- Firm branding + multi-notary isolation, admin **Team Roster** view
- **Stripe Billing Per Notary** (Feb 2026): monthly $19 / yearly $190 (save $38), 14-day free trial, hard block after expiry, admin bypass, customer portal, secure origin_url allowlisting, webhook sync
- **Firm Logo Upload** (Feb 2026): each notary uploads their firm logo; renders alongside Domini shield on certificates and QR posters. Base64 + PIL image validation (rejects spoofed bytes).
- **Invite by Email** (Feb 2026): admin/notary sends signup invitation with firm/state pre-filled. Atomic invite claim + unique email index prevents concurrent-accept races. 7-day expiry, revoke, list.
- **Login Lockout Hardening** (Feb 2026): identifier keyed on email alone (not proxy IP), atomic `$inc` increment prevents lost updates under concurrency. 5 fails → 15-min lockout.

## Key API Endpoints
- Auth: `/api/auth/{register,login,logout,me,refresh,profile,change-password,seal,firm-logo}`
- Journal: `/api/entries`, `/api/entries/{id}/{certificate.pdf,email-certificate,email-history}`, `/api/entries/export/pdf`
- Signers: `/api/clients`, `/api/document-types`
- Intake: `/api/intakes`, `/api/public/intake/{token}`, `/api/intakes/{id}/qr-poster.pdf`
- Alerts: `/api/alerts/{status,test}`, `/api/cron/commission-alerts`
- Renewal: `/api/renewal-checklist`, `/api/renewal-checklist/toggle`
- Admin: `/api/admin/roster`
- **Invites**: `/api/invites` (POST list), `/api/invites/{id}` (GET/DELETE), `/api/public/invite/{token}`, `/api/public/invite/{token}/accept`
- **Billing**: `/api/billing/{status,checkout,session/{id},portal}`, `/api/stripe/webhook`

## Data Models (Mongo)
- `users` {email (unique), password_hash, name, role, firm_name, state, seal_data, firm_logo_data, subscription_status, subscription_plan_key, stripe_customer_id, stripe_subscription_id, subscription_current_period_end, subscription_cancel_at_period_end, invited_by_user_id, created_at}
- `entries`, `clients`, `document_types`, `signer_intakes`
- `invites` {token (unique), email, firm_name, state, invited_by_user_id, invited_by_name, message, created_at, expires_at, used_at, email_status, email_error, accepted_user_id}
- `login_attempts` {identifier (indexed), count, locked_until, updated_at}
- `certificate_email_log`, `cron_runs`, `payment_transactions`

## 3rd-Party Integrations
- **Emergent Resend** (email) — commission alerts, entry certificates, invite emails
- **Stripe** (claimable sandbox, Flow A) — subscriptions, customer portal
  - Products: `notarium_pro_monthly`, `notarium_pro_yearly`
  - Trial: 14 days · Tax mode: `full` (SMP US SaaS)
  - Onboarding URL for Matthew: see `.env` `STRIPE_ONBOARDING_URL`

## Backlog / Roadmap
### P1
- **Split `server.py`** (2500+ lines) into `routes/{auth,billing,journal,intake,admin,invite,pdf}.py` — tech-debt refactor for maintainability
- **Real firm profile page** — collect firm details (address, license, contact info) not just logo

### P2
- **Delivery Bounce Alerts** — morning ping if certificate email bounced
- **Custom Checklist Steps** — user-defined renewal checklist items
- **Multi-State Commissions** — hold CA+NV etc. with separate alerts

### Hardening Notes (non-blocking)
- Make `login_attempts.identifier` a unique index (defense-in-depth)
- Wrap invite acceptance in a Mongo transaction (partial-state prevention on rare DB failures)
- Explicit-origin credentialed CORS (currently `allow_origins=['*']`, `allow_credentials=False`)

## Test Reports
`/app/test_reports/iteration_{1..15}.json` — iteration_15 confirms 35/35 tests pass (targeted 4/4 + iteration_14 regressions 16/16 + billing regressions 15/15).
