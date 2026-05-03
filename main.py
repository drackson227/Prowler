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

Actions possibles: ban, kick, mute, warn, delete_messages, none

Format de réponse JSON uniquement (pas de texte autour) :
{
  "action": "ban|kick|mute|warn|delete_messages|none",
  "target": "mention ou description de l'utilisateur ciblé",
  "duration_minutes": null ou nombre (pour mute),
  "count": null ou nombre (pour delete_messages),
  "reason": null,
  "confirmation_message": "Message lisible expliquant ce que tu vas faire (sans mentionner la raison)",
  "needs_clarification": false,
  "clarification_question": null
}

Si la cible n'est pas claire, mets needs_clarification à true et pose une question.
Si aucune action de modération n'est détectée, mets action à "none".
"""

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
        await mod_channel.send(f"❌ Impossible de trouver l'utilisateur : **{target_desc}**")
        return
    action = action_data.get("action")
    reason = action_data.get("reason", "Aucune raison spécifiée")
    try:
        if action == "ban":
            await member.ban(reason=reason)
            await mod_channel.send(f"✅ **{member.display_name}** a été banni. Raison : {reason}")
        elif action == "kick":
            await member.kick(reason=reason)
            await mod_channel.send(f"✅ **{member.display_name}** a été kické. Raison : {reason}")
        elif action == "mute":
            duration = action_data.get("duration_minutes") or 10
            await member.timeout(timedelta(minutes=duration), reason=reason)
            await mod_channel.send(f"✅ **{member.display_name}** a été mute pour {duration} minutes. Raison : {reason}")
        elif action == "warn":
            try:
                await member.send(f"⚠️ Tu as reçu un avertissement sur **{guild.name}** : {reason}")
            except:
                pass
            await mod_channel.send(f"✅ **{member.display_name}** a été averti. Raison : {reason}")
        elif action == "delete_messages":
            count = action_data.get("count") or 10
            deleted = 0
            async for msg in mod_channel.history(limit=200):
                if msg.author == member and deleted < count:
                    await msg.delete()
                    deleted += 1
            await mod_channel.send(f"✅ {deleted} messages de **{member.display_name}** supprimés.")
    except discord.Forbidden:
        await mod_channel.send(f"❌ Je n'ai pas les permissions pour faire ça sur **{member.display_name}**.")
    except Exception as e:
        await mod_channel.send(f"❌ Erreur : {e}")

async def send_confirmation(channel, action_data, author_id):
    action = action_data.get("action")
    target = action_data.get("target", "?")
    duration = action_data.get("duration_minutes")

    action_labels = {
        "ban": "🔨 Bannir",
        "kick": "👢 Kicker",
        "mute": "🔇 Mute",
        "warn": "⚠️ Avertir",
        "delete_messages": "🗑️ Supprimer les messages de"
    }
    label = action_labels.get(action, action)
    duration_txt = f" pendant **{duration} minutes**" if duration else ""

    reason = action_data.get("reason", "Aucune raison spécifiée")

    bot_msg = await channel.send(
        f"⚠️ **Confirmation requise**\n"
        f"Action : {label} **{target}**{duration_txt}\n"
        f"Raison : {reason}\n\n"
        f"✅ pour confirmer — ❌ pour annuler"
    )
    await bot_msg.add_reaction("✅")
    await bot_msg.add_reaction("❌")
    pending_actions[bot_msg.id] = (action_data, author_id)

    await asyncio.sleep(30)
    if bot_msg.id in pending_actions:
        pending_actions.pop(bot_msg.id)
        await channel.send("⏱️ Confirmation expirée, action annulée.")

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
        await reaction.message.channel.send("❌ Action annulée.")

@client.event
async def on_message(message):
    if message.author.bot:
        return
    channel_name = message.channel.name
    if "modération" not in channel_name and "moderation" not in channel_name:
        return
    if not has_permission(message.author):
        await message.channel.send("❌ Tu n'as pas la permission d'utiliser le bot de modération.")
        return

    # Si on attend la raison de ce modérateur
    if message.author.id in waiting_for_reason:
        action_data = waiting_for_reason.pop(message.author.id)
        action_data["reason"] = message.content
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

    # Demander la raison
    await message.channel.send("📝 **Quelle est la raison de cette sanction ?**")
    waiting_for_reason[message.author.id] = action_data

client.run(DISCORD_TOKEN)
