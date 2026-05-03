import discord
import os
import json
import asyncio
from datetime import timedelta
from openai import OpenAI

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
ALLOWED_ROLES = ["Modérateur"]

ai_client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY
)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

client = discord.Client(intents=intents)
pending_actions = {}
waiting_for_reason = {}
waiting_for_member_choice = {}

SYSTEM_PROMPT = """Tu es un assistant de modération Discord. 
À partir d'un message en langage naturel, tu dois extraire l'action de modération voulue et retourner un JSON.

Actions possibles: ban, kick, mute, warn, delete_messages, unmute, unban

Format de réponse JSON uniquement (pas de texte autour) :
{
  "action": "ban|kick|mute|warn|delete_messages|unmute|unban|none",
  "target": "mention ou description de l'utilisateur ciblé",
  "duration_minutes": null ou nombre (pour mute),
  "count": null ou nombre (pour delete_messages),
  "reason": null,
  "confirmation_message": "Message lisible expliquant ce que tu vas faire",
  "needs_clarification": false,
  "clarification_question": null
}

Si la cible n'est pas claire, mets needs_clarification à true et pose une question.
Si aucune action de modération n'est détectée, mets action à "none".
"""

REASON_PROMPT = """Tu es un assistant de modération Discord. 
Reformule la raison donnée par un modérateur en une raison officielle, courte et professionnelle.
Réponds UNIQUEMENT avec la raison reformulée, rien d'autre, pas de guillemets.

Exemples :
- "il est raciste" → "Comportement raciste"
- "spam" → "Spam répété"
- "il insulte tout le monde" → "Insultes envers les membres"
- "il a envoyé des images inappropriées" → "Envoi de contenu inapproprié"
- "trop chiant" → "Comportement perturbateur"
"""

ACTION_COLORS = {
    "ban": 0xe74c3c,
    "kick": 0xe67e22,
    "mute": 0xf39c12,
    "unmute": 0x2ecc71,
    "unban": 0x2ecc71,
    "warn": 0xf1c40f,
    "delete_messages": 0x9b59b6,
}

ACTION_LABELS = {
    "ban": "🔨 Bannissement",
    "kick": "👢 Kick",
    "mute": "🔇 Mute",
    "unmute": "🔊 Demute",
    "unban": "✅ Déban",
    "warn": "⚠️ Avertissement",
    "delete_messages": "🗑️ Suppression de messages",
}

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
    exact = []
    similar = []
    for member in guild.members:
        name_lower = member.display_name.lower()
        username_lower = member.name.lower()
        score = max(similarity(description_lower, name_lower), similarity(description_lower, username_lower))
        if score == 1.0 and (description_lower == name_lower or description_lower == username_lower):
            exact.append(member)
        elif score >= 0.5:
            similar.append((score, member))
    similar.sort(key=lambda x: x[0], reverse=True)
    similar_members = [m for _, m in similar if m not in exact]
    return exact, similar_members[:5]

async def find_member(guild, description, channel):
    if description.startswith("<@") and description.endswith(">"):
        uid = description.strip("<@!>")
        try:
            return [guild.get_member(int(uid))], []
        except:
            return [], []
    exact, similar = find_similar_members(guild, description)
    return exact, similar

async def reformulate_reason(raw_reason):
    try:
        response = ai_client.chat.completions.create(
            model="openrouter/free",
            messages=[{"role": "user", "content": f"{REASON_PROMPT}\n\nRaison brute : {raw_reason}"}]
        )
        return response.choices[0].message.content.strip()
    except:
        return raw_reason

async def execute_action(guild, action_data, mod_channel):
    member = action_data.get("resolved_member")
    if not member:
        await mod_channel.send("❌ Aucun membre résolu pour cette action.")
        return

    action = action_data.get("action")
    reason = action_data.get("reason", "Aucune raison spécifiée")

    try:
        if action == "ban":
            await member.ban(reason=reason)
        elif action == "kick":
            await member.kick(reason=reason)
        elif action == "mute":
            duration = action_data.get("duration_minutes") or 10
            await member.timeout(timedelta(minutes=duration), reason=reason)
        elif action == "unmute":
            await member.timeout(None)
        elif action == "unban":
            await guild.unban(member)
        elif action == "warn":
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

        color = ACTION_COLORS.get(action, 0x2ecc71)
        label = ACTION_LABELS.get(action, action)
        duration = action_data.get("duration_minutes")

        embed = discord.Embed(title=f"✅ Action effectuée — {label}", color=color)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Utilisateur", value=f"{member.mention}", inline=True)
        if duration and action == "mute":
            embed.add_field(name="Durée", value=f"{duration} minutes", inline=True)
        if action not in ["unmute", "unban"]:
            embed.add_field(name="Raison", value=reason, inline=False)
        embed.set_footer(text=f"ID : {member.id}")
        await mod_channel.send(embed=embed)

    except discord.Forbidden:
        embed = discord.Embed(
            title="❌ Permission refusée",
            description=f"Je n'ai pas les permissions pour agir sur **{member.display_name}**.",
            color=0xe74c3c
        )
        await mod_channel.send(embed=embed)
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
    embed.set_footer(text="Réagis ✅ pour confirmer ou ❌ pour annuler • Expire dans 30s")

    bot_msg = await channel.send(embed=embed)
    await bot_msg.add_reaction("✅")
    await bot_msg.add_reaction("❌")
    pending_actions[bot_msg.id] = (action_data, author_id)

    await asyncio.sleep(30)
    if bot_msg.id in pending_actions:
        pending_actions.pop(bot_msg.id)
        expired_embed = discord.Embed(
            title="⏱️ Confirmation expirée",
            description="L'action a été annulée automatiquement.",
            color=0x95a5a6
        )
        await channel.send(embed=expired_embed)

