"""
Email Service v8 — CID inline images for Gmail/Outlook.
Images attached as MIME parts, referenced via cid: in HTML.
Always renders. No external URLs. No base64 in HTML.
"""
import os
import smtplib
import logging
import hashlib
import re as _re
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from jinja2 import Environment, FileSystemLoader
from datetime import datetime
import payment_config

log = logging.getLogger("email")

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "..", "templates")
EMAIL_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "emails")
ASSETS_DIR = os.path.join(os.path.dirname(__file__), "..", "assets")

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_FROM = os.getenv("SMTP_FROM", "")

env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=False)


# Curated hotel photos — Unsplash CDN (free, fast, Gmail-trusted)
HOTEL_PHOTOS = [
    "https://images.unsplash.com/photo-1566073771259-6a8506099945?w=600&h=340&fit=crop&q=80",
    "https://images.unsplash.com/photo-1551882547-ff40c63fe5fa?w=600&h=340&fit=crop&q=80",
    "https://images.unsplash.com/photo-1542314831-068cd1dbfeeb?w=600&h=340&fit=crop&q=80",
    "https://images.unsplash.com/photo-1564501049412-61c2a3083791?w=600&h=340&fit=crop&q=80",
    "https://images.unsplash.com/photo-1520250497591-112f2f40a3f4?w=600&h=340&fit=crop&q=80",
    "https://images.unsplash.com/photo-1582719478250-c89cae4dc85b?w=600&h=340&fit=crop&q=80",
]

BED_PHOTOS = {
    "king":   "https://images.unsplash.com/photo-1590490360182-c33d955e4c47?w=120&h=120&fit=crop&q=80",
    "double": "https://images.unsplash.com/photo-1631049307264-da0ec9d70304?w=120&h=120&fit=crop&q=80",
    "suite":  "https://images.unsplash.com/photo-1591088398332-8a7791972843?w=120&h=120&fit=crop&q=80",
    "twin":   "https://images.unsplash.com/photo-1595576508898-0ad5c879a061?w=120&h=120&fit=crop&q=80",
}


def _ensure_dir():
    os.makedirs(EMAIL_OUTPUT_DIR, exist_ok=True)


def _fmt_date(d):
    try:
        dt = datetime.strptime(d, "%Y-%m-%d")
        return dt.strftime("%a, %b %d, %Y").replace(" 0", " ")
    except Exception:
        return d


def _nights(ci, co):
    try:
        return max((datetime.strptime(co, "%Y-%m-%d") - datetime.strptime(ci, "%Y-%m-%d")).days, 1)
    except Exception:
        return 1


def _smtp_ok():
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASS)


def _get_hotel_url(booking_ref):
    """Pick hotel photo URL — rotates based on booking ref hash."""
    custom = os.getenv("HERO_IMAGE_URL", "")
    if custom:
        return custom
    idx = int(hashlib.md5(booking_ref.encode()).hexdigest(), 16) % len(HOTEL_PHOTOS)
    return HOTEL_PHOTOS[idx]


def _get_bed_url(room_type):
    """Pick bed photo URL — matched to room type."""
    custom = os.getenv("BED_ICON_URL", "")
    if custom:
        return custom
    rt = (room_type or "").lower()
    if _re.search(r'suite|presidential|penthouse|executive', rt):
        return BED_PHOTOS["suite"]
    elif _re.search(r'double|2 queen|two queen|2 full|two full', rt):
        return BED_PHOTOS["double"]
    elif _re.search(r'twin|2 single|two single', rt):
        return BED_PHOTOS["twin"]
    else:
        return BED_PHOTOS["king"]


