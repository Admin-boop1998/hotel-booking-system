# Hotel Reservation Platform — System Blueprint

**Current State:** v7 | **Last Updated:** Oct 15, 2026

---

## STATUS TRACKER

### PHASE 1 — CRITICAL FIXES

| \# | Item | Status |
| --- | --- | --- |
| 1.1 | Email CTA routes by payment method (PayPal/Zelle/Venmo/Wire/Wise) | ✅ DONE (v7) |
| 1.2 | Phone number validation (min 7 digits) | ❌ OPEN |
| 1.3 | Paste confidence shown before advancing to Step 2 | ❌ OPEN |
| 1.4 | Manual override fields (edit dates, room, amount after paste) | ❌ OPEN |
| 1.5 | `.env.example` updated with all current vars | ❌ OPEN |

### PHASE 2 — SECURITY & COMPLIANCE

| \# | Item | Status |
| --- | --- | --- |
| 2.1 | Rate limiting (slowapi) | ❌ OPEN |
| 2.2 | Webhook idempotency (dedupe PayPal event_id) | ❌ OPEN |
| 2.3 | Input sanitization on raw_paste (10K char limit, strip HTML) | ❌ OPEN |
| 2.4 | CORS hardening (restrict origins) | ❌ OPEN |
| 2.5 | Booking ref path traversal protection | ❌ OPEN |

### PHASE 3 — AGENT EXPERIENCE

| \# | Item | Status |
| --- | --- | --- |
| 3.1 | Dashboard stats cards (today, pending, confirmed, revenue) | ❌ OPEN |
| 3.2 | Booking detail modal (click row → detail + actions) | ❌ OPEN |
| 3.3 | SSE notifications (replace 5s polling) | ❌ OPEN |
| 3.4 | Agent authentication (PIN + JWT) | ❌ OPEN |
| 3.5 | Multi-agent support (filter by agent) | ❌ OPEN |
| 3.6 | Booking edit (PATCH while Pending) | ❌ OPEN |

### PHASE 4 — EMAIL SYSTEM

| \# | Item | Status |
| --- | --- | --- |
| 4.1 | Email per payment method | ✅ DONE (v7) |
| 4.2 | Email open/click tracking | ❌ OPEN |
| 4.3 | PayPal link expiry notice in email ("expires in 3 hours") | ❌ OPEN |
| 4.4 | Plain-text email fallback | ❌ OPEN |

### PHASE 5 — PAYMENT SYSTEM

| \# | Item | Status |
| --- | --- | --- |
| 5.1 | PayPal Invoice API for ACH | ❌ OPEN |
| 5.2 | Payment config file (real account details) | ✅ DONE (v7) — `config/payment_methods.json` |
| 5.3 | Duplicate payment prevention (void PayPal on manual confirm) | ❌ OPEN |
| 5.4 | Manual payment confirmation ("Mark as Paid" for wire/Zelle) | ❌ OPEN |

### PHASE 6 — PARSER

| \# | Item | Status |
| --- | --- | --- |
| 6.1 | Priceline / Hotwire / Agoda formats | ❌ OPEN |
| 6.2 | Multi-room bookings | ❌ OPEN |
| 6.3 | Show parsed extras in preview + email | ❌ OPEN |

### PHASE 7 — INFRASTRUCTURE

| \# | Item | Status |
| --- | --- | --- |
| 7.1 | PostgreSQL migration path | ❌ LATER |
| 7.2 | Docker deployment | ❌ OPEN |
| 7.3 | Backup script | ❌ OPEN |
| 7.4 | Strict env validation in production mode | ❌ OPEN |

### GUEST EXPERIENCE (Added v7)

| \# | Item | Status |
| --- | --- | --- |
| G.1 | Receipt: "You're All Set" + confirmation number callout | ✅ DONE (v7) |
| G.2 | Receipt: check-in/out times (3PM / 11AM) | ✅ DONE (v7) |
| G.3 | Receipt: "What to Bring" (ID, email, CC for incidentals) | ✅ DONE (v7) |
| G.4 | Receipt: "What Happens Next" (arrival instructions) | ✅ DONE (v7) |
| G.5 | Receipt: cancellation/modification policy | ✅ DONE (v7) |
| G.6 | Receipt: agent contact with phone number | ✅ DONE (v7) |
| G.7 | Alt-payment: "Your Reservation Is Still Held" reassurance | ✅ DONE (v7) |
| G.8 | Alt-payment: "not unusual" explanation (bank security) | ✅ DONE (v7) |
| G.9 | Alt-payment: single primary CTA + alternatives below | ✅ DONE (v7) |
| G.10 | Alt-payment: 24-hour urgency + 48-hour consequence | ✅ DONE (v7) |
| G.11 | Alt-payment: agent availability with phone | ✅ DONE (v7) |
| G.12 | Agent phone field on form + stored in DB | ✅ DONE (v7) |

---

## WHAT TO BUILD NEXT (Recommended Order)

**Batch 1 — Remaining Phase 1 (Critical Fixes):**

- 1.2 Phone validation
- 1.3 Confidence pause on Step 1
- 1.4 Manual override fields
- 1.5 Update .env.example

**Batch 2 — Phase 5.4 + Phase 2 (Security + Manual Confirm):**

- 5.4 "Mark as Paid" button for wire/Zelle/Venmo/Wise
- 2.1–2.5 Security hardening

**Batch 3 — Phase 3.1–3.2 (Dashboard):**

- Stats cards + booking detail modal with actions

**Everything else → after the platform is live and agents are using it.**

---

## COMPLETED SUMMARY

**Done across v1–v7:**

- Full 4-step agent flow (Paste → Payment → Preview → Confirm+Send)
- Smart paste parser (multi-OTA, confidence scoring)
- 6 payment methods with correct email routing
- 3 email templates (confirmation, receipt, alt-payment)
- Guest experience emails (check-in instructions, what to bring, agent contact)
- PayPal Orders API v2 with token caching
- Webhook listener with state machine
- Database with WAL mode, indexes, event audit trail
- Health endpoint, stats endpoint, resend, cancel, regenerate-paypal
- Structured logging, request tracing, CORS, error middleware
- Payment config file for account details
- Dashboard with search and loading states
- Light theme UI with Poppins/Open Sans, SVG icons