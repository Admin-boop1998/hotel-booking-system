# Deployment Guide — booking.quotasolutions.net

## Option A: Render.com (Recommended)

### 1. Push to GitHub
```bash
cd hotel-booking-system
git init
git add .
git commit -m "Hotel Reservation v8.3"
git remote add origin https://github.com/YOUR_USERNAME/hotel-reservation.git
git push -u origin main
```

### 2. Deploy on Render
1. Go to https://render.com → New → Web Service
2. Connect your GitHub repo
3. Settings:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `cd backend && uvicorn main:app --host 0.0.0.0 --port $PORT`
   - **Add Disk:** mount path `/app/data`, 1 GB (stores SQLite + emails)
4. Add Environment Variables:
   - `PAYPAL_CLIENT_ID` — your PayPal app client ID
   - `PAYPAL_CLIENT_SECRET` — your PayPal app secret
   - `PAYPAL_MODE` — `sandbox` (for testing) or `live` (for production)
   - `APP_BASE_URL` — `https://booking.quotasolutions.net`
   - `SMTP_HOST` — your SMTP server (e.g. `smtp.gmail.com`)
   - `SMTP_PORT` — `587`
   - `SMTP_USER` — your email
   - `SMTP_PASS` — app password
   - `SMTP_FROM` — from address (e.g. `reservations@quotasolutions.net`)
   - `GOOGLE_SHEET_ID` — your Google Sheet ID (optional)
5. Deploy

### 3. Connect Cloudflare DNS
1. Go to Cloudflare → quotasolutions.net → DNS
2. Add CNAME record:
   - **Name:** `booking`
   - **Target:** your Render URL (e.g. `hotel-reservation-xxxx.onrender.com`)
   - **Proxy:** ON (orange cloud)
3. SSL: Cloudflare handles it automatically

### 4. Update PayPal Webhook URL
1. Go to https://developer.paypal.com/dashboard/applications
2. Under your app → Webhooks → Add webhook
3. URL: `https://booking.quotasolutions.net/api/webhooks/paypal`
4. Events: `CHECKOUT.ORDER.APPROVED`, `PAYMENT.CAPTURE.COMPLETED`, `PAYMENT.CAPTURE.DENIED`
5. Copy the Webhook ID → set as `PAYPAL_WEBHOOK_ID` env var

## Google Sheets Sync Setup

### 1. Create Google Cloud Service Account
1. Go to https://console.cloud.google.com
2. Create a project (or use existing)
3. Enable **Google Sheets API**
4. Go to IAM → Service Accounts → Create
5. Download the JSON key file
6. Save as `config/google-credentials.json` in the project

### 2. Create & Share the Google Sheet
1. Create a new Google Sheet
2. Name it "Hotel Reservations"
3. Copy the Sheet ID from the URL:
   `https://docs.google.com/spreadsheets/d/THIS_IS_THE_ID/edit`
4. Share the sheet with the service account email
   (found in the JSON key file: `"client_email": "..."`)
   Give it **Editor** access
5. Set `GOOGLE_SHEET_ID` env var to the Sheet ID

The system will auto-create two tabs:
- **Bookings** — one row per booking, updated on every status change
- **Events** — append-only log of all payment events

## Option B: Docker (any VPS)

```bash
docker build -t hotel-reservation .
docker run -d \
  -p 8100:8100 \
  -v hotel-data:/app/data \
  --env-file .env \
  --name booking \
  hotel-reservation
```

## Post-Deploy Checklist

- [ ] Visit `https://booking.quotasolutions.net` — see the agent portal
- [ ] Visit `https://booking.quotasolutions.net/api/health` — see system status
- [ ] Create a test booking → check email renders
- [ ] Check Google Sheet → booking row appears
- [ ] Test PayPal sandbox payment → webhook fires → status updates
- [ ] Verify SMTP sends real emails
- [ ] Fill in `config/payment_methods.json` with real Zelle/Venmo/Wise contacts
