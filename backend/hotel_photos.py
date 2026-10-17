"""
Hotel Photo Service — SerpApi Google Maps Integration
──────────────────────────────────────────────────────
Auto-fetches REAL hotel photos from Google Maps via SerpApi.
Photos served from lh3.googleusercontent.com (Google CDN).
Gmail displays these 100% of the time.

Caches results per hotel — same hotel = no API call.
Falls back to Unsplash if API unavailable.
"""
import os
import re
import httpx
import hashlib
import logging
import json

log = logging.getLogger("hotel_photos")

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "photo_cache")

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
    return os.getenv("SERPAPI_KEY", "")


def _cache_key(name):
    return hashlib.md5(name.lower().strip().encode()).hexdigest()


def _get_cached(name, ptype="hotel"):
    os.makedirs(CACHE_DIR, exist_ok=True)
    f = os.path.join(CACHE_DIR, f"{_cache_key(name)}_{ptype}.json")
    if os.path.exists(f):
        try:
            with open(f) as fh:
                d = json.load(fh)
            if d.get("url"):
                return d["url"]
        except Exception:
            pass
    return None


def _set_cache(name, ptype, url):
    os.makedirs(CACHE_DIR, exist_ok=True)
    f = os.path.join(CACHE_DIR, f"{_cache_key(name)}_{ptype}.json")
    with open(f, "w") as fh:
        json.dump({"name": name, "url": url, "type": ptype}, fh)


def _resize_google_url(url, width=600, height=340):
    """Resize a Google CDN image URL to email-friendly dimensions."""
    if "lh3.googleusercontent.com" in url and "=" in url:
        base = url.rsplit("=", 1)[0]
        return f"{base}=w{width}-h{height}-k-no"
    return url


def get_hotel_photo(hotel_name, booking_ref=""):
    """Get a real photo of the hotel from Google Maps via SerpApi."""
    if not hotel_name or hotel_name in ("Your Hotel", "Standard Room"):
        return _fallback(booking_ref)

    cached = _get_cached(hotel_name, "hotel")
    if cached:
        log.info(f"Photo cache hit: {hotel_name}")
        return cached

    key = _api_key()
    if not key:
        log.info("No SERPAPI_KEY set — using fallback photo")
        return _fallback(booking_ref)

    try:
        resp = httpx.get("https://serpapi.com/search.json", params={
            "engine": "google_maps",
            "q": hotel_name,
            "type": "search",
            "api_key": key,
        }, timeout=12)

        data = resp.json()
        place = data.get("place_results", {})
        thumb = place.get("thumbnail", "")

        if not thumb:
            results = data.get("local_results", [])
            if results:
                thumb = results[0].get("thumbnail", "")

        if thumb and "lh3.googleusercontent.com" in thumb:
            url = _resize_google_url(thumb, 600, 340)
            _set_cache(hotel_name, "hotel", url)
            log.info(f"Real hotel photo: {hotel_name} -> {url[:60]}...")
            return url
        else:
            log.warning(f"No Google photo for: {hotel_name}")
            return _fallback(booking_ref)

    except Exception as e:
        log.error(f"SerpApi error: {e}")
        return _fallback(booking_ref)


def get_bed_photo(room_type, hotel_name=""):
    """Get bed photo by room type. Curated Unsplash photos."""
    rt = (room_type or "").lower()
    if re.search(r'suite|presidential|penthouse|executive', rt):
        return FALLBACK_BEDS["suite"]
    elif re.search(r'double|2 queen|two queen|2 full|two full', rt):
        return FALLBACK_BEDS["double"]
    elif re.search(r'twin|2 single|two single', rt):
        return FALLBACK_BEDS["twin"]
    else:
        return FALLBACK_BEDS["king"]


def _fallback(booking_ref):
    if not booking_ref:
        return FALLBACK_HOTELS[0]
    idx = int(hashlib.md5(booking_ref.encode()).hexdigest(), 16) % len(FALLBACK_HOTELS)
    return FALLBACK_HOTELS[idx]
