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
                extra["Messages supprimés"] = s
