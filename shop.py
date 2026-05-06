import os
import json
import random
from datetime import datetime, timezone

SHOP_FILE = "/tmp/shop.json"

DEFAULT_SHOP = {
    "standard": [
        {"id": "role_bleu",       "name": "Rôle Bleu",              "type": "role_color",      "price": 200, "duration": None},
        {"id": "role_rouge",      "name": "Rôle Rouge",             "type": "role_color",      "price": 200, "duration": None},
        {"id": "role_vert",       "name": "Rôle Vert",              "type": "role_color",      "price": 200, "duration": None},
        {"id": "role_violet",     "name": "Rôle Violet",            "type": "role_color",      "price": 200, "duration": None},
        {"id": "role_orange",     "name": "Rôle Orange",            "type": "role_color",      "price": 200, "duration": None},
        {"id": "role_lgbt",       "name": "Rôle LGBT",              "type": "role_color",      "price": 200, "duration": None},
        {"id": "role_egirl",      "name": "Rôle eGirl",             "type": "role_color",      "price": 150, "duration": None},
        {"id": "role_eboy",       "name": "Rôle eBoy",              "type": "role_color",      "price": 150, "duration": None},
        {"id": "role_danger",     "name": "Rôle Danger de la Société", "type": "role_color",   "price": 500, "duration": None},
        {"id": "role_bleu_temp",  "name": "Rôle Bleu (1 semaine)",  "type": "role_color_temp", "price": 80,  "duration": 7},
        {"id": "role_rouge_temp", "name": "Rôle Rouge (1 semaine)", "type": "role_color_temp", "price": 80,  "duration": 7},
    ],
    "gacha": [
        {"id": "role_gold",        "name": "🌟 Rôle Gold",        "type": "role_color", "price": 0, "rarity": "légendaire"},
        {"id": "role_arc_en_ciel", "name": "🌈 Rôle Arc-en-ciel", "type": "role_color", "price": 0, "rarity": "épique"},
        {"id": "role_noir",        "name": "🖤 Rôle Noir",        "type": "role_color", "price": 0, "rarity": "rare"},
        {"id": "role_rose",        "name": "🌸 Rôle Rose",        "type": "role_color", "price": 0, "rarity": "commun"},
    ],
    "rotating": [],
    "last_rotate": None
}

ROTATING_POOL = [
    {"id": "role_cyan",      "name": "Rôle Cyan",      "type": "role_color", "price": 150, "duration": None},
    {"id": "role_jaune",     "name": "Rôle Jaune",     "type": "role_color", "price": 150, "duration": None},
    {"id": "role_magenta",   "name": "Rôle Magenta",   "type": "role_color", "price": 150, "duration": None},
    {"id": "role_blanc",     "name": "Rôle Blanc",     "type": "role_color", "price": 120, "duration": None},
    {"id": "role_turquoise", "name": "Rôle Turquoise", "type": "role_color", "price": 130, "duration": None},
    {"id": "role_corail",    "name": "Rôle Corail",    "type": "role_color", "price": 130, "duration": None},
]

ROLE_COLORS_HEX = {
    "role_bleu": 0x3498db, "role_rouge": 0xe74c3c, "role_vert": 0x2ecc71,
    "role_violet": 0x9b59b6, "role_orange": 0xe67e22, "role_cyan": 0x1abc9c,
    "role_jaune": 0xf1c40f, "role_magenta": 0xe91e8c, "role_blanc": 0xffffff,
    "role_turquoise": 0x40e0d0, "role_corail": 0xff6b6b, "role_rose": 0xff69b4,
    "role_noir": 0x2c2f33, "role_gold": 0xffd700, "role_arc_en_ciel": 0x9b59b6,
    "role_bleu_temp": 0x3498db, "role_rouge_temp": 0xe74c3c,
    "role_lgbt": 0xFF69B4, "role_egirl": 0xFF1493,
    "role_eboy": 0x00BFFF, "role_danger": 0xFF4500,
}

def load_shop():
    if os.path.exists(SHOP_FILE):
        with open(SHOP_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    save_shop(DEFAULT_SHOP)
    return DEFAULT_SHOP

def save_shop(shop):
    os.makedirs(os.path.dirname(SHOP_FILE), exist_ok=True)
    with open(SHOP_FILE, "w", encoding="utf-8") as f:
        json.dump(shop, f, ensure_ascii=False, indent=2)

def rotate_shop():
    shop = load_shop()
    new_rotating = random.sample(ROTATING_POOL, min(3, len(ROTATING_POOL)))
    shop["rotating"] = new_rotating
    shop["last_rotate"] = datetime.now(timezone.utc).isoformat()
    save_shop(shop)
    return new_rotating
