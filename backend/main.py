"""
Quota Solutions — Travel Desk API v9.3
─────────────────────────────────────────────────────
Features:
  - Mark as Paid (manual confirm for non-PayPal)
  - Switch Payment Method (change method + resend)
  - Retry Payment (regenerate PayPal + resend in one call)
  - Email open/click tracking
  - PayPal decline reason parsing
  - Phone validation
  - Input sanitization (raw_paste length limit)
  - Booking edit (PATCH while Pending)
  - Booking ref format validation on all endpoints
  - Structured logging, request tracing, health, stats
"""
import os
import json
import uuid
import hashlib
import re
import time
import logging
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
from typing import Optional

from database import (
    init_db, close_db, insert_booking, get_booking, get_all_bookings,
    update_booking_status, update_paypal_order, get_booking_by_paypal_order,
    log_payment_event, get_booking_stats, booking_ref_exists, get_booking_events,
    update_booking_fields, get_bookings_for_export,
)
from email_service import send_booking_confirmation, send_receipt_email, send_alternative_payment_email
from paypal_service import create_order, capture_order, verify_webhook_signature, void_order, create_invoice, upload_fulfillment
from paste_parser import parse_raw_booking
import payment_config
import sheets_sync
from invoice_generator import generate_invoice_html

# ── Logging ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("api")

# ── Config ─────────────────────────────────────────────────────────────
REF_PATTERN = re.compile(r'^QS-\d{8}-[A-F0-9]{6,8}$')
MAX_PASTE_LENGTH = 10000

def _validate_config():
    paypal_ok = bool(os.getenv("PAYPAL_CLIENT_ID")) and os.getenv("PAYPAL_CLIENT_ID") != "YOUR_PAYPAL_CLIENT_ID"
    smtp_ok = bool(os.getenv("SMTP_HOST"))
    hero_ok = bool(os.getenv("HERO_IMAGE_URL"))
    log.info(f"Config: PayPal={'LIVE' if paypal_ok else 'UNCONFIGURED'} | SMTP={'LIVE' if smtp_ok else 'FILE-ONLY'} | HeroImg={'SET' if hero_ok else 'BANNER'}")
    return {"paypal": paypal_ok, "smtp": smtp_ok, "hero_image": hero_ok}

_config_status = {}
VALID_METHODS = {"paypal", "paypal_invoice", "bank_transfer"}

# ── Helpers ────────────────────────────────────────────────────────────
def _validate_ref(ref: str):
    if not REF_PATTERN.match(ref):
        raise HTTPException(status_code=400, detail="Invalid booking reference format")

def _paypal_checkout_url(order_id: str) -> str:
    if not order_id:
        return ""
    mode = os.getenv("PAYPAL_MODE", "sandbox")
    domain = "www.sandbox.paypal.com" if mode == "sandbox" else "www.paypal.com"
    return f"https://{domain}/checkoutnow?token={order_id}"

def _validate_date(d: str) -> str:
    d = d.strip()
    try:
        datetime.strptime(d, "%Y-%m-%d")
        return d
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {d}. Use YYYY-MM-DD.")

async def generate_booking_ref() -> str:
    for _ in range(5):
        dp = datetime.utcnow().strftime("%Y%m%d")
        up = hashlib.sha256(uuid.uuid4().bytes).hexdigest()[:6].upper()
        ref = f"QS-{dp}-{up}"
        if not await booking_ref_exists(ref):
            return ref
    dp = datetime.utcnow().strftime("%Y%m%d")
    up = hashlib.sha256(uuid.uuid4().bytes).hexdigest()[:8].upper()
    return f"QS-{dp}-{up}"

# PAYPAL DECLINE REASON MAP
DECLINE_REASONS = {
    "INSTRUMENT_DECLINED": "The guest's card was declined by their bank. They may need to call their bank to authorize the transaction.",
    "PAYER_ACTION_REQUIRED": "PayPal requires the guest to take action in their account (verify identity, add funds, etc.).",
    "INTERNAL_SERVICE_ERROR": "PayPal had a temporary error. The guest should try again.",
    "PAYEE_ACCOUNT_RESTRICTED": "Our PayPal account has a restriction. Use an alternative payment method.",
    "TRANSACTION_REFUSED": "The transaction was refused. The guest should try a different card or payment method.",
    "MAX_NUMBER_OF_PAYMENT_ATTEMPTS_EXCEEDED": "Too many payment attempts. The guest should wait 24 hours or use a different method.",
    "DUPLICATE_INVOICE_ID": "This booking was already submitted to PayPal. Regenerate the payment link.",
}

# ── Models ─────────────────────────────────────────────────────────────
class BookingRequest(BaseModel):
    guest_name: str
    guest_email: str
    guest_phone: str
    checkin_date: str = ""
    checkout_date: str = ""
    room_type: str = ""
    room_details: Optional[str] = ""
    tour_package: Optional[str] = ""
    num_guests: Optional[int] = 1
    total_amount: float = 0.0
    currency: Optional[str] = "USD"
    agent_name: Optional[str] = ""
    agent_phone: Optional[str] = ""
    notes: Optional[str] = ""
    payment_method: Optional[str] = "paypal"
    raw_paste: Optional[str] = ""

    @field_validator("guest_email")
    @classmethod
    def val_email(cls, v):
        v = v.strip().lower()
        if not re.match(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$', v):
            raise ValueError("Invalid email address")
        return v

    @field_validator("guest_name")
    @classmethod
    def val_name(cls, v):
        v = v.strip()
        if len(v) < 2:
            raise ValueError("Guest name must be at least 2 characters")
        return v

    @field_validator("guest_phone")
    @classmethod
    def val_phone(cls, v):
        digits = re.sub(r'\D', '', v)
        if len(digits) < 7:
            raise ValueError("Phone number must have at least 7 digits")
        return v.strip()

    @field_validator("total_amount")
    @classmethod
    def val_amount(cls, v):
        if v < 0:
            raise ValueError("Amount cannot be negative")
        return round(v, 2)

    @field_validator("raw_paste")
    @classmethod
    def val_paste(cls, v):
        if v and len(v) > MAX_PASTE_LENGTH:
            raise ValueError(f"Paste data exceeds {MAX_PASTE_LENGTH} character limit")
        return v


class BookingEditRequest(BaseModel):
    guest_name: Optional[str] = None
    guest_email: Optional[str] = None
    guest_phone: Optional[str] = None
    room_details: Optional[str] = None
    notes: Optional[str] = None
    num_guests: Optional[int] = None


# ── App Lifecycle ──────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _config_status
    _config_status = _validate_config()
    payment_config.load_config()
    sheets_sync.init_sheets()
    await init_db()
    log.info("Quota Solutions Travel Desk API v9.3 started")
    yield
    await close_db()
    log.info("Quota Solutions Travel Desk API v9.3 stopped")

app = FastAPI(title="Quota Solutions — Travel Desk", version="9.3.0", lifespan=lifespan)

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["*"], allow_headers=["*"])

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
ASSETS_DIR = os.path.join(os.path.dirname(__file__), "..", "assets")
if os.path.isdir(ASSETS_DIR):
    app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")
