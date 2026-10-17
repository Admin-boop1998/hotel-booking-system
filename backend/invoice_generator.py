"""
Invoice Generator
─────────────────
Generates a professional PDF invoice for confirmed bookings.
Uses HTML template → rendered via weasyprint or falls back to text.
Stored alongside emails in data/invoices/.
"""
import os
import logging
from datetime import datetime
import payment_config
from hotel_photos import get_hotel_photo

log = logging.getLogger("invoice")

INVOICE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "invoices")


def _ensure_dir():
    os.makedirs(INVOICE_DIR, exist_ok=True)


def _format_date(d: str) -> str:
    try:
        return datetime.strptime(d, "%Y-%m-%d").strftime("%b %d, %Y")
    except Exception:
        return d


def generate_invoice_html(booking: dict) -> str:
    """Generate invoice as HTML file (can be printed to PDF by browser)."""
    _ensure_dir()
    pc = payment_config.get_all()
    company = pc.get("company_name", "Quota Solutions — Travel Desk")
    company_phone = pc.get("company_phone", "")
    company_email = pc.get("company_email", "")
    company_website = pc.get("company_website", "")

    ref = booking["booking_ref"]
    hotel = booking.get("parsed_hotel_name") or booking.get("room_type", "Hotel")
    address = booking.get("parsed_hotel_address", "")
    ci = _format_date(booking.get("checkin_date", ""))
    co = _format_date(booking.get("checkout_date", ""))

    # Parsed data
    nightly_rates = booking.get("parsed_nightly_rates", [])
    subtotal = booking.get("parsed_subtotal", "")
    taxes = booking.get("parsed_taxes", "")
    nights = booking.get("parsed_nights", 0)
    total = booking["total_amount"]

    # Discount
    original = round(total / 0.75, 2)
    discount = round(original - total, 2)

    # Build rate rows
    rate_rows = ""
    if nightly_rates:
        for i, r in enumerate(nightly_rates):
            rate_rows += f'<tr><td style="padding:8px 16px;border-bottom:1px solid #eee;color:#555;">Night {i+1} — {r.get("date","")}</td><td style="padding:8px 16px;border-bottom:1px solid #eee;text-align:right;">${r.get("amount","0.00")}</td></tr>'
    else:
        rate_rows = f'<tr><td style="padding:8px 16px;border-bottom:1px solid #eee;color:#555;">{booking.get("room_type","")} × {nights or 1} Night(s)</td><td style="padding:8px 16px;border-bottom:1px solid #eee;text-align:right;">${total:,.2f}</td></tr>'

    payment_method_display = {
        "paypal": "PayPal", "paypal_invoice": "PayPal Invoice",
        "bank_transfer": "Bank Transfer", "zelle": "Zelle",
        "venmo": "Venmo", "wise": "Wise",
    }.get(booking.get("payment_method", ""), booking.get("payment_method", ""))

    capture_id = booking.get("paypal_capture_id", "")
    txn_display = capture_id if capture_id and capture_id != "MANUAL" else "Manual Verification"

    invoice_date = datetime.utcnow().strftime("%B %d, %Y")
    hotel_photo = get_hotel_photo(hotel, ref)

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Invoice {ref}</title>
<style>
@page{{size:A4;margin:40px 50px}}
body{{font-family:Montserrat,'Helvetica Neue',Helvetica,sans-serif;color:#1a1a1a;margin:0;padding:40px 50px;font-size:13px;line-height:1.5}}
.header{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:32px;border-bottom:3px solid #1B2E4A;padding-bottom:20px}}
.brand{{font-size:20px;font-weight:700;color:#1B2E4A;letter-spacing:-.5px}}
.brand-sub{{font-size:11px;color:#64748B;margin-top:2px}}
.inv-label{{text-align:right}}
.inv-label .num{{font-size:24px;font-weight:700;color:#1B2E4A}}
.inv-label .date{{font-size:11px;color:#64748B;margin-top:4px}}
.parties{{display:flex;gap:40px;margin-bottom:28px}}
.party{{flex:1}}
.party-label{{font-size:10px;text-transform:uppercase;letter-spacing:1px;color:#94A3B8;font-weight:700;margin-bottom:6px}}
.party-name{{font-size:14px;font-weight:600;color:#1B2E4A}}
.party-detail{{font-size:12px;color:#64748B;line-height:1.6}}
.section-title{{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:#64748B;margin:24px 0 10px;border-bottom:1px solid #E2E8F0;padding-bottom:6px}}
table{{width:100%;border-collapse:collapse}}
th{{text-align:left;padding:10px 16px;background:#F8FAFC;font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:#64748B;font-weight:700;border-bottom:2px solid #E2E8F0}}
th:last-child{{text-align:right}}
td{{padding:8px 16px;font-size:13px;border-bottom:1px solid #F1F5F9}}
.total-row td{{font-size:16px;font-weight:700;color:#1B2E4A;border-top:2px solid #1B2E4A;border-bottom:none;padding-top:12px}}
.discount td{{color:#059669;font-weight:600}}
.strike{{text-decoration:line-through;color:#94A3B8}}
.policy{{margin-top:28px;padding:16px;background:#F8FAFC;border:1px solid #E2E8F0;border-radius:8px;font-size:11px;color:#64748B;line-height:1.7}}
.policy strong{{color:#334155}}
.footer{{margin-top:32px;border-top:1px solid #E2E8F0;padding-top:16px;font-size:10px;color:#94A3B8;text-align:center}}
@media print{{body{{padding:20px 30px}}}}
</style></head><body>

<!-- Hotel Photo -->
{'<div style="border-radius:10px;overflow:hidden;margin-bottom:24px;"><img src="' + hotel_photo + '" alt="' + hotel + '" style="width:100%;height:200px;object-fit:cover;display:block;" /></div>' if hotel_photo and hotel_photo.startswith('http') else ''}

<div class="header">
  <div><div class="brand">{company}</div>
  <div class="brand-sub">{company_website}{(' · ' + company_phone) if company_phone else ''}</div></div>
  <div class="inv-label"><div class="num">INVOICE</div><div class="date">{invoice_date}<br>Ref: {ref}</div></div>
</div>

<div class="parties">
  <div class="party">
    <div class="party-label">Bill To</div>
    <div class="party-name">{booking['guest_name']}</div>
    <div class="party-detail">{booking['guest_email']}<br>{booking.get('guest_phone','')}</div>
  </div>
  <div class="party">
    <div class="party-label">Property</div>
    <div class="party-name">{hotel}</div>
    <div class="party-detail">{address if address else ''}<br>Check-in: {ci}<br>Check-out: {co}<br>{booking.get('room_type','')} · {booking.get('num_guests',1)} Guest(s)</div>
  </div>
</div>

<div class="section-title">Booking Details</div>
<table>
  <thead><tr><th>Description</th><th>Amount</th></tr></thead>
  <tbody>
    <tr><td style="padding:8px 16px;color:#555;">Room: {booking.get('room_type','')} · {nights or 1} Night(s) · {booking.get('num_guests',1)} Guest(s)</td><td></td></tr>
    {rate_rows}
    <tr><td style="padding:8px 16px;color:#94A3B8;">Original Price</td><td style="padding:8px 16px;text-align:right;" class="strike">${original:,.2f}</td></tr>
    <tr class="discount"><td style="padding:8px 16px;">Member Discount (25%)</td><td style="padding:8px 16px;text-align:right;">−${discount:,.2f}</td></tr>
    {'<tr><td style="padding:8px 16px;color:#555;">Subtotal</td><td style="padding:8px 16px;text-align:right;">$'+subtotal+'</td></tr>' if subtotal else ''}
    {'<tr><td style="padding:8px 16px;color:#555;">Taxes & Fees</td><td style="padding:8px 16px;text-align:right;">$'+taxes+'</td></tr>' if taxes else ''}
    <tr class="total-row"><td>Total Paid</td><td style="text-align:right">${total:,.2f} USD</td></tr>
  </tbody>
</table>

<div class="section-title">Payment Information</div>
<table>
  <tbody>
    <tr><td style="padding:6px 16px;color:#64748B;width:40%">Payment Method</td><td style="padding:6px 16px;font-weight:500">{payment_method_display}</td></tr>
    <tr><td style="padding:6px 16px;color:#64748B;">Transaction ID</td><td style="padding:6px 16px;font-weight:500">{txn_display}</td></tr>
    <tr><td style="padding:6px 16px;color:#64748B;">Status</td><td style="padding:6px 16px;font-weight:700;color:{'#059669' if booking.get('status')=='Confirmed' else '#CA8A04'};">{'PAID — CONFIRMED' if booking.get('status')=='Confirmed' else 'PENDING PAYMENT'}</td></tr>
    <tr><td style="padding:6px 16px;color:#64748B;">Confirmation Date</td><td style="padding:6px 16px;">{invoice_date}</td></tr>
  </tbody>
</table>

<div class="policy">
  <strong>STRICTLY NON-REFUNDABLE:</strong><br>
  By completing this payment, you agreed that this discounted wholesale rate is not eligible for a cash refund. Voluntary cancellation forfeits the paid amount.<br><br>
  <strong>Terms:</strong> By completing this payment, you acknowledge that this discounted wholesale reservation is not eligible for a cash refund. You may voluntarily cancel prior to check-in, but no refunds will be issued. This invoice constitutes a binding agreement between {booking['guest_name']} and {company} (operated by Virtooall Core BPO, DTI Reg. No. 6481235) for the hotel reservation services described. {company} acts as an authorized booking agent and B2B travel fulfillment partner. {company} is not the hotel operator.
</div>

<div class="footer">
  {company}{(' · ' + company_website) if company_website else ''}{(' · ' + company_phone) if company_phone else ''}<br>
  This document serves as an official invoice and receipt. Ref: {ref} · Generated {invoice_date}
</div>

</body></html>"""

    filepath = os.path.join(INVOICE_DIR, f"invoice_{ref}.html")
    with open(filepath, "w") as f:
        f.write(html)
    log.info(f"Invoice generated: {filepath}")
    return filepath
