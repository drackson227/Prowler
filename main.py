import discord
import os
import json
import asyncio
import random
from datetime import timedelta, datetime, timezone
from openai import OpenAI
from collections import defaultdict

# ============================================================
# CONFIG
# ============================================================
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

ALLOWED_ROLES = ["Modérateur"]
MOD_CHANNEL = "modération"
LOG_CHANNEL = "📋・logs"
GENERAL_CHANNEL = "💬・chat-général"
REPORT_CHANNEL = "📝・rapport-prowler"
REPORT_HOUR = 22

ROLE_MEMBRE = "Membre"
ROLE_MEMBRE_ACTIF = "Membre Actif"
ACTIVE_MESSAGES_PER_DAY = 10
ACTIVE_DAYS_REQUIRED = 2
INACTIVE_DAYS_REQUIRED = 2

SPAM_THRESHOLD = 10
SPAM_WINDOW = 30

# XP & pièces
XP_PER_MESSAGE = 10
COINS_PER_MESSAGE = 1
COINS_BOOST = 2
BOOST_INTERVAL = 300
BOOST_DURATION = 1800
BOOST_INACTIVE = 360

# Boutique rotative
SHOP_ROTATE_INTERVAL = 10800  # 3h

# Gacha
GACHA_COST = 50

# Daily streak multipliers
STREAK_MULTIPLIERS = {3: 1.5, 7: 2.0, 14: 2.5, 30: 3.0}
DAILY_BASE_COINS = 50
# ============================================================

ai_client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_API_KEY)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

client = discord.Client(intents=intents)

# ---------- états en mémoire ----------
pending_actions = {}
waiting_for_reason = {}
waiting_for_member_choice = {}
waiting_for_action_choice = {}
waiting_for_comment = {}
spam_tracker = {}
member_message_days = {}
boost_tracker = {}

# ---------- DB JSON simple ----------
DB_FILE = "db.json"

def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_db(db):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def get_member_data(db, member_id):
    mid = str(member_id)
    if mid not in db:
        db[mid] = {
            "warns": 0,
            "total_warns": 0,
            "mutes": 0,
            "kicks": 0,
            "bans": 0,
            "spam_mute_count": 0,
            "comments": [],
            "sanctions": [],
            "xp": 0,
            "level": 0,
            "coins": 0,
            "inventory": [],
            "equipped": [],
            "daily_streak": 0,
            "last_daily": None,
            "godfather": None,
            "subscriptions": []
        }
    for key, default in [
        ("xp", 0), ("level", 0), ("coins", 0), ("inventory", []),
        ("equipped", []), ("daily_streak", 0), ("last_daily", None),
        ("godfather", None), ("subscriptions", [])
    ]:
        if key not in db[mid]:
            db[mid][key] = default
    return db[mid]

# ============================================================
# BOUTIQUE
# ============================================================
SHOP_FILE = "shop.json"

DEFAULT_SHOP = {
    "standard": [
        {"id": "role_bleu", "name": "Rôle Bleu", "type": "role_color", "price": 200, "duration": None},
        {"id": "role_rouge", "name": "Rôle Rouge", "type": "role_color", "price": 200, "duration": None},
        {"id": "role_vert", "name": "Rôle Vert", "type": "role_color", "price": 200, "duration": None},
        {"id": "role_violet", "name": "Rôle Violet", "type": "role_color", "price": 200, "duration": None},
        {"id": "role_orange", "name": "Rôle Orange", "type": "role_color", "price": 200, "duration": None},
        {"id": "role_bleu_temp", "name": "Rôle Bleu (1 semaine)", "type": "role_color_temp", "price": 80, "duration": 7},
        {"id": "role_rouge_temp", "name": "Rôle Rouge (1 semaine)", "type": "role_color_temp", "price": 80, "duration": 7},
    ],
    "gacha": [
        {"id": "role_gold", "name": "🌟 Rôle Gold", "type": "role_color", "price": 0, "rarity": "légendaire"},
        {"id": "role_arc_en_ciel", "name": "🌈 Rôle Arc-en-ciel", "type": "role_color", "price": 0, "rarity": "épique"},
        {"id": "role_noir", "name": "🖤 Rôle Noir", "type": "role_color", "price": 0, "rarity": "rare"},
        {"id": "role_rose", "name": "🌸 Rôle Rose", "type": "role_color", "price": 0, "rarity": "commun"},
    ],
    "rotating": [],
    "last_rotate": None
}

ROTATING_POOL = [
    {"id": "role_cyan", "name": "Rôle Cyan", "type": "role_color", "price": 150, "duration": None},
    {"id": "role_jaune", "name": "Rôle Jaune", "type": "role_color", "price": 150, "duration": None},
    {"id": "role_magenta", "name": "Rôle Magenta", "type": "role_color", "price": 150, "duration": None},
    {"id": "role_blanc", "name": "Rôle Blanc", "type": "role_color", "price": 120, "duration": None},
    {"id": "role_turquoise", "name": "Rôle Turquoise", "type": "role_color", "price": 130, "duration": None},
    {"id": "role_corail", "name": "Rôle Corail", "type": "role_color", "price": 130, "duration": None},
]

