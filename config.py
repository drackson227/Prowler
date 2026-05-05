import os

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

ALLOWED_ROLES = ["Modérateur", "Fondateur"]
FOUNDER_ROLES = ["Fondateur"]
MOD_CHANNEL = "modération"
LOG_CHANNEL = "📋・logs"
GENERAL_CHANNEL = "chat-général"
REPORT_CHANNEL = "📝・rapport-prowler"
REPORT_HOUR = 20

ROLE_MEMBRE = "Membre"
ROLE_MEMBRE_ACTIF = "Membre Actif"
ACTIVE_MESSAGES_PER_DAY = 10
ACTIVE_DAYS_REQUIRED = 2
INACTIVE_DAYS_REQUIRED = 2

SPAM_THRESHOLD = 10
SPAM_WINDOW = 10
XP_PER_MESSAGE = 10
COINS_PER_MESSAGE = 1
COINS_BOOST = 2
BOOST_INTERVAL = 300
BOOST_DURATION = 1800
BOOST_INACTIVE = 360
SHOP_ROTATE_INTERVAL = 10800
GACHA_COST = 50
STREAK_MULTIPLIERS = {3: 1.5, 7: 2.0, 14: 2.5, 30: 3.0}
DAILY_BASE_COINS = 50
AI_MODEL = "mistralai/mistral-7b-instruct:free"

TICKET_CATEGORY = "🎫 Tickets"
TICKET_CHANNEL = "tickets"
MOD_ROLES_FOR_TICKETS = ["Modérateur", "Fondateur"]
RAID_JOIN_THRESHOLD = 10
RAID_WINDOW = 60
ACTIVITY_ALERT_THRESHOLD = 15
ACTIVITY_ALERT_WINDOW = 60

SYSTEM_PROMPT = """Tu es un assistant de modération Discord.
À partir d'un message en langage naturel, tu dois extraire l'action de modération voulue et retourner un JSON.
Actions possibles: ban, kick, mute, warn, delete_messages, unmute, unban, show_profile, none
Format de réponse JSON uniquement :
{
  "action": "ban|kick|mute|warn|delete_messages|unmute|unban|show_profile|none",
  "target": "mention ou description de l'utilisateur ciblé",
  "duration_minutes": null ou nombre,
  "count": null ou nombre,
  "reason": null,
  "needs_clarification": false,
  "clarification_question": null
}"""

REASON_PROMPT = """Tu es un assistant de modération Discord.
Reformule la raison donnée par un modérateur en une raison officielle, courte et professionnelle.
Réponds UNIQUEMENT avec la raison reformulée, rien d'autre, pas de guillemets."""

ANALYSIS_PROMPT = """Analyse le comportement de cet utilisateur Discord basé sur ses derniers messages.
Donne une appréciation courte (3-5 lignes max) en français, de façon concise et professionnelle."""

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