if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

_start_time = time.time()

# ── Middleware ─────────────────────────────────────────────────────────
@app.middleware("http")
async def request_middleware(request: Request, call_next):
    req_id = request.headers.get("x-request-id", uuid.uuid4().hex[:8])
    request.state.request_id = req_id
    start = time.time()
    try:
        response = await call_next(request)
        elapsed = round((time.time() - start) * 1000)
        if request.url.path.startswith("/api"):
            log.info(f"[{req_id}] {request.method} {request.url.path} → {response.status_code} ({elapsed}ms)")
        return response
    except Exception as e:
        elapsed = round((time.time() - start) * 1000)
        log.error(f"[{req_id}] {request.method} {request.url.path} → 500 ({elapsed}ms): {e}")
        return JSONResponse(status_code=500, content={"error": "Internal server error", "request_id": req_id})

# ══════════════════════════════════════════════════════════════════════
#  SYSTEM ENDPOINTS
# ══════════════════════════════════════════════════════════════════════

@app.get("/api/health")
async def health():
    uptime = round(time.time() - _start_time)
    stats = await get_booking_stats()
    return {"status": "ok", "uptime_seconds": uptime, "version": "9.3.0", "config": _config_status, "bookings": stats}

@app.get("/api/stats")
async def stats(date_from: str = Query(""), date_to: str = Query("")):
    return await get_booking_stats(date_from=date_from, date_to=date_to)


@app.get("/api/export/csv")
async def export_csv(
    search: str = Query(""),
    status: str = Query(""),
    date_from: str = Query(""),
    date_to: str = Query(""),
):
    """Export filtered bookings as CSV."""
    from database import get_bookings_for_export
    import csv
    import io

    bookings = await get_bookings_for_export(search=search, status_filter=status, date_from=date_from, date_to=date_to)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Booking Ref", "Guest Name", "Guest Email", "Guest Phone",
        "Hotel", "Room Type", "Check-In", "Check-Out", "Nights",
        "Guests", "Total Amount", "Currency", "Payment Method",
        "Status", "Agent Name", "Notes", "Created At",
    ])
    for b in bookings:
        writer.writerow([
            b["booking_ref"], b["guest_name"], b["guest_email"], b["guest_phone"],
            b.get("parsed_hotel_name", ""), b["room_type"],
            b["checkin_date"], b["checkout_date"], b.get("parsed_nights", ""),
            b.get("num_guests", 1), b["total_amount"], b.get("currency", "USD"),
            b.get("payment_method", ""), b["status"],
            b.get("agent_name", ""), b.get("notes", ""), b.get("created_at", ""),
        ])

    csv_content = output.getvalue()
    ts = datetime.utcnow().strftime("%Y%m%d")
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="bookings_{ts}.csv"'},
    )

# ══════════════════════════════════════════════════════════════════════
#  CREATE BOOKING
# ══════════════════════════════════════════════════════════════════════

@app.post("/api/bookings")
async def create_booking(req: BookingRequest):
    booking_ref = await generate_booking_ref()
    data = req.model_dump()
    data["booking_ref"] = booking_ref

    method = (req.payment_method or "paypal").lower()
    if method not in VALID_METHODS:
        raise HTTPException(status_code=400, detail=f"Invalid payment method. Options: {', '.join(sorted(VALID_METHODS))}")
    data["payment_method"] = method

    # Parse raw paste
    parsed = {}
    parse_confidence = 0
    if req.raw_paste and req.raw_paste.strip():
        parsed = parse_raw_booking(req.raw_paste)
        parse_confidence = parsed.get("confidence", 0)
        if not data["checkin_date"] and parsed.get("checkin_date"):
            data["checkin_date"] = parsed["checkin_date"]
        if not data["checkout_date"] and parsed.get("checkout_date"):
            data["checkout_date"] = parsed["checkout_date"]
        if not data["room_type"] and parsed.get("room_type"):
            data["room_type"] = parsed["room_type"]
        if data["total_amount"] == 0 and parsed.get("total_amount"):
            data["total_amount"] = parsed["total_amount"]
        if data["num_guests"] == 1 and parsed.get("adults", 0) > 1:
            data["num_guests"] = parsed["adults"]
        if parsed.get("currency"):
            data["currency"] = parsed["currency"]
        data["parsed_hotel_name"] = parsed.get("hotel_name", "")
        data["parsed_hotel_address"] = parsed.get("hotel_address", "")
        data["parsed_nightly_rates"] = parsed.get("nightly_rates", [])
        data["parsed_subtotal"] = parsed.get("subtotal", "")
        data["parsed_taxes"] = parsed.get("taxes", "")
        data["parsed_nights"] = parsed.get("nights", 0)
        data["parsed_extras"] = parsed.get("extras", [])

    # Validate
    if not data["checkin_date"] or not data["checkout_date"]:
        raise HTTPException(status_code=400, detail="Check-in and check-out dates are required.")
    data["checkin_date"] = _validate_date(data["checkin_date"])
    data["checkout_date"] = _validate_date(data["checkout_date"])
    if data["checkout_date"] <= data["checkin_date"]:
        raise HTTPException(status_code=400, detail="Check-out must be after check-in.")
    if data["total_amount"] <= 0:
        raise HTTPException(status_code=400, detail="Total amount must be greater than zero.")
    if not data["room_type"]:
        data["room_type"] = "Standard Room"

    # PayPal
    paypal_url = ""
    paypal_order_id = ""
    paypal_error = ""
    if method == "paypal":
        try:
            pp = await create_order(data)
            paypal_url = pp["approval_url"]
            paypal_order_id = pp["order_id"]
            data["paypal_order_id"] = paypal_order_id
        except Exception as e:
            paypal_error = str(e)
            log.warning(f"PayPal checkout failed for {booking_ref}: {e}")
    elif method == "paypal_invoice":
        try:
            inv = await create_invoice(data)
            paypal_url = inv.get("invoice_url", "")
            paypal_order_id = inv.get("invoice_id", "")
            data["paypal_order_id"] = paypal_order_id
        except Exception as e:
            paypal_error = str(e)
            log.warning(f"PayPal invoice failed for {booking_ref}: {e}")

    saved = await insert_booking(data)

    # Email
    email_path = ""
    email_error = ""
    try:
        email_path = send_booking_confirmation(saved, paypal_url, method)
        generate_invoice_html(saved)  # Generate invoice as bill-to-pay doc
    except Exception as e:
        email_error = str(e)
        log.warning(f"Email/invoice failed for {booking_ref}: {e}")

    # Sync to Google Sheets
    try:
        sheets_sync.sync_booking(saved)
        sheets_sync.sync_event(booking_ref, "BOOKING_CREATED", f"method={method}, total={data['total_amount']}")
    except Exception as e:
        log.warning(f"Sheets sync failed: {e}")

    smtp_live = bool(os.getenv("SMTP_HOST"))
    log.info(f"Booking {booking_ref}: method={method}, paypal={'ok' if paypal_order_id else 'n/a'}, email={'ok' if email_path else 'fail'}")

    return JSONResponse(status_code=201, content={
        "success": True,
        "booking_ref": booking_ref,
        "status": "Pending",
        "payment_method": method,
        "paypal_order_id": paypal_order_id,
        "paypal_checkout_url": paypal_url or "",
        "paypal_error": paypal_error or None,
        "email_rendered": bool(email_path),
        "email_delivered": smtp_live and bool(email_path),
        "email_error": email_error or None,
        "email_preview": f"/api/emails/confirmation/{booking_ref}" if email_path else None,
        "parsed_hotel": parsed.get("hotel_name", "") if parsed else "",
        "parse_confidence": parse_confidence,
        "message": f"Booking {booking_ref} created.",
    })

