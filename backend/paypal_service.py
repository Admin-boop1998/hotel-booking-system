"""
PayPal Orders API v2 — Dispute-Proof Integration
──────────────────────────────────────────────────
Anti-chargeback engineering:
  - Dynamic soft descriptor: QS*[HOTEL NAME] on bank statement
  - Guest name in item description for identity cross-reference
  - Contradiction-free note (no "free" near "cancellation")
  - Native discount field proves value exchange
  - 22-char descriptor limit enforced with alphanumeric sanitization
  - Idempotent, timeout-protected, token-cached
"""
import httpx
import os
import re
import time
import base64
import json
import hashlib
import logging
from datetime import datetime, timedelta

log = logging.getLogger("paypal")

API_TIMEOUT = httpx.Timeout(15.0, connect=10.0)

# ── Runtime Config ─────────────────────────────────────────────────────

def _client_id():
    return os.getenv("PAYPAL_CLIENT_ID", "")

def _client_secret():
    return os.getenv("PAYPAL_CLIENT_SECRET", "")

def _mode():
    return os.getenv("PAYPAL_MODE", "sandbox")

def _base_url():
    return "https://api-m.paypal.com" if _mode() == "live" else "https://api-m.sandbox.paypal.com"

def _app_url():
    return os.getenv("APP_BASE_URL", "http://localhost:8100")

def _brand_name():
    try:
        import payment_config
        return payment_config.get_all().get("company_name", "Quota Solutions — Travel Desk")
    except Exception:
        return "Quota Solutions — Travel Desk"


# ── Dynamic Soft Descriptor ───────────────────────────────────────────

def _build_soft_descriptor(hotel_name: str) -> str:
    """
    Build a bank statement descriptor from the hotel name.
    Rules:
      - Max 22 chars total
      - Prefix: "QS*" (3 chars) → 19 chars for hotel
      - Alphanumeric + spaces only (no hyphens, commas, apostrophes)
      - Uppercase
      - If hotel name is empty or too short, fall back to "QS*HOTEL BKNG"
    """
    PREFIX = "QS*"
    MAX_HOTEL = 22 - len(PREFIX)  # 19 chars

    if not hotel_name or len(hotel_name.strip()) < 3:
        return "QS*HOTEL BKNG"

    # Strip special characters, keep only alphanumeric + spaces
    clean = re.sub(r'[^A-Za-z0-9 ]', '', hotel_name)
    # Collapse multiple spaces
    clean = re.sub(r'\s+', ' ', clean).strip().upper()

    if len(clean) <= MAX_HOTEL:
        return f"{PREFIX}{clean}"

    # Truncate intelligently — try to keep meaningful words
    # Remove common filler words first
    short = clean
    for filler in ['HOTEL AND ', 'HOTEL & ', 'BY ', 'THE ', 'INN ', 'SUITES ', 'RESORT ']:
        if len(short) > MAX_HOTEL:
            short = short.replace(filler, '')

    if len(short) <= MAX_HOTEL:
        return f"{PREFIX}{short.strip()}"

    # Still too long — just truncate
    return f"{PREFIX}{clean[:MAX_HOTEL].strip()}"


# ── Token Cache ────────────────────────────────────────────────────────

_token_cache = {"token": None, "expires_at": 0}

async def _get_access_token() -> str:
    cid = _client_id()
    secret = _client_secret()
    if not cid or not secret or cid == "YOUR_PAYPAL_CLIENT_ID":
        raise RuntimeError("PayPal credentials not configured")

    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

    credentials = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
        resp = await client.post(
            f"{_base_url()}/v1/oauth2/token",
            headers={"Authorization": f"Basic {credentials}", "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials"},
        )
        resp.raise_for_status()
        data = resp.json()
        token = data["access_token"]
        expires_in = data.get("expires_in", 3600)
        _token_cache["token"] = token
        _token_cache["expires_at"] = now + expires_in - 120
        return token


# ── Create Order ───────────────────────────────────────────────────────

