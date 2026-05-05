import discord
from discord.ext import commands
import asyncio
import json
import unicodedata
from datetime import datetime, timezone, timedelta
from openai import OpenAI
from collections import Counter

from config import (
    DISCORD_TOKEN, OPENROUTER_API_KEY, SYSTEM_PROMPT,
    ROLE_MEMBRE, ROLE_MEMBRE_ACTIF, ACTIVE_MESSAGES_PER_DAY,
    ACTIVE_DAYS_REQUIRED, INACTIVE_DAYS_REQUIRED,
    XP_PER_MESSAGE, COINS_PER_MESSAGE, COINS_BOOST,
    BOOST_INTERVAL, BOOST_INACTIVE, REPORT_HOUR
)
from db import load_db, save_db, get_member_data
from utils import (
    has_permission, find_member, get_channel_by_name,
    get_log_channel, log_action, reformulate_reason, update_boost
)
from economy import (
    add_xp_and_coins, cmd_profil, cmd_inventaire, cmd_boutique,
    cmd_acheter, cmd_equiper, cmd_spin, cmd_classement, cmd_daily, cmd_parrainer
)
from shop import rotate_shop, load_shop
from moderation import (
    show_profile, execute_action, send_confirmation, ask_action_choice,
    handle_member_resolution, cmd_give, is_moderation_command,
    pending_actions, waiting_for_reason, waiting_for_member_choice,
    waiting_for_action_choice, waiting_for_comment, mod_commands_log
)

ai_client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_API_KEY)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

client = commands.Bot(command_prefix="!", intents=intents, help_command=None)

member_message_days = {}

def normalize_name(s):
    s = s.lower().replace("・", "")
    return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode("ascii")

