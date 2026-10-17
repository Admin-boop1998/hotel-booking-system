"""
Email Service — v7
Passes payment_method + config to templates for correct CTA routing.
Includes agent contact, check-in/out times, and payment account details.
"""
import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from jinja2 import Environment, FileSystemLoader
from datetime import datetime
import payment_config

log = logging.getLogger("email")

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "..", "templates")
EMAIL_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "emails")

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_FROM = os.getenv("SMTP_FROM", "")
HERO_IMAGE_URL = os.getenv("HERO_IMAGE_URL", "")

env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=False)

import hashlib as _hl
import re as _re

_img_data = None
def _load_images():
    global _img_data
    if _img_data: return _img_data
    try:
        import sys
        cfg = os.path.join(os.path.dirname(__file__), "..", "config")
        if cfg not in sys.path: sys.path.insert(0, cfg)
        from image_data import HOTEL_IMAGES, BED_IMAGES
        _img_data = {"hotels": HOTEL_IMAGES, "beds": BED_IMAGES}
    except Exception:
        _img_data = {"hotels": [], "beds": {}}
    return _img_data

def _get_hotel_image(booking_ref: str) -> str:
    """Pick a hotel exterior photo — rotates based on booking ref hash."""
    url = os.getenv("HERO_IMAGE_URL", "")
    if url: return url
    data = _load_images()
    hotels = data["hotels"]
    if not hotels: return ""
    idx = int(_hl.md5(booking_ref.encode()).hexdigest(), 16) % len(hotels)
    return hotels[idx]

def _get_bed_image(room_type: str) -> str:
    """Pick bed photo matching the room type."""
    url = os.getenv("BED_ICON_URL", "")
    if url: return url
    data = _load_images()
    beds = data["beds"]
    if not beds: return ""
    rt = (room_type or "").lower()
    if _re.search(r'suite|presidential|penthouse|executive', rt):
        return beds.get("bed_suite", "")
    elif _re.search(r'double|2 queen|two queen|2 full|two full', rt):
        return beds.get("bed_double", "")
    elif _re.search(r'twin|2 single|two single', rt):
        return beds.get("bed_twin", "")
    else:
        return beds.get("bed_king", "")


def _ensure_dir():
    os.makedirs(EMAIL_OUTPUT_DIR, exist_ok=True)


def _fmt_date(d: str) -> str:
    try:
        dt = datetime.strptime(d, "%Y-%m-%d")
        return dt.strftime("%a, %b %-d, %Y")
    except Exception:
        try:
            dt = datetime.strptime(d, "%Y-%m-%d")
            return dt.strftime("%a, %b %d, %Y").replace(" 0", " ")
        except Exception:
            return d


def _nights(ci: str, co: str) -> int:
    try:
        return max((datetime.strptime(co, "%Y-%m-%d") - datetime.strptime(ci, "%Y-%m-%d")).days, 1)
    except Exception:
        return 1


def _smtp_ok() -> bool:
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASS)


