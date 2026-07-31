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
- **Stripe Billing Per Notary** (Feb 2026): monthly $19 / yearly $190, 14-day free trial, hard block after expiry, admin bypass, customer portal (both endpoints validate origin_url against ALERT_APP_URL allowlist), webhook sync
- **Firm Logo Upload** (Feb 2026): PIL image validation, renders alongside Domini shield on certificates + QR posters
- **Invite by Email** (Feb 2026): atomic invite claim + unique email index; concurrent-accept safe; 7-day expiry, revoke, list
- **Login Lockout Hardening** (Feb 2026): identifier keyed on email only, atomic `$inc` increment
- **Firm Profile** (Feb 2026): firm_address, firm_phone, firm_website, firm_license_number persisted via PUT /api/auth/profile (with explicit-null clearing via $set/$unset split); rendered in the certificate PDF footer only when set
- **Delivery Bounce Alerts** (Feb 2026): daily 8:30 UTC cron emails each notary a digest of certificate emails that bounced in the last 24h with one-click resend links. `GET /api/alerts/bounces` for live view, `POST /api/cron/delivery-bounces/preview` for on-demand preview. Certificate log unified to `recipient`/`error_msg` with legacy `to`/`error` fallback throughout API + digest rendering.
- **Modular Refactor v1** (Feb 2026): extracted Stripe billing (~330 lines) into `/app/backend/routes/billing.py` with a `register_billing(api, db, get_current_user)` function pattern. server.py shrank from 2800→2487 lines. Same pattern to extract auth, journal, invites, alerts next.

## Key API Endpoints
- Auth: `/api/auth/{register,login,logout,me,refresh,profile,change-password,seal,firm-logo}`
- Journal: `/api/entries`, `/api/entries/{id}/{certificate.pdf,email-certificate,email-history}`, `/api/entries/export/pdf`
- Signers: `/api/clients`, `/api/document-types`
- Intake: `/api/intakes`, `/api/public/intake/{token}`, `/api/intakes/{id}/qr-poster.pdf`
- Alerts: `/api/alerts/{status,test,bounces}`, `/api/cron/{commission-alerts,delivery-bounces,delivery-bounces/preview}`
- Renewal: `/api/renewal-checklist`, `/api/renewal-checklist/toggle`
- Admin: `/api/admin/roster`
- Invites: `/api/invites`, `/api/invites/{id}`, `/api/public/invite/{token}`, `/api/public/invite/{token}/accept`
- Billing: `/api/billing/{status,checkout,session/{id},portal}`, `/api/stripe/webhook`

## Data Models (Mongo)
- `users` {email (unique), password_hash, name, role, firm_name, firm_address, firm_phone, firm_website, firm_license_number, state, seal_data, firm_logo_data, subscription_status, subscription_plan_key, stripe_customer_id, stripe_subscription_id, subscription_current_period_end, subscription_cancel_at_period_end, invited_by_user_id, created_at}
- `entries`, `clients`, `document_types`, `signer_intakes`
- `invites` {token (unique), email, firm_name, state, invited_by_user_id, invited_by_name, message, created_at, expires_at, used_at, email_status, email_error, accepted_user_id}
- `login_attempts` {identifier (indexed), count, locked_until, updated_at}
- `certificate_email_log` {user_id, entry_id, recipient, signer_name, subject, email_id, error_msg, status, sent_at, bounce_digest_sent, bounce_digest_sent_at} — legacy rows may use `to`/`error`; all readers fall back
- `cron_runs`, `payment_transactions`

## Backend Layout
```
backend/
  server.py          # 2487 lines — remaining routes (auth, journal, invite, intake, admin, alerts, PDF)
  routes/
    __init__.py
    billing.py       # 330 lines — Stripe subscription + webhook + require_active_subscription gate
```

## 3rd-Party Integrations
- **Emergent Resend** (email) — commission alerts, entry certificates, invite emails, bounce digests
- **Stripe** (claimable sandbox, Flow A) — subscriptions, customer portal
  - Products: `notarium_pro_monthly`, `notarium_pro_yearly`; Trial: 14 days
  - Onboarding URL for Matthew: `.env` `STRIPE_ONBOARDING_URL`

## Backlog / Roadmap
### P1
- **Further server.py split**: extract auth, journal, invite, alerts, intake into their own `routes/*.py` following the billing pattern (target <700-line modules each)
- **Migrate legacy certificate_email_log rows** to unified schema then drop the fallback code paths
- **Async Stripe SDK** — move blocking Stripe calls to a threadpool to avoid event-loop stalls under provider latency

### P2
- **Custom Checklist Steps** — user-defined renewal checklist items
- **Multi-State Commissions** — hold CA+NV etc. with separate alerts and separate checklists
- **Firm profile in intake portal** — show firm branding on the public signer intake page

### Hardening Notes
- Explicit-origin credentialed CORS (currently `allow_origins=['*']`, `allow_credentials=False`)
- Wrap invite acceptance in a Mongo transaction (partial-state prevention on DB failures)
- Unique index on `login_attempts.identifier`

## Test Reports
`/app/test_reports/iteration_{1..19}.json` — iteration_19 confirms email-history compat (3/3), billing portal origin validation (verified via curl post-restart), all 41 requested regressions pass; 3 tests skipped due to external Resend quota exhaustion (temporary infra issue, not a code defect).
