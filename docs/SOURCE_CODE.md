# Notarium — Source Code Guide

> A professional notary-public journal & compliance SaaS for **Domini Business Services**. Multi-tenant: every notary owns their journal, clients, intake portal, subscription and firm branding.

---

## 1. Tech Stack

| Layer        | Choice                                                              |
| ------------ | ------------------------------------------------------------------- |
| Frontend     | React 18, react-router-dom, Tailwind CSS, Shadcn UI, Recharts       |
| Backend      | FastAPI, Motor (async MongoDB), Pydantic v2, Uvicorn                |
| Auth         | JWT in HttpOnly cookies, bcrypt password hashing                    |
| Database     | MongoDB (single database, multi-tenant by `user_id`)                |
| PDF          | ReportLab (certificates, journal exports, QR posters)               |
| QR Codes     | `qrcode` + Pillow                                                   |
| Payments     | Stripe (Subscriptions, Checkout, Billing Portal, Webhooks)          |
| Email        | Emergent-managed Resend (via `emergentintegrations` HTTP endpoint)  |
| Scheduling   | Emergent platform crons defined in `.emergent/crons.yml`            |
| Package mgr  | `yarn` (frontend), `pip` (backend)                                  |

---

## 2. Repository Layout

```
/app
├── backend/
│   ├── server.py                     # ~2000 lines: auth, journal, intake, checklists, PDFs, admin
│   ├── deps.py                       # send_email + PDF stamp helpers (shared)
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── alerts.py                 # commission alerts + bounce digests + intake reminders (cron)
│   │   ├── billing.py                # Stripe checkout / portal / webhook
│   │   └── invites.py                # colleague invite create + atomic accept
│   ├── requirements.txt
│   ├── setup_stripe.py               # one-time helper: create Stripe products/prices
│   ├── .env                          # secrets (never commit)
│   └── assets/domini-logo.png        # watermark
│
├── frontend/
│   ├── src/
│   │   ├── api.js                    # axios instance w/ credentials + REACT_APP_BACKEND_URL
│   │   ├── App.js                    # routes + auth gate
│   │   ├── context/AuthContext.jsx   # user/session provider + login/logout/register
│   │   ├── components/
│   │   │   ├── Layout.jsx            # sidebar + main frame
│   │   │   ├── SignaturePad.jsx      # canvas signature capture
│   │   │   ├── InviteNotaryDialog.jsx
│   │   │   ├── BillingBanner.jsx     # trial-days-left / expired banner
│   │   │   └── ui/                   # Shadcn UI primitives
│   │   └── pages/
│   │       ├── Login.jsx             # sign-in + sign-up
│   │       ├── Dashboard.jsx         # stats & charts
│   │       ├── Journal.jsx           # journal CRUD, PDF, email, presets
│   │       ├── Clients.jsx           # signer database
│   │       ├── DocumentTypes.jsx     # per-notary doc catalog
│   │       ├── Intakes.jsx           # pre-fill intake links + QR posters
│   │       ├── SignerIntake.jsx      # PUBLIC signer form (no auth)
│   │       ├── Settings.jsx          # commission, seal, firm brand, checklist, alerts
│   │       ├── Billing.jsx           # plans + Stripe portal launcher
│   │       ├── Roster.jsx            # admin-only team view
│   │       └── InviteAccept.jsx      # PUBLIC invite acceptance
│   ├── public/domini-logo.png
│   └── .env                          # REACT_APP_BACKEND_URL only
│
├── .emergent/
│   └── crons.yml                     # commission-alerts, delivery-bounces, intake-reminders
│
├── docs/
│   ├── SOURCE_CODE.md                # this file
│   └── USER_MANUAL.md                # subscriber-facing guide
│
├── memory/
│   ├── PRD.md                        # product spec + backlog
│   └── test_credentials.md           # test accounts (never commit real creds)
│
└── test_reports/                     # JSON reports from the testing agent
```

---

## 3. Environment Variables

### `backend/.env`