def _send_smtp_with_images(to, subject, html):
    """Send HTML email via SMTP. Images are external URLs, no attachments needed."""
    if not _smtp_ok():
        return False
    try:
        msg = MIMEMultipart("related")
        sender = SMTP_FROM or SMTP_USER
        msg["From"] = f"Hotel Booking <{sender}>"
        msg["To"] = to
        msg["Subject"] = subject
        msg["Reply-To"] = sender
        msg["X-Mailer"] = "Quota Solutions Travel Desk"
        msg["MIME-Version"] = "1.0"
        msg["List-Unsubscribe"] = f"<mailto:{sender}?subject=Unsubscribe>"
        msg_alt = MIMEMultipart("alternative")
        msg_alt.attach(MIMEText(html, "html", "utf-8"))
        msg.attach(msg_alt)
        for img_path, cid in (images or []):
            if img_path and os.path.exists(img_path):
                with open(img_path, "rb") as f:
                    img_data = f.read()
                mime_img = MIMEImage(img_data, _subtype="jpeg")
                mime_img.add_header("Content-ID", f"<{cid}>")
                mime_img.add_header("Content-Disposition", "inline", filename=f"{cid}.jpg")
                msg.attach(mime_img)
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.ehlo()
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
        log.info(f"SMTP sent to {to} (with {len(images or [])} images)")
        return True
    except Exception as e:
        log.error(f"SMTP error: {e}")
        return False


def _base_vars(booking):
    pc = payment_config.get_all()
    n = booking.get("parsed_nights") or _nights(booking["checkin_date"], booking["checkout_date"])
    hero_url = _get_hotel_url(booking.get("booking_ref", ""))
    bed_url = _get_bed_url(booking.get("room_type", ""))
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
        "hero_image": hero_url,
        "bed_icon": bed_url,
        "year": datetime.now().year,
        "company_name": pc.get("company_name", "Quota Solutions"),
        "company_phone": pc.get("company_phone", ""),
        "company_email": pc.get("company_email", ""),
        "company_website": pc.get("company_website", ""),
        "support_hours": pc.get("support_hours", ""),
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


def send_booking_confirmation(booking, paypal_url, payment_method="paypal"):
    _ensure_dir()
    tmpl = env.get_template("booking_confirmation.html")
    v = _base_vars(booking)
    v["payment_method"] = payment_method
    v["paypal_url"] = paypal_url
    v["nightly_rates"] = booking.get("parsed_nightly_rates", [])
    v["subtotal"] = booking.get("parsed_subtotal", "")
    v["taxes"] = booking.get("parsed_taxes", "")
    real_total = booking["total_amount"]
    original_price = round(real_total / 0.75, 2)
    discount_amount = round(original_price - real_total, 2)
    v["original_price"] = f"{original_price:,.2f}"
    v["discount_percent"] = "25"
    v["discount_amount"] = f"{discount_amount:,.2f}"
    html = tmpl.render(**v)
    app_url = os.getenv("APP_BASE_URL", "http://localhost:8100")
    ref = booking["booking_ref"]
    pixel = f'<img src="{app_url}/api/track/open/{ref}" width="1" height="1" style="display:none" alt="" />'
    html = html.replace("</body>", f"{pixel}</body>")
    if paypal_url and paypal_url.startswith("http"):
        tracked = f"{app_url}/api/track/click/{ref}?url={paypal_url}"
        html = html.replace(f'href="{paypal_url}"', f'href="{tracked}"')
    fp = os.path.join(EMAIL_OUTPUT_DIR, f"confirmation_{ref}.html")
    with open(fp, "w") as f:
        f.write(html)
    _send_smtp_with_images(booking["guest_email"], f"Your Reservation — {ref}", html)
    log.info(f"Confirmation -> {booking['guest_email']} (method={payment_method}, images={len(images)})")
    return fp


def send_receipt_email(booking):
    _ensure_dir()
    tmpl = env.get_template("receipt.html")
    v = _base_vars(booking)
    v["paypal_capture_id"] = booking.get("paypal_capture_id", "N/A")
    html = tmpl.render(**v)
    fp = os.path.join(EMAIL_OUTPUT_DIR, f"receipt_{booking['booking_ref']}.html")
    with open(fp, "w") as f:
        f.write(html)
    _send_smtp_with_images(booking.get("guest_email", ""), f"Payment Confirmed — {booking['booking_ref']}", html)
    log.info(f"Receipt -> {booking.get('guest_email', '?')}")
    return fp


def send_alternative_payment_email(booking):
    _ensure_dir()
    tmpl = env.get_template("alternative_payment.html")
    v = _base_vars(booking)
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
    _send_smtp_with_images(booking.get("guest_email", ""), f"Complete Your Reservation — {booking['booking_ref']}", html)
    log.info(f"Alt-payment -> {booking.get('guest_email', '?')}")
    return fp
