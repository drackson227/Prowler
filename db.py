import os
import json
from pymongo import MongoClient

MONGO_URI = os.environ.get("MONGO_URI")
client = MongoClient(MONGO_URI)
collection = client["prowler"]["members"]

def load_db():
    docs = collection.find({})
    db = {}
    for doc in docs:
        mid = doc["_id"]
        db[mid] = {k: v for k, v in doc.items() if k != "_id"}
    return db

def save_db(db):
    for mid, data in db.items():
        collection.update_one({"_id": mid}, {"$set": data}, upsert=True)

def get_member_data(db, member_id):
    mid = str(member_id)
    if mid not in db:
        db[mid] = {}
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
    return db[mid]
