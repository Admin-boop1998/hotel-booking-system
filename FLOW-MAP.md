# Hotel Reservation — Complete Flow Map (Verified)

All flows tested and passing — v9.4 | Oct 15, 2026

---

## Flow 1: PayPal Happy Path ✅

```
Agent pastes booking → Picks PayPal → Preview → Send
  │
  ├─ TRIGGER: Confirmation email sent to guest
  │   ├─ Has: Hotel name, discount, PayPal CTA button
  │   ├─ Has: Cancellation policy
  │   └─ NO: Proof reminder (PayPal handles it)
  │
  ├─ Guest opens email
  │   └─ TRIGGER: Tracking pixel fires → EMAIL_OPENED event logged
  │
  ├─ Guest clicks "Complete Reservation & Pay Securely"
  │   └─ TRIGGER: Click redirect → CTA_CLICKED event logged
  │   └─ Guest lands on PayPal checkout page
  │       ├─ Shows: "Quota Solutions — Travel Desk"
  │       ├─ Shows: Itemized nightly rates + total
  │       ├─ Shows: Card/balance/bank payment options
  │       └─ No shipping address form
  │
  ├─ Guest pays successfully
  │   ├─ WEBHOOK: CHECKOUT.ORDER.APPROVED → system captures payment
  │   ├─ WEBHOOK: PAYMENT.CAPTURE.COMPLETED
  │   │   ├─ TRIGGER: Status → Confirmed
  │   │   ├─ TRIGGER: Receipt email sent (What Happens Next, What to Bring)
  │   │   ├─ TRIGGER: PDF Invoice generated (DTI reg, legal terms)
  │   │   └─ TRIGGER: Google Sheets synced
  │   └─ Agent sees: Green "Confirmed" on Step 4
  │
  └─ DONE: Guest has receipt + invoice, agent has confirmation
```

## Flow 2: PayPal Declined → Recovery ✅

```
Guest's card declined on PayPal
  │
  ├─ WEBHOOK: PAYMENT.CAPTURE.DENIED
  │   ├─ Decline reason parsed (INSTRUMENT_DECLINED, etc.)
  │   ├─ TRIGGER: Status → Declined
  │   ├─ TRIGGER: Alt-payment email sent
  │   │   ├─ Has: "Your reservation is still held for 24 hours"
  │   │   ├─ Has: "Try Again with a Different Card" (primary CTA)
  │   │   ├─ Has: Bank Transfer option with account details
  │   │   ├─ Has: 24-hour urgency, 48-hour release warning
  │   │   └─ NO: Zelle/Venmo/Wise (paused)
  │   └─ Agent sees: Red "Declined" + decline reason
  │
  ├─ RECOVERY OPTION A: Agent clicks "Retry"
  │   ├─ Regenerates PayPal link
  │   ├─ Status → Pending
  │   ├─ Resends confirmation email with new link
  │   └─ Guest tries again
  │
  ├─ RECOVERY OPTION B: Agent clicks "Switch Method"
  │   ├─ Picks bank_transfer
  │   ├─ Updates method in DB
  │   ├─ TRIGGER: New confirmation email with bank details
  │   │   ├─ Has: Copy-friendly account/routing cards
  │   │   ├─ Has: Proof of payment reminder
  │   │   └─ NO: PayPal button
  │   └─ Guest does wire transfer
  │
  └─ RECOVERY OPTION C: Agent clicks "Resend"
      └─ Same email resent (useful if guest's email went to spam)
```

## Flow 3: Bank Transfer → Manual Confirm ✅

```
Agent creates booking → Picks Bank Transfer → Send
  │
  ├─ TRIGGER: Confirmation email with bank details
  │   ├─ Has: Account Name, Bank, Account #, Routing # in card layout
  │   ├─ Has: user-select:all for easy tap-to-copy
  │   ├─ Has: Memo field with booking ref (green highlight)
  │   ├─ Has: "Send Proof of Payment" reminder (yellow)
  │   ├─ Has: Cancellation policy
  │   └─ NO: PayPal button
  │
  ├─ Guest looks up booking at /my-booking
  │   ├─ Enters ref + email → sees status, details, total
  │   └─ Sees "I've Completed My Payment" button (only for non-PayPal Pending)
  │
  ├─ Guest sends wire transfer
  │
  ├─ Guest clicks "I've Completed My Payment"
  │   ├─ Selects method, enters confirmation number
  │   └─ TRIGGER: GUEST_PAYMENT_NOTIFICATION event logged
  │
  ├─ Agent sees notification in events timeline
  │
  ├─ Agent clicks "Mark as Paid"
  │   ├─ Enters transaction ref (WIRE-REF-12345)
  │   ├─ TRIGGER: Status → Confirmed
  │   ├─ TRIGGER: Receipt email sent
  │   ├─ TRIGGER: PDF Invoice generated
  │   └─ TRIGGER: Google Sheets synced
  │
  └─ Guest checks /my-booking again → sees "Confirmed" (no I Paid button)
```

## Flow 4: Cancel ✅

```
Agent clicks "Cancel" on Step 4 or dashboard
  │
  ├─ Confirmation dialog
  ├─ TRIGGER: Status → Cancelled (terminal)
  ├─ TRIGGER: Google Sheets synced
  │
  └─ Cannot cancel again (state machine blocks: "Cannot cancel a Cancelled booking")
```

## Validation Gates ✅

```
Bad email (no @)        → 422 "Invalid email address"
Short phone (<7 digits) → 422 "Phone must have at least 7 digits"
Checkout ≤ checkin      → 400 "Check-out must be after check-in"
Invalid payment method  → 400 "Invalid payment method"
Total ≤ 0               → 400 "Total amount must be positive"
Guest lookup wrong email → 404 "Booking Not Found"
Double cancel           → 400 "Cannot cancel a Cancelled booking"
```

## Email Trigger Summary

| Trigger | Email Type | Conditions |
|---------|-----------|------------|
| Agent clicks Send | `confirmation` | Always — with chosen method's CTA |
| PayPal CAPTURE.COMPLETED | `receipt` + `invoice` | Auto — payment confirmed |
| PayPal CAPTURE.DENIED | `alt_payment` | Auto — with retry + bank fallback |
| Agent: Mark as Paid | `receipt` + `invoice` | After entering txn ref |
| Agent: Resend | `confirmation` | Same email, same method |
| Agent: Switch Method | `confirmation` | New email with new method's CTA |
| Agent: Retry | `confirmation` | New PayPal link + resend |

## Status State Machine

```
Pending → Confirmed (PayPal webhook or Mark as Paid)
Pending → Declined (PayPal CAPTURE.DENIED)
Pending → Cancelled (Agent cancel)
Declined → Pending (Agent retry)
Declined → Confirmed (Mark as Paid)
Declined → Cancelled (Agent cancel)
Confirmed → Refunded (future)
Cancelled → (terminal — no transitions)
```
