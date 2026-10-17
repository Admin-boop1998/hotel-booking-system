# Hotel Reservation Platform — System Map v8.2

## System Architecture

```
                    ┌─────────────────────────────┐
                    │     AGENT (Browser)          │
                    │                              │
                    │  Step 1: Paste + Guest Info   │
                    │  Step 2: Payment Method       │
                    │  Step 3: Preview + Confirm    │
                    │  Step 4: Command Center       │
                    │  Dashboard: Stats + Search    │
                    └──────────┬──────────────────┘
                               │ HTTP/JSON
                    ┌──────────▼──────────────────┐
                    │     FastAPI (main.py)         │
                    │     Port 8100                 │
                    │                              │
                    │  ┌── Middleware ────────────┐ │
                    │  │ Request ID tracing       │ │
                    │  │ Error handling           │ │
                    │  │ CORS                     │ │
                    │  └─────────────────────────┘ │
                    │                              │
                    │  ┌── Endpoints ─────────────┐ │
                    │  │ POST /api/bookings       │ │
                    │  │ POST /api/parse-paste    │ │
                    │  │ POST /api/webhooks/paypal│ │
                    │  │ POST .../mark-paid       │ │
                    │  │ POST .../switch-method   │ │
                    │  │ POST .../retry           │ │
                    │  │ POST .../resend          │ │
                    │  │ POST .../cancel          │ │
                    │  │ PATCH /api/bookings/{ref}│ │
                    │  │ GET  /api/bookings       │ │
                    │  │ GET  /api/bookings/{ref} │ │
                    │  │ GET  .../events          │ │
                    │  │ GET  /api/health         │ │
                    │  │ GET  /api/stats          │ │
                    │  │ GET  /api/track/open/... │ │
                    │  │ GET  /api/track/click/...│ │
                    │  └─────────────────────────┘ │
                    └──┬────┬────┬────┬───────────┘
                       │    │    │    │
          ┌────────────┘    │    │    └────────────┐
          ▼                 ▼    ▼                 ▼
   ┌─────────────┐  ┌──────────┐ ┌──────────┐ ┌──────────┐
   │ database.py │  │email_svc │ │paypal_svc│ │paste_    │
   │             │  │          │ │          │ │parser.py │
   │ SQLite/WAL  │  │ Jinja2   │ │Orders v2 │ │          │
   │ bookings    │  │ SMTP     │ │Token $   │ │Multi-OTA │
   │ events      │  │ Tracking │ │Capture   │ │Confidence│
   │ State Mach. │  │          │ │Verify    │ │          │
   │ Stats       │  │3 Templs  │ │Decline   │ │Discount  │
   └─────────────┘  └──────────┘ └──────────┘ └──────────┘
                         │
              ┌──────────┼──────────┐
              ▼          ▼          ▼
        confirmation  receipt  alt_payment
           .html       .html     .html
```

## File Map

```
hotel-booking-system/
├── backend/
│   ├── main.py              767 lines — FastAPI, all endpoints, middleware
│   ├── database.py          278 lines — SQLite/WAL, state machine, stats
│   ├── email_service.py     177 lines — Jinja2 render, SMTP, tracking, discount calc
│   ├── paypal_service.py    202 lines — Orders API v2, token cache, capture, verify
│   ├── paste_parser.py      257 lines — Multi-OTA regex, confidence scoring
│   ├── payment_config.py     54 lines — Load payment_methods.json
│   └── __init__.py
├── frontend/
│   ├── index.html           577 lines — Agent portal (steps + dashboard + actions)
│   └── images/
│       ├── hotel_room.jpg
│       └── hotel_lobby.jpg
├── templates/
│   ├── booking_confirmation.html  217 lines — Per-method CTA, discount, OTA style
│   ├── receipt.html               134 lines — Guest experience, check-in info
│   └── alternative_payment.html   153 lines — Reassurance, single CTA, fallbacks
├── config/
│   └── payment_methods.json        15 lines — Bank details, payment contacts
├── .env.example                    20 lines — All env vars documented
├── start.sh
├── README.md
├── BLUEPRINT.md
└── SYSTEM-MAP.md (this file)
```

## Data Flow — Complete Booking Lifecycle