async def create_order(booking: dict, attempt: int = 1) -> dict:
    """
    Create a dispute-proof PayPal checkout order.

    What the customer sees:
    - Brand: "Quota Solutions — Travel Desk"
    - Item: "Hotel Reservation: [Hotel Name]"
    - Description: "Guest: [Name] | [Dates] | Ref: [ID]"
    - Taxes as separate line
    - Discount via native PayPal field
    - Note: strictly non-refundable (no contradictions)
    - Bank statement: "QS*[HOTEL NAME]" (dynamic, 22-char max)
    """
    token = await _get_access_token()
    ref = booking["booking_ref"]
    total = float(booking["total_amount"])
    currency = booking.get("currency", "USD")

    # Hotel + guest info
    hotel_name = booking.get("parsed_hotel_name") or booking.get("room_type", "Hotel Reservation")
    room_type = booking.get("room_type", "Standard Room")
    guest_name = booking.get("guest_name", "Guest")

    # Dates
    checkin = datetime.strptime(booking["checkin_date"], "%Y-%m-%d")
    checkout = datetime.strptime(booking["checkout_date"], "%Y-%m-%d")
    nights = max((checkout - checkin).days, 1)
    checkin_display = checkin.strftime("%b %d, %Y")
    checkout_display = checkout.strftime("%b %d, %Y")

    # Discount math
    original_price = round(total / 0.75, 2)
    discount_amount = round(original_price - total, 2)

    # Taxes
    taxes_str = booking.get("parsed_taxes", "")
    taxes = float(taxes_str) if taxes_str else 0

    # ── Line Items ──
    items = []
    item_total = 0.0

    # Item 1: Hotel reservation with GUEST NAME in description
    room_subtotal = round(original_price - taxes, 2) if taxes else original_price
    items.append({
        "name": f"Hotel Reservation: {hotel_name}"[:127],
        "description": (
            f"Guest: {guest_name} | "
            f"{checkin_display} - {checkout_display} ({nights} Night{'s' if nights > 1 else ''}) | "
            f"Ref: {ref}"
        )[:127],
        "unit_amount": {"currency_code": currency, "value": f"{room_subtotal:.2f}"},
        "quantity": "1",
        "category": "PHYSICAL_GOODS",
    })
    item_total += room_subtotal

    # Item 2: Taxes
    if taxes > 0:
        items.append({
            "name": "Taxes & Recovery Fees",
            "description": "Government taxes and service fees",
            "unit_amount": {"currency_code": currency, "value": f"{taxes:.2f}"},
            "quantity": "1",
            "category": "PHYSICAL_GOODS",
        })
        item_total += taxes

    # Amount with native discount field
    order_amount = {
        "currency_code": currency,
        "value": f"{total:.2f}",
        "breakdown": {
            "item_total": {"currency_code": currency, "value": f"{item_total:.2f}"},
            "discount": {"currency_code": currency, "value": f"{discount_amount:.2f}"},
        },
    }

    # Invoice ID (unique per attempt)
    invoice_suffix = f"-{attempt}" if attempt > 1 else ""
    invoice_id = f"{ref}{invoice_suffix}"

    # Idempotency key
    request_id = hashlib.sha256(f"{ref}-{attempt}-{int(time.time()//60)}".encode()).hexdigest()[:32]

    # Dynamic soft descriptor
    soft_desc = _build_soft_descriptor(hotel_name)

    # Note — ZERO contradictions, absolute language
    note_to_payer = (
        f"STRICTLY NON-REFUNDABLE: By completing this payment, you agree this "
        f"discounted wholesale rate is not eligible for a cash refund. "
        f"Voluntary cancellation forfeits the paid amount."
    )

    # ── Build Order ──
    order_payload = {
        "intent": "CAPTURE",
        "purchase_units": [{
            "reference_id": ref,
            "description": f"Hotel Reservation: {hotel_name} | Guest: {guest_name}"[:127],
            "custom_id": ref,
            "invoice_id": invoice_id,
            "soft_descriptor": soft_desc,
            "amount": order_amount,
            "items": items,
        }],
        "payment_source": {
            "paypal": {
                "experience_context": {
                    "payment_method_preference": "IMMEDIATE_PAYMENT_REQUIRED",
                    "brand_name": _brand_name(),
                    "locale": "en-US",
                    "landing_page": "NO_PREFERENCE",
                    "shipping_preference": "NO_SHIPPING",
                    "user_action": "PAY_NOW",
                    "return_url": f"{_app_url()}/payment/success?ref={ref}",
                    "cancel_url": f"{_app_url()}/payment/cancel?ref={ref}",
                }
            }
        },
    }

    async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
        resp = await client.post(
            f"{_base_url()}/v2/checkout/orders",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "PayPal-Request-Id": request_id,
                "Prefer": "return=representation",
            },
            json=order_payload,
        )
        if resp.status_code == 422:
            error_data = resp.json()
            details = error_data.get("details", [])
            for d in details:
                if d.get("issue") == "DUPLICATE_INVOICE_ID":
                    if attempt < 5:
                        log.warning(f"Duplicate invoice_id {invoice_id}, retry attempt {attempt+1}")
                        return await create_order(booking, attempt + 1)
            log.error(f"PayPal 422: {error_data}")
            resp.raise_for_status()
        resp.raise_for_status()
        order = resp.json()

    approval_url = next(
        (link["href"] for link in order.get("links", []) if link["rel"] == "payer-action"),
        None,
    )
    if not approval_url:
        domain = "www.sandbox.paypal.com" if _mode() == "sandbox" else "www.paypal.com"
        approval_url = f"https://{domain}/checkoutnow?token={order['id']}"

    log.info(f"PayPal order {order['id']} for {ref}: ${total:.2f} | descriptor={soft_desc} | guest={guest_name}")
    return {
        "order_id": order["id"],
        "approval_url": approval_url,
        "status": order.get("status"),
    }


