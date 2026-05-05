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

async def is_moderation_command(content: str) -> bool:
    """Détecte si un message ressemble à une commande de modération."""
    content_lower = content.lower()
    keywords = [
        "ban", "kick", "mute", "warn", "unmute", "unban",
        "supprimer", "supprime", "profil", "show_profile",
        "sanction", "silence", "expulse", "expulser"
    ]
    return any(kw in content_lower for kw in keywords)
    
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
    from config import FOUNDER_ROLES
    if not any(role.name in FOUNDER_ROLES for role in message.author.roles):
        if message.guild.owner_id != message.author.id:
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
                "`!give @membre 500` — donne des pièces\n"
                "`!give @membre coins:500` — donne des pièces\n"
                "`!give @membre role NomDuRole` — donne un rôle (fuzzy)\n"
                "`!give @membre carte NomDeLaCarte` — donne une carte (fuzzy)"
            ),
            color=0x3498db
        ))
        return

    target = mentions[0]
    clean = args
    for m in message.mentions:
        clean = clean.replace(f"<@{m.id}>", "").replace(f"<@!{m.id}>", "")
    clean = clean.strip()

    from difflib import SequenceMatcher

    def fuzzy_score(a, b):
        a_l, b_l = a.lower(), b.lower()
        if a_l == b_l: return 1.0
        if a_l in b_l: return 0.9
        return SequenceMatcher(None, a_l, b_l).ratio()

    async def wait_reaction(channel, author_id, bot_msg, valid_emojis, timeout=30):
        def check(reaction, user):
            return (
                user.id == author_id
                and reaction.message.id == bot_msg.id
                and str(reaction.emoji) in valid_emojis
            )
        try:
            reaction, _ = await channel._state._get_client().wait_for(
                "reaction_add", timeout=timeout, check=check
            )
            return str(reaction.emoji)
        except asyncio.TimeoutError:
            return None

    # ── Pièces ──────────────────────────────────────────────
    if clean.isdigit() or clean.lower().startswith("coins:"):
        raw = clean[6:].strip() if clean.lower().startswith("coins:") else clean
        if not raw.isdigit():
            await message.channel.send("❌ Montant invalide. Ex: `!give @membre 200`")
            return
        amount = int(raw)
        db = load_db()
        data = get_member_data(db, target.id)
        data["coins"] += amount
        save_db(db)
        embed = discord.Embed(
            title="🪙 Pièces données !",
            description=f"**{amount}** 🪙 ont été ajoutées à {target.mention}\nNouveau solde : **{data['coins']}** 🪙",
            color=0xf1c40f
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        await message.channel.send(embed=embed)
        from utils import log_action
        await log_action(message.guild, "give_coins", message.author, target, extra={"Pièces": f"+{amount}", "Solde": data["coins"]})
        return

    # ── Rôle ────────────────────────────────────────────────
    if clean.lower().startswith("role:") or clean.lower().startswith("role "):
        role_query = clean[5:].strip()
        if not role_query:
            await message.channel.send("❌ Précise le nom du rôle. Ex: `!give @membre role Rose`")
            return

        roles_scored = []
        for role in message.guild.roles:
            if role.name == "@everyone":
                continue
            s = fuzzy_score(role_query, role.name)
            if s >= 0.4:
                roles_scored.append((s, role))
        roles_scored.sort(key=lambda x: x[0], reverse=True)
        top = roles_scored[:3]

        if not top:
            await message.channel.send(f"❌ Aucun rôle ressemblant à **{role_query}** trouvé.")
            return

        if top[0][0] >= 0.95:
            found_role = top[0][1]
        else:
            emojis = ["1️⃣", "2️⃣", "3️⃣"]
            lignes = []
            for i, (s, r) in enumerate(top):
                color_circle = "⬛"
                if r.color.value:
                    rv = (r.color.value >> 16) & 0xFF
                    gv = (r.color.value >> 8) & 0xFF
                    bv = r.color.value & 0xFF
                    if rv > 200 and gv < 100 and bv < 100: color_circle = "🔴"
                    elif gv > 200 and rv < 100 and bv < 100: color_circle = "🟢"
                    elif bv > 200 and rv < 100 and gv < 100: color_circle = "🔵"
                    elif rv > 200 and gv > 200 and bv < 100: color_circle = "🟡"
                    elif rv > 150 and bv > 150 and gv < 100: color_circle = "🟣"
                    elif rv > 200 and gv > 100 and bv < 50: color_circle = "🟠"
                    elif rv > 200 and gv > 200 and bv > 200: color_circle = "⬜"
                lignes.append(f"{emojis[i]} {color_circle} **{r.name}**")

            embed = discord.Embed(
                title="🔍 Quel rôle voulais-tu dire ?",
                description="\n".join(lignes),
                color=0xf39c12
            )
            embed.set_footer(text="Réagis avec le numéro • ❌ pour annuler • Expire dans 30s")
            bot_msg = await message.channel.send(embed=embed)
            for i in range(len(top)):
                await bot_msg.add_reaction(emojis[i])
            await bot_msg.add_reaction("❌")

            emoji = await wait_reaction(message.channel, message.author.id, bot_msg, emojis[:len(top)] + ["❌"])
            if emoji is None or emoji == "❌":
                await bot_msg.edit(embed=discord.Embed(title="❌ Action annulée", color=0x95a5a6))
                return
            found_role = top[emojis.index(emoji)][1]

        # Confirmation
        confirm_embed = discord.Embed(
            title="⚠️ Confirmer l'attribution",
            description=f"Donner le rôle **{found_role.name}** à {target.mention} ?",
            color=found_role.color if found_role.color.value else discord.Color(0x3498db)
        )
        confirm_embed.set_thumbnail(url=target.display_avatar.url)
        confirm_embed.set_footer(text="✅ confirmer • ❌ annuler • Expire dans 30s")
        confirm_msg = await message.channel.send(embed=confirm_embed)
        await confirm_msg.add_reaction("✅")
        await confirm_msg.add_reaction("❌")

        emoji = await wait_reaction(message.channel, message.author.id, confirm_msg, ["✅", "❌"])
        if emoji is None or emoji == "❌":
            await confirm_msg.edit(embed=discord.Embed(title="❌ Action annulée", color=0x95a5a6))
            return

        try:
            await target.add_roles(found_role, reason=f"!give par {message.author.display_name}")
            db = load_db()
            data = get_member_data(db, target.id)
            already = any(i.get("name", "") == found_role.name for i in data.get("inventory", []))
            if not already:
                data.setdefault("inventory", []).append({
                    "id": found_role.name.lower().replace(" ", "_"),
                    "name": found_role.name,
                    "type": "role_color"
                })
                save_db(db)
            await confirm_msg.edit(embed=discord.Embed(
                title="🎁 Rôle donné !",
                description=f"Le rôle **{found_role.name}** a été attribué à {target.mention}",
                color=found_role.color if found_role.color.value else discord.Color(0x2ecc71)
            ))
            from utils import log_action
            await log_action(message.guild, "give_role", message.author, target, extra={"Rôle": found_role.name})
        except discord.Forbidden:
            await confirm_msg.edit(embed=discord.Embed(
                title="❌ Permission refusée",
                description=f"Je n'ai pas la permission d'attribuer **{found_role.name}**.",
                color=0xe74c3c
            ))
        return

    # ── Carte ────────────────────────────────────────────────
    if clean.lower().startswith("carte:") or clean.lower().startswith("carte "):
        carte_query = clean[6:].strip()
        if not carte_query:
            await message.channel.send("❌ Précise le nom de la carte. Ex: `!give @membre carte Kebab Froid`")
            return

        # Import du catalogue de cartes
        try:
            from cards import CARTES, RARETES
        except ImportError:
            await message.channel.send("❌ Impossible de charger le catalogue de cartes.")
            return

        cartes_scored = []
        for carte in CARTES:
            s = fuzzy_score(carte_query, carte["nom"])
            if s >= 0.4:
                cartes_scored.append((s, carte))
        cartes_scored.sort(key=lambda x: x[0], reverse=True)
        top = cartes_scored[:3]

        if not top:
            await message.channel.send(f"❌ Aucune carte ressemblant à **{carte_query}** trouvée.")
            return

        if top[0][0] >= 0.95:
            found_carte = top[0][1]
        else:
            emojis = ["1️⃣", "2️⃣", "3️⃣"]
            lignes = []
            for i, (s, carte) in enumerate(top):
                rarete_info = RARETES.get(carte["rarete"], {})
                emoji_r = rarete_info.get("emoji", "❓")
                label_r = rarete_info.get("label", carte["rarete"])
                lignes.append(f"{emojis[i]} {emoji_r} **{carte['nom']}** — *{label_r}*")

            embed = discord.Embed(
                title="🔍 Quelle carte voulais-tu dire ?",
                description="\n".join(lignes),
                color=0xf39c12
            )
            embed.set_footer(text="Réagis avec le numéro • ❌ pour annuler • Expire dans 30s")
            bot_msg = await message.channel.send(embed=embed)
            for i in range(len(top)):
                await bot_msg.add_reaction(emojis[i])
            await bot_msg.add_reaction("❌")

            emoji = await wait_reaction(message.channel, message.author.id, bot_msg, emojis[:len(top)] + ["❌"])
            if emoji is None or emoji == "❌":
                await bot_msg.edit(embed=discord.Embed(title="❌ Action annulée", color=0x95a5a6))
                return
            found_carte = top[emojis.index(emoji)][1]

        # Confirmation
        rarete_info = RARETES.get(found_carte["rarete"], {})
        rarete_color = rarete_info.get("couleur", 0x3498db)
        rarete_emoji = rarete_info.get("emoji", "❓")
        rarete_label = rarete_info.get("label", found_carte["rarete"])

        confirm_embed = discord.Embed(
            title="⚠️ Confirmer le don de carte",
            description=(
                f"Donner la carte **{found_carte['nom']}** à {target.mention} ?\n\n"
                f"{rarete_emoji} *{rarete_label}* — {found_carte['description']}"
            ),
            color=rarete_color
        )
        confirm_embed.set_thumbnail(url=target.display_avatar.url)
        confirm_embed.set_image(url=found_carte.get("image_url", ""))
        confirm_embed.set_footer(text="✅ confirmer • ❌ annuler • Expire dans 30s")
        confirm_msg = await message.channel.send(embed=confirm_embed)
        await confirm_msg.add_reaction("✅")
        await confirm_msg.add_reaction("❌")

        emoji = await wait_reaction(message.channel, message.author.id, confirm_msg, ["✅", "❌"])
        if emoji is None or emoji == "❌":
            await confirm_msg.edit(embed=discord.Embed(title="❌ Action annulée", color=0x95a5a6))
            return

        # Ajout de la carte à l'inventaire
        db = load_db()
        data = get_member_data(db, target.id)
        data.setdefault("cartes", []).append({
            "id": found_carte["id"],
            "nom": found_carte["nom"],
            "rarete": found_carte["rarete"]
        })
        save_db(db)

        await confirm_msg.edit(embed=discord.Embed(
            title="🎴 Carte donnée !",
            description=(
                f"{rarete_emoji} **{found_carte['nom']}** a été ajoutée à l'inventaire de {target.mention} !\n"
                f"*{rarete_label}*"
            ),
            color=rarete_color
        ))
        from utils import log_action
        await log_action(message.guild, "give_carte", message.author, target,
                         extra={"Carte": found_carte["nom"], "Rareté": rarete_label})
        return

    # ── Rien reconnu ────────────────────────────────────────
    await message.channel.send(embed=discord.Embed(
        title="❓ Usage de !give",
        description=(
            "`!give @membre 500` — donne des pièces\n"
            "`!give @membre coins:500` — donne des pièces\n"
            "`!give @membre role NomDuRole` — donne un rôle (fuzzy)\n"
            "`!give @membre carte NomDeLaCarte` — donne une carte (fuzzy)"
        ),
        color=0x3498db
    ))
    embed.set_thumbnail(url=target.display_avatar.url)
    await message.channel.send(embed=embed)
    await log_action(message.guild, "give_coins", message.author, target, extra={"Pièces": f"+{amount}", "Solde": data["coins"]})
