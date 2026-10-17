"""
Database Layer — v9 Enterprise
──────────────────────────────
- Pagination with total count
- Date range filters (today/week/month/custom)
- Full-text search (ref, name, email, hotel)
- CSV export query
- Weekly + monthly revenue stats
- Auto-backup on startup
"""
import aiosqlite
import os
import json
import logging
import shutil
from datetime import datetime

log = logging.getLogger("db")

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "bookings.db")
BACKUP_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "backups")

_db: aiosqlite.Connection | None = None

VALID_TRANSITIONS = {
    "Pending":   {"Confirmed", "Declined", "Cancelled"},
    "Confirmed": {"Refunded"},
    "Declined":  {"Pending", "Confirmed", "Cancelled"},
    "Cancelled": set(),
    "Refunded":  set(),
}


async def init_db():
    global _db
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    os.makedirs(BACKUP_DIR, exist_ok=True)

    # Auto-backup existing DB before startup
    if os.path.exists(DB_PATH):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(BACKUP_DIR, f"bookings_{ts}.db")
        try:
            shutil.copy2(DB_PATH, backup_path)
            # Keep only last 7 backups
            backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.endswith('.db')])
            for old in backups[:-7]:
                os.remove(os.path.join(BACKUP_DIR, old))
            log.info(f"DB backed up → {backup_path}")
        except Exception as e:
            log.warning(f"Backup failed: {e}")

    _db = await aiosqlite.connect(DB_PATH)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA busy_timeout=5000")

    await _db.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            booking_ref TEXT UNIQUE NOT NULL,
            guest_name TEXT NOT NULL,
            guest_email TEXT NOT NULL,
            guest_phone TEXT NOT NULL,
            checkin_date TEXT NOT NULL,
            checkout_date TEXT NOT NULL,
            room_type TEXT NOT NULL,
            room_details TEXT DEFAULT '',
            tour_package TEXT DEFAULT '',
            num_guests INTEGER DEFAULT 1,
            total_amount REAL NOT NULL,
            currency TEXT DEFAULT 'USD',
            status TEXT DEFAULT 'Pending',
            payment_method TEXT DEFAULT 'paypal',
            paypal_order_id TEXT DEFAULT '',
            paypal_capture_id TEXT DEFAULT '',
            agent_name TEXT DEFAULT '',
            agent_phone TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            raw_paste TEXT DEFAULT '',
            parsed_data TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    await _db.execute("""
        CREATE TABLE IF NOT EXISTS payment_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            booking_ref TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (booking_ref) REFERENCES bookings(booking_ref) ON DELETE CASCADE
        )
    """)
    for idx in [
        "CREATE INDEX IF NOT EXISTS idx_bookings_ref ON bookings(booking_ref)",
        "CREATE INDEX IF NOT EXISTS idx_bookings_paypal ON bookings(paypal_order_id)",
        "CREATE INDEX IF NOT EXISTS idx_bookings_status ON bookings(status)",
        "CREATE INDEX IF NOT EXISTS idx_bookings_email ON bookings(guest_email)",
        "CREATE INDEX IF NOT EXISTS idx_bookings_created ON bookings(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_events_ref ON payment_events(booking_ref)",
    ]:
        await _db.execute(idx)
    await _db.commit()
    log.info("Database initialized (WAL mode, indexed, auto-backup)")


async def close_db():
    global _db
    if _db:
        await _db.close()
        _db = None


def _conn():
    if not _db:
        raise RuntimeError("Database not initialized")
    return _db


def _hydrate(row: dict) -> dict:
    pd_raw = row.get("parsed_data", "{}")
    try:
        pd = json.loads(pd_raw) if pd_raw else {}
        row["parsed_hotel_name"] = pd.get("hotel_name", "")
        row["parsed_hotel_address"] = pd.get("hotel_address", "")
        row["parsed_nightly_rates"] = pd.get("nightly_rates", [])
        row["parsed_subtotal"] = pd.get("subtotal", "")
        row["parsed_taxes"] = pd.get("taxes", "")
        row["parsed_nights"] = pd.get("nights", 0)
        row["parsed_extras"] = pd.get("extras", [])
    except (json.JSONDecodeError, TypeError):
        for k in ["parsed_hotel_name","parsed_hotel_address","parsed_subtotal","parsed_taxes"]:
            row[k] = ""
        row["parsed_nightly_rates"] = []
        row["parsed_nights"] = 0
        row["parsed_extras"] = []
    return row