def _send_smtp(to: str, subject: str, html: str) -> bool:
    if not _smtp_ok():
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = SMTP_FROM or SMTP_USER
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(html, "html", "utf-8"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.ehlo(); s.starttls(); s.login(SMTP_USER, SMTP_PASS); s.send_message(msg)
        log.info(f"SMTP sent to {to}")
        return True
    except Exception as e:
        log.error(f"SMTP error: {e}")
        return False


def _base_vars(booking: dict) -> dict:
    """Common template variables shared across all email types."""
    pc = payment_config.get_all()
    n = booking.get("parsed_nights") or _nights(booking["checkin_date"], booking["checkout_date"])
    return {
        "guest_name": booking["guest_name"],
        "booking_ref": booking["booking_ref"],
        "hotel_name": booking.get("parsed_hotel_name") or booking.get("room_type", "Your Hotel"),
        "hotel_address": booking.get("parsed_hotel_address", ""),
        "checkin_date": _fmt_date(booking["checkin_date"]),
        "checkout_date": _fmt_date(booking["checkout_date"]),
        "room_type": booking["room_type"],
        "num_guests": booking.get("num_guests", 1),
        "total_amount": f"{booking['total_amount']:,.2f}",
        "currency": booking.get("currency", "USD"),
        "nights": n,
        "agent_name": booking.get("agent_name") or "Reservations Team",
        "agent_phone": booking.get("agent_phone") or pc.get("agent_default_phone", ""),
        "agent_email": booking.get("agent_email") or pc.get("agent_default_email", ""),
        "checkin_time": pc.get("checkin_time", "3:00 PM"),
        "checkout_time": pc.get("checkout_time", "11:00 AM"),
        "hero_image": _get_hotel_image(booking.get("booking_ref", "")),
        "bed_icon": _get_bed_image(booking.get("room_type", "")),
        "year": datetime.now().year,
        # Business identity
        "company_name": pc.get("company_name", "Quota Solutions — Travel Desk"),
        "company_phone": pc.get("company_phone", ""),
        "company_email": pc.get("company_email", ""),
        "company_website": pc.get("company_website", ""),
        "support_hours": pc.get("support_hours", ""),
        # Payment account details
        "beneficiary_name": pc["beneficiary_name"],
        "wire_account": pc["wire_account"],
        "wire_routing": pc["wire_routing"],
        "wire_swift": pc["wire_swift"],
        "bank_name": pc.get("bank_name", ""),
        "bank_address": pc.get("bank_address", ""),
        "zelle_contact": pc["zelle_contact"],
        "venmo_handle": pc["venmo_handle"],
        "wise_email": pc["wise_email"],
    }


def send_booking_confirmation(booking: dict, paypal_url: str, payment_method: str = "paypal") -> str:
    _ensure_dir()
    tmpl = env.get_template("booking_confirmation.html")
    v = _base_vars(booking)
    v["payment_method"] = payment_method
    v["paypal_url"] = paypal_url
    v["nightly_rates"] = booking.get("parsed_nightly_rates", [])
    v["subtotal"] = booking.get("parsed_subtotal", "")
    v["taxes"] = booking.get("parsed_taxes", "")

    # Fake discount calculation — 25% "savings"
    # Original price = total / 0.75 (so total IS the discounted price)
    real_total = booking["total_amount"]
    original_price = round(real_total / 0.75, 2)
    discount_amount = round(original_price - real_total, 2)
    v["original_price"] = f"{original_price:,.2f}"
    v["discount_percent"] = "25"
    v["discount_amount"] = f"{discount_amount:,.2f}"

    html = tmpl.render(**v)

    # Inject tracking pixel + wrap CTA with click tracker
    app_url = os.getenv("APP_BASE_URL", "http://localhost:8100")
    ref = booking['booking_ref']
    tracking_pixel = f'<img src="{app_url}/api/track/open/{ref}" width="1" height="1" style="display:none" alt="" />'
    html = html.replace("</body>", f"{tracking_pixel}</body>")

    # Wrap PayPal CTA with click tracker
    if paypal_url and paypal_url.startswith("http"):
        tracked_url = f"{app_url}/api/track/click/{ref}?url={paypal_url}"
        html = html.replace(f'href="{paypal_url}"', f'href="{tracked_url}"')

    fp = os.path.join(EMAIL_OUTPUT_DIR, f"confirmation_{ref}.html")
    with open(fp, "w") as f:
        f.write(html)
    _send_smtp(booking["guest_email"], f"Your Reservation — {ref}", html)
    log.info(f"Confirmation → {booking['guest_email']} (method={payment_method})")
    return fp


def send_receipt_email(booking: dict) -> str:
    _ensure_dir()
    tmpl = env.get_template("receipt.html")
    v = _base_vars(booking)
    v["paypal_capture_id"] = booking.get("paypal_capture_id", "N/A")

    html = tmpl.render(**v)
    fp = os.path.join(EMAIL_OUTPUT_DIR, f"receipt_{booking['booking_ref']}.html")
    with open(fp, "w") as f:
        f.write(html)
    _send_smtp(booking.get("guest_email", ""), f"Payment Confirmed — {booking['booking_ref']}", html)
    log.info(f"Receipt → {booking.get('guest_email', '?')}")
    return fp


def send_alternative_payment_email(booking: dict) -> str:
    _ensure_dir()
    tmpl = env.get_template("alternative_payment.html")
    v = _base_vars(booking)

    # Build PayPal retry URL from stored order ID
    paypal_order_id = booking.get("paypal_order_id", "")
    if paypal_order_id:
        mode = os.getenv("PAYPAL_MODE", "sandbox")
        domain = "www.sandbox.paypal.com" if mode == "sandbox" else "www.paypal.com"
        v["paypal_retry_url"] = f"https://{domain}/checkoutnow?token={paypal_order_id}"
    else:
        v["paypal_retry_url"] = ""

    html = tmpl.render(**v)
    fp = os.path.join(EMAIL_OUTPUT_DIR, f"alt_payment_{booking['booking_ref']}.html")
    with open(fp, "w") as f:
        f.write(html)
    _send_smtp(booking.get("guest_email", ""), f"Complete Your Reservation — {booking['booking_ref']}", html)
    log.info(f"Alt-payment → {booking.get('guest_email', '?')}")
    return fp