# ══════════════════════════════════════════════════════════════════════
#  PARSE-ONLY
# ══════════════════════════════════════════════════════════════════════

@app.post("/api/parse-paste")
async def parse_paste(request: Request):
    body = await request.json()
    raw = body.get("raw_paste", "")
    if not raw.strip():
        raise HTTPException(status_code=400, detail="No text to parse")
    if len(raw) > MAX_PASTE_LENGTH:
        raise HTTPException(status_code=400, detail=f"Paste exceeds {MAX_PASTE_LENGTH} chars")
    return parse_raw_booking(raw)

# ══════════════════════════════════════════════════════════════════════
#  PAYPAL WEBHOOK
# ══════════════════════════════════════════════════════════════════════

@app.post("/api/webhooks/paypal")
async def paypal_webhook(request: Request):
    body = await request.body()
    headers = dict(request.headers)

    if os.getenv("PAYPAL_WEBHOOK_ID") and os.getenv("PAYPAL_WEBHOOK_ID") != "YOUR_WEBHOOK_ID":
        verified = await verify_webhook_signature(headers, body)
        if not verified:
            log.warning("Webhook signature verification FAILED — rejecting")
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        event = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_type = event.get("event_type", "")
    event_id = event.get("id", "")
    resource = event.get("resource", {})
    log.info(f"Webhook: {event_type} (id={event_id})")

    # Resolve booking ref
    booking_ref = None
    order_id = None

    if event_type == "CHECKOUT.ORDER.APPROVED":
        order_id = resource.get("id")
        pu = resource.get("purchase_units", [])
        if pu:
            booking_ref = pu[0].get("reference_id") or pu[0].get("custom_id")
    elif event_type in ("PAYMENT.CAPTURE.COMPLETED", "PAYMENT.CAPTURE.DENIED"):
        order_id = resource.get("supplementary_data", {}).get("related_ids", {}).get("order_id")
        booking_ref = resource.get("invoice_id") or resource.get("custom_id")
        if not booking_ref and order_id:
            bk = await get_booking_by_paypal_order(order_id)
            if bk:
                booking_ref = bk["booking_ref"]

    if not booking_ref:
        return {"status": "ignored", "reason": "no_booking_ref"}

    # Idempotency: check if we already processed this event_id
    if event_id:
        existing_events = await get_booking_events(booking_ref)
        for ev in existing_events:
            try:
                payload = json.loads(ev.get("payload", "{}"))
                if payload.get("id") == event_id:
                    log.info(f"Duplicate webhook {event_id} for {booking_ref} — skipping")
                    return {"status": "duplicate", "event_id": event_id}
            except Exception:
                pass

    await log_payment_event(booking_ref, event_type, json.dumps(event))

    booking = await get_booking(booking_ref)
    if not booking:
        return {"status": "ignored", "reason": "booking_not_found"}

    # ── Route ──
    if event_type == "CHECKOUT.ORDER.APPROVED":
        try:
            cap = await capture_order(order_id)
            if cap.get("status") == "COMPLETED":
                cap_id = ""
                for pu in cap.get("purchase_units", []):
                    for c in pu.get("payments", {}).get("captures", []):
                        cap_id = c.get("id", "")
                        break
                if await update_booking_status(booking_ref, "Confirmed", cap_id):
                    booking = await get_booking(booking_ref)
                    send_receipt_email(booking)
                    generate_invoice_html(booking)
                    sheets_sync.sync_booking(booking)
                    sheets_sync.sync_event(booking_ref, "PAYMENT_CAPTURED", f"capture_id={cap_id}")
                return {"status": "captured", "booking_ref": booking_ref}
            else:
                await log_payment_event(booking_ref, "CAPTURE_INCOMPLETE", json.dumps(cap))
                send_alternative_payment_email(booking)
                return {"status": "capture_incomplete"}
        except Exception as e:
            log.error(f"Capture failed {booking_ref}: {e}")
            send_alternative_payment_email(booking)
            return {"status": "capture_failed"}

    elif event_type == "PAYMENT.CAPTURE.COMPLETED":
        cap_id = resource.get("id", "")
        if await update_booking_status(booking_ref, "Confirmed", cap_id):
            booking = await get_booking(booking_ref)
            send_receipt_email(booking)
            generate_invoice_html(booking)
            sheets_sync.sync_booking(booking)
            sheets_sync.sync_event(booking_ref, "PAYMENT_CONFIRMED", f"capture_id={cap_id}")
        return {"status": "confirmed", "booking_ref": booking_ref}

    elif event_type == "PAYMENT.CAPTURE.DENIED":
        # Parse decline reason
        decline_issue = resource.get("status_details", {}).get("reason", "UNKNOWN")
        decline_msg = DECLINE_REASONS.get(decline_issue, f"Payment was declined (reason: {decline_issue}).")
        await log_payment_event(booking_ref, "DECLINE_REASON", json.dumps({"reason": decline_issue, "message": decline_msg}))
        await update_booking_status(booking_ref, "Declined")
        booking = await get_booking(booking_ref)
        send_alternative_payment_email(booking)
        sheets_sync.sync_booking(booking)
        sheets_sync.sync_event(booking_ref, "PAYMENT_DECLINED", decline_msg)
        return {"status": "declined", "booking_ref": booking_ref, "decline_reason": decline_issue, "decline_message": decline_msg, "fallback_sent": True}

    elif event_type == "INVOICING.INVOICE.PAID":
        invoice_id = resource.get("id", "")
        if not booking_ref:
            ref_field = resource.get("detail", {}).get("reference", "")
            if ref_field:
                booking_ref = ref_field
                booking = await get_booking(booking_ref)

        if booking:
            if await update_booking_status(booking_ref, "Confirmed", invoice_id):
                booking = await get_booking(booking_ref)
                send_receipt_email(booking)
                generate_invoice_html(booking)
                try:
                    sheets_sync.sync_booking(booking)
                    sheets_sync.sync_event(booking_ref, "INVOICE_PAID", f"invoice_id={invoice_id}")
                except Exception:
                    pass
            return {"status": "confirmed", "booking_ref": booking_ref, "via": "invoice"}
        return {"status": "invoice_paid_no_booking", "invoice_id": invoice_id}

    elif event_type == "INVOICING.INVOICE.CANCELLED":
        if booking:
            await log_payment_event(booking_ref, "INVOICE_CANCELLED", json.dumps(event))
        return {"status": "invoice_cancelled"}

    return {"status": "unhandled", "event_type": event_type}