| Key                       | Purpose                                                                 |
| ------------------------- | ----------------------------------------------------------------------- |
| `MONGO_URL`               | MongoDB connection string                                               |
| `DB_NAME`                 | Mongo database name                                                     |
| `JWT_SECRET`              | HMAC secret for JWT signing                                             |
| `ADMIN_EMAIL`             | Bootstrap admin (Matthew) — super-admin bypass                          |
| `ADMIN_PASSWORD`          | Bootstrap admin password (seeded once at startup)                       |
| `EMERGENT_EMAIL_KEY`      | Universal key for Emergent-managed Resend                               |
| `EMAIL_FROM_NAME`         | Display name on outgoing emails                                         |
| `WEBHOOK_CRON_SECRET`     | Bearer token gate for `/api/cron/*` endpoints                           |
| `ALERT_APP_URL`           | Public origin used in email/pdf links                                   |
| `STRIPE_API_KEY`          | Stripe secret key                                                       |
| `STRIPE_WEBHOOK_SECRET`   | Stripe webhook signing secret                                           |

### `frontend/.env`

| Key                     | Purpose                                                     |
| ----------------------- | ----------------------------------------------------------- |
| `REACT_APP_BACKEND_URL` | Public base URL of the backend (must be used, never hard-coded) |

> **All backend routes are prefixed with `/api`.** The Kubernetes ingress redirects `/api/*` traffic to the backend on port `8001`; everything else is served by the frontend on `3000`.

---

## 4. Backend — File-by-File

### `server.py` (main FastAPI app)

Contains everything not yet extracted to a route module:

- **Auth**: `POST /api/auth/register|login|logout|refresh`, `GET /api/auth/me`, `PUT /api/auth/profile`, `POST /api/auth/change-password`, plus seal + firm-logo endpoints. JWT is issued via HttpOnly cookie; bcrypt hashing; login lockout after 5 failed attempts (atomic `$inc`, 15-minute cool-down).
- **Subscription gate**: `require_active_subscription` dependency blocks writes with 402 when trial + subscription both lapsed.
- **Journal**: `entries` CRUD (`POST/GET/PUT/DELETE /api/entries`), `GET /api/entries/{id}/certificate.pdf`, `GET /api/entries/export/pdf`, `POST /api/entries/{id}/email-certificate`, `GET /api/entries/{id}/email-history`, `GET /api/public/certificate/{token}` for shareable read-only links.
- **Clients / Signers**: `POST/GET/PUT/DELETE /api/clients` with case-insensitive substring search.
- **Document Types**: per-notary catalog for form dropdowns.
- **Signer Intakes**: `POST/GET/DELETE /api/intakes`, `POST /api/intakes/{id}/mark-used`, `POST /api/intakes/{id}/send-reminder`, `GET /api/public/intake/{token}`, `POST /api/public/intake/{token}` (submit). Fields include `signer_email`, `appointment_at`, `reminder_sent_at`.
- **QR posters**: `GET /api/intakes/{id}/qr-poster.pdf` and `.../qr.png` — Domini-branded PDF flyer with QR code.
- **Renewal Checklist**: state-specific + user-authored custom steps, atomic toggle.
- **Journal Presets**: `GET/POST/DELETE /api/journal-presets` — saved filter combinations.
- **Dashboard stats**: `GET /api/stats/dashboard`.
- **Admin roster**: `GET /api/admin/roster` (super-admin only).

### `deps.py` — shared runtime helpers

- `send_email(to, subject, html, reply_to=None)` — posts to Resend via Emergent integration; returns `(email_id, error_msg)`.
- `draw_domini_stamp(canvas, w, h, page)` — watermark + footer + red top rule on every PDF page.
- `firm_logo_flowable(user)` — small right-aligned firm logo above certificates when uploaded.
- Constants: `LOGO_PATH`, `EMAIL_BASE_URL`.

### `routes/alerts.py` — cron endpoints

- `POST /api/cron/commission-alerts` — daily 8:00 UTC. Scans users whose commission expires within 60/30/14/7/1/0 days and emails the notary. Idempotent per bucket via `last_alert_bucket`.
- `POST /api/cron/delivery-bounces` — daily 8:30 UTC. Groups yesterday's failed certificate emails per notary; sends one digest with one-click resend links. Marks rows `bounce_digest_sent=true` to prevent re-sends.
- `POST /api/cron/intake-reminders` — every 4h. Finds pending intakes with `appointment_at ∈ [now+20h, now+32h]` and unsent `reminder_sent_at`; emails the signer.
- Support endpoints for the UI: `POST /api/alerts/test`, `GET /api/alerts/status`, `GET /api/alerts/bounces`, `POST /api/cron/delivery-bounces/preview`, `POST /api/intakes/{id}/send-reminder`.
- All cron endpoints require `Authorization: Bearer $WEBHOOK_CRON_SECRET` **and** honor `X-Webhook-Id` for idempotent redelivery (deduped by `db.cron_runs.run_id` unique index).