# ── Capture ────────────────────────────────────────────────────────────

async def capture_order(order_id: str) -> dict:
    token = await _get_access_token()
    async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
        resp = await client.post(
            f"{_base_url()}/v2/checkout/orders/{order_id}/capture",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
        )
        resp.raise_for_status()
        return resp.json()


# ── Void ───────────────────────────────────────────────────────────────

async def void_order(order_id: str) -> bool:
    try:
        token = await _get_access_token()
        async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
            resp = await client.post(
                f"{_base_url()}/v2/checkout/orders/{order_id}/void",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
            return resp.status_code in (200, 204)
    except Exception as e:
        log.warning(f"Void failed for {order_id}: {e}")
        return False


# ── Webhook Signature Verification ─────────────────────────────────────

async def verify_webhook_signature(headers: dict, body: bytes) -> bool:
    webhook_id = os.getenv("PAYPAL_WEBHOOK_ID", "")
    if not webhook_id or webhook_id == "YOUR_WEBHOOK_ID":
        log.warning("No webhook ID — skipping verification (dev mode)")
        return True

    token = await _get_access_token()
    verification_payload = {
        "auth_algo": headers.get("paypal-auth-algo", ""),
        "cert_url": headers.get("paypal-cert-url", ""),
        "transmission_id": headers.get("paypal-transmission-id", ""),
        "transmission_sig": headers.get("paypal-transmission-sig", ""),
        "transmission_time": headers.get("paypal-transmission-time", ""),
        "webhook_id": webhook_id,
        "webhook_event": json.loads(body),
    }

    async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
        resp = await client.post(
            f"{_base_url()}/v1/notifications/verify-webhook-signature",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=verification_payload,
        )
        if resp.status_code == 200:
            result = resp.json().get("verification_status") == "SUCCESS"
            if not result:
                log.warning("Webhook signature INVALID")
            return result
        log.error(f"Webhook verify returned {resp.status_code}")
        return False



# ══════════════════════════════════════════════════════════════════════
#  PAYPAL INVOICING API v2
# ══════════════════════════════════════════════════════════════════════

async def create_invoice(booking: dict) -> dict:
    """Create and send a PayPal Invoice to the guest."""
    cid = _client_id()
    if not cid or cid == "YOUR_PAYPAL_CLIENT_ID":
        raise RuntimeError("PayPal credentials not configured")

    token = await _get_access_token()
    ref = booking.get("booking_ref", "")
    guest_name = booking.get("guest_name", "Guest")
    guest_email = booking.get("guest_email", "")
    total = float(booking.get("total_amount", 0))
    currency = booking.get("currency", "USD")
    hotel_name = booking.get("parsed_hotel_name") or booking.get("room_type", "Hotel Reservation")
    room_type = booking.get("room_type", "Standard Room")
    checkin = booking.get("checkin_date", "")
    checkout = booking.get("checkout_date", "")
    nights = booking.get("parsed_nights") or 1

    original_price = round(total / 0.75, 2)
    discount_amount = round(original_price - total, 2)
    nightly_rate = round(original_price / max(nights, 1), 2)

    name_parts = guest_name.strip().split(" ", 1)
    first_name = name_parts[0]
    last_name = name_parts[1] if len(name_parts) > 1 else ""

    invoice_payload = {
        "detail": {
            "invoice_number": f"{ref}-INV",
            "reference": ref,
            "invoice_date": datetime.utcnow().strftime("%Y-%m-%d"),
            "currency_code": currency,
            "note": (
                f"Reservation at {hotel_name}. "
                f"{checkin} to {checkout}, {nights} night(s). "
                f"STRICTLY NON-REFUNDABLE: By completing this payment, you agree "
                f"this discounted wholesale rate is not eligible for a cash refund. "
                f"Voluntary cancellation forfeits the paid amount."
            ),
            "terms_and_conditions": (
                "This discounted wholesale reservation is strictly non-refundable. "
                "Voluntary cancellation forfeits the paid amount."
            ),
            "payment_term": {
                "term_type": "DUE_ON_RECEIPT"
            },
        },
        "invoicer": {
            "business_name": _brand_name(),
            "website": "https://quotasolutions.net",
        },
        "primary_recipients": [{
            "billing_info": {
                "name": {
                    "given_name": first_name,
                    "surname": last_name,
                },
                "email_address": guest_email,
            }
        }],
        "items": [
            {
                "name": f"Hotel Reservation: {hotel_name}",
                "description": f"{room_type} | {checkin} to {checkout} | {nights} Night(s) | Ref: {ref}",
                "quantity": str(nights),
                "unit_amount": {
                    "currency_code": currency,
                    "value": str(nightly_rate),
                },
                "unit_of_measure": "QUANTITY",
            },
            {
                "name": "Member Discount (25%)",
                "description": "Wholesale rate discount applied",
                "quantity": "1",
                "unit_amount": {
                    "currency_code": currency,
                    "value": f"-{discount_amount}",
                },
                "unit_of_measure": "QUANTITY",
            },
        ],
        "configuration": {
            "allow_tip": False,
            "tax_calculated_after_discount": False,
        },
    }

    async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
        # Step 1: Create draft invoice
        resp = await client.post(
            f"{_base_url()}/v2/invoicing/invoices",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
            json=invoice_payload,
        )
        resp.raise_for_status()
        invoice = resp.json()
        invoice_id = invoice["id"]

        # Step 2: Send the invoice (PayPal emails it to the guest)
        send_resp = await client.post(
            f"{_base_url()}/v2/invoicing/invoices/{invoice_id}/send",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "send_to_invoicer": True,
                "send_to_recipient": True,
                "additional_recipients": [],
            },
        )
        send_resp.raise_for_status()

    invoice_url = next(
        (link["href"] for link in invoice.get("links", []) if link.get("rel") == "payer-view"),
        "",
    )

    log.info(f"PayPal Invoice {invoice_id} created and sent for {ref} to {guest_email}: ${total:.2f}")
    return {
        "invoice_id": invoice_id,
        "invoice_url": invoice_url,
        "status": "SENT",
    }
[4906 chars total]