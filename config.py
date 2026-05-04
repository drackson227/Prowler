import os

# ============================================================
# TOKENS & CLÉS
# ============================================================
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

# ============================================================
# RÔLES & SALONS
# ============================================================
ALLOWED_ROLES = ["Modérateur", "Fondateur"]
FOUNDER_ROLES = ["Fondateur"]
MOD_CHANNEL = "modération"
LOG_CHANNEL = "📋・logs"
GENERAL_CHANNEL = "💬・chat-général"
REPORT_CHANNEL = "📝・rapport-prowler"
REPORT_HOUR = 20

ROLE_MEMBRE = "Membre"
ROLE_MEMBRE_ACTIF = "Membre Actif"
ACTIVE_MESSAGES_PER_DAY = 10
ACTIVE_DAYS_REQUIRED = 2
INACTIVE_DAYS_REQUIRED = 2

# ============================================================
# ANTI-SPAM
# ============================================================
SPAM_THRESHOLD = 10
SPAM_WINDOW = 30

# ============================================================
# XP & PIÈCES
# ============================================================
XP_PER_MESSAGE = 10
COINS_PER_MESSAGE = 1
COINS_BOOST = 2
BOOST_INTERVAL = 300
BOOST_DURATION = 1800
BOOST_INACTIVE = 360

# ============================================================
# BOUTIQUE & GACHA
# ============================================================
SHOP_ROTATE_INTERVAL = 10800  # 3h
GACHA_COST = 50

# ============================================================
# DAILY
# ============================================================
STREAK_MULTIPLIERS = {3: 1.5, 7: 2.0, 14: 2.5, 30: 3.0}
DAILY_BASE_COINS = 50

# ============================================================
# COULEURS & LABELS (partagés)
# ============================================================
ACTION_COLORS = {
    "ban": 0xe74c3c, "kick": 0xe67e22, "mute": 0xf39c12,
    "unmute": 0x2ecc71, "unban": 0x2ecc71, "warn": 0xf1c40f,
    "delete_messages": 0x9b59b6,
}
ACTION_LABELS = {
    "ban": "🔨 Bannissement", "kick": "👢 Kick", "mute": "🔇 Mute",
    "unmute": "🔊 Demute", "unban": "✅ Déban", "warn": "⚠️ Avertissement",
    "delete_messages": "🗑️ Suppression de messages",
}
RARITY_COLORS = {
    "légendaire": 0xf1c40f, "épique": 0x9b59b6, "rare": 0x3498db, "commun": 0x95a5a6
}
ROLE_COLORS_HEX = {
    "role_bleu": 0x3498db, "role_rouge": 0xe74c3c, "role_vert": 0x2ecc71,
    "role_violet": 0x9b59b6, "role_orange": 0xe67e22, "role_cyan": 0x1abc9c,
    "role_jaune": 0xf1c40f, "role_magenta": 0xe91e8c, "role_blanc": 0xffffff,
    "role_turquoise": 0x40e0d0, "role_corail": 0xff6b6b, "role_rose": 0xff69b4,
    "role_noir": 0x2c2f33, "role_gold": 0xffd700, "role_arc_en_ciel": 0x9b59b6,
    "role_bleu_temp": 0x3498db, "role_rouge_temp": 0xe74c3c,
}

# ============================================================
# PROMPTS IA
# ============================================================
SYSTEM_PROMPT = """Tu es un assistant de modération Discord.
À partir d'un message en langage naturel, tu dois extraire l'action de modération voulue et retourner un JSON.

Actions possibles: ban, kick, mute, warn, delete_messages, unmute, unban, show_profile, none

show_profile : quand le modérateur veut voir le profil, les infos, ou revoir un membre (ex: "montre le profil de X", "remontre son profil", "infos sur X", "qui est X", "vérifie X").

Format de réponse JSON uniquement (pas de texte autour) :
{
  "action": "ban|kick|mute|warn|delete_messages|unmute|unban|show_profile|none",
  "target": "mention ou description de l'utilisateur ciblé",
  "duration_minutes": null ou nombre (pour mute),
  "count": null ou nombre (pour delete_messages),
  "reason": null,
  "needs_clarification": false,
  "clarification_question": null
}

Si la cible n'est pas claire, mets needs_clarification à true.
Si aucune action de modération n'est détectée, mets action à "none".
"""

REASON_PROMPT = """Tu es un assistant de modération Discord.
Reformule la raison donnée par un modérateur en une raison officielle, courte et professionnelle.
Réponds UNIQUEMENT avec la raison reformulée, rien d'autre, pas de guillemets.

Exemples :
- "il est raciste" → "Comportement raciste"
- "spam" → "Spam répété"
- "il insulte tout le monde" → "Insultes envers les membres"
- "trop chiant" → "Comportement perturbateur"
"""

ANALYSIS_PROMPT = """Analyse le comportement de cet utilisateur Discord basé sur ses derniers messages.
Donne une appréciation courte (3-5 lignes max) mentionnant :
- Son ton général (poli, agressif, neutre...)
- S'il insulte souvent ou non
- Son niveau d'activité dans les discussions
- Une appréciation globale

Réponds en français, de façon concise et professionnelle."""