### `routes/billing.py` — Stripe

- `GET /api/billing/status` — computes effective status: `trialing` (14-day trial), `active`, `expired`, `canceled`, `past_due`, plus `is_admin_bypass` for the super admin.
- `POST /api/billing/checkout` — creates a Stripe Checkout Session for a `lookup_key` (`notarium_pro_monthly` / `notarium_pro_yearly`).
- `POST /api/billing/portal` — creates a Stripe Billing Portal session so notaries can update cards, cancel, or resume.
- `GET /api/billing/session/{id}` — polled after checkout to confirm `payment_status`.
- `POST /api/stripe/webhook` — verifies signature, updates the user's `subscription_status`, `current_period_end`, `cancel_at_period_end`, and mirrors event ids in `payment_transactions` for audit.

### `routes/invites.py` — colleague invites

- `POST /api/invites` — create a signed invite token; emails the invitee via Resend.
- `GET /api/invites`, `GET /api/invites/{id}`, `DELETE /api/invites/{id}` — list / view / revoke.
- `GET /api/public/invite/{token}` — public preview: shows firm name + inviting admin.
- `POST /api/public/invite/{token}/accept` — atomically consumes the invite (`find_one_and_update` with status guard), creates the new notary, seeds their trial.

---

## 5. Frontend — File-by-File

### `context/AuthContext.jsx`

Wraps the app. `user` is `null` while checking, `false` when logged out, or the user object when logged in. Exposes memoized `login/register/logout/refresh/updateProfile`. Auto-hits `/auth/me` on mount.

### `components/Layout.jsx`

Two-column: navy left sidebar (logo + nav + user card) and a white main pane. Roster link renders only for admins.

### `components/SignaturePad.jsx`

Canvas-based drawing with touch + mouse. Two effects — (a) mount sets up the DPR-scaled canvas & resize listener, (b) `[initialData]` redraws the seed PNG when editing an existing entry. Exposes `clear()`, `isEmpty()`, `toDataURL()` via `useImperativeHandle`.

### `pages/Journal.jsx` (~780 lines)

Filter bar, saved-presets chips, entries table, create/edit dialog with signature capture, view dialog with email history + resend, export PDF button (per-preset or current filter). Loader callbacks are memoized with `useCallback` and drive their own `useEffect`s.

### `pages/Settings.jsx` (~800 lines)

Commission details, alert toggles + preview send, notary seal upload (Data URL, ≤500 KB), firm branding block (name/address/phone/website/license + logo), custom checklist steps, delivery bounce report, change password.

### `pages/Intakes.jsx`

Table of intake links with status pills (pending / completed / used / expired). Create dialog collects **signer email** and **appointment date-time**; on submit converts the naive datetime-local into a UTC ISO before POST. Share dialog copies link, opens SMS, downloads QR poster. "Remind" row action manually triggers `POST /intakes/{id}/send-reminder` if the automatic 24h cron would be too late.

### `pages/SignerIntake.jsx`

Public form (no auth). Renders under the firm's brand (logo + firm name + website) if the notary has uploaded one. Submits atomically — the backend guards against double-submit via `find_one_and_update` matching `status: "pending"`.

### `pages/Billing.jsx`

Shows current status badge + next-invoice date. If trialing/expired, presents monthly + yearly plan cards. After returning from Stripe (via `?session_id=…`) it polls `GET /billing/session/{id}` until `payment_status=paid`, then refreshes `/billing/status`.

### `pages/Roster.jsx`

Admin-only. Lists every notary in the firm with subscription status, entry count, signer count, last activity. "Invite Notary" opens `InviteNotaryDialog`.

---

## 6. Data Models (Mongo Collections)

