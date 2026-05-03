import discord
import google.generativeai as genai
import os
import json
import asyncio
from datetime import timedelta

# ============================================================
# CONFIG — Remplace ces valeurs par les tiennes
# ============================================================
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
MODERATION_CHANNEL = "⚠️・modération"
ALLOWED_ROLES = ["Modérateur"]  # + le propriétaire du serveur
# ============================================================

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

client = discord.Client(intents=intents)

pending_actions = {}  # Stocke les actions en attente de confirmation

SYSTEM_PROMPT = """Tu es un assistant de modération Discord. 
À partir d'un message en langage naturel, tu dois extraire l'action de modération voulue et retourner un JSON.

Actions possibles: ban, kick, mute, warn, delete_messages, none

Format de réponse JSON uniquement (pas de texte autour) :
{
  "action": "ban|kick|mute|warn|delete_messages|none",
  "target": "mention ou description de l'utilisateur ciblé",
  "duration_minutes": null ou nombre (pour mute),
  "count": null ou nombre (pour delete_messages),
  "reason": "raison de la sanction",
  "confirmation_message": "Message lisible expliquant ce que tu vas faire",
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
    """Cherche un membre par mention, nom, ou 'le dernier à avoir dit X'"""
    # Mention directe
    if description.startswith("<@") and description.endswith(">"):
        uid = description.strip("<@!>")
        try:
            return guild.get_member(int(uid))
        except:
            return None

    # Chercher par nom
    description_lower = description.lower()
    for member in guild.members:
        if description_lower in member.display_name.lower() or description_lower in member.name.lower():
            return member

    # Chercher dans les derniers messages du channel
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

@client.event
async def on_ready():
    print(f"✅ Bot connecté en tant que {client.user}")

@client.event
async def on_message(message):
    if message.author.bot:
        return

    # Vérifie que c'est le bon salon
    if message.channel.name != MODERATION_CHANNEL.lstrip("⚠️・").strip() and message.channel.name != MODERATION_CHANNEL:
        # Comparaison flexible
        channel_clean = MODERATION_CHANNEL.replace("⚠️・", "").replace("・", "-").strip()
        msg_channel = message.channel.name.replace("⚠️・", "").replace("・", "-").strip()
        if channel_clean not in msg_channel and msg_channel not in channel_clean:
            return

    # Vérifie les permissions
    if not has_permission(message.author):
        await message.channel.send("❌ Tu n'as pas la permission d'utiliser le bot de modération.")
        return

    # Gestion des confirmations (✅ ou ❌)
    if message.content.strip() in ["✅", "oui", "yes", "confirme", "ok"]:
        action_data = pending_actions.pop(message.author.id, None)
        if action_data:
            await execute_action(message.guild, action_data, message.channel)
        return

    if message.content.strip() in ["❌", "non", "no", "annule", "cancel"]:
        if message.author.id in pending_actions:
            pending_actions.pop(message.author.id)
            await message.channel.send("❌ Action annulée.")
        return

    # Analyse le message avec Gemini
    async with message.channel.typing():
        try:
            response = model.generate_content(
                f"{SYSTEM_PROMPT}\n\nMessage du modérateur: {message.content}"
            )
            raw = response.text.strip()

            # Nettoie le JSON si besoin
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
        return  # Pas d'action, on ignore

    if action_data.get("needs_clarification"):
        await message.channel.send(f"❓ {action_data.get('clarification_question')}")
        return

    # Message de confirmation
    confirm_msg = action_data.get("confirmation_message", "Action de modération détectée.")
    await message.channel.send(
        f"⚠️ **Confirmation requise**\n{confirm_msg}\n\nRéponds ✅ pour confirmer ou ❌ pour annuler."
    )
    pending_actions[message.author.id] = action_data

    # Timeout auto après 30 secondes
    await asyncio.sleep(30)
    if message.author.id in pending_actions:
        pending_actions.pop(message.author.id)
        await message.channel.send("⏱️ Confirmation expirée, action annulée.")

client.run(DISCORD_TOKEN)
