"""
Google Sheets Sync
──────────────────
Pushes booking data to a Google Sheet in real-time.
Every create, status change, and payment event gets synced.

Setup:
  1. Create a Google Cloud project
  2. Enable Google Sheets API
  3. Create a Service Account, download JSON key
  4. Save the key as config/google-credentials.json
  5. Create a Google Sheet, share it with the service account email
  6. Set GOOGLE_SHEET_ID in .env

The sheet will have these tabs:
  - Bookings: one row per booking, updated on every status change
  - Events: append-only log of all payment events
"""
import os
import json
import logging
from datetime import datetime

log = logging.getLogger("sheets")

# Config
CREDENTIALS_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "google-credentials.json")
SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")

_client = None
_sheet = None
_enabled = False

BOOKING_HEADERS = [
    "Booking Ref", "Guest Name", "Guest Email", "Guest Phone",
    "Hotel", "Room Type", "Check-In", "Check-Out", "Nights",
    "Guests", "Total Amount", "Currency", "Payment Method",
    "Status", "PayPal Order ID", "PayPal Capture ID",
    "Agent Name", "Agent Phone", "Notes",
    "Created At", "Updated At"
]

EVENT_HEADERS = [
    "Timestamp", "Booking Ref", "Event Type", "Details"
]


def init_sheets():
    """Initialize Google Sheets connection. Fails silently if not configured."""
    global _client, _sheet, _enabled

    if not SHEET_ID:
        log.info("Google Sheets sync disabled — GOOGLE_SHEET_ID not set")
        return

    if not os.path.exists(CREDENTIALS_PATH):
        log.info("Google Sheets sync disabled — config/google-credentials.json not found")
        return

    try:
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=scopes)
        _client = gspread.authorize(creds)
        _sheet = _client.open_by_key(SHEET_ID)

        # Ensure Bookings tab exists with headers
        try:
            ws = _sheet.worksheet("Bookings")
        except gspread.WorksheetNotFound:
            ws = _sheet.add_worksheet(title="Bookings", rows=1000, cols=len(BOOKING_HEADERS))
            ws.append_row(BOOKING_HEADERS, value_input_option="RAW")
            log.info("Created 'Bookings' tab with headers")

        # Check if headers are present
        if ws.row_count == 0 or ws.row_values(1) != BOOKING_HEADERS:
            existing = ws.row_values(1) if ws.row_count > 0 else []
            if not existing:
                ws.append_row(BOOKING_HEADERS, value_input_option="RAW")

        # Ensure Events tab exists with headers
        try:
            ev = _sheet.worksheet("Events")
        except gspread.WorksheetNotFound:
            ev = _sheet.add_worksheet(title="Events", rows=5000, cols=len(EVENT_HEADERS))
            ev.append_row(EVENT_HEADERS, value_input_option="RAW")
            log.info("Created 'Events' tab with headers")

        if ev.row_count == 0 or ev.row_values(1) != EVENT_HEADERS:
            existing = ev.row_values(1) if ev.row_count > 0 else []
            if not existing:
                ev.append_row(EVENT_HEADERS, value_input_option="RAW")

        _enabled = True
        log.info(f"Google Sheets sync enabled — Sheet ID: {SHEET_ID[:12]}...")

    except ImportError:
        log.warning("Google Sheets sync disabled — install gspread: pip install gspread google-auth")
    except Exception as e:
        log.warning(f"Google Sheets sync failed to initialize: {e}")


def is_enabled() -> bool:
    return _enabled


def _booking_to_row(b: dict) -> list:
    """Convert a booking dict to a spreadsheet row."""
    return [
        b.get("booking_ref", ""),
        b.get("guest_name", ""),
        b.get("guest_email", ""),
        b.get("guest_phone", ""),
        b.get("parsed_hotel_name", "") or b.get("room_type", ""),
        b.get("room_type", ""),
        b.get("checkin_date", ""),
        b.get("checkout_date", ""),
        b.get("parsed_nights", "") or "",
        b.get("num_guests", 1),
        b.get("total_amount", 0),
        b.get("currency", "USD"),
        b.get("payment_method", ""),
        b.get("status", "Pending"),
        b.get("paypal_order_id", ""),
        b.get("paypal_capture_id", ""),
        b.get("agent_name", ""),
        b.get("agent_phone", ""),
        b.get("notes", ""),
        b.get("created_at", ""),
        datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
    ]


def sync_booking(booking: dict):
    """Create or update a booking row in the sheet."""
    if not _enabled:
        return
    try:
        ws = _sheet.worksheet("Bookings")
        ref = booking.get("booking_ref", "")

        # Find existing row by booking ref (column A)
        try:
            cell = ws.find(ref, in_column=1)
            row_num = cell.row
            # Update the existing row
            row_data = _booking_to_row(booking)
            ws.update(f"A{row_num}:{chr(64+len(row_data))}{row_num}", [row_data], value_input_option="RAW")
            log.info(f"Sheet sync: updated {ref} (row {row_num})")
        except Exception:
            # New booking — append row
            ws.append_row(_booking_to_row(booking), value_input_option="RAW")
            log.info(f"Sheet sync: added {ref}")

    except Exception as e:
        log.warning(f"Sheet sync failed for {booking.get('booking_ref', '?')}: {e}")


def sync_event(booking_ref: str, event_type: str, details: str = ""):
    """Append a payment event to the Events tab."""
    if not _enabled:
        return
    try:
        ev = _sheet.worksheet("Events")
        ev.append_row([
            datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            booking_ref,
            event_type,
            details[:500] if details else "",
        ], value_input_option="RAW")
        log.info(f"Sheet event: {booking_ref} → {event_type}")
    except Exception as e:
        log.warning(f"Sheet event sync failed: {e}")