async def ask_member_choice(channel, action_data, author_id, candidates):
    embed = discord.Embed(
        title="🔍 Plusieurs membres trouvés",
        description="Réagis avec le numéro correspondant au bon membre :",
        color=0x3498db
    )
    emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
    for i, member in enumerate(candidates[:5]):
        embed.add_field(
            name=f"{emojis[i]} {member.display_name}",
            value=f"`{member.name}` • ID: {member.id}",
            inline=False
        )
    embed.set_footer(text="Réagis ❌ pour annuler")
    bot_msg = await channel.send(embed=embed)
    for i in range(len(candidates[:5])):
        await bot_msg.add_reaction(emojis[i])
    await bot_msg.add_reaction("❌")
    waiting_for_member_choice[bot_msg.id] = (action_data, author_id, candidates[:5])

async def handle_member_resolution(channel, action_data, author_id, exact, similar):
    all_candidates = exact + similar

    if len(all_candidates) == 0:
        embed = discord.Embed(
            title="❌ Membre introuvable",
            description=f"Aucun membre trouvé pour **{action_data.get('target')}**.",
            color=0xe74c3c
        )
        await channel.send(embed=embed)
        return

    if len(exact) == 1 and len(similar) == 0:
        action_data["resolved_member"] = exact[0]
        if action_data.get("action") in ["ban", "kick", "mute", "warn", "delete_messages"]:
            embed = discord.Embed(
                title="📝 Raison de la sanction",
                description="Quelle est la raison de cette sanction ?",
                color=0x3498db
            )
            await channel.send(embed=embed)
            waiting_for_reason[author_id] = action_data
        else:
            await send_confirmation(channel, action_data, author_id)
        return

    if len(all_candidates) == 1:
        action_data["resolved_member"] = all_candidates[0]
        member = all_candidates[0]
        embed = discord.Embed(
            title="🔍 Membre similaire trouvé",
            description=f"Je n'ai pas trouvé **{action_data.get('target')}** exactement.\nVoulais-tu dire **{member.display_name}** ?",
            color=0xf39c12
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="Réagis ✅ pour confirmer ou ❌ pour annuler")
        bot_msg = await channel.send(embed=embed)
        await bot_msg.add_reaction("✅")
        await bot_msg.add_reaction("❌")
        pending_actions[bot_msg.id] = (action_data, author_id)
        return

    await ask_member_choice(channel, action_data, author_id, all_candidates)

@client.event
async def on_ready():
    print(f"✅ Bot connecté en tant que {client.user}")

@client.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return

    if reaction.message.id in waiting_for_member_choice:
        action_data, requester_id, candidates = waiting_for_member_choice[reaction.message.id]
        if user.id != requester_id:
            return
        emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
        if str(reaction.emoji) == "❌":
            waiting_for_member_choice.pop(reaction.message.id)
            await reaction.message.channel.send(embed=discord.Embed(title="❌ Action annulée", color=0x95a5a6))
            return
        if str(reaction.emoji) in emojis:
            idx = emojis.index(str(reaction.emoji))
            if idx < len(candidates):
                waiting_for_member_choice.pop(reaction.message.id)
                action_data["resolved_member"] = candidates[idx]
                if action_data.get("action") in ["ban", "kick", "mute", "warn", "delete_messages"]:
                    embed = discord.Embed(title="📝 Raison de la sanction", description="Quelle est la raison de cette sanction ?", color=0x3498db)
                    await reaction.message.channel.send(embed=embed)
                    waiting_for_reason[requester_id] = action_data
                else:
                    await send_confirmation(reaction.message.channel, action_data, requester_id)
        return

    if reaction.message.id in pending_actions:
        action_data, requester_id = pending_actions[reaction.message.id]
        if user.id != requester_id:
            return
        if str(reaction.emoji) == "✅":
            pending_actions.pop(reaction.message.id)
            await execute_action(reaction.message.guild, action_data, reaction.message.channel)
        elif str(reaction.emoji) == "❌":
            pending_actions.pop(reaction.message.id)
            await reaction.message.channel.send(embed=discord.Embed(title="❌ Action annulée", color=0x95a5a6))

@client.event
async def on_message(message):
    if message.author.bot:
        return
    channel_name = message.channel.name
    if "modération" not in channel_name and "moderation" not in channel_name:
        return
    if not has_permission(message.author):
        embed = discord.Embed(title="❌ Permission refusée", description="Tu n'as pas la permission d'utiliser le bot de modération.", color=0xe74c3c)
        await message.channel.send(embed=embed)
        return

    if message.author.id in waiting_for_reason:
        action_data = waiting_for_reason.pop(message.author.id)
        async with message.channel.typing():
            refined_reason = await reformulate_reason(message.content)
        action_data["reason"] = refined_reason
        await send_confirmation(message.channel, action_data, message.author.id)
        return

    async with message.channel.typing():
        try:
            response = ai_client.chat.completions.create(
                model="openrouter/free",
                messages=[{"role": "user", "content": f"{SYSTEM_PROMPT}\n\nMessage du modérateur: {message.content}"}]
            )
            raw = response.choices[0].message.content.strip()
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
    if action_data.get("needs_clarification"):
        await message.channel.send(f"❓ {action_data.get('clarification_question')}")
        return

    exact, similar = await find_member(message.guild, action_data.get("target", ""), message.channel)
    await handle_member_resolution(message.channel, action_data, message.author.id, exact, similar)

client.run(DISCORD_TOKEN)