```
AGENT PASTES HOTEL DATA
         │
         ▼
   POST /api/parse-paste ──► paste_parser.py
         │                   - Extract hotel, dates, room, rates
         │                   - Multi-OTA format support
         │                   - Return confidence score (0-100)
         │
         ▼
   Frontend shows results
   - Confidence ≥ 75: auto-advance to Step 2
   - Confidence < 75: pause, show override fields
   - Agent can edit dates/room/amount manually
         │
         ▼
   AGENT PICKS PAYMENT METHOD
   (paypal | paypal_invoice | bank_transfer | zelle | venmo | wise)
         │
         ▼
   AGENT REVIEWS PREVIEW + CHECKS CONFIRM BOX
         │
         ▼
   POST /api/bookings ──────────────────────────────────┐
         │                                               │
         ├── Validate inputs (email, phone, dates, amt) │
         ├── Parse raw_paste (if provided)               │
         ├── Generate unique ref (QS-YYYYMMDD-XXXXXX)   │
         ├── Create PayPal order (if paypal/invoice)     │
         ├── Save to DB (status: Pending)                │
         ├── Calculate fake discount (÷ 0.75)            │
         ├── Render email (per payment method)           │
         ├── Inject tracking pixel + CTA click tracker   │
         ├── Send via SMTP (if configured)               │
         └── Return ref, preview link, status             │
                                                          │
   ┌──────────────────────────────────────────────────────┘
   │
   ▼
   GUEST RECEIVES EMAIL
   │
   ├── Opens email ──► GET /api/track/open/{ref}
   │                   └── Log EMAIL_OPENED event
   │
   ├── Clicks CTA ──► GET /api/track/click/{ref}?url=...
   │                   ├── Log CTA_CLICKED event
   │                   └── 302 redirect to payment URL
   │
   ├── Completes PayPal ──► PayPal webhook
   │   POST /api/webhooks/paypal
   │   ├── CHECKOUT.ORDER.APPROVED → Capture → Confirmed
   │   ├── PAYMENT.CAPTURE.COMPLETED → Confirmed
   │   ├── PAYMENT.CAPTURE.DENIED → Declined
   │   │   ├── Parse decline reason code
   │   │   ├── Log DECLINE_REASON event
   │   │   ├── Send alt-payment email
   │   │   └── Agent sees reason on screen
   │   └── Idempotency: skip duplicate event_id
   │
   └── Pays via bank/Zelle/Venmo/Wise
       └── Agent manually confirms:
           POST /api/bookings/{ref}/mark-paid
           ├── Enter transaction reference
           ├── Status → Confirmed
           └── Receipt email sent to guest
```

## Agent Actions — Scenario Matrix

```
┌──────────────────────┬────────────────────────────────────────┐
│ SCENARIO             │ AGENT ACTION → SYSTEM RESPONSE          │
├──────────────────────┼────────────────────────────────────────┤
│ Email in guest's     │ Copy Link → clipboard                  │
│ spam folder          │ Agent reads URL to guest on phone       │
├──────────────────────┼────────────────────────────────────────┤
│ Guest opened but     │ Agent sees "Opened" in tracking bar    │
│ didn't click         │ Agent guides: "click the gold button"   │
├──────────────────────┼────────────────────────────────────────┤
│ Guest clicked but    │ Agent sees "Clicked" in tracking bar   │
│ didn't complete      │ Retry → new PayPal link + resend       │
├──────────────────────┼────────────────────────────────────────┤
│ PayPal declined      │ Agent sees decline reason              │
│ (bank blocked it)    │ → "Guest needs to call bank"           │
│                      │ → Retry once guest authorizes           │
├──────────────────────┼────────────────────────────────────────┤
│ PayPal not working   │ Switch Method → pick Zelle/Wire/etc.   │
│ at all               │ New email auto-sent with correct CTA    │
├──────────────────────┼────────────────────────────────────────┤
│ Guest paid via       │ Mark as Paid → enter Zelle conf #       │
│ Zelle/Wire/Venmo     │ Status → Confirmed, receipt email sent  │
├──────────────────────┼────────────────────────────────────────┤
│ Guest wants to       │ Cancel booking                          │
│ cancel               │ Status → Cancelled (terminal)           │
├──────────────────────┼────────────────────────────────────────┤
│ Agent made a typo    │ Resend Email (same method, same link)   │
│ or guest didn't get  │                                         │
├──────────────────────┼────────────────────────────────────────┤
│ Agent entered wrong  │ Edit Booking (PATCH) while Pending      │
│ guest email/name     │ Then Resend                             │
├──────────────────────┼────────────────────────────────────────┤
│ Payment confirmed    │ Auto: receipt email with check-in info  │
│ (PayPal webhook)     │ Agent sees green "Payment confirmed"     │
│                      │ Browser notification fires               │
└──────────────────────┴────────────────────────────────────────┘
```

## Status State Machine

```
                 ┌─────────────┐
                 │   PENDING   │ ← Initial state
                 └──┬───┬───┬──┘
                    │   │   │
         Confirmed  │   │   │  Cancelled
         (PayPal or │   │   │  (Agent)
         Manual)    │   │   │
                    ▼   │   ▼
            ┌──────────┐│┌──────────┐
            │CONFIRMED ││ │CANCELLED │ ← Terminal
            └────┬─────┘│└──────────┘
                 │      │
          Refund │      │ Declined
          (future)      │ (PayPal)
                 ▼      ▼
           ┌──────────┐┌──────────┐
           │ REFUNDED ││ DECLINED │
           └──────────┘└──┬───┬───┘
                          │   │
                Retry →   │   │  Cancel
                Pending   │   │
                          ▼   ▼
                    Back to PENDING
                    or CANCELLED
```

