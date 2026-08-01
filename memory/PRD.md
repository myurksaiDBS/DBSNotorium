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
- **Stripe Billing Per Notary** (Feb 2026): monthly $19 / yearly $190, 14-day free trial, hard block after expiry, admin bypass, customer portal (both endpoints origin-allowlisted), webhook sync
- **Firm Logo Upload** (Feb 2026): PIL image validation, renders alongside Domini shield on certificates + QR posters
- **Invite by Email** (Feb 2026): atomic invite claim + unique email index; concurrent-accept safe
- **Login Lockout Hardening** (Feb 2026): identifier keyed on email only, atomic `$inc`
- **Firm Profile** (Feb 2026): address, phone, website, license number persisted with explicit-null clearing; renders in certificate PDF footer
- **Delivery Bounce Alerts** (Feb 2026): daily 8:30 UTC cron digest of certificate email bounces + one-click resend links; unified `recipient`/`error_msg` log schema with legacy fallback
- **Custom Checklist Steps** (Feb 2026): notaries add up to 20 personal renewal steps alongside state-specific defaults; atomic 20-cap prevents concurrent overflow (`custom_checklist_items.19` guard); toggle + delete work like built-ins
- **Public Intake Branding** (Feb 2026): public signer-intake page shows the notary's firm logo + name + website alongside the Domini shield
- **Modular Refactor** (Feb 2026): billing routes → `/app/backend/routes/billing.py` (333 lines), invite routes → `/app/backend/routes/invites.py` (262 lines). server.py: 2800 → 2320 lines. Pattern: `register_*(api, db, get_current_user, deps)` called just before `app.include_router(api)`.

## Key API Endpoints
- Auth: `/api/auth/{register,login,logout,me,refresh,profile,change-password,seal,firm-logo}`
- Journal: `/api/entries`, `/api/entries/{id}/{certificate.pdf,email-certificate,email-history}`, `/api/entries/export/pdf`
- Signers: `/api/clients`, `/api/document-types`
- Intake: `/api/intakes`, `/api/public/intake/{token}`, `/api/intakes/{id}/qr-poster.pdf`
- Alerts: `/api/alerts/{status,test,bounces}`, `/api/cron/{commission-alerts,delivery-bounces,delivery-bounces/preview}`
- Renewal: `/api/renewal-checklist`, `/api/renewal-checklist/toggle`, `/api/renewal-checklist/custom`, `/api/renewal-checklist/custom/{id}`
- Admin: `/api/admin/roster`
- Invites (extracted): `/api/invites`, `/api/invites/{id}`, `/api/public/invite/{token}`, `/api/public/invite/{token}/accept`
- Billing (extracted): `/api/billing/{status,checkout,session/{id},portal}`, `/api/stripe/webhook`

## Backend Layout
```
backend/
  server.py          # 2320 lines — remaining routes (auth, journal, intake, admin, alerts, PDF, renewal-checklist)
  routes/
    __init__.py
    billing.py       # 333 lines — Stripe subscriptions + webhook + require_active_subscription
    invites.py       # 262 lines — invite CRUD + public accept flow
```

## Data Models (Mongo)
- `users` {email (unique), password_hash, name, role, firm_name, firm_address, firm_phone, firm_website, firm_license_number, state, seal_data, firm_logo_data, subscription_status, subscription_plan_key, stripe_customer_id, stripe_subscription_id, subscription_current_period_end, subscription_cancel_at_period_end, renewal_checklist_progress[], custom_checklist_items[{id,title,detail,created_at}], invited_by_user_id, created_at}
- `entries`, `clients`, `document_types`, `signer_intakes`
- `invites` {token (unique), email, firm_name, state, invited_by_user_id, invited_by_name, message, created_at, expires_at, used_at, email_status, email_error, accepted_user_id}
- `login_attempts` {identifier (indexed), count, locked_until, updated_at}
- `certificate_email_log` {user_id, entry_id, recipient, signer_name, subject, email_id, error_msg, status, sent_at, bounce_digest_sent, bounce_digest_sent_at}
- `cron_runs`, `payment_transactions`

## 3rd-Party Integrations
- **Emergent Resend** (email) — commission alerts, entry certificates, invite emails, bounce digests
- **Stripe** (claimable sandbox, Flow A) — subscriptions, customer portal, `.env` `STRIPE_ONBOARDING_URL`

## Backlog / Roadmap
### P1
- **Server split Phase 3**: extract auth, journal, alerts, intake into their own `routes/*.py` (target every file <700 lines)
- **Firm Profile on Journal PDF**: extend firm footer rendering into the full multi-entry journal export (not just individual certificates)

### P2
- **Multi-State Commissions** — hold CA+NV etc. with separate alerts and separate checklists
- **Async Stripe SDK** — move blocking Stripe calls to a threadpool to avoid event-loop stalls
- **Signer copy of certificate as text summary** — for those without PDF viewers

### Hardening
- Explicit-origin credentialed CORS (currently `allow_origins=['*']`, `allow_credentials=False`)
- Wrap invite acceptance in a Mongo transaction (for partial-state prevention on rare DB failures)
- Migrate legacy certificate_email_log rows to unified schema then drop fallback code paths

## Test Reports
`/app/test_reports/iteration_{1..20}.json` — iteration_20 flagged one race defect (custom-step 20-cap); post-fix self-test ran 3 concurrent 25-request rounds and confirmed exactly 20 admitted every time. Full regression: billing 15/15, iteration_14 17/17, iteration_15 4/4, iteration_16 non-provider 6/6, iteration_17-19 5/5, iteration_20 targeted 56/57 (all after fix).
