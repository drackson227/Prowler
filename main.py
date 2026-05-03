import discord
import os
import json
import asyncio
import re
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
            "sanctions": []
        }
    return db[mid]

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
            model="google/gemma-3-4b-it:free",
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
    }
    labels = {
        "ban": "🔨 Bannissement", "kick": "👢 Kick", "mute": "🔇 Mute",
        "unmute": "🔊 Demute", "unban": "✅ Déban", "warn": "⚠️ Avertissement",
        "spam_mute": "🤖 Mute anti-spam", "join": "📥 Arrivée", "leave": "📤 Départ",
        "comment_add": "💬 Commentaire ajouté", "comment_remove": "🗑️ Commentaire supprimé",
        "delete_messages": "🗑️ Messages supprimés", "show_profile": "🔍 Profil consulté",
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

    ch = message.channel
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

    await ch.send(embed=embed)
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
    public_channels = [
        ch for ch in guild.text_channels
        if ch.permissions_for(guild.default_role).read_messages
    ]
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
                model="google/gemma-3-4b-it:free",
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

    embed = discord.Embed(title=f"👤 Profil — {member.display_name}", color=0x3498db)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="🏷️ Pseudo", value=f"{member.name}", inline=True)
    embed.add_field(name="🆔 ID", value=f"`{member.id}`", inline=True)
    embed.add_field(name="📅 Compte créé", value=created, inline=True)
    embed.add_field(name="📥 A rejoint le", value=joined, inline=True)
    embed.add_field(name="🎭 Rôles", value=roles_text, inline=False)
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
    channel_name = channel.name.lower().replace("・", "")
    embed = discord.Embed(color=0x3498db, timestamp=datetime.now(timezone.utc))

    if "jeux" in channel_name:
        embed.title = "📖 Commandes — 🎮・jeux"
        embed.description = (
            "**Profil & Stats**\n"
            "`!profil` — voir ton niveau, pièces, rôles équipés\n"
            "`!inventaire` — voir tous tes rôles achetés\n"
            "`!classement` — top des membres les plus actifs\n\n"
            "**Social**\n"
            "`!parrainer @pseudo` — parrainer un ami\n"
            "`!abonner #salon` — s'abonner aux notifs d'un salon\n"
            "`!désabonner #salon` — se désabonner\n\n"
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
            "`!spin` — tenter le gacha (50 pièces)\n\n"
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
            "💬 Commentaires modos • 🤖 Mutes anti-spam"
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
                waiting_for_action_choice[msg_id] = (choice_type, member, action_data, requester_id)
                return
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
            if not has_permission(user if isinstance(user, discord.Member) else reaction.message.guild.get_member(user.id)):
                return
            waiting_for_action_choice.pop(msg_id)
            mod_member = reaction.message.guild.get_member(user.id)
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

    channel_name = message.channel.name.lower().replace("・", "")

    # --- Suivi activité membre ---
    mid = str(message.author.id)
    today = datetime.now(timezone.utc).date().isoformat()
    if mid not in member_message_days:
        member_message_days[mid] = {}
    member_message_days[mid][today] = member_message_days[mid].get(today, 0) + 1

    # --- Commande !help / ?help ---
    if message.content.strip().lower() in ["!help", "?help"]:
        await send_help(message.channel)
        return

    # --- Salon modération uniquement pour les commandes de mod ---
    if "modération" not in channel_name and "moderation" not in channel_name:
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

    # --- Anti-spam (pour tous les membres non-modo) ---
    if not has_permission(message.author):
        spammed = await check_spam(message)
        if spammed:
            return

    # --- Analyse IA de la commande ---
    async with message.channel.typing():
        try:
            r = ai_client.chat.completions.create(
                model="google/gemma-3-4b-it:free",
                messages=[{"role": "user", "content": f"{SYSTEM_PROMPT}\n\nMessage du modérateur: {message.content}"}]
            )
            raw = r.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()
            action_data = json.loads(raw)
        except Exception as e:
            await message.channel.send(f"❌ Erreur lors de l'analyse : {e}")
            return

    if action_data.get("action") == "none":
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