## Payment Methods

| Code | Label | Email CTA | Webhook? | Confirm How? |
| --- | --- | --- | --- | --- |
| `paypal` | PayPal | Gold button → PayPal checkout | Yes (auto) | PayPal webhook |
| `paypal_invoice` | PayPal Invoice | Gold button → PayPal checkout | Yes (auto) | PayPal webhook |
| `bank_transfer` | Bank Transfer | Bank details card (Lead Bank) | No | Agent: Mark as Paid |
| `zelle` | Zelle | Zelle contact + memo | No | Agent: Mark as Paid |
| `venmo` | Venmo | Venmo handle + note | No | Agent: Mark as Paid |
| `wise` | Wise | Wise email + ref | No | Agent: Mark as Paid |

## Email Templates

| Template | Trigger | Key Content |
| --- | --- | --- |
| `booking_confirmation.html` | Agent sends booking | Hotel name (28px serif), room, dates, 25% fake discount, per-method CTA, tracking pixel |
| `receipt.html` | Payment confirmed | "You're All Set", confirmation #, check-in info, what to bring, agent contact |
| `alternative_payment.html` | PayPal declined | "Reservation Still Held", single retry CTA, bank/Zelle/Venmo/Wise options, 24hr urgency |

## API Reference

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/bookings` | Create booking + send email |
| POST | `/api/parse-paste` | Parse raw OTA text |
| POST | `/api/webhooks/paypal` | PayPal event handler |
| POST | `/api/bookings/{ref}/mark-paid` | Manual payment confirm |
| POST | `/api/bookings/{ref}/switch-method` | Change payment method |
| POST | `/api/bookings/{ref}/retry` | Regenerate + resend |
| POST | `/api/bookings/{ref}/resend` | Resend same email |
| POST | `/api/bookings/{ref}/cancel` | Cancel booking |
| POST | `/api/bookings/{ref}/regenerate-paypal` | New PayPal link only |
| PATCH | `/api/bookings/{ref}` | Edit guest info |
| GET | `/api/bookings` | List all bookings |
| GET | `/api/bookings/{ref}` | Booking detail + tracking |
| GET | `/api/bookings/{ref}/events` | Payment event timeline |
| GET | `/api/health` | System health + stats |
| GET | `/api/stats` | Dashboard numbers |
| GET | `/api/track/open/{ref}` | Email open pixel |
| GET | `/api/track/click/{ref}` | CTA click redirect |
| GET | `/api/emails/confirmation/{ref}` | Preview confirmation |
| GET | `/api/emails/receipt/{ref}` | Preview receipt |
| GET | `/api/emails/alt-payment/{ref}` | Preview alt-payment |
| GET | `/payment/success` | Guest success page |
| GET | `/payment/cancel` | Guest cancel page |
| GET | `/` | Agent portal frontend |

## Environment Variables

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `PAYPAL_CLIENT_ID` | For PayPal | — | PayPal API client ID |
| `PAYPAL_CLIENT_SECRET` | For PayPal | — | PayPal API secret |
| `PAYPAL_MODE` | No | `sandbox` | `sandbox` or `live` |
| `PAYPAL_WEBHOOK_ID` | For production | — | Webhook signature verification |
| `APP_BASE_URL` | Yes | `http://localhost:8100` | Public URL for callbacks |
| `SMTP_HOST` | For email delivery | — | SMTP server |
| `SMTP_PORT` | No | `587` | SMTP port |
| `SMTP_USER` | For email delivery | — | SMTP login |
| `SMTP_PASS` | For email delivery | — | SMTP password |
| `SMTP_FROM` | No | SMTP_USER | From address |
| `HERO_IMAGE_URL` | No | — | CDN URL for email hero image |
| `ALLOWED_ORIGINS` | No | `*` | CORS allowed origins |

## Consistency Audit (v8.2)

| Check | Status |
| --- | --- |
| Payment method names aligned (6 files) | ✅ |
| No "Quota Solutions" references | ✅ |
| No "paypal_ach" references | ✅ |
| No "wire" (old name) references | ✅ |
| agent_phone in DB schema | ✅ |
| agent_phone in insert query | ✅ |
| agent_phone in frontend form | ✅ |
| agent_phone in email templates | ✅ |
| Discount calc in email service | ✅ |
| Tracking pixel in confirmation | ✅ |
| CTA click redirect wrapper | ✅ |
| Webhook idempotency (event_id) | ✅ |
| Ref format validation on endpoints | ✅ |
| CSP headers on email previews | ✅ |
| Input sanitization (10K paste limit) | ✅ |
| Phone validation (7+ digits) | ✅ |