| Collection                | Purpose                                                                                  |
| ------------------------- | ---------------------------------------------------------------------------------------- |
| `users`                   | Auth + firm profile + commission info + `subscription_status`, `trial_ends_at`, seal, firm_logo_data |
| `entries`                 | Journal entries (one per notarial act)                                                   |
| `clients`                 | Signer directory                                                                         |
| `document_types`          | Per-user catalog                                                                         |
| `signer_intakes`          | Pre-fill portal links + submissions (`signer_email`, `appointment_at`, `reminder_sent_at`)|
| `journal_presets`         | Saved filter sets                                                                        |
| `invites`                 | Pending team invites (unique token)                                                      |
| `certificate_email_log`   | Every certificate send attempt (sent / failed) + resend audit                            |
| `login_attempts`          | Atomic counter per email for lockout                                                     |
| `cron_runs`               | Idempotency ledger for webhook redeliveries                                              |
| `payment_transactions`    | Mirror of Stripe events for local audit                                                  |

MongoDB `_id` (ObjectId) is always serialised to a string `id` field before returning JSON.

---

## 7. Cron / Scheduled Jobs

Defined in `.emergent/crons.yml`:

| Name                 | Schedule          | Endpoint                          |
| -------------------- | ----------------- | --------------------------------- |
| `commission-alerts`  | daily 08:00 UTC   | `POST /api/cron/commission-alerts`|
| `delivery-bounces`   | daily 08:30 UTC   | `POST /api/cron/delivery-bounces` |
| `intake-reminders`   | every 4h          | `POST /api/cron/intake-reminders` |

Each request carries `Authorization: Bearer $WEBHOOK_CRON_SECRET` and a unique `X-Webhook-Id`. Handlers respond `2xx` immediately and hand work to FastAPI `BackgroundTasks`.

---

## 8. Third-Party Integrations

### Stripe

Products & prices are created by `python backend/setup_stripe.py` (one-time). Lookup keys:

- `notarium_pro_monthly` — $9 / month
- `notarium_pro_yearly` — $90 / year

Webhook events consumed: `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.payment_failed`, `invoice.payment_succeeded`.

### Resend (via Emergent integrations)

`send_email` in `deps.py` posts to `https://integrations.emergentagent.com/api/v1/email/send` with `X-Email-Key: $EMERGENT_EMAIL_KEY`. The universal `EMERGENT_EMAIL_KEY` handles quota + throughput. Bounces surface via the daily `delivery-bounces` cron.

---

## 9. Local Development

```bash
# backend
cd /app/backend
pip install -r requirements.txt
# service is managed by supervisord; hot-reload is on

# frontend
cd /app/frontend
yarn install
# service also managed by supervisord

# view logs
tail -f /var/log/supervisor/backend.err.log
tail -f /var/log/supervisor/frontend.err.log

# restart when .env / package changes
sudo supervisorctl restart backend
sudo supervisorctl restart frontend
```

Public preview URL is `$REACT_APP_BACKEND_URL` from `frontend/.env`. Never use `localhost:8001` in the browser — the ingress rewrites `/api/*` accordingly.

---

## 10. Testing

- **Backend curl checks**: use the preview URL + a cookie jar (`curl -c/-b`) since auth is cookie-based.
- **Automated tests**: `pytest` suites live in `/app/backend/tests` and are exercised by the platform testing agent. Reports land in `/app/test_reports/iteration_{n}.json`.
- **Seeded accounts**: see `/app/memory/test_credentials.md`.

---

## 11. Downloading the Source

The complete codebase lives under `/app`. To pull it off the platform:

1. **"Save to GitHub"** — top-right of the chat input; commits the whole tree to a repo you own.
2. **Emergent download** — Profile → *Download source* zips `/app` (excluding `.git`, `node_modules`, `.emergent`).
3. **Manual zip inside the container** —

   ```bash
   cd /app && zip -r /tmp/notarium.zip . \
     -x '*/node_modules/*' -x '.git/*' -x 'test_reports/*' \
     -x 'backend/__pycache__/*'
   ```

---

## 12. Roadmap

- Preset **sharing** — copyable link so teammates adopt the same filter.
- **Server split Phase 3** — extract intake / journal / auth into `routes/` modules.
- **Settings page split** — sub-tabs for account / firm / notifications / renewal.
- **Bulk signer CSV import**.
- **Multi-state commissions** (CA + NV etc.).
- **Reminder insight card** — dashboard tile counting weekly intake reminders.

More detail in `/app/memory/PRD.md`.
