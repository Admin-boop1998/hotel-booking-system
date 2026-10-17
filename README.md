# Quota Solutions — Travel Desk v9.3

Payment link automation platform for hotel reservation agents. Operated by Virtooall Core BPO (DTI Reg. 6481235). Agent pastes booking data, picks payment method, reviews, confirms, sends. Guest pays while on the phone. No credit cards over the phone. PCI compliant.

## Quick Start

```bash
pip install fastapi uvicorn aiofiles jinja2 aiosqlite python-multipart httpx
cp .env.example .env   # Edit with your PayPal + SMTP credentials
chmod +x start.sh
./start.sh              # Open http://localhost:8100
```

## Agent Workflow

1. **Paste** hotel booking data from any OTA (Expedia, Hotels.com, Booking.com)
2. **Pick** payment method (PayPal, PayPal Invoice, Bank Transfer, Zelle, Venmo, Wise)
3. **Preview** everything the guest will see — including 25% member discount
4. **Confirm** and send — guest receives professional email with payment CTA
5. **Monitor** — real-time tracking (opened, clicked, paid) + agent actions

## Payment Methods

| Method | Email Shows | Confirmed By |
|--------|-------------|-------------|
| PayPal | Checkout button | PayPal webhook (auto) |
| PayPal Invoice | Checkout button | PayPal webhook (auto) |
| Bank Transfer | Lead Bank account details | Agent: Mark as Paid |
| Zelle | Zelle contact | Agent: Mark as Paid |
| Venmo | Venmo handle | Agent: Mark as Paid |
| Wise | Wise email | Agent: Mark as Paid |

## Agent Actions

- **Copy Link** — paste/read payment URL to guest
- **Resend** — resend same email
- **Retry** — new PayPal link + resend (after bank authorization)
- **Switch Method** — change payment method and resend
- **Mark as Paid** — manual confirm for non-PayPal (enter transaction ref)
- **Cancel** — cancel booking

## Configuration

- `config/payment_methods.json` — bank account details, Zelle/Venmo/Wise contacts
- `.env` — PayPal API, SMTP, hero image URL

## Documentation

- `SYSTEM-MAP.md` — full architecture, data flow, API reference, scenario matrix
- `BLUEPRINT.md` — feature tracker and roadmap
