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

async def find_member(guild, description, channel):
    if description.startswith("<@") and description.endswith(">"):
        uid = description.strip("<@!>")
        try:
            return guild.get_member(int(uid))
        except:
            return None
    description_lower = description.lower()
    for member in guild.members:
        if description_lower in member.display_name.lower() or description_lower in member.name.lower():
            return member
    async for msg in channel.history(limit=50):
        if description_lower in msg.content.lower() and not msg.author.bot:
            return msg.author
    return None

async def execute_action(guild, action_data, mod_channel):
    target_desc = action_data.get("target", "")
    member = await find_member(guild, target_desc, mod_channel)
    if not member:
        embed = discord.Embed(
            title="❌ Utilisateur introuvable",
            description=f"Impossible de trouver : **{target_desc}**",
            color=0xe74c3c
        )
        await mod_channel.send(embed=embed)
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

        color = 0x2ecc71 if action in ["unmute", "unban"] else ACTION_COLORS.get(action, 0x2ecc71)
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

async def send_confirmation(channel, action_data, author_id, member=None):
    action = action_data.get("action")
    target = action_data.get("target", "?")
    duration = action_data.get("duration_minutes")
    reason = action_data.get("reason")

    label = ACTION_LABELS.get(action, action)
    color = ACTION_COLORS.get(action, 0xf39c12)

    embed = discord.Embed(
        title=f"⚠️ Confirmation requise — {label}",
        color=color
    )
    if member:
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Utilisateur", value=f"{member.mention}", inline=True)
    else:
        embed.add_field(name="Cible", value=f"**{target}**", inline=True)

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

@client.event
async def on_ready():
    print(f"✅ Bot connecté en tant que {client.user}")

@client.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return
    if reaction.message.id not in pending_actions:
        return
    action_data, requester_id = pending_actions[reaction.message.id]
    if user.id != requester_id:
        return
    if str(reaction.emoji) == "✅":
        pending_actions.pop(reaction.message.id)
        await execute_action(reaction.message.guild, action_data, reaction.message.channel)
    elif str(reaction.emoji) == "❌":
        pending_actions.pop(reaction.message.id)
        cancelled_embed = discord.Embed(
            title="❌ Action annulée",
            color=0x95a5a6
        )
        await reaction.message.channel.send(embed=cancelled_embed)

@client.event
async def on_message(message):
    if message.author.bot:
        return
    channel_name = message.channel.name
    if "modération" not in channel_name and "moderation" not in channel_name:
        return
    if not has_permission(message.author):
        embed = discord.Embed(
            title="❌ Permission refusée",
            description="Tu n'as pas la permission d'utiliser le bot de modération.",
            color=0xe74c3c
        )
        await message.channel.send(embed=embed)
        return

    if message.author.id in waiting_for_reason:
        action_data = waiting_for_reason.pop(message.author.id)
        action_data["reason"] = message.content
        member = await find_member(message.guild, action_data.get("target", ""), message.channel)
        await send_confirmation(message.channel, action_data, message.author.id, member=member)
        return

    async with message.channel.typing():
        try:
            response = ai_client.chat.completions.create(
                model="google/gemma-3-27b-it:free",
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

    if action_data.get("action") in ["ban", "kick", "mute", "warn", "delete_messages"]:
        embed = discord.Embed(
            title="📝 Raison de la sanction",
            description="Quelle est la raison de cette sanction ?",
            color=0x3498db
        )
        await message.channel.send(embed=embed)
        waiting_for_reason[message.author.id] = action_data
    else:
        member = await find_member(message.guild, action_data.get("target", ""), message.channel)
        await send_confirmation(message.channel, action_data, message.author.id, member=member)

client.run(DISCORD_TOKEN)
