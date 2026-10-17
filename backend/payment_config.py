"""
Payment Config — loads payment method details from config/payment_methods.json.
Provides defaults so the system never renders empty placeholders.
"""
import os
import json
import logging

log = logging.getLogger("config")

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "payment_methods.json")

_config = {}

DEFAULTS = {
    "company_name": "Quota Solutions — Travel Desk",
    "company_phone": "",
    "company_email": "",
    "company_website": "",
    "support_hours": "",
    "beneficiary_name": "Your Business Name",
    "bank_name": "",
    "wire_account": "",
    "wire_routing": "",
    "wire_swift": "",
    "bank_address": "",
    "zelle_contact": "",
    "venmo_handle": "",
    "wise_email": "",
    "agent_default_phone": "",
    "agent_default_email": "",
    "checkin_time": "3:00 PM",
    "checkout_time": "11:00 AM",
}


def load_config():
    global _config
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                _config = json.load(f)
            log.info(f"Payment config loaded from {CONFIG_PATH}")
        except Exception as e:
            log.warning(f"Failed to load payment config: {e} — using defaults")
            _config = {}
    else:
        log.info("No payment_methods.json found — using defaults")
        _config = {}


def get(key: str) -> str:
    return _config.get(key, DEFAULTS.get(key, ""))


def get_all() -> dict:
    result = dict(DEFAULTS)
    result.update(_config)
    return result
