import discord
import asyncio
import json
from datetime import datetime, timezone, timedelta
from openai import OpenAI

from config import (
    OPENROUTER_API_KEY, FOUNDER_ROLES, ACTION_COLORS, ACTION_LABELS,
    REASON_PROMPT, ALLOWED_ROLES, AI_MODEL
)
from db import load_db, save_db, get_member_data
from utils import has_permission, find_member, get_channel_by_name, log_action, reformulate_reason
from economy import analyze_member_messages, get_level_from_xp

ai_client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_API_KEY)

pending_actions = {}
waiting_for_reason = {}
waiting_for_member_choice = {}
waiting_for_action_choice = {}
waiting_for_comment = {}
mod_commands_log = []

ROLE_HIERARCHY = ["Fondateur", "Modérateur"]

def get_role_level(member):
    for i, role_name in enumerate(ROLE_HIERARCHY):
        if any(r.name == role_name for r in member.roles):
            return i
    return 99

def can_sanction(moderator, target):
    if target.bot:
        return False, "Tu ne peux pas sanctionner un bot."
    mod_level = get_role_level(moderator)
    target_level = get_role_level(target)
    if target_level <= mod_level:
        target_role = next((r.name for r in target.roles if r.name in ROLE_HIERARCHY), "Membre")
        return False, f"Tu ne peux pas sanctionner **{target.display_name}** qui est **{target_role}**."
    return True, None

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
    embed.add_field(name="🏷️ Pseudo", value=member.name, inline=True)
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
            embed.add_field(
                name="💬 Commentaires modos",
                value="\n".join([f"• {c}" for c in data["comments"]]),
                inline=False
            )
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
    if moderator and action in ["ban", "kick", "mute", "warn", "delete_messages"]:
        authorized, reason_denied = can_sanction(moderator, member)
        if not authorized:
            await mod_channel.send(embed=discord.Embed(
                title="🚫 Action refusée", description=reason_denied, color=0xe74c3c
            ))
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
            all_msgs = []
            for ch in guild.text_channels:
                try:
                    async for msg in ch.history(limit=2000):
                        if msg.author.id == member.id:
                            all_msgs.append((msg, ch.name))
                except:
                    continue
            all_msgs.sort(key=lambda x: x[0].created_at, reverse=True)
            deleted, deleted_by_channel = 0, {}
            for msg, ch_name in all_msgs[:count]:
                try:
                    await msg.delete()
                    deleted += 1
                    deleted_by_channel[ch_name] = deleted_by_channel.get(ch_name, 0) + 1
                except:
                    pass
            action_data["deleted_count"] = deleted
            action_data["deleted_by_channel"] = deleted_by_channel
        if action not in ["unmute", "unban", "delete_messages", "show_profile"]:
            data["sanctions"].append({
                "type": action, "reason": reason,
                "date": datetime.now(timezone.utc).isoformat(),
                "duration": action_data.get("duration_minutes")
            })
        save_db(db)
        if moderator and action != "show_profile":
            tgt_name = member.display_name if hasattr(member, "display_name") else str(member)
            mod_commands_log.append((datetime.now(timezone.utc).timestamp(), moderator.display_name, action, tgt_name))
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
        if action == "delete_messages":
            deleted_count = action_data.get("deleted_count", 0)
            by_ch = action_data.get("deleted_by_channel", {})
            ch_detail = ", ".join([f"#{ch} ({n})" for ch, n in by_ch.items()]) if by_ch else "—"
            embed.add_field(name="🗑️ Messages supprimés", value=str(deleted_count), inline=True)
            embed.add_field(name="📍 Salons", value=ch_detail, inline=True)
        embed.set_footer(text=f"ID : {member.id}")
        await mod_channel.send(embed=embed)
        if action != "show_profile":
            extra = {"Durée": f"{duration} min" if duration else None}
            if action == "delete_messages":
                extra["Messages supprimés"] = str(action_data.get("deleted_count", 0))
                extra["Salons"] = ", ".join([f"#{ch} ({n})" for ch, n in action_data.get("deleted_by_channel", {}).items()]) or "—"
            await log_action(guild, action, moderator, member, reason=reason, extra=extra)
    except discord.Forbidden:
        await mod_channel.send(embed=discord.Embed(
            title="❌ Permission refusée",
            description=f"Je n'ai pas les permissions pour agir sur **{member.display_name}**.",
            color=0xe74c3c
        ))
    except Exception as e:
        await mod_channel.send(f"❌ Erreur : {e}")

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
            title="🔍 Membre trouvé",
            description=f"Voulais-tu dire **{m.display_name}** (`{m.name}`) ?",
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