def load_shop():
    if os.path.exists(SHOP_FILE):
        with open(SHOP_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    save_shop(DEFAULT_SHOP)
    return DEFAULT_SHOP

def save_shop(shop):
    with open(SHOP_FILE, "w", encoding="utf-8") as f:
        json.dump(shop, f, ensure_ascii=False, indent=2)

def rotate_shop():
    shop = load_shop()
    new_rotating = random.sample(ROTATING_POOL, min(3, len(ROTATING_POOL)))
    shop["rotating"] = new_rotating
    shop["last_rotate"] = datetime.now(timezone.utc).isoformat()
    save_shop(shop)
    return new_rotating

# ============================================================
# XP & NIVEAUX
# ============================================================
def xp_for_level(level):
    return int(100 * (1.1 ** level))

def get_level_from_xp(xp):
    level = 0
    total = 0
    while True:
        needed = xp_for_level(level)
        if total + needed > xp:
            return level, xp - total, needed
        total += needed
        level += 1

async def add_xp_and_coins(member, guild, xp_gain, coin_gain):
    db = load_db()
    data = get_member_data(db, member.id)
    old_level = data["level"]
    data["xp"] += xp_gain
    data["coins"] += coin_gain
    new_level, current_xp, needed_xp = get_level_from_xp(data["xp"])
    data["level"] = new_level
    save_db(db)
    if new_level > old_level:
        try:
            embed = discord.Embed(
                title="🎉 Level Up !",
                description=f"Tu es maintenant **niveau {new_level}** sur **{guild.name}** !",
                color=0xf1c40f
            )
            embed.add_field(name="✨ XP total", value=str(data["xp"]), inline=True)
            embed.add_field(name="🪙 Pièces", value=str(data["coins"]), inline=True)
            await member.send(embed=embed)
        except:
            pass

# ============================================================
# BOOST
# ============================================================
def update_boost(member_id):
    now = datetime.now(timezone.utc).timestamp()
    tracker = boost_tracker.get(member_id, {"active": False, "last_msg": now, "start": now, "last_boost_msg": 0})
    if now - tracker["last_msg"] > BOOST_INACTIVE:
        tracker = {"active": False, "last_msg": now, "start": now, "last_boost_msg": 0}
    tracker["last_msg"] = now
    if not tracker["active"] and now - tracker.get("last_boost_msg", 0) >= BOOST_INTERVAL:
        tracker["last_boost_msg"] = now
        tracker["active"] = True
        tracker["start"] = now
    if tracker.get("active") and now - tracker["start"] > BOOST_DURATION:
        tracker["active"] = False
    boost_tracker[member_id] = tracker
    return tracker.get("active", False)

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

# ============================================================
# COULEURS & LABELS
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

# ============================================================
# UTILITAIRES
# ============================================================
def has_permission(member):
    if member.guild.owner_id == member.id:
        return True
    return any(role.name in ALLOWED_ROLES for role in member.roles)

def similarity(a, b):
    a, b = a.lower(), b.lower()
    if a in b or b in a:
        return 1.0
    matches = sum(c in b for c in a)
    return matches / max(len(a), len(b))

def find_similar_members(guild, description):
    description_lower = description.lower().strip()
    exact, similar = [], []
    for member in guild.members:
        name_lower = member.display_name.lower()
        username_lower = member.name.lower()
        score = max(similarity(description_lower, name_lower), similarity(description_lower, username_lower))
        if score == 1.0 and (description_lower in [name_lower, username_lower]):
            exact.append(member)
        elif score >= 0.5:
            similar.append((score, member))
    similar.sort(key=lambda x: x[0], reverse=True)
    return exact, [m for _, m in similar if m not in exact][:5]

async def find_member(guild, description, channel):
    if description.strip().isdigit():
        uid = int(description.strip())
        m = guild.get_member(uid)
        if m:
            return [m], [], True, False
        try:
            ban_entry = await guild.fetch_ban(discord.Object(id=uid))
            return [ban_entry.user], [], True, True
        except discord.NotFound:
            pass
        return [], [], True, False

    if description.startswith("<@") and description.endswith(">"):
        uid_str = description.strip("<@!>")
        try:
            uid = int(uid_str)
            m = guild.get_member(uid)
            if m:
                return [m], [], True, False
            ban_entry = await guild.fetch_ban(discord.Object(id=uid))
            return [ban_entry.user], [], True, True
        except:
            return [], [], True, False

    exact, similar = find_similar_members(guild, description)
    return exact, similar, False, False

async def reformulate_reason(raw_reason):
    try:
        r = ai_client.chat.completions.create(
            model="meta-llama/llama-3.1-8b-instruct:free",
            messages=[{"role": "user", "content": f"{REASON_PROMPT}\n\nRaison brute : {raw_reason}"}]
        )
        return r.choices[0].message.content.strip()
    except:
        return raw_reason

def get_log_channel(guild):
    for ch in guild.text_channels:
        if LOG_CHANNEL in ch.name or "logs" in ch.name.lower():
            return ch
    return None

def get_channel_by_name(guild, name):
    for ch in guild.text_channels:
        if name.lower().replace("・", "") in ch.name.lower().replace("・", ""):
            return ch
    return None

# ============================================================
# LOGS
# ============================================================
async def log_action(guild, action, moderator, target, reason=None, extra=None):
    log_ch = get_log_channel(guild)
    if not log_ch:
        return
    colors = {
        "ban": 0xe74c3c, "kick": 0xe67e22, "mute": 0xf39c12,
        "unmute": 0x2ecc71, "unban": 0x2ecc71, "warn": 0xf1c40f,
        "spam_mute": 0xff6b35, "join": 0x2ecc71, "leave": 0x95a5a6,
        "comment_add": 0x3498db, "comment_remove": 0xe74c3c,
        "delete_messages": 0x9b59b6, "show_profile": 0x95a5a6,
        "shop_buy": 0x2ecc71, "shop_equip": 0x3498db, "gacha": 0xf1c40f, "daily": 0xf39c12,
    }
    labels = {
        "ban": "🔨 Bannissement", "kick": "👢 Kick", "mute": "🔇 Mute",
        "unmute": "🔊 Demute", "unban": "✅ Déban", "warn": "⚠️ Avertissement",
        "spam_mute": "🤖 Mute anti-spam", "join": "📥 Arrivée", "leave": "📤 Départ",
        "comment_add": "💬 Commentaire ajouté", "comment_remove": "🗑️ Commentaire supprimé",
        "delete_messages": "🗑️ Messages supprimés", "show_profile": "🔍 Profil consulté",
        "shop_buy": "🛍️ Achat boutique", "shop_equip": "👗 Équipement",
        "gacha": "🎰 Gacha", "daily": "🎁 Daily",
    }
    embed = discord.Embed(
        title=labels.get(action, action),
        color=colors.get(action, 0x95a5a6),
        timestamp=datetime.now(timezone.utc)
    )
    if target:
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Utilisateur", value=f"{target.mention} (`{target.id}`)", inline=True)
    if moderator:
        embed.add_field(name="Modérateur", value=f"{moderator.mention}", inline=True)
    if reason:
        embed.add_field(name="Raison", value=reason, inline=False)
    if extra:
        for k, v in extra.items():
            if v is not None:
                embed.add_field(name=k, value=str(v), inline=True)
    await log_ch.send(embed=embed)

# ============================================================
# ANTI-SPAM
# ============================================================
async def check_spam(message):
    mid = message.author.id
    content = message.content.strip().lower()
    now = datetime.now(timezone.utc).timestamp()
    if mid not in spam_tracker:
        spam_tracker[mid] = {"content": content, "times": []}
    tracker = spam_tracker[mid]
    if tracker["content"] != content:
        spam_tracker[mid] = {"content": content, "times": [now]}
        return False
    tracker["times"] = [t for t in tracker["times"] if now - t < SPAM_WINDOW]
    tracker["times"].append(now)
    if len(tracker["times"]) >= SPAM_THRESHOLD:
        spam_tracker[mid] = {"content": "", "times": []}
        await apply_spam_mute(message)
        return True
    return False

async def apply_spam_mute(message):
    member = message.author
    guild = message.guild
    db = load_db()
    data = get_member_data(db, member.id)
    count = data.get("spam_mute_count", 0)
    data["spam_mute_count"] = count + 1
    if count == 0:
        duration = timedelta(minutes=15)
        data["warns"] = min(data["warns"] + 1, 3)
        data["total_warns"] += 1
        duration_txt = "15 minutes"
    elif count == 1:
        duration = timedelta(minutes=20)
        data["warns"] = min(data["warns"] + 1, 3)
        data["total_warns"] += 1
        duration_txt = "20 minutes"
    else:
        duration = None
        data["warns"] = min(data["warns"] + 1, 3)
        data["total_warns"] += 1
        duration_txt = "permanent"
    data["mutes"] += 1
    data["sanctions"].append({
        "type": "spam_mute",
        "reason": "Spam répété (anti-spam automatique)",
        "date": datetime.now(timezone.utc).isoformat(),
        "duration": duration_txt
    })
    save_db(db)
    try:
        if duration:
            await member.timeout(duration, reason="Spam répété (anti-spam automatique)")
        else:
            await member.timeout(timedelta(days=28), reason="Spam répété — mute permanent (anti-spam)")
    except discord.Forbidden:
        pass
    embed = discord.Embed(
        title="🤖 Anti-spam déclenché",
        description=f"{member.mention} a été mute **{duration_txt}** pour spam répété.\nAvertissements actuels : **{data['warns']}/3**",
        color=0xff6b35
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    if duration is None:
        ticket_ch = get_channel_by_name(guild, "ticket")
        if ticket_ch:
            embed.add_field(name="📩 Contestation", value=f"Tu peux ouvrir un ticket dans {ticket_ch.mention} même muté.", inline=False)
    await message.channel.send(embed=embed)
    await log_action(guild, "spam_mute", None, member,
                     reason="Spam répété (anti-spam automatique)",
                     extra={"Durée": duration_txt, "Warns": f"{data['warns']}/3"})

# ============================================================
# EXECUTE ACTION
# ============================================================
async def execute_action(guild, action_data, mod_channel, moderator=None):
    member = action_data.get("resolved_member")
    if not member:
        await mod_channel.send("❌ Aucun membre résolu.")
        return
    action = action_data.get("action")
    if action == "show_profile":
        await show_profile(mod_channel, member, guild)
        await log_action(guild, "show_profile", moderator, member)
        return
    reason = action_data.get("reason", "Aucune raison spécifiée")
    db = load_db()
    data = get_member_data(db, member.id)
    try:
        if action == "ban":
            await member.ban(reason=reason)
            data["bans"] += 1
        elif action == "kick":
            await member.kick(reason=reason)
            data["kicks"] += 1
        elif action == "mute":
            duration = action_data.get("duration_minutes") or 10
            await member.timeout(timedelta(minutes=duration), reason=reason)
            data["mutes"] += 1
        elif action == "unmute":
            await member.timeout(None)
            if data.get("spam_mute_count", 0) >= 3 or data["warns"] >= 3:
                data["warns"] = 1
                data["spam_mute_count"] = 0
        elif action == "unban":
            await guild.unban(member)
        elif action == "warn":
            data["warns"] = min(data["warns"] + 1, 3)
            data["total_warns"] += 1
            try:
                await member.send(f"⚠️ Tu as reçu un avertissement sur **{guild.name}** : {reason}")
            except:
                pass
        elif action == "delete_messages":
            count = action_data.get("count") or 10
            deleted = 0
            async for msg in mod_channel.history(limit=200):
                if msg.author == member and deleted < count:
                    await msg.delete()
                    deleted += 1
        if action not in ["unmute", "unban", "delete_messages", "show_profile"]:
            data["sanctions"].append({
                "type": action,
                "reason": reason,
                "date": datetime.now(timezone.utc).isoformat(),
                "duration": action_data.get("duration_minutes")
            })
        save_db(db)
        color = ACTION_COLORS.get(action, 0x2ecc71)
        label = ACTION_LABELS.get(action, action)
        duration = action_data.get("duration_minutes")
        embed = discord.Embed(title=f"✅ Action effectuée — {label}", color=color)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Utilisateur", value=f"{member.mention}", inline=True)
        if duration and action == "mute":
            embed.add_field(name="Durée", value=f"{duration} minutes", inline=True)
        if action not in ["unmute", "unban", "show_profile"]:
            embed.add_field(name="Raison", value=reason, inline=False)
        if action == "warn":
            embed.add_field(name="Avertissements", value=f"{data['warns']}/3", inline=True)
        embed.set_footer(text=f"ID : {member.id}")
        await mod_channel.send(embed=embed)
        if action != "show_profile":
            await log_action(guild, action, moderator, member, reason=reason,
                             extra={"Durée": f"{duration} min" if duration else None})
    except discord.Forbidden:
        await mod_channel.send(embed=discord.Embed(
            title="❌ Permission refusée",
            description=f"Je n'ai pas les permissions pour agir sur **{member.display_name}**.",
            color=0xe74c3c
        ))
    except Exception as e:
        await mod_channel.send(f"❌ Erreur : {e}")

# ============================================================
# PROFIL MEMBRE
# ============================================================
async def analyze_member_messages(guild, member):
    messages = []
    public_channels = [ch for ch in guild.text_channels if ch.permissions_for(guild.default_role).read_messages]
    for channel in public_channels:
        try:
            async for msg in channel.history(limit=200):
                if msg.author.id == member.id:
                    messages.append(msg)
                if len(messages) >= 50:
                    break
        except:
            continue
        if len(messages) >= 50:
            break
    activity_days = defaultdict(int)
    for msg in messages:
        day = msg.created_at.strftime("%Y-%m-%d")
        activity_days[day] += 1
    if activity_days:
        avg = round(sum(activity_days.values()) / len(activity_days), 1)
        last = max(activity_days.keys())
        days_since = (datetime.now() - datetime.strptime(last, "%Y-%m-%d")).days
        if days_since == 0:
            status = "🟢 Actif aujourd'hui"
        elif days_since <= 3:
            status = f"🟡 Actif il y a {days_since} jours"
        elif days_since <= 7:
            status = f"🟠 Peu actif ({days_since} jours)"
        else:
            status = f"🔴 Inactif ({days_since} jours)"
    else:
        avg, status = 0, "⚫ Aucune activité détectée"
    ai_analysis = "Aucun message à analyser."
    if messages:
        msgs_text = "\n".join([f"- {m.content}" for m in messages[:50] if m.content])
        try:
            r = ai_client.chat.completions.create(
                model="meta-llama/llama-3.1-8b-instruct:free",
                messages=[{"role": "user", "content": f"{ANALYSIS_PROMPT}\n\nMessages :\n{msgs_text}"}]
            )
            ai_analysis = r.choices[0].message.content.strip()
        except:
            ai_analysis = "Analyse indisponible."
    return {"status": status, "avg": avg, "total": len(messages), "ai": ai_analysis}

async def show_profile(channel, member, guild, show_mod_data=True):
    loading = discord.Embed(
        title=f"🔍 Analyse de {member.display_name} en cours...",
        description="Patiente quelques secondes...",
        color=0x3498db
    )
    msg = await channel.send(embed=loading)
    data_msg = await analyze_member_messages(guild, member)
    db = load_db()
    data = get_member_data(db, member.id)
    roles = [r.mention for r in member.roles if r.name != "@everyone"]
    roles_text = ", ".join(roles) if roles else "Aucun rôle"
    joined = member.joined_at.strftime("%d/%m/%Y") if member.joined_at else "Inconnu"
    created = member.created_at.strftime("%d/%m/%Y")
    level, current_xp, needed_xp = get_level_from_xp(data["xp"])
    progress = int((current_xp / needed_xp) * 10) if needed_xp > 0 else 0
    progress_bar = "█" * progress + "░" * (10 - progress)
    embed = discord.Embed(title=f"👤 Profil — {member.display_name}", color=0x3498db)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="🏷️ Pseudo", value=f"{member.name}", inline=True)
    embed.add_field(name="🆔 ID", value=f"`{member.id}`", inline=True)
    embed.add_field(name="📅 Compte créé", value=created, inline=True)
    embed.add_field(name="📥 A rejoint le", value=joined, inline=True)
    embed.add_field(name="🎭 Rôles", value=roles_text, inline=False)
    embed.add_field(
        name="⭐ Niveau & XP",
        value=f"Niveau **{level}** — {current_xp}/{needed_xp} XP\n`{progress_bar}`",
        inline=False
    )
    embed.add_field(name="🪙 Pièces", value=str(data["coins"]), inline=True)
    embed.add_field(name="🔥 Streak daily", value=f"{data['daily_streak']} jours", inline=True)
    equipped = data.get("equipped", [])
    embed.add_field(name="👗 Rôle équipé", value=", ".join(equipped) if equipped else "Aucun", inline=True)
    embed.add_field(
        name="📊 Activité",
        value=f"{data_msg['status']}\n~{data_msg['avg']} msgs/jour • {data_msg['total']} analysés",
        inline=False
    )
    embed.add_field(name="🤖 Appréciation IA", value=data_msg["ai"], inline=False)
    if show_mod_data:
        sanction_status = []
        if member.is_timed_out():
            until = member.timed_out_until
            if until:
                remaining = until - datetime.now(timezone.utc)
                mins = int(remaining.total_seconds() // 60)
                if mins > 1440:
                    sanction_status.append(f"🔇 **Muté** — {mins // 1440}j {(mins % 1440) // 60}h restantes")
                elif mins > 60:
                    sanction_status.append(f"🔇 **Muté** — {mins // 60}h{mins % 60}min restantes")
                else:
                    sanction_status.append(f"🔇 **Muté** — {mins} min restantes")
            else:
                sanction_status.append("🔇 **Muté** (durée inconnue)")
        if not sanction_status:
            sanction_status.append("✅ Aucune sanction active")
        embed.add_field(name="⚡ Statut actuel", value="\n".join(sanction_status), inline=False)
        embed.add_field(
            name="🛡️ Historique sanctions",
            value=(
                f"⚠️ Warns actuels : **{data['warns']}/3** (total : {data['total_warns']})\n"
                f"🔇 Mutes : {data['mutes']} | 👢 Kicks : {data['kicks']} | 🔨 Bans : {data['bans']}"
            ),
            inline=False
        )
        if data.get("comments"):
            comments_text = "\n".join([f"• {c}" for c in data["comments"]])
            embed.add_field(name="💬 Commentaires modos", value=comments_text, inline=False)
    embed.set_footer(text=f"Analyse basée sur {data_msg['total']} messages publics")
    await msg.edit(embed=embed)
    if show_mod_data:
        action_embed = discord.Embed(
            title="💬 Gestion des commentaires",
            description="➕ pour ajouter un commentaire\n➖ pour supprimer un commentaire",
            color=0x3498db
        )
        action_msg = await channel.send(embed=action_embed)
        await action_msg.add_reaction("➕")
        await action_msg.add_reaction("➖")
        waiting_for_action_choice[action_msg.id] = ("comment_mgmt", member, None, None)

# ============================================================
# COMMANDES BOUTIQUE / JEUX
# ============================================================
async def cmd_profil(message):
    db = load_db()
    data = get_member_data(db, message.author.id)
    level, current_xp, needed_xp = get_level_from_xp(data["xp"])
    progress = int((current_xp / needed_xp) * 10) if needed_xp > 0 else 0
    progress_bar = "█" * progress + "░" * (10 - progress)
    embed = discord.Embed(title=f"👤 Profil — {message.author.display_name}", color=0x3498db)
    embed.set_thumbnail(url=message.author.display_avatar.url)
    embed.add_field(name="⭐ Niveau", value=str(level), inline=True)
    embed.add_field(name="✨ XP", value=f"{current_xp}/{needed_xp}", inline=True)
    embed.add_field(name="🪙 Pièces", value=str(data["coins"]), inline=True)
    embed.add_field(name="📊 Progression", value=f"`{progress_bar}`", inline=False)
    embed.add_field(name="🔥 Streak daily", value=f"{data['daily_streak']} jours", inline=True)
    equipped = data.get("equipped", [])
    embed.add_field(name="👗 Rôle équipé", value=", ".join(equipped) if equipped else "Aucun", inline=True)
    await message.channel.send(embed=embed)

async def cmd_inventaire(message):
    db = load_db()
    data = get_member_data(db, message.author.id)
    inventory = data.get("inventory", [])
    embed = discord.Embed(title=f"🎒 Inventaire — {message.author.display_name}", color=0x9b59b6)
    if not inventory:
        embed.description = "Tu n'as aucun article dans ton inventaire."
    else:
        items_text = "\n".join([f"• **{item['name']}**" + (f" — expire le {item.get('expires', '?')}" if item.get('expires') else "") for item in inventory])
        embed.description = items_text
    await message.channel.send(embed=embed)

async def cmd_boutique(message):
    shop = load_shop()
    embed = discord.Embed(title="🛍️ Boutique", color=0x2ecc71)
    standard_text = "\n".join([f"• **{i['name']}** — {i['price']} 🪙" for i in shop["standard"]])
    embed.add_field(name="📦 Articles permanents", value=standard_text or "Aucun", inline=False)
    if shop["rotating"]:
        last = shop.get("last_rotate")
        if last:
            dt = datetime.fromisoformat(last)
            next_rotate = dt + timedelta(seconds=SHOP_ROTATE_INTERVAL)
            remaining = next_rotate - datetime.now(timezone.utc)
            mins = int(remaining.total_seconds() // 60)
            rotate_txt = f"Se renouvelle dans **{mins // 60}h{mins % 60}min**"
        else:
            rotate_txt = ""
        rotating_text = "\n".join([f"• **{i['name']}** — {i['price']} 🪙" for i in shop["rotating"]])
        embed.add_field(name=f"🔄 Boutique rotative — {rotate_txt}", value=rotating_text, inline=False)
    embed.set_footer(text="!acheter [nom] pour acheter • !spin pour le gacha (50 🪙)")
    await message.channel.send(embed=embed)

async def cmd_acheter(message, item_name):
    if not item_name:
        await message.channel.send("❌ Usage : `!acheter [nom de l'article]`")
        return
    shop = load_shop()
    all_items = shop["standard"] + shop["rotating"]
    item = next((i for i in all_items if i["name"].lower() == item_name.lower()), None)
    if not item:
        await message.channel.send(f"❌ Article **{item_name}** introuvable dans la boutique.")
        return
    db = load_db()
    data = get_member_data(db, message.author.id)
    if data["coins"] < item["price"]:
        await message.channel.send(f"❌ Tu n'as pas assez de pièces. (Tu as **{data['coins']}** 🪙, il faut **{item['price']}** 🪙)")
        return
    if item.get("duration") is None:
        already = any(i["id"] == item["id"] for i in data["inventory"])
        if already:
            await message.channel.send(f"❌ Tu possèdes déjà **{item['name']}**.")
            return
    data["coins"] -= item["price"]
    inv_item = {"id": item["id"], "name": item["name"], "type": item["type"]}
    if item.get("duration"):
        expires = (datetime.now(timezone.utc) + timedelta(days=item["duration"])).strftime("%d/%m/%Y")
        inv_item["expires"] = expires
    data["inventory"].append(inv_item)
    save_db(db)
    embed = discord.Embed(
        title="✅ Achat réussi !",
        description=f"Tu as acheté **{item['name']}** pour **{item['price']}** 🪙\nSolde restant : **{data['coins']}** 🪙",
        color=0x2ecc71
    )
    await message.channel.send(embed=embed)
    await log_action(message.guild, "shop_buy", None, message.author, extra={"Article": item["name"], "Prix": f"{item['price']} 🪙"})

async def cmd_equiper(message, item_name):
    if not item_name:
        await message.channel.send("❌ Usage : `!équiper [nom du rôle]`")
        return
    db = load_db()
    data = get_member_data(db, message.author.id)
    inventory = data.get("inventory", [])
    item = next((i for i in inventory if i["name"].lower() == item_name.lower()), None)
    if not item:
        await message.channel.send(f"❌ Tu ne possèdes pas **{item_name}**. Achète-le d'abord !")
        return
    data["equipped"] = [item["name"]]
    save_db(db)
    embed = discord.Embed(
        title="👗 Rôle équipé !",
        description=f"Tu as équipé **{item['name']}**.\n⚠️ Demande à un modo d'attribuer le rôle Discord correspondant.",
        color=0x3498db
    )
    await message.channel.send(embed=embed)
    await log_action(message.guild, "shop_equip", None, message.author, extra={"Rôle équipé": item["name"]})

async def cmd_spin(message):
    db = load_db()
    data = get_member_data(db, message.author.id)
    if data["coins"] < GACHA_COST:
        await message.channel.send(f"❌ Tu n'as pas assez de pièces pour le gacha. (Tu as **{data['coins']}** 🪙, il faut **{GACHA_COST}** 🪙)")
        return
    shop = load_shop()
    gacha_pool = shop["gacha"]
    if not gacha_pool:
        await message.channel.send("❌ Le gacha est vide pour l'instant.")
        return
    weights = []
    for item in gacha_pool:
        r = item.get("rarity", "commun")
        if r == "légendaire": weights.append(2)
        elif r == "épique": weights.append(8)
        elif r == "rare": weights.append(20)
        else: weights.append(70)
    won_item = random.choices(gacha_pool, weights=weights, k=1)[0]
    data["coins"] -= GACHA_COST
    already = any(i["id"] == won_item["id"] for i in data["inventory"])
    if not already:
        data["inventory"].append({"id": won_item["id"], "name": won_item["name"], "type": won_item["type"]})
        result_txt = f"Tu as obtenu **{won_item['name']}** !"
    else:
        refund = 10
        data["coins"] += refund
        result_txt = f"Tu as obtenu **{won_item['name']}** (déjà possédé → **+{refund}** 🪙 remboursés)"
    save_db(db)
    rarity = won_item.get("rarity", "commun")
    color = RARITY_COLORS.get(rarity, 0x95a5a6)
    embed = discord.Embed(title="🎰 Résultat du Gacha !", color=color)
    embed.add_field(name="🎁 Récompense", value=result_txt, inline=False)
    embed.add_field(name="✨ Rareté", value=rarity.capitalize(), inline=True)
    embed.add_field(name="🪙 Solde", value=str(data["coins"]), inline=True)
    embed.set_footer(text=f"Coût : {GACHA_COST} 🪙")
    await message.channel.send(embed=embed)
    await log_action(message.guild, "gacha", None, message.author, extra={"Obtenu": won_item["name"], "Rareté": rarity})

async def cmd_classement(message):
    db = load_db()
    members_data = []
    for mid, data in db.items():
        member = message.guild.get_member(int(mid))
        if member:
            level, _, _ = get_level_from_xp(data.get("xp", 0))
            members_data.append((member.display_name, level, data.get("xp", 0), data.get("coins", 0)))
    members_data.sort(key=lambda x: x[2], reverse=True)
    top = members_data[:10]
    embed = discord.Embed(title="🏆 Classement — Top 10", color=0xf1c40f)
    medals = ["🥇", "🥈", "🥉"] + ["4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    lines = []
    for i, (name, level, xp, coins) in enumerate(top):
        lines.append(f"{medals[i]} **{name}** — Niv. {level} • {xp} XP • {coins} 🪙")
    embed.description = "\n".join(lines) if lines else "Aucun membre classé."
    await message.channel.send(embed=embed)

async def cmd_daily(message):
    channel_name = message.channel.name.lower().replace("・", "")
    if "daily" not in channel_name:
        daily_ch = get_channel_by_name(message.guild, "daily")
        if daily_ch:
            await message.channel.send(f"❌ La commande `!daily` est réservée à {daily_ch.mention} !")
        return
    db = load_db()
    data = get_member_data(db, message.author.id)
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    last = data.get("last_daily")
    if last == today:
        await message.channel.send(f"⏳ Tu as déjà récupéré ta récompense aujourd'hui ! Reviens demain.")
        return
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    if last == yesterday:
        data["daily_streak"] += 1
    else:
        data["daily_streak"] = 1
    streak = data["daily_streak"]
    multiplier = 1.0
    for days, mult in sorted(STREAK_MULTIPLIERS.items()):
        if streak >= days:
            multiplier = mult
    coins_earned = int(DAILY_BASE_COINS * multiplier)
    xp_earned = int(20 * multiplier)
    data["coins"] += coins_earned
    data["xp"] += xp_earned
    data["last_daily"] = today
    save_db(db)
    embed = discord.Embed(title="🎁 Récompense quotidienne !", color=0xf39c12)
    embed.set_thumbnail(url=message.author.display_avatar.url)
    embed.add_field(name="🪙 Pièces gagnées", value=str(coins_earned), inline=True)
    embed.add_field(name="✨ XP gagnés", value=str(xp_earned), inline=True)
    embed.add_field(name="🔥 Streak", value=f"{streak} jours", inline=True)
    if multiplier > 1.0:
        embed.add_field(name="⚡ Bonus streak", value=f"x{multiplier}", inline=True)
    embed.add_field(name="🪙 Solde total", value=str(data["coins"]), inline=True)
    embed.set_footer(text="Reviens demain pour continuer ton streak !")
    await message.channel.send(embed=embed)
    await log_action(message.guild, "daily", None, message.author, extra={"Pièces": coins_earned, "Streak": streak})

async def cmd_parrainer(message, args):
    mentions = message.mentions
    if not mentions:
        await message.channel.send("❌ Usage : `!parrainer @pseudo`")
        return
    target = mentions[0]
    if target.id == message.author.id:
        await message.channel.send("❌ Tu ne peux pas te parrainer toi-même !")
        return
    db = load_db()
    data_author = get_member_data(db, message.author.id)
    data_target = get_member_data(db, target.id)
    if data_target.get("godfather"):
        await message.channel.send(f"❌ **{target.display_name}** a déjà un parrain.")
        return
    bonus = 100
    data_author["coins"] += bonus
    data_target["coins"] += bonus
    data_target["godfather"] = str(message.author.id)
    save_db(db)
    embed = discord.Embed(
        title="🤝 Parrainage réussi !",
        description=f"**{message.author.display_name}** a parrainé **{target.display_name}** !\nVous recevez chacun **{bonus}** 🪙",
        color=0x2ecc71
    )
    await message.channel.send(embed=embed)

# ============================================================
# CONFIRMATION
# ============================================================
async def send_confirmation(channel, action_data, author_id):
    action = action_data.get("action")
    duration = action_data.get("duration_minutes")
    reason = action_data.get("reason")
    member = action_data.get("resolved_member")
    label = ACTION_LABELS.get(action, action)
    color = ACTION_COLORS.get(action, 0xf39c12)
    embed = discord.Embed(title=f"⚠️ Confirmation requise — {label}", color=color)
    if member:
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Utilisateur", value=f"{member.mention}", inline=True)
    if duration:
        embed.add_field(name="Durée", value=f"{duration} minutes", inline=True)
    if reason:
        embed.add_field(name="Raison", value=reason, inline=False)
    embed.set_footer(text="✅ confirmer — ❌ annuler • Expire dans 30s")
    bot_msg = await channel.send(embed=embed)
    await bot_msg.add_reaction("✅")
    await bot_msg.add_reaction("❌")
    pending_actions[bot_msg.id] = (action_data, author_id)
    await asyncio.sleep(30)
    if bot_msg.id in pending_actions:
        pending_actions.pop(bot_msg.id)
        await channel.send(embed=discord.Embed(
            title="⏱️ Confirmation expirée",
            description="Action annulée automatiquement.",
            color=0x95a5a6
        ))

# ============================================================
# CHOIX MEMBRE / ACTION
# ============================================================
async def ask_action_choice(channel, member, action_data, author_id):
    embed = discord.Embed(
        title=f"👤 {member.display_name} trouvé !",
        description="Que veux-tu faire ?",
        color=0x3498db
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="⚔️ Sanctionner", value="Applique une sanction", inline=True)
    embed.add_field(name="🔍 Voir le profil", value="Analyse complète + IA", inline=True)
    embed.set_footer(text="⚔️ sanctionner — 🔍 profil — ❌ annuler")
    bot_msg = await channel.send(embed=embed)
    await bot_msg.add_reaction("⚔️")
    await bot_msg.add_reaction("🔍")
    await bot_msg.add_reaction("❌")
    waiting_for_action_choice[bot_msg.id] = ("sanction_or_profile", member, action_data, author_id)

async def ask_member_choice(channel, action_data, author_id, candidates):
    embed = discord.Embed(
        title="🔍 Plusieurs membres trouvés",
        description="Réagis avec le numéro correspondant au bon membre :",
        color=0x3498db
    )
    emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
    for i, m in enumerate(candidates[:5]):
        embed.add_field(name=f"{emojis[i]} {m.display_name}", value=f"`{m.name}` • ID: {m.id}", inline=False)
    embed.set_footer(text="❌ pour annuler")
    bot_msg = await channel.send(embed=embed)
    for i in range(len(candidates[:5])):
        await bot_msg.add_reaction(emojis[i])
    await bot_msg.add_reaction("❌")
    waiting_for_member_choice[bot_msg.id] = (action_data, author_id, candidates[:5])

async def handle_member_resolution(channel, action_data, author_id, exact, similar, is_id=False, is_banned=False):
    all_candidates = exact + similar
    if not all_candidates:
        await channel.send(embed=discord.Embed(
            title="❌ Membre introuvable",
            description=f"Aucun membre trouvé pour **{action_data.get('target')}**.",
            color=0xe74c3c
        ))
        return

    if is_id and len(exact) == 1:
        action_data["resolved_member"] = exact[0]
        action_data["is_banned"] = is_banned
        if is_banned:
            user = exact[0]
            embed = discord.Embed(
                title=f"🔨 {user.display_name} est banni",
                description="Cet utilisateur est actuellement banni du serveur.",
                color=0xe74c3c
            )
            embed.set_thumbnail(url=user.display_avatar.url)
            embed.add_field(name="🆔 ID", value=f"`{user.id}`", inline=True)
            embed.set_footer(text="✅ débannir — 🔍 voir profil — ❌ annuler")
            bot_msg = await channel.send(embed=embed)
            await bot_msg.add_reaction("✅")
            await bot_msg.add_reaction("🔍")
            await bot_msg.add_reaction("❌")
            waiting_for_action_choice[bot_msg.id] = ("banned_choice", user, action_data, author_id)
            return
        if action_data.get("action") == "show_profile":
            await show_profile(channel, exact[0], channel.guild)
            return
        if action_data.get("reason") and action_data.get("action") in ["ban", "kick", "mute", "warn", "delete_messages"]:
            await send_confirmation(channel, action_data, author_id)
        elif action_data.get("action") in ["ban", "kick", "mute", "warn", "delete_messages"]:
            await channel.send(embed=discord.Embed(
                title="📝 Raison de la sanction",
                description="Quelle est la raison de cette sanction ?",
                color=0x3498db
            ))
            waiting_for_reason[author_id] = action_data
        else:
            await send_confirmation(channel, action_data, author_id)
        return

    if len(exact) == 1 and not similar:
        action_data["resolved_member"] = exact[0]
        if action_data.get("action") == "show_profile":
            await show_profile(channel, exact[0], exact[0].guild)
            return
        await ask_action_choice(channel, exact[0], action_data, author_id)
        return

    if len(all_candidates) == 1:
        action_data["resolved_member"] = all_candidates[0]
        m = all_candidates[0]
        embed = discord.Embed(
            title="🔍 Membre similaire trouvé",
            description=f"Voulais-tu dire **{m.display_name}** ?",
            color=0xf39c12
        )
        embed.set_thumbnail(url=m.display_avatar.url)
        embed.set_footer(text="✅ confirmer — ❌ annuler")
        bot_msg = await channel.send(embed=embed)
        await bot_msg.add_reaction("✅")
        await bot_msg.add_reaction("❌")
        waiting_for_member_choice[bot_msg.id] = (action_data, author_id, [m])
        return

    await ask_member_choice(channel, action_data, author_id, all_candidates)

# ============================================================
# RAPPORT QUOTIDIEN
# ============================================================
async def send_daily_report(guild):
    report_ch = get_channel_by_name(guild, "rapport-prowler")
    if not report_ch:
        return
    db = load_db()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total_bans = total_kicks = total_mutes = total_warns = 0
    for mid, data in db.items():
        for s in data.get("sanctions", []):
            if s.get("date", "").startswith(today):
                t = s.get("type", "")
                if t == "ban": total_bans += 1
                elif t == "kick": total_kicks += 1
                elif t in ["mute", "spam_mute"]: total_mutes += 1
                elif t == "warn": total_warns += 1
    embed = discord.Embed(
        title=f"📝 Rapport de modération — {today}",
        color=0x3498db,
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(name="🔨 Bans", value=str(total_bans), inline=True)
    embed.add_field(name="👢 Kicks", value=str(total_kicks), inline=True)
    embed.add_field(name="🔇 Mutes", value=str(total_mutes), inline=True)
    embed.add_field(name="⚠️ Warns", value=str(total_warns), inline=True)
    embed.set_footer(text="Rapport automatique quotidien")
    await report_ch.send(embed=embed)

async def daily_report_loop():
    await client.wait_until_ready()
    while not client.is_closed():
        now = datetime.now(timezone.utc)
        next_run = now.replace(hour=REPORT_HOUR, minute=0, second=0, microsecond=0)
        if now >= next_run:
            next_run += timedelta(days=1)
        await asyncio.sleep((next_run - now).total_seconds())
        for guild in client.guilds:
            await send_daily_report(guild)

# ============================================================
# BOUTIQUE ROTATIVE LOOP
# ============================================================
async def shop_rotate_loop():
    await client.wait_until_ready()
    shop = load_shop()
    if not shop["rotating"]:
        rotate_shop()
    while not client.is_closed():
        shop = load_shop()
        last = shop.get("last_rotate")
        if last:
            dt = datetime.fromisoformat(last)
            next_rotate = dt + timedelta(seconds=SHOP_ROTATE_INTERVAL)
            wait = (next_rotate - datetime.now(timezone.utc)).total_seconds()
            if wait > 0:
                await asyncio.sleep(wait)
        else:
            await asyncio.sleep(SHOP_ROTATE_INTERVAL)
        new_items = rotate_shop()
        for guild in client.guilds:
            boutique_ch = get_channel_by_name(guild, "boutique")
            if boutique_ch:
                embed = discord.Embed(
                    title="🔄 La boutique rotative s'est renouvelée !",
                    description="\n".join([f"• **{i['name']}** — {i['price']} 🪙" for i in new_items]),
                    color=0x2ecc71
                )
                embed.set_footer(text="!acheter [nom] pour acheter")
                await boutique_ch.send(embed=embed)

# ============================================================
# RÔLE MEMBRE ACTIF
# ============================================================
async def update_active_roles_loop():
    await client.wait_until_ready()
    while not client.is_closed():
        await asyncio.sleep(3600)
        for guild in client.guilds:
            role_actif = discord.utils.get(guild.roles, name=ROLE_MEMBRE_ACTIF)
            role_membre = discord.utils.get(guild.roles, name=ROLE_MEMBRE)
            if not role_actif or not role_membre:
                continue
            today = datetime.now(timezone.utc).date()
            for mid, days_data in member_message_days.items():
                member = guild.get_member(int(mid))
                if not member:
                    continue
                active_streak = 0
                for i in range(ACTIVE_DAYS_REQUIRED):
                    day = (today - timedelta(days=i)).isoformat()
                    if days_data.get(day, 0) >= ACTIVE_MESSAGES_PER_DAY:
                        active_streak += 1
                inactive_streak = 0
                for i in range(INACTIVE_DAYS_REQUIRED):
                    day = (today - timedelta(days=i)).isoformat()
                    if days_data.get(day, 0) == 0:
                        inactive_streak += 1
                has_actif = role_actif in member.roles
                if active_streak >= ACTIVE_DAYS_REQUIRED and not has_actif:
                    try:
                        await member.add_roles(role_actif)
                        if role_membre in member.roles:
                            await member.remove_roles(role_membre)
                    except:
                        pass
                elif inactive_streak >= INACTIVE_DAYS_REQUIRED and has_actif:
                    try:
                        await member.remove_roles(role_actif)
                        if role_membre not in member.roles:
                            await member.add_roles(role_membre)
                    except:
                        pass

# ============================================================
# HELP
# ============================================================
async def send_help(channel):
    import unicodedata
    channel_name = unicodedata.normalize("NFD", channel.name.lower().replace("・", "")).encode("ascii", "ignore").decode("ascii")
    embed = discord.Embed(color=0x3498db, timestamp=datetime.now(timezone.utc))

    if "jeux" in channel_name:
        embed.title = "📖 Commandes — 🎮・jeux"
        embed.description = (
            "**Profil & Stats**\n"
            "`!profil` — voir ton niveau, pièces, rôles équipés\n"
            "`!inventaire` — voir tous tes rôles achetés\n"
            "`!classement` — top des membres les plus actifs\n\n"
            "**Social**\n"
            "`!parrainer @pseudo` — parrainer un ami\n\n"
            "💡 Boutique → 🛍️・boutique\n"
            "🎁 Récompense quotidienne → 🎁・daily"
        )
    elif "boutique" in channel_name:
        embed.title = "📖 Commandes — 🛍️・boutique"
        embed.description = (
            "**Boutique & Gacha**\n"
            "`!boutique` — voir la boutique standard et rotative\n"
            "`!acheter [nom]` — acheter un article\n"
            "`!équiper [nom]` — équiper un rôle cosmétique\n"
            "`!spin` — tenter le gacha (50 🪙)\n\n"
            "💡 La boutique rotative se renouvelle toutes les **3h**"
        )
    elif "daily" in channel_name:
        embed.title = "📖 Commandes — 🎁・daily"
        embed.description = (
            "`!daily` — récupère ta récompense quotidienne\n\n"
            "🔥 **Streak bonus :**\n"
            "3 jours de suite → x1.5\n"
            "7 jours de suite → x2\n"
            "14 jours de suite → x2.5\n"
            "30 jours de suite → x3\n\n"
            "⚠️ Si tu rates un jour, ton streak repart à 0 !"
        )
    elif "modération" in channel_name or "moderation" in channel_name:
        embed.title = "📖 Commandes — Modération"
        embed.description = (
            "Tu peux écrire en **langage naturel** :\n\n"
            "• `mute @pseudo 30 minutes` — mute un membre\n"
            "• `ban @pseudo` — bannit un membre\n"
            "• `kick @pseudo` — kick un membre\n"
            "• `warn @pseudo` — avertit un membre\n"
            "• `unmute @pseudo` — démute un membre\n"
            "• `unban @pseudo` — débannit un membre\n"
            "• `supprime 10 messages de @pseudo` — supprime ses messages\n"
            "• `profil de @pseudo` — voir le profil complet d'un membre"
        )
    elif "log" in channel_name:
        embed.title = "📖 Lecture des logs"
        embed.description = (
            "Les logs enregistrent automatiquement :\n\n"
            "🔨 Bans • 👢 Kicks • 🔇 Mutes • ⚠️ Warns\n"
            "🔊 Demutes • ✅ Débans • 📥 Arrivées • 📤 Départs\n"
            "💬 Commentaires modos • 🤖 Mutes anti-spam\n"
            "🛍️ Achats • 🎰 Gacha • 🎁 Daily"
        )
    else:
        embed.title = "📖 Aide — Prowler Bot"
        embed.description = (
            "**Salons disponibles :**\n\n"
            "🎮・jeux — profil, classement, social\n"
            "🛍️・boutique — boutique, gacha, achats\n"
            "🎁・daily — récompense quotidienne\n\n"
            "Tape `?help` dans ces salons pour les commandes détaillées."
        )

    embed.set_footer(text="Prowler Bot")
    await channel.send(embed=embed)

# ============================================================
# ÉVÉNEMENTS
# ============================================================
@client.event
async def on_ready():
    print(f"✅ Bot connecté en tant que {client.user}")
    client.loop.create_task(daily_report_loop())
    client.loop.create_task(update_active_roles_loop())
    client.loop.create_task(shop_rotate_loop())

@client.event
async def on_member_join(member):
    guild = member.guild
    role = discord.utils.get(guild.roles, name=ROLE_MEMBRE)
    if role:
        try:
            await member.add_roles(role)
        except:
            pass
    general = get_channel_by_name(guild, "chat-général")
    if general:
        await general.send(f"👋 Bienvenue sur le serveur, {member.mention} !")
    await log_action(guild, "join", None, member)

@client.event
async def on_member_remove(member):
    await log_action(member.guild, "leave", None, member)

@client.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return
    msg_id = reaction.message.id

    if msg_id in waiting_for_action_choice:
        choice_type, member, action_data, requester_id = waiting_for_action_choice[msg_id]

        if choice_type == "banned_choice":
            if user.id != requester_id:
                return
            waiting_for_action_choice.pop(msg_id, None)
            if str(reaction.emoji) == "✅":
                action_data["action"] = "unban"
                await send_confirmation(reaction.message.channel, action_data, requester_id)
            elif str(reaction.emoji) == "🔍":
                db = load_db()
                data = get_member_data(db, member.id)
                embed = discord.Embed(title=f"👤 Profil (banni) — {member.display_name}", color=0xe74c3c)
                embed.set_thumbnail(url=member.display_avatar.url)
                embed.add_field(name="🏷️ Pseudo", value=f"{member.name}", inline=True)
                embed.add_field(name="🆔 ID", value=f"`{member.id}`", inline=True)
                embed.add_field(name="⚡ Statut actuel", value="🔨 **Banni du serveur**", inline=False)
                embed.add_field(
                    name="🛡️ Historique sanctions",
                    value=(
                        f"⚠️ Warns total : {data['total_warns']}\n"
                        f"🔇 Mutes : {data['mutes']} | 👢 Kicks : {data['kicks']} | 🔨 Bans : {data['bans']}"
                    ),
                    inline=False
                )
                if data.get("comments"):
                    embed.add_field(name="💬 Commentaires modos", value="\n".join([f"• {c}" for c in data["comments"]]), inline=False)
                await reaction.message.channel.send(embed=embed)
            elif str(reaction.emoji) == "❌":
                await reaction.message.channel.send(embed=discord.Embed(title="❌ Action annulée", color=0x95a5a6))
            return

        if choice_type == "sanction_or_profile":
            if user.id != requester_id:
                return
            waiting_for_action_choice.pop(msg_id)
            if str(reaction.emoji) == "⚔️":
                if action_data.get("action") in ["ban", "kick", "mute", "warn", "delete_messages"]:
                    await reaction.message.channel.send(embed=discord.Embed(
                        title="📝 Raison de la sanction",
                        description="Quelle est la raison de cette sanction ?",
                        color=0x3498db
                    ))
                    waiting_for_reason[requester_id] = action_data
                else:
                    await send_confirmation(reaction.message.channel, action_data, requester_id)
            elif str(reaction.emoji) == "🔍":
                await show_profile(reaction.message.channel, member, reaction.message.guild)
            elif str(reaction.emoji) == "❌":
                await reaction.message.channel.send(embed=discord.Embed(title="❌ Action annulée", color=0x95a5a6))

        elif choice_type == "comment_mgmt":
            mod_member = reaction.message.guild.get_member(user.id)
            if not mod_member or not has_permission(mod_member):
                return
            waiting_for_action_choice.pop(msg_id)
            if str(reaction.emoji) == "➕":
                await reaction.message.channel.send(embed=discord.Embed(
                    title="💬 Ajouter un commentaire",
                    description=f"Écris ton commentaire pour **{member.display_name}** :",
                    color=0x3498db
                ))
                waiting_for_comment[user.id] = (member.id, "add", None)
            elif str(reaction.emoji) == "➖":
                db = load_db()
                data = get_member_data(db, member.id)
                comments = data.get("comments", [])
                if not comments:
                    await reaction.message.channel.send("Aucun commentaire à supprimer.")
                    return
                emojis_c = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
                embed = discord.Embed(title="🗑️ Supprimer un commentaire", color=0xe74c3c)
                for i, c in enumerate(comments[:5]):
                    embed.add_field(name=f"{emojis_c[i]}", value=c, inline=False)
                cmsg = await reaction.message.channel.send(embed=embed)
                for i in range(len(comments[:5])):
                    await cmsg.add_reaction(emojis_c[i])
                waiting_for_comment[user.id] = (member.id, "remove_pick", cmsg.id)
                waiting_for_action_choice[cmsg.id] = ("comment_remove_pick", member, None, user.id)

        elif choice_type == "comment_remove_pick":
            if user.id != requester_id:
                return
            emojis_c = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
            if str(reaction.emoji) in emojis_c:
                idx = emojis_c.index(str(reaction.emoji))
                db = load_db()
                data = get_member_data(db, member.id)
                comments = data.get("comments", [])
                if idx < len(comments):
                    removed = comments.pop(idx)
                    save_db(db)
                    waiting_for_action_choice.pop(msg_id, None)
                    waiting_for_comment.pop(user.id, None)
                    await reaction.message.channel.send(embed=discord.Embed(
                        title="✅ Commentaire supprimé",
                        description=f"Le commentaire a été supprimé.",
                        color=0x2ecc71
                    ))
                    target = reaction.message.guild.get_member(member.id)
                    await log_action(reaction.message.guild, "comment_remove", mod_member if 'mod_member' in dir() else None, target, extra={"Commentaire supprimé": removed})
        return

    if msg_id in waiting_for_member_choice:
        action_data, requester_id, candidates = waiting_for_member_choice[msg_id]
        if user.id != requester_id:
            return
        emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
        if str(reaction.emoji) == "✅" and len(candidates) == 1:
            waiting_for_member_choice.pop(msg_id)
            action_data["resolved_member"] = candidates[0]
            if action_data.get("action") == "show_profile":
                await show_profile(reaction.message.channel, candidates[0], reaction.message.guild)
            else:
                await ask_action_choice(reaction.message.channel, candidates[0], action_data, requester_id)
            return
        if str(reaction.emoji) == "❌":
            waiting_for_member_choice.pop(msg_id)
            await reaction.message.channel.send(embed=discord.Embed(title="❌ Action annulée", color=0x95a5a6))
            return
        if str(reaction.emoji) in emojis:
            idx = emojis.index(str(reaction.emoji))
            if idx < len(candidates):
                waiting_for_member_choice.pop(msg_id)
                action_data["resolved_member"] = candidates[idx]
                if action_data.get("action") == "show_profile":
                    await show_profile(reaction.message.channel, candidates[idx], reaction.message.guild)
                else:
                    await ask_action_choice(reaction.message.channel, candidates[idx], action_data, requester_id)
        return

    if msg_id in pending_actions:
        action_data, requester_id = pending_actions[msg_id]
        if user.id != requester_id:
            return
        if str(reaction.emoji) == "✅":
            pending_actions.pop(msg_id)
            mod = reaction.message.guild.get_member(user.id)
            await execute_action(reaction.message.guild, action_data, reaction.message.channel, moderator=mod)
        elif str(reaction.emoji) == "❌":
            pending_actions.pop(msg_id)
            await reaction.message.channel.send(embed=discord.Embed(title="❌ Action annulée", color=0x95a5a6))

@client.event
async def on_message(message):
    if message.author.bot:
        return

    import unicodedata
    def normalize_name(s):
        s = s.lower().replace("・", "")
        return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode("ascii")
    channel_name = normalize_name(message.channel.name)
    content = message.content.strip()
    content_lower = content.lower()

    # --- Suivi activité membre ---
    mid = str(message.author.id)
    today = datetime.now(timezone.utc).date().isoformat()
    if mid not in member_message_days:
        member_message_days[mid] = {}
    member_message_days[mid][today] = member_message_days[mid].get(today, 0) + 1

    # --- XP & pièces sur chaque message ---
    is_boosted = update_boost(message.author.id)
    coin_gain = COINS_BOOST if is_boosted else COINS_PER_MESSAGE
    await add_xp_and_coins(message.author, message.guild, XP_PER_MESSAGE, coin_gain)

    # --- Commande !help / ?help ---
    if content_lower in ["!help", "?help"]:
        await send_help(message.channel)
        return

    # --- Commandes salon jeux ---
    if "jeux" in channel_name:
        if content_lower == "!profil":
            await cmd_profil(message)
            return
        if content_lower == "!inventaire":
            await cmd_inventaire(message)
            return
        if content_lower == "!classement":
            await cmd_classement(message)
            return
        if content_lower.startswith("!parrainer"):
            await cmd_parrainer(message, content[10:].strip())
            return
        if content_lower in ["!boutique", "!spin"] or content_lower.startswith("!acheter") or content_lower.startswith("!équiper"):
            boutique_ch = get_channel_by_name(message.guild, "boutique")
            if boutique_ch:
                await message.channel.send(f"❌ Cette commande est réservée à {boutique_ch.mention} !")
            return
        if content_lower == "!daily":
            daily_ch = get_channel_by_name(message.guild, "daily")
            if daily_ch:
                await message.channel.send(f"❌ La commande `!daily` est réservée à {daily_ch.mention} !")
            return

    # --- Commandes salon boutique ---
    if "boutique" in channel_name:
        if content_lower == "!boutique":
            await cmd_boutique(message)
            return
        if content_lower.startswith("!acheter "):
            await cmd_acheter(message, content[9:].strip())
            return
        if content_lower.startswith("!équiper "):
            await cmd_equiper(message, content[9:].strip())
            return
        if content_lower == "!spin":
            await cmd_spin(message)
            return

    # --- Commande daily ---
    if content_lower == "!daily":
        await cmd_daily(message)
        return

    # --- Salon modération uniquement pour les commandes de mod ---
    if "moderation" not in channel_name:
        return

    if not has_permission(message.author):
        await message.channel.send(embed=discord.Embed(
            title="❌ Permission refusée",
            description="Tu n'as pas la permission d'utiliser le bot de modération.",
            color=0xe74c3c
        ))
        return

    # --- Attente de commentaire ---
    if message.author.id in waiting_for_comment:
        member_id, action, extra = waiting_for_comment[message.author.id]
        if action == "add":
            waiting_for_comment.pop(message.author.id)
            db = load_db()
            data = get_member_data(db, member_id)
            comment_text = f"[{datetime.now(timezone.utc).strftime('%d/%m/%Y')}] {message.author.display_name} : {message.content}"
            data["comments"].append(comment_text)
            save_db(db)
            target = message.guild.get_member(member_id)
            await message.channel.send(embed=discord.Embed(
                title="✅ Commentaire ajouté", description=comment_text, color=0x2ecc71
            ))
            await log_action(message.guild, "comment_add", message.author, target, extra={"Commentaire": message.content})
        return

    # --- Attente de raison ---
    if message.author.id in waiting_for_reason:
        action_data = waiting_for_reason.pop(message.author.id)
        async with message.channel.typing():
            refined = await reformulate_reason(message.content)
        action_data["reason"] = refined
        await send_confirmation(message.channel, action_data, message.author.id)
        return

    # --- Anti-spam ---
    if not has_permission(message.author):
        spammed = await check_spam(message)
        if spammed:
            return

    # --- Analyse IA de la commande ---
    action_data = None
    try:
        async with message.channel.typing():
            r = ai_client.chat.completions.create(
                model="meta-llama/llama-3.1-8b-instruct:free",
                messages=[{"role": "user", "content": f"{SYSTEM_PROMPT}\n\nMessage du modérateur: {message.content}"}]
            )
            raw = r.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()
            action_data = json.loads(raw)
    except json.JSONDecodeError:
        # Réponse IA mal formée → traiter le message brut comme un pseudo
        action_data = {"action": "none", "target": message.content.strip(), "needs_clarification": False}
    except Exception as e:
        await message.channel.send(embed=discord.Embed(
            title="❌ Erreur IA",
            description=f"```{e}```",
            color=0xe74c3c
        ))
        return

    if action_data.get("action") == "none":
        # Essayer le target extrait par l'IA, sinon utiliser le message brut comme fallback
        target = action_data.get("target", "").strip() or message.content.strip()
        if target:
            exact, similar, is_id, is_banned = await find_member(message.guild, target, message.channel)
            all_candidates = exact + similar
            if all_candidates:
                action_data["action"] = "show_profile"
                action_data["target"] = target
                action_data["resolved_member"] = all_candidates[0]
                await ask_action_choice(message.channel, all_candidates[0], action_data, message.author.id)
                return
        # Aucun membre trouvé → ignorer silencieusement (pas de message d'erreur parasite)
        return

    if action_data.get("action") == "show_profile" and not action_data.get("target"):
        await message.channel.send("❓ De quel membre veux-tu voir le profil ?")
        return
    if action_data.get("needs_clarification"):
        await message.channel.send(f"❓ {action_data.get('clarification_question')}")
        return

    exact, similar, is_id, is_banned = await find_member(message.guild, action_data.get("target", ""), message.channel)
    await handle_member_resolution(message.channel, action_data, message.author.id, exact, similar, is_id, is_banned)

client.run(DISCORD_TOKEN)
