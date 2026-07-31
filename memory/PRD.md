# Notarium — Notary Public Log Application

## Original Problem Statement
Design and build a log application for a notary public.

## User Choices (v1)
- Features: Journal entries, Client/signer DB with search, Document types tracking, PDF export, Dashboard with monthly stats & revenue
- Auth: JWT (email/password, httpOnly cookies)
- Signature: HTML5 canvas signature pad
- Design vibe: Professional/Legal (deep navy + Playfair serif)

## Architecture
- **Backend:** FastAPI + Motor (MongoDB), JWT auth (PyJWT + bcrypt), ReportLab for PDF export
- **Frontend:** React 19 + Tailwind + shadcn/ui + Recharts + Sonner toasts
- **DB collections:** users, entries, clients, document_types, login_attempts
- **Owner account seeded on startup:** matthew.yurksaitis@dominibusservices.com

## User Personas
- Solo Notary Public — records notarizations, tracks fees, exports audit-ready journals

## Core Requirements (Static)
- Legally-defensible journal of notarial acts
- Fast entry with signer + ID + document + fee + signature
- Filter/search + PDF export for audits
- Private per-user data (all API scoped by user_id)

## Implemented (2026-02)
- JWT auth (register/login/logout/refresh/me/profile) with cookie sessions + brute force lockout
- Admin seeding with real owner email
- 8 default document types seeded for owner
- Journal entries CRUD with search, type + date filters
- HTML5 canvas signature pad (base64 PNG)
- Signers/clients DB with auto-creation from journal entries
- Document Types library management
- Dashboard: 4 stat cards + monthly bar chart + notarization pie chart
- Commission settings (name, number, state, expiration)
- PDF export via ReportLab (landscape, header w/ commission info, zebra rows, totals)

## Backlog (P1 / P2)
- P1: Thumbprint image capture per entry (some states require it)
- P1: Two-factor auth (TOTP) for the owner account
- P1: Email reminders when commission is nearing expiration
- P2: Multi-notary teams with role-based dashboards
- P2: Client portal — signer can pre-fill their details via a shareable link
- P2: State-specific journal templates (CA, TX, FL) and required-field enforcement