async def cmd_give(message, args):
    is_founder = any(role.name in FOUNDER_ROLES for role in message.author.roles)
    is_owner = message.guild.owner_id == message.author.id
    if not is_founder and not is_owner:
        await message.channel.send(embed=discord.Embed(
            title="❌ Permission refusée",
            description="Seul le **Fondateur** peut utiliser `!give`.",
            color=0xe74c3c
        ))
        return
    mentions = message.mentions
    if not mentions or not args:
        await message.channel.send(embed=discord.Embed(
            title="❓ Usage de !give",
            description=(
                "**Pièces :**\n"
                "`!give @membre 500` — donne 500 pièces\n"
                "`!give @membre coins:500` — même chose\n\n"
                "**Rôle :**\n"
                "`!give @membre role:NomDuRole` — donne un rôle Discord\n\n"
                "Exemples :\n• `!give @Zertyx 1000000`\n• `!give @Zertyx role:Rôle Gold`"
            ),
            color=0x3498db
        ))
        return
    target = mentions[0]
    clean = args
    for m in message.mentions:
        clean = clean.replace(f"<@{m.id}>", "").replace(f"<@!{m.id}>", "")
    clean = clean.strip()
    if clean.lower().startswith("role:"):
        role_name = clean[5:].strip()
        role = discord.utils.get(message.guild.roles, name=role_name)
        if not role:
            await message.channel.send(f"❌ Rôle **{role_name}** introuvable sur le serveur.")
            return
        try:
            await target.add_roles(role, reason=f"!give par {message.author.display_name}")
            db = load_db()
            data = get_member_data(db, target.id)
            if not any(i.get("name", "") == role_name for i in data["inventory"]):
                data["inventory"].append({"id": role_name.lower().replace(" ", "_"), "name": role_name, "type": "role_color"})
            save_db(db)
            embed = discord.Embed(
                title="🎁 Rôle donné !",
                description=f"Le rôle **{role_name}** a été attribué à {target.mention}",
                color=role.color.value if role.color.value else 0x2ecc71
            )
            embed.set_thumbnail(url=target.display_avatar.url)
            await message.channel.send(embed=embed)
            await log_action(message.guild, "give_role", message.author, target, extra={"Rôle": role_name})
        except discord.Forbidden:
            await message.channel.send(f"❌ Je n'ai pas la permission d'attribuer le rôle **{role_name}**.")
        return
    if clean.lower().startswith("coins:"):
        try:
            amount = int(clean[6:].strip().split()[0])
        except:
            await message.channel.send("❌ Format invalide. Ex: `!give @membre coins:200`")
            return
    else:
        parts = clean.split()
        if parts and parts[0].lstrip("-").isdigit():
            try:
                amount = int(parts[0])
            except:
                await message.channel.send("❌ Montant invalide.")
                return
        else:
            await message.channel.send("❌ Format invalide.\nEx: `!give @membre 500` • `!give @membre role:Rôle Gold`")
            return
    if amount <= 0:
        await message.channel.send("❌ Le montant doit être supérieur à 0.")
        return
    db = load_db()
    data = get_member_data(db, target.id)
    data["coins"] += amount
    save_db(db)
    embed = discord.Embed(
        title="🪙 Pièces données !",
        description=f"**{amount:,}** 🪙 ont été ajoutées au compte de {target.mention}\nNouveau solde : **{data['coins']:,}** 🪙",
        color=0xf1c40f
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    await message.channel.send(embed=embed)
    await log_action(message.guild, "give_coins", message.author, target, extra={"Pièces": f"+{amount}", "Solde": data["coins"]})
