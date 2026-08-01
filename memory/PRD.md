# Notarium — Product Requirements

## Product
Notarium is a professional notary public journal & compliance SaaS built for **Domini Business Services**. Multi-tenant: each notary manages their own journal, clients, intake portal, subscription, and firm brand.

## Tech Stack
- Frontend: React + Tailwind + Shadcn UI, react-router
- Backend: FastAPI + Motor (async Mongo), JWT httpOnly cookies
- PDF: ReportLab • QR: qrcode • Image validation: Pillow
- Email: Emergent-managed Resend • Payments: Stripe (claimable sandbox)

## Backend Layout
```
backend/
  server.py          # ~1994 lines — auth, journal, intake, admin, PDF, checklists, presets
  deps.py            # 114 lines — send_email, draw_domini_stamp, firm_logo_flowable, LOGO_PATH
  routes/
    __init__.py
    billing.py       # 333 lines
    invites.py       # 262 lines
    alerts.py        # 324 lines — commission-alerts + bounce-digest + email renderers
```

## Feature Snapshot (all shipped)
Auth · Journal · Clients · Document Types · Signature capture · PDF certificates (Domini watermark + notary seal + firm footer) · Signer Intake portal + QR posters (with firm branding on public page) · Commission expiration alerts · Entry Certificates + resend + email log · State-specific renewal checklists · Custom checklist steps (20-cap atomic) · Firm branding (multi-notary) · Admin Team Roster · Stripe Billing (monthly/yearly, 14-day trial, hard block, portal) · Firm Logo Upload (PIL validation) · Firm Profile (address/phone/website/license) · Invite by Email (atomic accept race) · Login lockout ($inc atomic on email) · Delivery Bounce Digest (cron 8:30 UTC + preview) · Journal Search Presets (save/apply/delete, 20-cap dedup by name) · Firm footer on multi-entry journal PDF · **Preset Export button** on each preset chip (Feb 2026, this iteration).

## What was attempted but NOT shipped this iteration
- **Preset Sharing**: I tried adding a "copy shareable link" button per chip plus a "shared filter adoption" banner. The change introduced a subtle syntax breakage in Journal.jsx that cascaded through multiple search_replace passes and eventually corrupted the closing structure of the `<Dialog>` block. Restored from git commit 413fe47 (last known-good) and re-added ONLY the preset export button. Preset sharing is deferred to a fresh session where the JSX changes can be made in one atomic pass. Frontend now compiles cleanly.
- **Extract Intake Module**: still tightly coupled to shared PDF helpers not yet in `deps.py`; deferred.

## API Endpoints (unchanged from prior iteration)
Auth · Journal · Presets · Signers · Intake · Alerts (extracted) · Renewal · Admin · Invites (extracted) · Billing (extracted)

## Data Models (unchanged)
users · journal_presets · entries · clients · document_types · signer_intakes · invites · login_attempts · certificate_email_log · cron_runs · payment_transactions

## Backlog / Roadmap
### P1 (recommend fresh session)
- **Preset Sharing** — copy link + adoption banner + auto-append `(shared)` for dedup. Small, ~80 lines. Was broken by a mid-edit brace mismatch; do in one clean pass on a fresh Journal.jsx.
- **Intake module extraction** — after promoting intake poster helpers into deps.py.

### P2
- Multi-State Commissions (CA + NV etc.)
- Bulk Signer Import (CSV)
- Client Portal Follow-ups (T-24h reminder)

### Hardening
- Explicit-origin credentialed CORS (currently `*`, no credentials)
- Async Stripe SDK
- Migrate legacy certificate_email_log rows

## Verification
- `import server` OK
- Journal page loads cleanly (screenshot)
- Preset Export button in place (`data-testid="preset-export-<id>"`)
- Backend curl checks from prior iterations still pass

## Test Reports
`/app/test_reports/iteration_{1..20}.json`