# ══════════════════════════════════════════════════════════════════════
#  AGENT ACTIONS
# ══════════════════════════════════════════════════════════════════════

@app.post("/api/bookings/{booking_ref}/mark-paid")
async def mark_paid(booking_ref: str, request: Request):
    """Manual payment confirmation for wire/Zelle/Venmo/Wise."""
    _validate_ref(booking_ref)
    booking = await get_booking(booking_ref)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking["status"] == "Confirmed":
        raise HTTPException(status_code=400, detail="Already confirmed")
    if booking["status"] == "Cancelled":
        raise HTTPException(status_code=400, detail="Cannot confirm a cancelled booking")
    method = booking.get("payment_method", "")
    if method in ("paypal", "paypal_invoice") and booking["status"] != "Declined":
        raise HTTPException(status_code=400, detail="PayPal bookings are confirmed automatically via webhook. Use this only after a PayPal decline.")

    try:
        body = await request.json()
    except Exception:
        body = {}
    txn_ref = body.get("transaction_ref", "MANUAL")
    note = body.get("note", "")

    await log_payment_event(booking_ref, "MANUAL_CONFIRM", json.dumps({"transaction_ref": txn_ref, "note": note, "confirmed_by": "agent"}))
    transitioned = await update_booking_status(booking_ref, "Confirmed", txn_ref)
    if not transitioned:
        raise HTTPException(status_code=400, detail=f"Cannot confirm a {booking['status']} booking")

    booking = await get_booking(booking_ref)
    try:
        send_receipt_email(booking)
        generate_invoice_html(booking)
    except Exception as e:
        log.warning(f"Receipt/invoice failed for {booking_ref}: {e}")

    try:
        sheets_sync.sync_booking(booking)
        sheets_sync.sync_event(booking_ref, "MANUAL_PAYMENT_CONFIRMED", f"txn={txn_ref}, note={note}")
    except Exception:
        pass
    log.info(f"Manual confirm: {booking_ref} (txn={txn_ref})")
    return {"success": True, "status": "Confirmed", "transaction_ref": txn_ref}


@app.post("/api/bookings/{booking_ref}/switch-method")
async def switch_method(booking_ref: str, request: Request):
    """Change payment method and resend email."""
    _validate_ref(booking_ref)
    booking = await get_booking(booking_ref)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking["status"] == "Confirmed":
        raise HTTPException(status_code=400, detail="Already confirmed — cannot switch")

    body = await request.json()
    new_method = body.get("payment_method", "").lower()
    if new_method not in VALID_METHODS:
        raise HTTPException(status_code=400, detail=f"Invalid method. Options: {', '.join(VALID_METHODS)}")

    # Update method in DB
    await update_booking_fields(booking_ref, {"payment_method": new_method})
    # Reset Declined → Pending on method switch
    if booking["status"] == "Declined":
        await update_booking_status(booking_ref, "Pending")

    # If switching to PayPal, generate new order
    paypal_url = ""
    if new_method in ("paypal", "paypal_invoice"):
        try:
            pp = await create_order(booking)
            paypal_url = pp["approval_url"]
            await update_paypal_order(booking_ref, pp["order_id"])
        except Exception as e:
            log.warning(f"PayPal order failed on switch: {e}")

    # Resend email with new method
    booking = await get_booking(booking_ref)
    try:
        send_booking_confirmation(booking, paypal_url, new_method)
    except Exception as e:
        log.warning(f"Email failed on switch: {e}")

    await log_payment_event(booking_ref, "METHOD_SWITCHED", json.dumps({"new_method": new_method}))
    try:
        sheets_sync.sync_booking(booking)
        sheets_sync.sync_event(booking_ref, "METHOD_SWITCHED", f"new_method={new_method}")
    except Exception:
        pass
    log.info(f"Method switched: {booking_ref} → {new_method}")
    return {"success": True, "payment_method": new_method, "email_preview": f"/api/emails/confirmation/{booking_ref}"}


