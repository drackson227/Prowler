import os
import json

DB_FILE = "/data/db.json"

def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_db(db):
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def get_member_data(db, member_id):
    mid = str(member_id)
    if mid not in db:
        db[mid] = {
            "warns": 0, "total_warns": 0, "mutes": 0, "kicks": 0,
            "bans": 0, "spam_mute_count": 0, "comments": [], "sanctions": [],
            "xp": 0, "level": 0, "coins": 0, "inventory": [], "equipped": [],
            "daily_streak": 0, "last_daily": None, "godfather": None,
            "subscriptions": [], "cartes": [], "levelup_notif": True
        }
    defaults = {
        "xp": 0, "level": 0, "coins": 0, "inventory": [], "equipped": [],
        "daily_streak": 0, "last_daily": None, "godfather": None,
        "subscriptions": [], "cartes": [], "spam_mute_count": 0,
        "warns": 0, "total_warns": 0, "mutes": 0, "kicks": 0, "bans": 0,
        "comments": [], "sanctions": [], "levelup_notif": True
    }
    for key, default in defaults.items():
        if key not in db[mid]:
            db[mid][key] = default
    # ✅ FIX CRITIQUE : était "return db[mid][phases.setup]" (erreur copier-coller)
    return db[mid]