# ============================================================
# HELP
# ============================================================
async def send_help(channel):
    channel_name = normalize_name(channel.name)
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
            "**3 jours** → x1.5 | **7 jours** → x2 | **14 jours** → x2.5 | **30 jours** → x3\n\n"
            "💰 **Récompense de base :** 50 🪙 + 20 XP\n"
            "⚠️ Si tu rates un jour, ton streak repart à **0** !"
        )
    elif "moderation" in channel_name or "modération" in channel_name:
        embed.title = "📖 Commandes — Modération"
        embed.description = (
            "Tu peux écrire en **langage naturel** :\n\n"
            "• `mute @pseudo 30 minutes`\n• `ban @pseudo`\n• `kick @pseudo`\n"
            "• `warn @pseudo`\n• `unmute @pseudo`\n• `unban @pseudo`\n"
            "• `supprime les 10 derniers messages de @pseudo`\n"
            "• `profil de @pseudo`"
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
# RAPPORT QUOTIDIEN
# ============================================================
async def send_daily_report(guild):
    report_ch = get_channel_by_name(guild, "rapport-prowler")
    if not report_ch:
        return
    db = load_db()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    bans, kicks, mutes, warns = [], [], [], []
    for mid, data in db.items():
        member = guild.get_member(int(mid))
        name = member.display_name if member else f"ID:{mid}"
        for s in data.get("sanctions", []):
            if s.get("date", "").startswith(today):
                t = s.get("type", "")
                reason = s.get("reason", "—")
                if t == "ban": bans.append((name, reason))
                elif t == "kick": kicks.append((name, reason))
                elif t in ["mute", "spam_mute"]: mutes.append((name, reason))
                elif t == "warn": warns.append((name, reason))
    embed = discord.Embed(
        title=f"📝 Rapport de modération — {today}",
        color=0x3498db,
        timestamp=datetime.now(timezone.utc)
    )
    def fmt_list(lst):
        if not lst: return "Aucun"
        return "\n".join([f"• **{n}** — {r}" for n, r in lst[:10]])
    embed.add_field(name=f"🔨 Bans ({len(bans)})", value=fmt_list(bans), inline=False)
    embed.add_field(name=f"👢 Kicks ({len(kicks)})", value=fmt_list(kicks), inline=False)
    embed.add_field(name=f"🔇 Mutes ({len(mutes)})", value=fmt_list(mutes), inline=False)
    embed.add_field(name=f"⚠️ Warns ({len(warns)})", value=fmt_list(warns), inline=False)
    today_cmds = [(t, mod, act, tgt) for t, mod, act, tgt in mod_commands_log
                  if datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d") == today]
    if today_cmds:
        cmd_counts = Counter(f"{mod} → {act}" for _, mod, act, _ in today_cmds)
        cmd_txt = "\n".join([f"• **{k}** × {v}" for k, v in cmd_counts.most_common(10)])
        embed.add_field(name=f"🖱️ Actions modos ({len(today_cmds)})", value=cmd_txt, inline=False)
    else:
        embed.add_field(name="🖱️ Actions modos", value="Aucune action aujourd'hui", inline=False)
    embed.set_footer(text="Rapport automatique quotidien")
    await report_ch.send(embed=embed)

# ============================================================
# LOOPS
# ============================================================
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

async def shop_rotate_loop():
    await client.wait_until_ready()
    shop = load_shop()
    if not shop["rotating"]:
        rotate_shop()
    while not client.is_closed():
        shop = load_shop()
        last = shop.get("last_rotate")
        if last:
            from config import SHOP_ROTATE_INTERVAL
            dt = datetime.fromisoformat(last)
            next_rotate = dt + timedelta(seconds=SHOP_ROTATE_INTERVAL)
            wait = (next_rotate - datetime.now(timezone.utc)).total_seconds()
            if wait > 0:
                await asyncio.sleep(wait)
        else:
            from config import SHOP_ROTATE_INTERVAL
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
                active_streak = sum(
                    1 for i in range(ACTIVE_DAYS_REQUIRED)
                    if days_data.get((today - timedelta(days=i)).isoformat(), 0) >= ACTIVE_MESSAGES_PER_DAY
                )
                inactive_streak = sum(
                    1 for i in range(INACTIVE_DAYS_REQUIRED)
                    if days_data.get((today - timedelta(days=i)).isoformat(), 0) == 0
                )
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
# ÉVÉNEMENTS
# ============================================================
@client.event
async def on_ready():
    print(f"✅ Bot connecté en tant que {client.user}")
    await client.add_cog(__import__('trades').Trades(client))
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
                    if action_data.get("reason"):
                        await send_confirmation(reaction.message.channel, action_data, requester_id)
                    else:
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
                        title="✅ Commentaire supprimé", color=0x2ecc71
                    ))
                    target = reaction.message.guild.get_member(member.id)
                    mod_m = reaction.message.guild.get_member(user.id)
                    await log_action(reaction.message.guild, "comment_remove", mod_m, target, extra={"Commentaire supprimé": removed})
        return

    if msg_id in waiting_for_member_choice:
        action_data, requester_id, candidates = waiting_for_member_choice[msg_id]
        if user.id != requester_id:
            return
        emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
        if str(reaction.emoji) == "✅" and len(candidates) == 1:
            waiting_for_member_choice.pop(msg_id)
            action_data["resolved_member"] = candidates[0]
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

    from utils import check_spam
    channel_name = normalize_name(message.channel.name)
    content = message.content.strip()
    content_lower = content.lower()

    # Suivi activité
    mid = str(message.author.id)
    today = datetime.now(timezone.utc).date().isoformat()
    if mid not in member_message_days:
        member_message_days[mid] = {}
    member_message_days[mid][today] = member_message_days[mid].get(today, 0) + 1

    # XP & pièces
    is_boosted = update_boost(message.author.id)
    coin_gain = COINS_BOOST if is_boosted else COINS_PER_MESSAGE
    await add_xp_and_coins(message.author, message.guild, XP_PER_MESSAGE, coin_gain)

    if content_lower in ["!help", "?help"]:
        await send_help(message.channel)
        return

    if "jeux" in channel_name:
        if content_lower == "!profil": await cmd_profil(message); return
        if content_lower == "!inventaire": await cmd_inventaire(message); return
        if content_lower == "!classement": await cmd_classement(message); return
        if content_lower.startswith("!parrainer"): await cmd_parrainer(message, content[10:].strip()); return
        if content_lower in ["!boutique", "!spin"] or content_lower.startswith(("!acheter", "!équiper")):
            boutique_ch = get_channel_by_name(message.guild, "boutique")
            if boutique_ch:
                await message.channel.send(f"❌ Cette commande est réservée à {boutique_ch.mention} !")
            return
        if content_lower == "!daily":
            daily_ch = get_channel_by_name(message.guild, "daily")
            if daily_ch:
                await message.channel.send(f"❌ La commande `!daily` est réservée à {daily_ch.mention} !")
            return

    if "boutique" in channel_name:
        if content_lower == "!boutique": await cmd_boutique(message); return
        if content_lower.startswith("!acheter "): await cmd_acheter(message, content[9:].strip()); return
        if content_lower.startswith("!équiper "): await cmd_equiper(message, content[9:].strip()); return
        if content_lower == "!spin": await cmd_spin(message); return

    if content_lower == "!daily":
        await cmd_daily(message)
        return

    if "moderation" not in channel_name:
        return

    if not has_permission(message.author):
        await message.channel.send(embed=discord.Embed(
            title="❌ Permission refusée",
            description="Tu n'as pas la permission d'utiliser le bot de modération.",
            color=0xe74c3c
        ))
        return

    if content_lower.startswith("!give"):
        await cmd_give(message, content[5:].strip())
        return

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

    if message.author.id in waiting_for_reason:
        action_data = waiting_for_reason.pop(message.author.id)
        async with message.channel.typing():
            refined = await reformulate_reason(message.content)
        action_data["reason"] = refined
        await send_confirmation(message.channel, action_data, message.author.id)
        return

    if not has_permission(message.author):
        spammed = await check_spam(message)
        if spammed:
            return

    # ── DÉTECTION D'INTENTION ──────────────────────────────────
    # Si le modo n'attend pas de réponse en cours (raison, commentaire),
    # on vérifie que le message est bien une commande avant de le traiter.
    already_waiting = (
        message.author.id in waiting_for_reason or
        message.author.id in waiting_for_comment
    )
    if not already_waiting:
        if not await is_moderation_command(content):
            return  # "probablement noir" → ignoré silencieusement ✅
    # ──────────────────────────────────────────────────────────────

    action_data = None
    try:
        async with message.channel.typing():
            r = ai_client.chat.completions.create(
                model="openrouter/free",
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
        action_data = {"action": "none", "target": message.content.strip(), "needs_clarification": False}
    except Exception as e:
        await message.channel.send(embed=discord.Embed(
            title="❌ Erreur IA", description=f"```{e}```", color=0xe74c3c
        ))
        return

    if action_data.get("action") == "none":
        target = action_data.get("target", "").strip()
        if not target:
            words = message.content.strip().split()
            if len(words) == 1:
                target = words[0]
        if target:
            exact, similar, is_id, is_banned = await find_member(message.guild, target, message.channel)
            all_candidates = exact + similar
            if all_candidates:
                action_data["action"] = "show_profile"
                action_data["target"] = target
                action_data["resolved_member"] = all_candidates[0]
                if len(all_candidates) == 1:
                    await ask_action_choice(message.channel, all_candidates[0], action_data, message.author.id)
                else:
                    await handle_member_resolution(message.channel, action_data, message.author.id, exact, similar, is_id, is_banned)
        return

    if action_data.get("action") == "show_profile" and not action_data.get("target"):
        await message.channel.send("❓ De quel membre veux-tu voir le profil ?")
        return
    if action_data.get("needs_clarification"):
        await message.channel.send(f"❓ {action_data.get('clarification_question')}")
        return process_commands

    exact, similar, is_id, is_banned = await find_member(message.guild, action_data.get("target", ""), message.channel)
    await handle_member_resolution(message.channel, action_data, message.author.id, exact, similar, is_id, is_banned)

client.run(DISCORD_TOKEN)