@app.post("/api/bookings/{booking_ref}/retry")
async def retry_payment(booking_ref: str):
    """Regenerate PayPal link + resend email in one call."""
    _validate_ref(booking_ref)
    booking = await get_booking(booking_ref)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking["status"] == "Confirmed":
        raise HTTPException(status_code=400, detail="Already confirmed")

    method = booking.get("payment_method", "paypal")
    paypal_url = ""

    # For PayPal methods: regenerate order
    if method in ("paypal", "paypal_invoice"):
        try:
            pp = await create_order(booking)
            paypal_url = pp["approval_url"]
            await update_paypal_order(booking_ref, pp["order_id"])
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"PayPal error: {e}")
    else:
        # For non-PayPal: just resend the email
        pass

    # If was Declined, reset to Pending
    if booking["status"] == "Declined":
        await update_booking_status(booking_ref, "Pending")

    booking = await get_booking(booking_ref)
    try:
        send_booking_confirmation(booking, paypal_url, method)
    except Exception as e:
        log.warning(f"Email failed on retry: {e}")

    await log_payment_event(booking_ref, "PAYMENT_RETRY", json.dumps({"method": method}))
    log.info(f"Payment retry: {booking_ref}")
    return {"success": True, "paypal_checkout_url": paypal_url, "email_preview": f"/api/emails/confirmation/{booking_ref}"}


@app.post("/api/bookings/{booking_ref}/resend")
async def resend_email(booking_ref: str):
    _validate_ref(booking_ref)
    booking = await get_booking(booking_ref)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    try:
        if booking["status"] == "Confirmed":
            send_receipt_email(booking)
            log.info(f"Receipt resent: {booking_ref}")
            return {"success": True, "email_preview": f"/api/emails/receipt/{booking_ref}"}
        else:
            method = booking.get("payment_method", "paypal")
            paypal_url = _paypal_checkout_url(booking.get("paypal_order_id", ""))
            send_booking_confirmation(booking, paypal_url, method)
            log.info(f"Confirmation resent: {booking_ref}")
            return {"success": True, "email_preview": f"/api/emails/confirmation/{booking_ref}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Email error: {e}")


@app.post("/api/bookings/{booking_ref}/fulfill")
async def fulfill_booking(booking_ref: str, request: Request):
    """Agent submits hotel confirmation number. Auto-uploads to PayPal as proof of fulfillment."""
    _validate_ref(booking_ref)
    booking = await get_booking(booking_ref)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking["status"] != "Confirmed":
        raise HTTPException(status_code=400, detail="Can only fulfill a Confirmed booking")

    try:
        body = await request.json()
    except Exception:
        body = {}
    hotel_conf = body.get("hotel_confirmation", "")
    if not hotel_conf:
        raise HTTPException(status_code=400, detail="Hotel confirmation number is required")

    hotel_name = booking.get("parsed_hotel_name") or booking.get("room_type", "")

    # Save to DB
    await update_booking_fields(booking_ref, {"notes": f"Hotel Conf: {hotel_conf} | {booking.get('notes', '')}"})
    await log_payment_event(booking_ref, "HOTEL_FULFILLED", json.dumps({
        "hotel_confirmation": hotel_conf,
        "hotel_name": hotel_name,
    }))

    # Upload to PayPal
    paypal_result = {"skipped": True}
    order_id = booking.get("paypal_order_id", "")
    if order_id:
        paypal_result = await upload_fulfillment(order_id, booking_ref, hotel_conf, hotel_name)

    try:
        sheets_sync.sync_event(booking_ref, "HOTEL_FULFILLED", f"conf={hotel_conf}")
    except Exception:
        pass

    log.info(f"Fulfilled {booking_ref}: hotel_conf={hotel_conf}, paypal={paypal_result}")
    return {
        "success": True,
        "hotel_confirmation": hotel_conf,
        "paypal_fulfillment": paypal_result,
    }


@app.post("/api/bookings/{booking_ref}/cancel")
async def cancel_booking(booking_ref: str):
    _validate_ref(booking_ref)
    booking = await get_booking(booking_ref)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if not await update_booking_status(booking_ref, "Cancelled"):
        raise HTTPException(status_code=400, detail=f"Cannot cancel a {booking['status']} booking")
    # Void PayPal order if exists
    if booking.get("paypal_order_id"):
        try:
            await void_order(booking["paypal_order_id"])
            log.info(f"PayPal order voided for {booking_ref}")
        except Exception as e:
            log.warning(f"PayPal void failed for {booking_ref}: {e}")
    booking = await get_booking(booking_ref)
    try:
        sheets_sync.sync_booking(booking)
        sheets_sync.sync_event(booking_ref, "BOOKING_CANCELLED", "")
    except Exception:
        pass
    log.info(f"Cancelled: {booking_ref}")
    return {"success": True, "status": "Cancelled"}


