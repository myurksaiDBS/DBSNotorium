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
- **Stripe Billing Per Notary** (Feb 2026): monthly $19 / yearly $190, 14-day free trial, hard block, admin bypass, customer portal (both origin-allowlisted), webhook sync
- **Firm Logo Upload** (Feb 2026): PIL-validated PNG/JPEG on certificates + QR posters
- **Invite by Email** (Feb 2026): atomic invite claim + unique email index
- **Login Lockout Hardening** (Feb 2026): email-only identifier, atomic `$inc`
- **Firm Profile** (Feb 2026): address, phone, website, license number rendered in certificate PDF footer
- **Delivery Bounce Alerts** (Feb 2026): daily 8:30 UTC digest with one-click resend links; unified `recipient`/`error_msg` log schema
- **Custom Checklist Steps** (Feb 2026): up to 20 personal steps, atomic 20-cap guard, toggle+delete
- **Public Intake Branding** (Feb 2026): firm logo + name + website on the public signer-intake page
- **Firm Footer on Journal Export** (Feb 2026): the multi-entry PDF journal now carries the same firm-details footer as individual certificate PDFs — firm name, address, phone, website, license — centered under the entry table when populated.
- **Modular Refactor Phases 1-2** (Feb 2026): billing → `/app/backend/routes/billing.py` (333 lines), invites → `/app/backend/routes/invites.py` (262 lines). server.py: 2800 → 2323 lines.

## Backend Layout
```
backend/
  server.py          # 2323 lines — auth, journal, alerts, intake, admin, PDF, renewal-checklist
  routes/
    __init__.py
    billing.py       # 333 lines — Stripe subscriptions + webhook + require_active_subscription
    invites.py       # 262 lines — invite CRUD + public accept flow
```

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

## Data Models (Mongo)
- `users` {email (unique), password_hash, name, role, firm_name, firm_address, firm_phone, firm_website, firm_license_number, state, seal_data, firm_logo_data, subscription_status, subscription_plan_key, stripe_customer_id, stripe_subscription_id, subscription_current_period_end, subscription_cancel_at_period_end, renewal_checklist_progress[], custom_checklist_items[{id,title,detail,created_at}], invited_by_user_id, created_at}
- `entries`, `clients`, `document_types`, `signer_intakes`
- `invites` {token (unique), email, firm_name, state, invited_by_user_id, invited_by_name, message, created_at, expires_at, used_at, email_status, email_error, accepted_user_id}
- `login_attempts` {identifier (indexed), count, locked_until, updated_at}
- `certificate_email_log` {user_id, entry_id, recipient, signer_name, subject, email_id, error_msg, status, sent_at, bounce_digest_sent}
- `cron_runs`, `payment_transactions`

## Backlog / Roadmap
### P1 — Split Phase 3 (deferred)
- **Alerts module**: extract commission-alerts + bounce-digest + `_send_email` helper into `/app/backend/routes/alerts.py`. Blocked by tight coupling with the certificate email flow inside `email_certificate` — needs `_send_email` promoted to a shared helper first (5-10 lines of surgery).
- **Intake module**: extract QR poster + public intake + intake CRUD into `/app/backend/routes/intake.py`. Depends on `_draw_domini_stamp` and `_firm_logo_flowable` PDF helpers being surfaced via deps.
- **Journal & auth**: too deeply entangled with cross-cutting helpers (PDF, JWT session cookies) to peel off safely in a single pass — leave in server.py for now, revisit once helpers are pulled into `/app/backend/deps.py`.

### P2 — Feature Backlog
- **Multi-State Commissions** — hold CA+NV etc. with separate alerts and separate checklists
- **Bulk Signer Import** — CSV upload to seed the client database
- **Journal Search Presets** — save named filters ("all Acknowledgements in Q1") for repeat exports
- **Client Portal Follow-ups** — auto-remind signers whose intake link is still `pending` 24h before their appointment

### Hardening
- Explicit-origin credentialed CORS (currently `allow_origins=['*']`, `allow_credentials=False`)
- Async Stripe SDK — move blocking Stripe calls to a threadpool
- Migrate legacy certificate_email_log rows to unified schema then drop fallback code paths

## 3rd-Party Integrations
- **Emergent Resend** (email) — commission alerts, entry certificates, invite emails, bounce digests
- **Stripe** (claimable sandbox, Flow A) — subscriptions, customer portal, `.env` `STRIPE_ONBOARDING_URL`

## Test Reports
`/app/test_reports/iteration_{1..20}.json` — iteration_20 confirmed all fixes with 56/57 targeted + regressions (post-fix); post-atomic-cap self-test confirmed 20 admitted every time under 3× 25-way concurrent load. Firm footer on journal export verified via curl: with-firm PDF is larger than no-firm PDF, both valid `%PDF` streams.