def _serialize_parsed(data: dict) -> str:
    return json.dumps({
        "hotel_name": data.get("parsed_hotel_name", ""),
        "hotel_address": data.get("parsed_hotel_address", ""),
        "nightly_rates": data.get("parsed_nightly_rates", []),
        "subtotal": data.get("parsed_subtotal", ""),
        "taxes": data.get("parsed_taxes", ""),
        "nights": data.get("parsed_nights", 0),
        "extras": data.get("parsed_extras", []),
    }, ensure_ascii=False)


async def insert_booking(data: dict) -> dict:
    db = _conn()
    await db.execute("""
        INSERT INTO bookings (
            booking_ref, guest_name, guest_email, guest_phone,
            checkin_date, checkout_date, room_type, room_details,
            tour_package, num_guests, total_amount, currency,
            status, payment_method, paypal_order_id, agent_name,
            agent_phone, notes, raw_paste, parsed_data
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        data["booking_ref"], data["guest_name"], data["guest_email"],
        data["guest_phone"], data["checkin_date"], data["checkout_date"],
        data["room_type"], data.get("room_details", ""),
        data.get("tour_package", ""), data.get("num_guests", 1),
        data["total_amount"], data.get("currency", "USD"),
        "Pending", data.get("payment_method", "paypal"),
        data.get("paypal_order_id", ""),
        data.get("agent_name", ""), data.get("agent_phone", ""),
        data.get("notes", ""), data.get("raw_paste", ""),
        _serialize_parsed(data),
    ))
    await db.commit()
    log.info(f"Booking {data['booking_ref']} created")
    return await get_booking(data["booking_ref"])


async def update_booking_status(booking_ref: str, new_status: str, capture_id: str = None) -> bool:
    db = _conn()
    booking = await get_booking(booking_ref)
    if not booking:
        return False
    current = booking["status"]
    if new_status not in VALID_TRANSITIONS.get(current, set()):
        log.warning(f"Invalid transition: {booking_ref} {current} → {new_status}")
        return False
    if capture_id:
        await db.execute("UPDATE bookings SET status=?,paypal_capture_id=?,updated_at=datetime('now') WHERE booking_ref=?", (new_status, capture_id, booking_ref))
    else:
        await db.execute("UPDATE bookings SET status=?,updated_at=datetime('now') WHERE booking_ref=?", (new_status, booking_ref))
    await db.commit()
    log.info(f"Booking {booking_ref}: {current} → {new_status}")
    return True


async def update_paypal_order(booking_ref: str, paypal_order_id: str):
    db = _conn()
    await db.execute("UPDATE bookings SET paypal_order_id=?,updated_at=datetime('now') WHERE booking_ref=?", (paypal_order_id, booking_ref))
    await db.commit()


async def get_booking(booking_ref: str) -> dict | None:
    db = _conn()
    cursor = await db.execute("SELECT * FROM bookings WHERE booking_ref=?", (booking_ref,))
    row = await cursor.fetchone()
    return _hydrate(dict(row)) if row else None


async def get_booking_by_paypal_order(order_id: str) -> dict | None:
    db = _conn()
    cursor = await db.execute("SELECT * FROM bookings WHERE paypal_order_id=?", (order_id,))
    row = await cursor.fetchone()
    return _hydrate(dict(row)) if row else None


async def get_all_bookings(
    page: int = 1,
    per_page: int = 50,
    search: str = "",
    status_filter: str = "",
    date_from: str = "",
    date_to: str = "",
    agent: str = "",
) -> dict:
    """Paginated, filtered, searchable booking list."""
    db = _conn()
    conditions = []
    params = []

    if search:
        conditions.append("(booking_ref LIKE ? OR guest_name LIKE ? OR guest_email LIKE ? OR room_type LIKE ? OR parsed_data LIKE ?)")
        s = f"%{search}%"
        params.extend([s, s, s, s, s])

    if status_filter:
        conditions.append("status=?")
        params.append(status_filter)

    if date_from:
        conditions.append("date(created_at)>=?")
        params.append(date_from)

    if date_to:
        conditions.append("date(created_at)<=?")
        params.append(date_to)

    if agent:
        conditions.append("agent_name LIKE ?")
        params.append(f"%{agent}%")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    # Total count
    count_row = await (await db.execute(f"SELECT COUNT(*) FROM bookings {where}", params)).fetchone()
    total = count_row[0]

    # Paginated results
    offset = (page - 1) * per_page
    query_params = params + [per_page, offset]
    cursor = await db.execute(f"SELECT * FROM bookings {where} ORDER BY created_at DESC LIMIT ? OFFSET ?", query_params)
    rows = [_hydrate(dict(r)) for r in await cursor.fetchall()]

    return {
        "bookings": rows,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": max(1, (total + per_page - 1) // per_page),
    }


async def get_bookings_for_export(
    search: str = "",
    status_filter: str = "",
    date_from: str = "",
    date_to: str = "",
) -> list:
    """All matching bookings (no pagination) for CSV export."""
    db = _conn()
    conditions = []
    params = []
    if search:
        s = f"%{search}%"
        conditions.append("(booking_ref LIKE ? OR guest_name LIKE ? OR guest_email LIKE ? OR room_type LIKE ? OR parsed_data LIKE ?)")
        params.extend([s, s, s, s, s])
    if status_filter:
        conditions.append("status=?")
        params.append(status_filter)
    if date_from:
        conditions.append("date(created_at)>=?")
        params.append(date_from)
    if date_to:
        conditions.append("date(created_at)<=?")
        params.append(date_to)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    cursor = await db.execute(f"SELECT * FROM bookings {where} ORDER BY created_at DESC", params)
    return [_hydrate(dict(r)) for r in await cursor.fetchall()]


async def log_payment_event(booking_ref: str, event_type: str, payload: str):
    db = _conn()
    await db.execute("INSERT INTO payment_events (booking_ref,event_type,payload) VALUES (?,?,?)", (booking_ref, event_type, payload))
    await db.commit()


async def get_booking_events(booking_ref: str) -> list:
    db = _conn()
    cursor = await db.execute("SELECT * FROM payment_events WHERE booking_ref=? ORDER BY created_at DESC", (booking_ref,))
    return [dict(r) for r in await cursor.fetchall()]


async def get_booking_stats(date_from: str = "", date_to: str = "") -> dict:
    db = _conn()
    date_conds = []
    date_params = []
    if date_from:
        date_conds.append("date(created_at)>=?")
        date_params.append(date_from)
    if date_to:
        date_conds.append("date(created_at)<=?")
        date_params.append(date_to)

    def _where(extra: str = "") -> str:
        parts = list(date_conds)
        if extra:
            parts.append(extra)
        return f"WHERE {' AND '.join(parts)}" if parts else ""

    async def _count(extra: str = "") -> int:
        w = _where(extra)
        p = list(date_params)
        return (await (await db.execute(f"SELECT COUNT(*) FROM bookings {w}", p)).fetchone())[0]

    async def _sum(extra: str = "") -> float:
        w = _where(extra)
        p = list(date_params)
        return (await (await db.execute(f"SELECT COALESCE(SUM(total_amount),0) FROM bookings {w}", p)).fetchone())[0]

    total = await _count()
    pending = await _count("status='Pending'")
    confirmed = await _count("status='Confirmed'")
    declined = await _count("status='Declined'")
    cancelled = await _count("status='Cancelled'")
    revenue = await _sum("status='Confirmed'")
    today = (await (await db.execute("SELECT COUNT(*) FROM bookings WHERE date(created_at)=date('now')")).fetchone())[0]

    return {
        "total": total, "pending": pending, "confirmed": confirmed,
        "declined": declined, "cancelled": cancelled,
        "revenue": round(revenue, 2), "today": today,
    }


async def update_booking_fields(booking_ref: str, fields: dict):
    db = _conn()
    allowed = {"guest_name", "guest_email", "guest_phone", "room_details", "notes",
               "num_guests", "payment_method", "agent_name", "agent_phone"}
    safe = {k: v for k, v in fields.items() if k in allowed}
    if not safe:
        return
    sets = ", ".join(f"{k}=?" for k in safe)
    vals = list(safe.values()) + [booking_ref]
    await db.execute(f"UPDATE bookings SET {sets}, updated_at=datetime('now') WHERE booking_ref=?", vals)
    await db.commit()


async def booking_ref_exists(ref: str) -> bool:
    db = _conn()
    cursor = await db.execute("SELECT 1 FROM bookings WHERE booking_ref=?", (ref,))
    return (await cursor.fetchone()) is not None