@app.post("/api/bookings/{booking_ref}/regenerate-paypal")
async def regenerate_paypal(booking_ref: str):
    _validate_ref(booking_ref)
    booking = await get_booking(booking_ref)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking["status"] == "Confirmed":
        raise HTTPException(status_code=400, detail="Already confirmed")
    try:
        result = await create_order(booking)
        await update_paypal_order(booking_ref, result["order_id"])
        log.info(f"PayPal regenerated: {booking_ref}")
        return {"success": True, "paypal_checkout_url": result["approval_url"], "order_id": result["order_id"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PayPal error: {e}")


# ══════════════════════════════════════════════════════════════════════
#  BOOKING EDIT
# ══════════════════════════════════════════════════════════════════════

@app.patch("/api/bookings/{booking_ref}")
async def edit_booking(booking_ref: str, req: BookingEditRequest):
    _validate_ref(booking_ref)
    booking = await get_booking(booking_ref)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking["status"] not in ("Pending", "Declined"):
        raise HTTPException(status_code=400, detail=f"Cannot edit a {booking['status']} booking")

    updates = {}
    if req.guest_name is not None:
        updates["guest_name"] = req.guest_name.strip()
    if req.guest_email is not None:
        updates["guest_email"] = req.guest_email.strip().lower()
    if req.guest_phone is not None:
        updates["guest_phone"] = req.guest_phone.strip()
    if req.room_details is not None:
        updates["room_details"] = req.room_details
    if req.notes is not None:
        updates["notes"] = req.notes
    if req.num_guests is not None:
        updates["num_guests"] = req.num_guests

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    await update_booking_fields(booking_ref, updates)
    await log_payment_event(booking_ref, "BOOKING_EDITED", json.dumps(updates))
    log.info(f"Edited: {booking_ref} fields={list(updates.keys())}")
    updated = await get_booking(booking_ref)
    return {"success": True, "booking": updated}


# ══════════════════════════════════════════════════════════════════════
#  EMAIL TRACKING
# ══════════════════════════════════════════════════════════════════════

@app.get("/api/track/open/{booking_ref}")
async def track_open(booking_ref: str):
    """1x1 pixel tracking — logs when guest opens the email."""
    if REF_PATTERN.match(booking_ref):
        await log_payment_event(booking_ref, "EMAIL_OPENED", json.dumps({"timestamp": datetime.utcnow().isoformat()}))
        log.info(f"Email opened: {booking_ref}")
    # Return 1x1 transparent GIF
    pixel = b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00\x21\xf9\x04\x00\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b'
    return Response(content=pixel, media_type="image/gif", headers={"Cache-Control": "no-store, no-cache"})


@app.get("/api/track/click/{booking_ref}")
async def track_click(booking_ref: str, url: str = Query("")):
    """CTA click tracking — logs and redirects to actual payment URL."""
    if REF_PATTERN.match(booking_ref):
        await log_payment_event(booking_ref, "CTA_CLICKED", json.dumps({"url": url, "timestamp": datetime.utcnow().isoformat()}))
        log.info(f"CTA clicked: {booking_ref}")
    if url and (url.startswith("https://") or url.startswith("http://")):
        return RedirectResponse(url=url, status_code=302)
    return HTMLResponse("<html><body><p>Redirecting...</p></body></html>")


# ══════════════════════════════════════════════════════════════════════
#  BOOKING CRUD
# ══════════════════════════════════════════════════════════════════════

@app.get("/api/bookings")
async def list_bookings(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    search: str = Query(""),
    status: str = Query(""),
    date_from: str = Query(""),
    date_to: str = Query(""),
    agent: str = Query(""),
):
    return await get_all_bookings(
        page=page, per_page=per_page, search=search,
        status_filter=status, date_from=date_from, date_to=date_to, agent=agent,
    )

@app.get("/api/bookings/{booking_ref}")
async def get_booking_detail(booking_ref: str):
    _validate_ref(booking_ref)
    booking = await get_booking(booking_ref)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    # Enrich with tracking status
    events = await get_booking_events(booking_ref)
    booking["email_opened"] = any(e["event_type"] == "EMAIL_OPENED" for e in events)
    booking["cta_clicked"] = any(e["event_type"] == "CTA_CLICKED" for e in events)
    booking["decline_reason"] = None
    for e in events:
        if e["event_type"] == "DECLINE_REASON":
            try:
                booking["decline_reason"] = json.loads(e["payload"])
            except Exception:
                pass
            break
    return booking

@app.get("/api/bookings/{booking_ref}/events")
async def get_events(booking_ref: str):
    _validate_ref(booking_ref)
    booking = await get_booking(booking_ref)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    events = await get_booking_events(booking_ref)
    return {"booking_ref": booking_ref, "events": events}


# ══════════════════════════════════════════════════════════════════════
#  EMAIL PREVIEWS
# ══════════════════════════════════════════════════════════════════════

def _email_fp(kind: str, ref: str) -> str:
    _validate_ref(ref)
    return os.path.join(os.path.dirname(__file__), "..", "data", "emails", f"{kind}_{ref}.html")

@app.get("/api/emails/confirmation/{booking_ref}")
async def preview_email(booking_ref: str):
    fp = _email_fp("confirmation", booking_ref)
    if not os.path.exists(fp):
        raise HTTPException(status_code=404, detail="Email not generated yet")
    return FileResponse(fp, media_type="text/html", headers={"Content-Security-Policy": "script-src 'none'"})

@app.get("/api/emails/receipt/{booking_ref}")
async def preview_receipt(booking_ref: str):
    fp = _email_fp("receipt", booking_ref)
    if not os.path.exists(fp):
        raise HTTPException(status_code=404, detail="Not generated yet")
    return FileResponse(fp, media_type="text/html", headers={"Content-Security-Policy": "script-src 'none'"})

@app.get("/api/invoices/{booking_ref}")
async def preview_invoice(booking_ref: str):
    _validate_ref(booking_ref)
    fp = os.path.join(os.path.dirname(__file__), "..", "data", "invoices", f"invoice_{booking_ref}.html")
    if not os.path.exists(fp):
        raise HTTPException(status_code=404, detail="Invoice not generated yet — booking must be confirmed first")
    return FileResponse(fp, media_type="text/html", headers={"Content-Security-Policy": "script-src 'none'"})

@app.get("/api/emails/alt-payment/{booking_ref}")
async def preview_alt(booking_ref: str):
    fp = _email_fp("alt_payment", booking_ref)
    if not os.path.exists(fp):
        raise HTTPException(status_code=404, detail="Not generated yet")
    return FileResponse(fp, media_type="text/html", headers={"Content-Security-Policy": "script-src 'none'"})


# ══════════════════════════════════════════════════════════════════════
#  GUEST-FACING PAGES
# ══════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════
#  GUEST SELF-SERVICE
# ══════════════════════════════════════════════════════════════════════

@app.get("/my-booking")
async def guest_lookup(ref: str = Query(""), email: str = Query("")):
    """Guest-facing booking lookup — no login, just ref + email."""
    import payment_config as pc
    cfg = pc.get_all()
    company = cfg.get("company_name", "Quota Solutions — Travel Desk")
    company_phone = cfg.get("company_phone", "")

    if not ref or not email:
        return HTMLResponse(f"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Look Up Your Booking — {company}</title>
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@600;700&family=Open+Sans:wght@400;500&display=swap" rel="stylesheet">
<style>body{{font-family:Montserrat,Helvetica,sans-serif;background:#F1F5F9;margin:0;display:flex;align-items:center;justify-content:center;min-height:100vh}}
.c{{background:#fff;padding:40px;border-radius:16px;box-shadow:0 4px 24px rgba(0,0,0,.04);max-width:420px;width:100%;border:1px solid #E2E8F0}}
h1{{font-family:Montserrat,sans-serif;font-size:20px;color:#1B2E4A;margin:0 0 4px}}
p{{font-size:13px;color:#64748B;margin:0 0 20px}}
label{{font-size:12px;font-weight:600;color:#334155;display:block;margin:0 0 4px}}
input{{width:100%;padding:10px 12px;border:1px solid #E2E8F0;border-radius:8px;font-size:14px;margin:0 0 14px;box-sizing:border-box;font-family:Montserrat,Helvetica,sans-serif}}
input:focus{{outline:none;border-color:#3B82F6;box-shadow:0 0 0 2px #DBEAFE}}
button{{width:100%;padding:12px;background:#1B2E4A;color:#fff;border:none;border-radius:8px;font-size:14px;font-weight:600;font-family:Montserrat,sans-serif;cursor:pointer}}
button:hover{{background:#1E3070}}</style></head>
<body><div class="c"><h1>Look Up Your Booking</h1><p>Enter your booking reference and email address.</p>
<form method="get" action="/my-booking">
<label>Booking Reference</label><input name="ref" placeholder="QS-20261015-XXXXXX" required>
<label>Email Address</label><input name="email" type="email" placeholder="your@email.com" required>
<button type="submit">Find My Booking</button>
</form></div></body></html>""")

    booking = await get_booking(ref)
    if not booking or booking["guest_email"].lower() != email.strip().lower():
        return HTMLResponse(f"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Not Found</title>
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@600&family=Open+Sans:wght@400&display=swap" rel="stylesheet">
<style>body{{font-family:Montserrat,Helvetica,sans-serif;background:#F1F5F9;margin:0;display:flex;align-items:center;justify-content:center;min-height:100vh}}
.c{{background:#fff;padding:40px;border-radius:16px;box-shadow:0 4px 24px rgba(0,0,0,.04);max-width:420px;text-align:center;border:1px solid #E2E8F0}}
h1{{font-family:Montserrat,sans-serif;font-size:18px;color:#1B2E4A;margin:0 0 8px}}
p{{font-size:13px;color:#64748B}}a{{color:#1B2E4A}}</style></head>
<body><div class="c"><div style="font-size:40px;margin-bottom:12px">🔍</div><h1>Booking Not Found</h1>
<p>We couldn't find a booking matching that reference and email. Please double-check and <a href="/my-booking">try again</a>.</p>
{f'<p style="margin-top:16px">Need help? Call <strong>{company_phone}</strong></p>' if company_phone else ''}
</div></body></html>""", status_code=404)

    status_colors = {"Pending": "#CA8A04", "Confirmed": "#059669", "Declined": "#DC2626", "Cancelled": "#64748B"}
    sc = status_colors.get(booking["status"], "#64748B")
    hotel = booking.get("parsed_hotel_name") or booking.get("room_type", "")
    show_ipaid = booking["status"] == "Pending" and booking.get("payment_method") not in ("paypal", "paypal_invoice")

    return HTMLResponse(f"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Your Booking — {booking['booking_ref']}</title>
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@500;600;700&family=Open+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>body{{font-family:Montserrat,Helvetica,sans-serif;background:#F1F5F9;margin:0;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px}}
.c{{background:#fff;padding:0;border-radius:16px;box-shadow:0 4px 24px rgba(0,0,0,.04);max-width:480px;width:100%;border:1px solid #E2E8F0;overflow:hidden}}
.hd{{background:#1B2E4A;padding:20px 28px;color:#fff}}
.hd h1{{font-family:Montserrat,sans-serif;font-size:16px;margin:0;font-weight:700}}
.hd small{{font-size:11px;color:rgba(255,255,255,.5)}}
.bd{{padding:24px 28px}}
.badge{{display:inline-block;padding:5px 14px;border-radius:20px;font-size:12px;font-weight:700;color:#fff;background:{sc}}}
.row{{display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid #F1F5F9;font-size:13px}}
.row:last-child{{border:none}}.k{{color:#64748B}}.v{{color:#1B2E4A;font-weight:500;text-align:right}}
.total{{background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:14px 18px;margin:16px 0;display:flex;justify-content:space-between;align-items:center}}
.total .k{{font-size:13px;font-weight:700;color:#1B2E4A}}.total .v{{font-family:Montserrat,sans-serif;font-size:22px;font-weight:700;color:#1B2E4A}}
.ft{{background:#F8FAFC;padding:16px 28px;border-top:1px solid #E2E8F0;text-align:center}}
.ft p{{font-size:10px;color:#94A3B8;margin:0}}
.ipaid{{display:block;width:100%;padding:14px;background:#059669;color:#fff;border:none;border-radius:10px;font-size:14px;font-weight:700;font-family:Montserrat,sans-serif;cursor:pointer;margin-top:16px;text-align:center;text-decoration:none}}
.ipaid:hover{{background:#047857}}</style></head>
<body><div class="c">
<div class="hd"><h1>{company}</h1><small>Booking {booking['booking_ref']}</small></div>
<div class="bd">
<div style="text-align:center;margin-bottom:16px"><span class="badge">{booking['status']}</span></div>
<div class="row"><span class="k">Hotel</span><span class="v">{hotel}</span></div>
<div class="row"><span class="k">Room</span><span class="v">{booking['room_type']}</span></div>
<div class="row"><span class="k">Check-In</span><span class="v">{booking['checkin_date']}</span></div>
<div class="row"><span class="k">Check-Out</span><span class="v">{booking['checkout_date']}</span></div>
<div class="row"><span class="k">Guest</span><span class="v">{booking['guest_name']}</span></div>
<div class="total"><span class="k">Total</span><span class="v">${booking['total_amount']:,.2f}</span></div>
{'<a href="/i-paid?ref='+booking['booking_ref']+'&email='+email+'" class="ipaid">I\'ve Completed My Payment</a>' if show_ipaid else ''}
{f'<p style="font-size:12px;color:#64748B;margin-top:14px;text-align:center">Need help? Call <strong>{company_phone}</strong></p>' if company_phone else ''}
</div>
<div class="ft"><p>{company} · © 2026</p></div>
</div></body></html>""")


@app.get("/i-paid")
async def guest_ipaid_form(ref: str = Query(""), email: str = Query("")):
    """Guest notifies agent they've completed a non-PayPal payment."""
    import payment_config as pc
    company = pc.get_all().get("company_name", "Quota Solutions — Travel Desk")

    if not ref or not email:
        return RedirectResponse("/my-booking")

    booking = await get_booking(ref)
    if not booking or booking["guest_email"].lower() != email.strip().lower():
        return RedirectResponse("/my-booking")

    if booking["status"] == "Confirmed":
        return HTMLResponse(f"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Already Confirmed</title>
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@600&family=Open+Sans:wght@400&display=swap" rel="stylesheet">
<style>body{{font-family:Montserrat,Helvetica,sans-serif;background:#F1F5F9;margin:0;display:flex;align-items:center;justify-content:center;min-height:100vh}}
.c{{background:#fff;padding:40px;border-radius:16px;box-shadow:0 4px 24px rgba(0,0,0,.04);max-width:420px;text-align:center;border:1px solid #E2E8F0}}
h1{{font-family:Montserrat,sans-serif;font-size:18px;color:#059669;margin:0 0 8px}}</style></head>
<body><div class="c"><div style="font-size:48px;margin-bottom:12px">✅</div><h1>Booking Already Confirmed</h1>
<p style="font-size:13px;color:#64748B;">Your reservation <strong>{ref}</strong> is confirmed. No further action needed.</p></div></body></html>""")

    return HTMLResponse(f"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Payment Notification — {ref}</title>
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@600;700&family=Open+Sans:wght@400;500&display=swap" rel="stylesheet">
<style>body{{font-family:Montserrat,Helvetica,sans-serif;background:#F1F5F9;margin:0;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px}}
.c{{background:#fff;padding:36px;border-radius:16px;box-shadow:0 4px 24px rgba(0,0,0,.04);max-width:420px;width:100%;border:1px solid #E2E8F0}}
h1{{font-family:Montserrat,sans-serif;font-size:18px;color:#1B2E4A;margin:0 0 4px}}
p{{font-size:13px;color:#64748B;margin:0 0 18px}}
label{{font-size:12px;font-weight:600;color:#334155;display:block;margin:0 0 4px}}
input,select{{width:100%;padding:10px 12px;border:1px solid #E2E8F0;border-radius:8px;font-size:14px;margin:0 0 14px;box-sizing:border-box;font-family:Montserrat,Helvetica,sans-serif}}
button{{width:100%;padding:14px;background:#059669;color:#fff;border:none;border-radius:10px;font-size:14px;font-weight:700;font-family:Montserrat,sans-serif;cursor:pointer}}
button:hover{{background:#047857}}
.ok{{display:none;text-align:center;padding:24px 0}}
.ok h2{{font-family:Montserrat,sans-serif;font-size:18px;color:#059669;margin:8px 0 4px}}
.ok p{{font-size:13px;color:#64748B}}</style></head>
<body><div class="c">
<div id="form">
<h1>Notify Your Agent</h1>
<p>Let us know you've sent the payment for booking <strong>{ref}</strong> (${ booking['total_amount']:,.2f}).</p>
<label>Payment Method Used</label>
<select id="pMethod"><option>Zelle</option><option>Bank Transfer / Wire</option><option>Venmo</option><option>Wise</option><option>Other</option></select>
<label>Confirmation / Reference Number</label>
<input id="pRef" placeholder="e.g. Zelle conf #, wire reference">
<button id="btnSubmit" type="button">Submit Payment Notification</button>
</div>
<div class="ok" id="success"><div style="font-size:48px">✅</div><h2>Thank You!</h2><p>Your agent has been notified. You'll receive a confirmation email once your payment is verified.</p></div>
</div>
<script>
document.getElementById('btnSubmit').addEventListener('click',function(){{
  var btn=this;btn.disabled=true;btn.textContent='Submitting...';
  var method=document.getElementById('pMethod').value;
  var pref=document.getElementById('pRef').value;
  fetch('/api/bookings/{ref}/guest-payment-notify',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{email:'{email}',method:method,reference:pref}})
  }}).then(function(r){{return r.json()}}).then(function(d){{
    document.getElementById('form').style.display='none';
    document.getElementById('success').style.display='block';
  }}).catch(function(e){{btn.disabled=false;btn.textContent='Submit Payment Notification';alert('Error: '+e.message)}});
}});
</script>
</body></html>""")


@app.post("/api/bookings/{booking_ref}/guest-payment-notify")
async def guest_payment_notify(booking_ref: str, request: Request):
    """Guest notifies agent they've paid (non-PayPal). Logs event, doesn't change status."""
    _validate_ref(booking_ref)
    booking = await get_booking(booking_ref)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    body = await request.json()
    guest_email = body.get("email", "").strip().lower()
    if guest_email != booking["guest_email"].lower():
        raise HTTPException(status_code=403, detail="Email mismatch")

    method = body.get("method", "")
    reference = body.get("reference", "")

    await log_payment_event(booking_ref, "GUEST_PAYMENT_NOTIFICATION", json.dumps({
        "method": method, "reference": reference, "submitted_by": "guest",
    }))
    try:
        sheets_sync.sync_event(booking_ref, "GUEST_NOTIFIED_PAYMENT", f"method={method}, ref={reference}")
    except Exception:
        pass
    log.info(f"Guest payment notification: {booking_ref} via {method} (ref={reference})")
    return {"success": True, "message": "Agent has been notified."}


@app.get("/payment/success")
async def payment_success(ref: str = ""):
    return HTMLResponse(f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Payment Successful</title>
    <link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@600;700&family=Open+Sans:wght@400;500&display=swap" rel="stylesheet">
    <style>body{{font-family:Montserrat,Helvetica,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;background:#F1F5F9;margin:0}}
    .c{{background:#fff;padding:48px;border-radius:12px;box-shadow:0 4px 12px rgba(0,0,0,.06);text-align:center;max-width:460px;border:1px solid #E2E8F0}}
    h1{{font-family:Montserrat,sans-serif;margin:0 0 8px;font-size:22px;color:#1B2E4A}}p{{color:#475569;font-size:14px}}</style></head>
    <body><div class="c"><div style="font-size:48px;margin-bottom:12px">✅</div><h1>Payment Successful</h1><p>Your reservation is confirmed.</p>
    <p style="color:#64748B;font-size:13px;margin-top:12px">Booking: <strong style="color:#1B2E4A">{ref}</strong></p></div></body></html>""")

@app.get("/payment/cancel")
async def payment_cancel(ref: str = ""):
    return HTMLResponse(f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Payment Cancelled</title>
    <link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@600;700&family=Open+Sans:wght@400;500&display=swap" rel="stylesheet">
    <style>body{{font-family:Montserrat,Helvetica,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;background:#F1F5F9;margin:0}}
    .c{{background:#fff;padding:48px;border-radius:12px;box-shadow:0 4px 12px rgba(0,0,0,.06);text-align:center;max-width:460px;border:1px solid #E2E8F0}}
    h1{{font-family:Montserrat,sans-serif;margin:0 0 8px;font-size:22px;color:#1B2E4A}}p{{color:#475569;font-size:14px}}</style></head>
    <body><div class="c"><div style="font-size:48px;margin-bottom:12px">⚠️</div><h1>Payment Cancelled</h1><p>No charges made. Your agent will follow up.</p>
    <p style="color:#64748B;font-size:13px;margin-top:12px">Booking: <strong style="color:#1B2E4A">{ref}</strong></p></div></body></html>""")

@app.get("/")
async def serve_frontend():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))
