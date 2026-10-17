"""
Smart Paste Parser — Multi-OTA Edition
───────────────────────────────────────
Extracts structured booking data from raw text agents paste.
Supports: Expedia, Hotels.com, Booking.com, Priceline, and generic formats.
Returns a confidence score so the agent knows if auto-fill needs manual review.
"""
import re
from datetime import datetime


def parse_raw_booking(raw_text: str) -> dict:
    lines = [l.strip() for l in raw_text.strip().split('\n') if l.strip()]
    full = '\n'.join(lines)

    result = {
        "hotel_name": "", "hotel_address": "",
        "checkin_date": "", "checkout_date": "",
        "room_type": "", "nights": 0,
        "num_guests": 1, "adults": 0, "children": 0,
        "nightly_rates": [],
        "subtotal": "", "taxes": "", "total_amount": 0.0,
        "currency": "USD", "extras": [],
        "confidence": 0, "parsed_fields": [],
    }

    # ── HOTEL NAME ──
    for line in lines:
        if re.search(r'(arrival|departure|check.?in|check.?out|nightly|subtotal|tax|total|adults|children|\$\d|price for|your booking)', line, re.I):
            continue
        if re.match(r'^\d{2}/\d{2}/\d{4}', line) or re.match(r'^\d+ Room', line, re.I):
            continue
        if re.match(r'^(Hotel Stay|Booking Detail|Reservation|Confirmation|Property)', line, re.I):
            continue
        if len(line) > 3:
            result["hotel_name"] = line
            result["parsed_fields"].append("hotel_name")
            break

    # ── ADDRESS ──
    addr_parts = []
    found_name = False
    for line in lines:
        if line == result["hotel_name"]:
            found_name = True
            continue
        if found_name:
            if re.match(r'^\d+\s', line) or re.search(r',\s*[A-Z]{2}\s+\d{4,5}', line):
                addr_parts.append(line)
            elif re.search(r'(road|street|ave|blvd|drive|lane|way|suite|floor|highway|hwy)', line, re.I):
                addr_parts.append(line)
            elif re.match(r'^[A-Z]{2}\s*$', line):
                addr_parts.append(line)
            else:
                break
    if addr_parts:
        result["hotel_address"] = ', '.join(addr_parts)
        result["parsed_fields"].append("hotel_address")

    # ── DATES ──
    # Format 1: "Arrival: Thu, October 08, 2026 Departure: Sun, October 11, 2026"
    dm = re.search(r'Arrival[:\s]*(?:\w+,?\s*)?(\w+\s+\d{1,2},?\s+\d{4})\s*Departure[:\s]*(?:\w+,?\s*)?(\w+\s+\d{1,2},?\s+\d{4})', full, re.I)
    if dm:
        result["checkin_date"] = _parse_date(dm.group(1))
        result["checkout_date"] = _parse_date(dm.group(2))

    # Format 2: "Check-in: Oct 8, 2026" or "Check-in: Oct. 15, 2026"
    if not result["checkin_date"]:
        ci = re.search(r'Check.?in[:\s]*([A-Za-z]+\.?\s+\d{1,2},?\s+\d{4}|\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})', full, re.I)
        co = re.search(r'Check.?out[:\s]*([A-Za-z]+\.?\s+\d{1,2},?\s+\d{4}|\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})', full, re.I)
        if ci: result["checkin_date"] = _parse_date(ci.group(1))
        if co: result["checkout_date"] = _parse_date(co.group(1))

    # Format 3: Booking.com "Mon, Oct 7, 2026 — Thu, Oct 10, 2026" or "Mon 7 Oct 2026 – Thu 10 Oct 2026"
    if not result["checkin_date"]:
        bk = re.search(r'(\w{3},?\s+\w{3}\.?\s+\d{1,2},?\s+\d{4})\s*[—–\-]+\s*(\w{3},?\s+\w{3}\.?\s+\d{1,2},?\s+\d{4})', full)
        if bk:
            result["checkin_date"] = _parse_date(bk.group(1))
            result["checkout_date"] = _parse_date(bk.group(2))

    # Format 4: Booking.com "Mon 7 Oct 2026 – Thu 10 Oct 2026"
    if not result["checkin_date"]:
        bk2 = re.search(r'(\w{3}\s+\d{1,2}\s+\w{3,}\s+\d{4})\s*[—–\-]+\s*(\w{3}\s+\d{1,2}\s+\w{3,}\s+\d{4})', full)
        if bk2:
            result["checkin_date"] = _parse_date(bk2.group(1))
            result["checkout_date"] = _parse_date(bk2.group(2))

    if result["checkin_date"]:
        result["parsed_fields"].append("checkin_date")
    if result["checkout_date"]:
        result["parsed_fields"].append("checkout_date")

    # ── ROOM TYPE ──
    room_pats = [
        r'((?:Standard|Deluxe|Superior|Executive|Presidential|Suite|King|Queen|Double|Twin|Ocean View|Premium|Classic|Studio|Junior|Family|Connecting)\s*[^,\n]*(?:Bed|Room|Suite|Villa|Studio|Chamber)?)',
        r'((?:1|2|one|two|single)\s+(?:King|Queen|Double|Twin)\s+Bed[s]?)',
        r'Room\s+type[:\s]+([^\n]+)',
    ]
    for pat in room_pats:
        rm = re.search(pat, full, re.I)
        if rm:
            result["room_type"] = rm.group(1).strip()
            result["parsed_fields"].append("room_type")
            break

    # ── NIGHTS ──
    nm = re.search(r'(\d+)\s*Night', full, re.I)
    if not nm:
        nm = re.search(r'Price\s+for\s+(\d+)\s*night', full, re.I)
    if nm:
        result["nights"] = int(nm.group(1))
        result["parsed_fields"].append("nights")

    # ── GUESTS ──
    am = re.search(r'Adults?\s*[:\s]*(\d+)', full, re.I)
    cm = re.search(r'Children?\s*[:\s]*(\d+)', full, re.I)
    gm = re.search(r'(\d+)\s*(?:guest|traveler|person)', full, re.I)
    if am:
        result["adults"] = int(am.group(1))
        result["num_guests"] = result["adults"]
        result["parsed_fields"].append("guests")
    elif gm:
        result["adults"] = int(gm.group(1))
        result["num_guests"] = result["adults"]
        result["parsed_fields"].append("guests")
    if cm:
        result["children"] = int(cm.group(1))

    # ── NIGHTLY RATES ──
    # Format 1: "10/08/2026  $161.99"
    rate_matches = re.findall(r'(\d{1,2}/\d{1,2}/\d{4})\s+\$?([\d,]+\.?\d*)', full)
    for ds, amt in rate_matches:
        try:
            dt = datetime.strptime(ds, "%m/%d/%Y")
            result["nightly_rates"].append({"date": dt.strftime("%b %d, %Y"), "amount": amt.replace(',', '')})
        except Exception:
            pass

    # Format 2: "Night 1: $161.99" or "Oct 8: $161.99"
    if not result["nightly_rates"]:
        rate2 = re.findall(r'(?:Night\s*\d+|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2})[:\s]+\$?([\d,]+\.?\d+)', full, re.I)
        for i, amt in enumerate(rate2):
            result["nightly_rates"].append({"date": f"Night {i+1}", "amount": amt.replace(',', '')})

    if result["nightly_rates"]:
        result["parsed_fields"].append("nightly_rates")

    # ── SUBTOTAL ──
    sub = re.search(r'Subtotal[^$\n]*(?:[:\s]*\n?\s*)\$?([\d,]+\.?\d*)', full, re.I)
    if not sub:
        sub = re.search(r'Price\s+for\s+\d+\s*night[s]?\s*[:\s]*\$?([\d,]+\.?\d*)', full, re.I)
    if sub:
        result["subtotal"] = sub.group(1).replace(',', '')
        result["parsed_fields"].append("subtotal")

    # ── TAXES ──
    tax = re.search(r'Tax\s+Recovery[^\n]*?[:\s]*[\n\s]*\$?([\d,]+\.?\d*)', full, re.I)
    if not tax:
        tax = re.search(r'(?:Taxes?\s*(?:and|&)\s*(?:charges?|fees?))[:\s]*[\n\s]*\$?([\d,]+\.?\d*)', full, re.I)
    if not tax:
        tax = re.search(r'(?:includes?\s+)?taxes[:\s]*[\n\s]*\$?([\d,]+\.?\d*)', full, re.I)
        # Avoid matching "Subtotal Before Taxes"
        if tax and 'subtotal' in full[max(0, tax.start()-30):tax.start()].lower():
            tax = None
    if tax:
        result["taxes"] = tax.group(1).replace(',', '')
        result["parsed_fields"].append("taxes")

    # ── TOTAL ──
    total = re.search(r'Total\s+(?:for\s+\d+\s+Room|Amount|Due|Price|Cost|Charge)[:\s]*\n?\s*\$?([\d,]+\.?\d*)', full, re.I)
    if not total:
        total = re.search(r'(?:Grand\s+)?Total[:\s]*\n?\s*\$?([\d,]+\.?\d*)', full, re.I)
    if not total:
        total_all = re.findall(r'Total[^$\n]*[:\s]*\n?\s*\$?([\d,]+\.?\d+)', full, re.I)
        if total_all:
            result["total_amount"] = float(total_all[-1].replace(',', ''))
            result["parsed_fields"].append("total_amount")
    else:
        result["total_amount"] = float(total.group(1).replace(',', ''))
        result["parsed_fields"].append("total_amount")

    # ── Booking.com "Price" pattern ──
    if not result["total_amount"]:
        price = re.search(r'(?:Your\s+)?Price[:\s]*\$?([\d,]+\.?\d+)', full, re.I)
        if price:
            result["total_amount"] = float(price.group(1).replace(',', ''))
            result["parsed_fields"].append("total_amount")

    # ── CURRENCY ──
    curr = re.search(r'(USD|EUR|GBP|CAD|AUD|PHP|MXN|JPY)', full)
    if curr:
        result["currency"] = curr.group(1)

    # ── EXTRAS ──
    for extra in re.findall(r'\+\s*(.+)', full):
        cleaned = extra.strip()
        if cleaned and not re.search(r'\$', cleaned):
            result["extras"].append(cleaned)

    # ── NIGHTS FROM DATES ──
    if not result["nights"] and result["checkin_date"] and result["checkout_date"]:
        try:
            ci = datetime.strptime(result["checkin_date"], "%Y-%m-%d")
            co = datetime.strptime(result["checkout_date"], "%Y-%m-%d")
            result["nights"] = max((co - ci).days, 1)
            if "nights" not in result["parsed_fields"]:
                result["parsed_fields"].append("nights")
        except Exception:
            result["nights"] = len(result["nightly_rates"]) or 1

    # ── FALLBACK TOTAL ──
    if not result["total_amount"] and result["nightly_rates"]:
        result["total_amount"] = sum(float(r["amount"]) for r in result["nightly_rates"])
        result["parsed_fields"].append("total_amount")

    # ── CONFIDENCE SCORE ──
    critical = ["checkin_date", "checkout_date", "total_amount"]
    important = ["hotel_name", "room_type", "nights"]
    bonus = ["nightly_rates", "subtotal", "taxes", "guests", "hotel_address"]

    score = 0
    for f in critical:
        if f in result["parsed_fields"]:
            score += 25
    for f in important:
        if f in result["parsed_fields"]:
            score += 8
    for f in bonus:
        if f in result["parsed_fields"]:
            score += 2

    result["confidence"] = min(score, 100)

    return result


def _parse_date(date_str: str) -> str:
    date_str = date_str.strip().rstrip(',')
    # Remove day-of-week prefix like "Mon, " or "Thu, " (only 3-letter day names followed by comma or space+digit)
    date_str = re.sub(r'^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),?\s+', '', date_str, flags=re.I)
    # Remove period after abbreviated month (Oct. → Oct)
    date_str = re.sub(r'([A-Za-z]{3})\.', r'\1', date_str)
    formats = [
        "%B %d %Y", "%B %d, %Y",
        "%b %d %Y", "%b %d, %Y",
        "%b. %d %Y", "%b. %d, %Y",
        "%d %B %Y", "%d %b %Y",
        "%d %b. %Y",
        "%m/%d/%Y", "%m-%d-%Y",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return date_str
