"""
Hotel Photo Service
───────────────────
Auto-fetches real hotel photos using Google Places API.
Falls back to Unsplash curated photos if API unavailable.

Setup: Set GOOGLE_PLACES_API_KEY in your environment variables.
Get one at: https://console.cloud.google.com → APIs → Places API → Credentials
"""
import os
import httpx
import hashlib
import logging
import json
from pathlib import Path

log = logging.getLogger("hotel_photos")

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "photo_cache")

# Unsplash fallbacks (guaranteed to work in Gmail)
FALLBACK_HOTELS = [
    "https://images.unsplash.com/photo-1566073771259-6a8506099945?w=600&h=340&fit=crop&q=80",
    "https://images.unsplash.com/photo-1551882547-ff40c63fe5fa?w=600&h=340&fit=crop&q=80",
    "https://images.unsplash.com/photo-1542314831-068cd1dbfeeb?w=600&h=340&fit=crop&q=80",
    "https://images.unsplash.com/photo-1564501049412-61c2a3083791?w=600&h=340&fit=crop&q=80",
    "https://images.unsplash.com/photo-1520250497591-112f2f40a3f4?w=600&h=340&fit=crop&q=80",
    "https://images.unsplash.com/photo-1582719478250-c89cae4dc85b?w=600&h=340&fit=crop&q=80",
]

FALLBACK_BEDS = {
    "king":   "https://images.unsplash.com/photo-1590490360182-c33d955e4c47?w=120&h=120&fit=crop&q=80",
    "double": "https://images.unsplash.com/photo-1631049307264-da0ec9d70304?w=120&h=120&fit=crop&q=80",
    "suite":  "https://images.unsplash.com/photo-1591088398332-8a7791972843?w=120&h=120&fit=crop&q=80",
    "twin":   "https://images.unsplash.com/photo-1595576508898-0ad5c879a061?w=120&h=120&fit=crop&q=80",
}


def _api_key():
    return os.getenv("GOOGLE_PLACES_API_KEY", "")


def _cache_key(hotel_name):
    return hashlib.md5(hotel_name.lower().strip().encode()).hexdigest()


def _get_cached(hotel_name, photo_type="hotel"):
    """Check if we already fetched this hotel's photo."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(CACHE_DIR, f"{_cache_key(hotel_name)}_{photo_type}.json")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r") as f:
                data = json.load(f)
            if data.get("url"):
                return data["url"]
        except Exception:
            pass
    return None


def _set_cache(hotel_name, photo_type, url):
    """Cache the photo URL for future bookings at the same hotel."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(CACHE_DIR, f"{_cache_key(hotel_name)}_{photo_type}.json")
    with open(cache_file, "w") as f:
        json.dump({"hotel": hotel_name, "url": url, "type": photo_type}, f)


def get_hotel_photo(hotel_name, booking_ref=""):
    """Get a real photo of the hotel. Uses Google Places API with local caching."""
    if not hotel_name or hotel_name == "Your Hotel":
        return _fallback_hotel(booking_ref)

    # Check cache first (same hotel = same photo, no API call)
    cached = _get_cached(hotel_name, "hotel")
    if cached:
        log.info(f"Hotel photo cache hit: {hotel_name}")
        return cached

    # Try Google Places API
    key = _api_key()
    if not key:
        log.info("No GOOGLE_PLACES_API_KEY — using fallback")
        return _fallback_hotel(booking_ref)

    try:
        # Step 1: Find the hotel
        with httpx.Client(timeout=10.0) as client:
            search_resp = client.get(
                "https://maps.googleapis.com/maps/api/place/findplacefromtext/json",
                params={
                    "input": hotel_name,
                    "inputtype": "textquery",
                    "fields": "place_id,photos",
                    "key": key,
                }
            )
            search_data = search_resp.json()
            candidates = search_data.get("candidates", [])
            if not candidates:
                log.warning(f"No Google Places result for: {hotel_name}")
                return _fallback_hotel(booking_ref)

            place = candidates[0]
            photos = place.get("photos", [])
            if not photos:
                log.warning(f"No photos for: {hotel_name}")
                return _fallback_hotel(booking_ref)

            # Step 2: Get the photo URL
            photo_ref = photos[0]["photo_reference"]
            photo_url = (
                f"https://maps.googleapis.com/maps/api/place/photo"
                f"?maxwidth=600&photo_reference={photo_ref}&key={key}"
            )

            # Google Places Photo API returns a redirect to the actual image URL
            # We follow the redirect to get the final CDN URL
            photo_resp = client.get(photo_url, follow_redirects=True)
            final_url = str(photo_resp.url)

            # Cache it
            _set_cache(hotel_name, "hotel", final_url)
            log.info(f"Hotel photo fetched: {hotel_name} -> {final_url[:60]}...")
            return final_url

    except Exception as e:
        log.error(f"Google Places API error: {e}")
        return _fallback_hotel(booking_ref)


def get_bed_photo(room_type, hotel_name=""):
    """Get a bed/room photo. Uses Google Places for the hotel's room, falls back to Unsplash."""
    import re
    rt = (room_type or "").lower()

    # For now, use curated bed photos by type
    # Google Places doesn't have room-specific photos reliably
    if re.search(r'suite|presidential|penthouse|executive', rt):
        return FALLBACK_BEDS["suite"]
    elif re.search(r'double|2 queen|two queen|2 full|two full', rt):
        return FALLBACK_BEDS["double"]
    elif re.search(r'twin|2 single|two single', rt):
        return FALLBACK_BEDS["twin"]
    else:
        return FALLBACK_BEDS["king"]


def _fallback_hotel(booking_ref):
    """Pick a fallback Unsplash hotel photo based on booking ref hash."""
    if not booking_ref:
        return FALLBACK_HOTELS[0]
    idx = int(hashlib.md5(booking_ref.encode()).hexdigest(), 16) % len(FALLBACK_HOTELS)
    return FALLBACK_HOTELS[idx]
