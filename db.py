import os
import json

DB_FILE = "/data/db.json"  # ← seul changement, était "db.json"

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
            "daily_streak": 0, "last_daily": None, "godfather": None, "subscriptions": []
        }
    for key, default in [
        ("xp", 0), ("level", 0), ("coins", 0), ("inventory", []),
        ("equipped", []), ("daily_streak", 0), ("last_daily", None),
        ("godfather", None), ("subscriptions", [])
    ]:
        if key not in db[mid]:
            db[mid][key] = default
    return db[mid]
