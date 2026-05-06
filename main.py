import discord
from discord.ext import commands
from discord import app_commands
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
    get_log_channel, log_action, reformulate_reason, update_boost, check_spam
)
from economy import (
    add_xp_and_coins, cmd_profil, cmd_inventaire, cmd_boutique,
    cmd_acheter, cmd_equiper, cmd_spin, cmd_classement, cmd_daily,
    cmd_parrainer, get_level_from_xp
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
intents.voice_states = True

client = commands.Bot(command_prefix="!", intents=intents, help_command=None)
member_message_days = {}

# ── VOICE COINS ──────────────────────────────────────────────────────────────
voice_timers = {}
voice_start_time = {}
VOICE_COINS_PER_MINUTE = 2
VOICE_XP_PER_MINUTE = 0

# ── SHADOW BAN ────────────────────────────────────────────────────────────────
shadow_banned = {}

def normalize_name(s):
    s = s.lower().replace("・", "")
    return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode("ascii")

# ============================================================
# VOICE COINS
# ============================================================
async def voice_coins_loop(member):
    while True:
        await asyncio.sleep(60)
        if member.voice and not member.voice.self_mute and not member.voice.self_deaf:
            await add_xp_and_coins(member, member.guild, VOICE_XP_PER_MINUTE, VOICE_COINS_PER_MINUTE)
        elif not member.voice:
            break

@client.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return
    uid = member.id
    if after.channel and (not before.channel or before.channel != after.channel):
        voice_start_time[uid] = datetime.now(timezone.utc)
        if uid in voice_timers:
            voice_timers[uid].cancel()
        voice_timers[uid] = asyncio.create_task(voice_coins_loop(member))
    elif before.channel and (not after.channel or after.channel != before.channel):
        if uid in voice_timers:
            voice_timers[uid].cancel()
            del voice_timers[uid]
        if uid in voice_start_time:
            duree_min = (datetime.now(timezone.utc) - voice_start_time[uid]).total_seconds() / 60
            coins = int(duree_min * VOICE_COINS_PER_MINUTE)
            if coins > 0:
                await add_xp_and_coins(member, member.guild, 0, coins)
                try:
                    await member.send(f"🎤 **+{coins} 🪙** pour **{int(duree_min)} min** passées en vocal !")
                except Exception:
                    pass
            del voice_start_time[uid]

# ============================================================
# SHADOW BAN
# ============================================================
async def shadow_ban_user(guild, moderator, target):
    db = load_db()
    data = get_member_data(db, target.id)
    data["shadow_banned"] = True
    data["shadow_ban_since"] = datetime.now(timezone.utc).isoformat()
    data["shadow_ban_count"] = data.get("shadow_ban_count", 0) + 1
    save_db(db)
    shadow_banned[target.id] = {"since": datetime.now(timezone.utc), "blocked": 0, "spam_score": 0}
    report_ch = get_channel_by_name(guild, "rapport-prowler")
    if report_ch:
        embed = discord.Embed(title="🕵️ SHADOW MODE ACTIF", color=0x2C2C2C, timestamp=datetime.now(timezone.utc))
        embed.add_field(name="👤 Cible", value=f"{target.mention} [SHADOW #{data['shadow_ban_count']}]", inline=True)
        embed.add_field(name="👮 Modérateur", value=moderator.mention, inline=True)
        embed.add_field(name="📊 Statut", value="0 msg bloqués | Score spam 0%", inline=False)
        msg = await report_ch.send(embed=embed)
        await msg.add_reaction("👁️")
        await msg.add_reaction("❌")

async def shadow_unban_user(guild, target_id):
    db = load_db()
    data = get_member_data(db, target_id)
    data["shadow_banned"] = False
    save_db(db)
    shadow_banned.pop(target_id, None)

# ============================================================
# AUTO-APPEAL IA
# ============================================================
async def auto_appeal_check(guild, banned_user):
    db = load_db()
    data = db.get(str(banned_user.id), {})
    sanctions = data.get("sanctions", [])
    total_warns = data.get("total_warns", 0)
    bans_count = data.get("bans", 0)
    score_injuste = 100
    if total_warns > 3: score_injuste -= 20
    if total_warns > 6: score_injuste -= 20
    if bans_count > 1: score_injuste -= 30
    recent_sanctions = [s for s in sanctions if s.get("type") in ["spam_mute", "mute"]]
    if len(recent_sanctions) > 2: score_injuste -= 15
    score_injuste = max(10, min(95, score_injuste))
    recommandation = "Unban + surveillance" if score_injuste >= 60 else "Maintenir le ban"
    couleur = 0x2ECC71 if score_injuste >= 60 else 0xE74C3C
    try:
        await banned_user.send(
            f"🤖 **Auto-Appeal Prowler Bot**\n\n"
            f"Ton ban a été analysé automatiquement.\n"
            f"📊 **Score IA : {score_injuste}% potentiellement injuste**\n"
            f"Une révision par les modérateurs est en cours.\n"
            f"Résultat dans **3h** maximum."
        )
    except Exception:
        pass
    report_ch = get_channel_by_name(guild, "rapport-prowler")
    if not report_ch:
        return
    embed = discord.Embed(title="🤖 AUTO-APPEAL", color=couleur, timestamp=datetime.now(timezone.utc))
    embed.set_thumbnail(url=banned_user.display_avatar.url)
    embed.add_field(name="👤 Utilisateur", value=f"{banned_user} (ID: {banned_user.id})", inline=True)
    embed.add_field(name="📊 Score IA", value=f"**{score_injuste}% injuste**", inline=True)
    embed.add_field(name="📝 Historique", value=f"⚠️ Warns : {total_warns} | 🔇 Mutes : {len(recent_sanctions)} | 🔨 Bans : {bans_count}", inline=False)
    embed.add_field(name="✅ Recommandation", value=recommandation, inline=False)
    embed.set_footer(text="✅ Unban • ❌ Refuser • ℹ️ Profil")
    msg = await report_ch.send(embed=embed)
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")
    await msg.add_reaction("ℹ️")

# ============================================================
# HELP
# ============================================================
async def send_help(channel):
    channel_name = normalize_name(channel.name)
    embed = discord.Embed(color=0x3498db, timestamp=datetime.now(timezone.utc))

    VOC_SECTION = (
        "\n\n**🎙️ Salons vocaux (depuis n'importe quel salon)**\n"
        "`!createvoc NomDuSalon` — créer un salon vocal temporaire\n"
        "`!vockick @m` • `!vocmute @m` • `!vocunmute @m`\n"
        "`!voclock` • `!vocunlock` • `!vocrename Nom` • `!vocsuppr`"
    )

    if "cmds-" in channel_name:
        embed.title = "📖 Ton salon vocal privé 🎙️"
        embed.description = (
            "Ces commandes sont utilisables **depuis n'importe quel salon** :\n\n"
            "`!vockick @membre` — expulser un membre\n"
            "`!vocmute @membre` — muter un membre\n"
            "`!vocunmute @membre` — démuter un membre\n"
            "`!voclock` — fermer le salon aux nouveaux\n"
            "`!vocunlock` — rouvrir le salon\n"
            "`!vocrename NouveauNom` — renommer le salon\n"
            "`!vocsuppr` — supprimer le salon"
        )
    elif "jeux" in channel_name:
        embed.title = "📖 Commandes — 🎮・jeux"
        embed.description = (
            "**Profil & Stats**\n"
            "`!profil` / `/profil [@membre]` — niveau, XP, pièces\n"
            "`!inventaire` / `/inventaire` — rôles achetés\n"
            "`!classement` / `/classement` — top 10 membres\n"
            "`/solde` — vérifier ton solde (discret)\n\n"
            "**Cartes**\n"
            "`!collection [@pseudo]` — ta collection\n"
            "`!cartesinfo` — probabilités des raretés\n\n"
            "**Social**\n"
            "`!parrainer @pseudo` — parrainer un ami (+100 🪙 chacun)\n\n"
            "🎤 **+2 🪙/min** en vocal automatiquement !"
            + VOC_SECTION
        )
    elif "boutique" in channel_name:
        embed.title = "📖 Commandes — 🛍️・boutique"
        embed.description = (
            "**Boutique Rôles**\n"
            "`!boutique` / `/boutique` — voir la boutique\n"
            "`!acheter [nom]` / `/acheter` — acheter un article\n"
            "`!équiper [nom]` / `/equiper` — équiper un rôle\n"
            "`!spin` / `/rolespin` — gacha rôles (50 🪙)\n\n"
            "**Cartes**\n"
            "`!cardspin` / `/cardspin` — gacha cartes (100 🪙)\n"
            "`!cartesinfo` — probabilités des raretés\n\n"
            "💡 Boutique rotative renouvellement toutes les **3h**"
            + VOC_SECTION
        )
    elif "daily" in channel_name:
        embed.title = "📖 Commandes — 🎁・daily"
        embed.description = (
            "`!daily` / `/daily` — récompense quotidienne\n\n"
            "🔥 **Streak :** x1.5 (3j) → x2 (7j) → x2.5 (14j) → x3 (30j)\n"
            "💰 Base : 50 🪙 + 20 XP\n"
            "⚠️ Un jour manqué → streak repart à **0** !"
            + VOC_SECTION
        )
    elif "trade" in channel_name:
        embed.title = "📖 Commandes — 🔄・trades"
        embed.description = (
            "`!trade @membre` / `/trade` — trade interactif\n"
            "`!trade @membre give X contre Y` — trade rapide\n"
            "`!donner @membre [montant]` — donner des pièces\n"
            "`!collection [@pseudo]` — voir une collection\n\n"
            "**Modérateurs uniquement**\n"
            "`!tradecancel @membre` — débloquer un trade figé"
            + VOC_SECTION
        )
    elif "casino" in channel_name:
        embed.title = "📖 Commandes — 🎰・casino"
        embed.description = (
            "**🃏 Blackjack — 3 Tables**\n"
            "`/blackjack-low [mise]` — Table Low (10–100 🪙)\n"
            "`/blackjack-high [mise]` — Table High (500–5k 🪙)\n"
            "`/blackjack-vip [mise]` — VIP (10k+ 🪙)\n"
            "`/blackjack-stats` — tes stats\n\n"
            "**Règles Blackjack :**\n"
            "• But : atteindre 21 sans dépasser, battre le croupier\n"
            "• ✅ **Hit** → tirer une carte\n"
            "• ❌ **Stand** → rester sur sa main\n"
            "• ⚡ **Double** → doubler la mise + 1 seule carte\n"
            "• 🌟 Blackjack naturel (As + figure) = gain ×1.5\n"
            "• Le croupier tire jusqu'à 17 minimum\n\n"
            "**🎲 Dice — Double ou Rien**\n"
            "`/dice [mise]` — mise : 10–1000 🪙\n\n"
            "**Règles Dice :**\n"
            "• Chacun lance un dé (1–6)\n"
            "• Ton dé > croupier → **+mise** 🪙\n"
            "• Ton dé < croupier → **-mise** 🪙\n\n"
            "**⚔️ Gacha Duel — PVP (Cartes)**\n"
            "`/gacha-duel @adversaire [mise]` — mise : 25–500 🪙\n"
            "`/top-duel` — leaderboard duels\n\n"
            "**Règles Gacha Duel :**\n"
            "• Défie un membre : chacun tire une carte de sa collection\n"
            "• La carte de **rareté la plus haute** remporte le pot (mise ×2)\n"
            "• Ordre : ⚫Shlag < ⚪Commun < 🔵Rare < 🟣Épique < 🟢Hallal < 🟡Légendaire < 🔴Mythique < 🌈Secret\n"
            "• Égalité de rareté → victoire aléatoire !\n"
            "• Le défié a **30 secondes** pour accepter ou refuser\n\n"
            "**🎖️ Rôles Débloquables (automatiques)**\n"
            "• 🃏 **Card Shark** — 60%+ winrate blackjack (10 parties min)\n"
            "• 🎰 **Pro Gambler** — 100 parties de blackjack\n"
            "• 💎 **High Roller** — 5 000+ 🪙 en solde\n\n"
            "Utilise `/casino-help` pour le guide en embed !"
        )
    elif "ticket" in channel_name:
        embed.title = "📖 Commandes — 🎟️・tickets"
        embed.description = (
            "Réagis avec **🎫** pour ouvrir un ticket.\n\n"
            "• Contester une sanction\n"
            "• Signaler un problème\n"
            "• Demander de l'aide\n\n"
            "⚠️ Les membres **mutés** peuvent toujours ouvrir un ticket.\n"
            "🔒 Les modérateurs ferment le ticket avec **🔒**."
        )
    elif "moderation" in channel_name:
        embed.title = "📖 Commandes — Modération"
        embed.description = (
            "Écris en **langage naturel** :\n"
            "`mute @pseudo 30 minutes` • `ban @pseudo`\n"
            "`kick @pseudo` • `warn @pseudo`\n"
            "`unmute @pseudo` • `unban @pseudo`\n"
            "`supprime les 10 derniers messages de @pseudo`\n"
            "`profil de @pseudo`\n\n"
            "**Fondateur uniquement :**\n"
            "`!give @membre 500` — donner pièces\n"
            "`!give @membre role NomDuRole` — donner rôle\n\n"
            "**Shadow Ban :**\n"
            "`!shadowban @membre` — shadow-ban silencieux\n"
            "`!shadowunban @membre` — lever le shadow-ban\n\n"
            "**Trades :**\n"
            "`!tradecancel @membre` — débloquer un trade figé"
        )
    elif "log" in channel_name:
        embed.title = "📖 Lecture des logs"
        embed.description = (
            "🔨 Bans • 👢 Kicks • 🔇 Mutes • ⚠️ Warns\n"
            "🔊 Demutes • ✅ Débans • 📥 Arrivées • 📤 Départs\n"
            "💬 Commentaires modos • 🤖 Anti-spam\n"
            "🛍️ Achats • 🎰 Gacha • 🎁 Daily • 👗 Équipements\n"
            "🎟️ Tickets • 🔄 Trades • 🕵️ Shadow bans"
        )
    else:
        embed.title = "📖 Aide — Prowler Bot"
        embed.description = (
            "🎮・jeux — profil, classement, inventaire, cartes\n"
            "🛍️・boutique — boutique, gacha rôles & cartes\n"
            "🎁・daily — récompense quotidienne\n"
            "🔄・trades — échanges de cartes et dons\n"
            "🎰・casino — blackjack, dice, gacha duel\n"
            "🎟️・tickets — contester une sanction\n\n"
            "🎙️ **Salon vocal privé**\n"
            "`!createvoc NomDuSalon` — crée un salon vocal **+ un salon textuel privé** automatiquement\n"
            "→ Le salon texte privé sert à gérer ton vocal (`!voclock`, `!vockick`, etc.)\n"
            "→ Seuls toi et les membres que tu invites peuvent le voir\n\n"
            "🎤 **+2 🪙/min** en vocal automatiquement !\n\n"
            "Tape `?help` dans chaque salon pour les commandes détaillées."
        )

    embed.set_footer(text="Prowler Bot • ! et / sont tous les deux acceptés")
    await channel.send(embed=embed)

# ============================================================
# SLASH COMMANDS
# ============================================================
@client.tree.command(name="profil", description="Affiche ton profil : niveau, XP, pièces, rôle équipé")
@app_commands.describe(membre="Le membre dont tu veux voir le profil (optionnel)")
async def slash_profil(interaction: discord.Interaction, membre: discord.Member = None):
    await interaction.response.defer()
    target = membre or interaction.user
    db = load_db()
    data = get_member_data(db, target.id)
    level, current_xp, needed_xp = get_level_from_xp(data["xp"])
    progress = int((current_xp / needed_xp) * 10) if needed_xp > 0 else 0
    progress_bar = "█" * progress + "░" * (10 - progress)
    embed = discord.Embed(title=f"👤 Profil — {target.display_name}", color=0x3498db)
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="⭐ Niveau", value=str(level), inline=True)
    embed.add_field(name="✨ XP", value=f"{current_xp}/{needed_xp}", inline=True)
    embed.add_field(name="🪙 Pièces", value=str(data["coins"]), inline=True)
    embed.add_field(name="📊 Progression", value=f"`{progress_bar}`", inline=False)
    embed.add_field(name="🔥 Streak daily", value=f"{data['daily_streak']} jours", inline=True)
    equipped = data.get("equipped", [])
    embed.add_field(name="👗 Rôle équipé", value=", ".join(equipped) if equipped else "Aucun", inline=True)
    await interaction.followup.send(embed=embed)

@client.tree.command(name="solde", description="Vérifie rapidement ton solde de pièces")
async def slash_solde(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    db = load_db()
    data = get_member_data(db, interaction.user.id)
    await interaction.followup.send(f"🪙 Tu as **{data['coins']} pièces** et **{data['xp']} XP**.", ephemeral=True)

@client.tree.command(name="classement", description="Affiche le top 10 des membres les plus actifs")
async def slash_classement(interaction: discord.Interaction):
    await interaction.response.defer()
    db = load_db()
    members_data = []
    for mid, data in db.items():
        member = interaction.guild.get_member(int(mid))
        if member:
            level, _, _ = get_level_from_xp(data.get("xp", 0))
            members_data.append((member.display_name, level, data.get("xp", 0), data.get("coins", 0)))
    members_data.sort(key=lambda x: x[2], reverse=True)
    top = members_data[:10]
    medals = ["🥇", "🥈", "🥉"] + ["4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    lines = [f"{medals[i]} **{name}** — Niv. {level} • {xp} XP • {coins} 🪙" for i, (name, level, xp, coins) in enumerate(top)]
    embed = discord.Embed(title="🏆 Classement — Top 10", description="\n".join(lines) if lines else "Aucun membre.", color=0xf1c40f)
    await interaction.followup.send(embed=embed)

# ── BUG FIX #1 : slash_daily, slash_rolespin, slash_boutique, slash_acheter, slash_equiper
# Le FakeMsg ne répondait jamais à l'interaction → "L'application ne répond plus"
# Solution : envoyer la réponse via interaction.followup après avoir récupéré le résultat

@client.tree.command(name="daily", description="Récupère ta récompense quotidienne")
async def slash_daily(interaction: discord.Interaction):
    await interaction.response.defer()
    class FakeMsg:
        author = interaction.user
        guild = interaction.guild
        channel = interaction.channel
        mentions = []
        async def reply(self, *args, **kwargs):
            await interaction.followup.send(*args, **kwargs)
    await cmd_daily(FakeMsg())

@client.tree.command(name="rolespin", description="Lance le gacha de rôles (50 🪙)")
async def slash_rolespin(interaction: discord.Interaction):
    await interaction.response.defer()
    class FakeMsg:
        author = interaction.user
        guild = interaction.guild
        channel = interaction.channel
        mentions = []
        async def reply(self, *args, **kwargs):
            await interaction.followup.send(*args, **kwargs)
    await cmd_spin(FakeMsg())

@client.tree.command(name="boutique", description="Affiche la boutique")
async def slash_boutique(interaction: discord.Interaction):
    await interaction.response.defer()
    class FakeMsg:
        author = interaction.user
        guild = interaction.guild
        channel = interaction.channel
        mentions = []
        async def reply(self, *args, **kwargs):
            await interaction.followup.send(*args, **kwargs)
    await cmd_boutique(FakeMsg())

@client.tree.command(name="acheter", description="Achète un article de la boutique")
@app_commands.describe(article="Le nom de l'article à acheter")
async def slash_acheter(interaction: discord.Interaction, article: str):
    await interaction.response.defer()
    class FakeMsg:
        author = interaction.user
        guild = interaction.guild
        channel = interaction.channel
        mentions = []
        async def reply(self, *args, **kwargs):
            await interaction.followup.send(*args, **kwargs)
    await cmd_acheter(FakeMsg(), article)

@client.tree.command(name="equiper", description="Équipe un rôle cosmétique de ton inventaire")
@app_commands.describe(role="Le nom du rôle à équiper")
async def slash_equiper(interaction: discord.Interaction, role: str):
    await interaction.response.defer()
    class FakeMsg:
        author = interaction.user
        guild = interaction.guild
        channel = interaction.channel
        mentions = []
        async def reply(self, *args, **kwargs):
            await interaction.followup.send(*args, **kwargs)
    await cmd_equiper(FakeMsg(), role)

@client.tree.command(name="notif", description="Active ou désactive les notifications de level up en MP")
@app_commands.describe(etat="on pour activer, off pour désactiver")
@app_commands.choices(etat=[
    app_commands.Choice(name="Activer", value="on"),
    app_commands.Choice(name="Désactiver", value="off"),
])
async def slash_notif(interaction: discord.Interaction, etat: str):
    await interaction.response.defer(ephemeral=True)
    db = load_db()
    data = get_member_data(db, interaction.user.id)
    data["levelup_notif"] = (etat == "on")
    save_db(db)
    status = "✅ activées" if etat == "on" else "❌ désactivées"
    await interaction.followup.send(f"Notifications de level up **{status}**.", ephemeral=True)

@client.tree.command(name="help", description="Affiche l'aide des commandes disponibles")
async def slash_help(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await send_help(interaction.channel)
    await interaction.followup.send("✅ Aide envoyée !", ephemeral=True)

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
    embed = discord.Embed(title=f"📝 Rapport de modération — {today}", color=0x3498db, timestamp=datetime.now(timezone.utc))
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
    if shadow_banned:
        sb_txt = "\n".join([f"• ID:{uid} — {v['blocked']} msgs bloqués" for uid, v in list(shadow_banned.items())[:5]])
        embed.add_field(name=f"🕵️ Shadow bans actifs ({len(shadow_banned)})", value=sb_txt, inline=False)
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
    from config import SHOP_ROTATE_INTERVAL
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
                await asyncio.sleep(10800)
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
                embed.set_footer(text="!acheter [nom] ou /acheter pour acheter")
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
                    except: pass
                elif inactive_streak >= INACTIVE_DAYS_REQUIRED and has_actif:
                    try:
                        await member.remove_roles(role_actif)
                        if role_membre not in member.roles:
                            await member.add_roles(role_membre)
                    except: pass

# ============================================================
# ÉVÉNEMENTS
# ============================================================
@client.event
async def on_ready():
    print(f"✅ {client.user} connecté !")
    client.loop.create_task(daily_report_loop())
    client.loop.create_task(update_active_roles_loop())
    client.loop.create_task(shop_rotate_loop())
    print("✅ Bot prêt !")

async def setup_hook():
    for ext in ["cards", "trades", "voc", "tickets", "casino", "imposteur"]:
        try:
            await client.load_extension(ext)
            print(f"✅ Extension {ext} chargée")
        except Exception as e:
            print(f"❌ Erreur chargement {ext} : {e}")
    try:
        synced = await client.tree.sync()
        print(f"✅ {len(synced)} slash commands synchronisées")
    except Exception as e:
        print(f"❌ Erreur sync : {e}")

@client.event
async def on_member_join(member):
    guild = member.guild
    role = discord.utils.get(guild.roles, name=ROLE_MEMBRE)
    if role:
        try: await member.add_roles(role)
        except: pass
    general = get_channel_by_name(guild, "chat-général")
    if general:
        await general.send(f"👋 Bienvenue sur le serveur, {member.mention} !")
    await log_action(guild, "join", None, member)

@client.event
async def on_member_remove(member):
    await log_action(member.guild, "leave", None, member)

@client.event
async def on_member_ban(guild, user):
    await asyncio.sleep(2)
    await auto_appeal_check(guild, user)

@client.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return
    msg_id = reaction.message.id

    embed = reaction.message.embeds[0] if reaction.message.embeds else None
    if embed and "AUTO-APPEAL" in (embed.title or ""):
        if not has_permission(reaction.message.guild.get_member(user.id)):
            return
        for field in embed.fields:
            if "ID:" in field.value:
                try:
                    uid = int(field.value.split("ID: ")[1].rstrip(")"))
                    if str(reaction.emoji) == "✅":
                        try:
                            await reaction.message.guild.unban(discord.Object(id=uid), reason="Auto-appeal IA approuvé")
                            await reaction.message.channel.send(f"✅ Utilisateur (ID: {uid}) débanni suite à l'auto-appeal.")
                        except Exception as e:
                            await reaction.message.channel.send(f"❌ Erreur unban : {e}")
                    elif str(reaction.emoji) == "❌":
                        await reaction.message.channel.send(f"❌ Appeal refusé pour ID: {uid}.")
                except Exception:
                    pass
                break

    if msg_id in waiting_for_action_choice:
        choice_type, member, action_data, requester_id = waiting_for_action_choice[msg_id]

        if choice_type == "banned_choice":
            if user.id != requester_id: return
            waiting_for_action_choice.pop(msg_id, None)
            if str(reaction.emoji) == "✅":
                action_data["action"] = "unban"
                await send_confirmation(reaction.message.channel, action_data, requester_id)
            elif str(reaction.emoji) == "🔍":
                db = load_db()
                data = get_member_data(db, member.id)
                embed2 = discord.Embed(title=f"👤 Profil (banni) — {member.display_name}", color=0xe74c3c)
                embed2.set_thumbnail(url=member.display_avatar.url)
                embed2.add_field(name="🏷️ Pseudo", value=member.name, inline=True)
                embed2.add_field(name="🆔 ID", value=f"`{member.id}`", inline=True)
                embed2.add_field(name="⚡ Statut", value="🔨 **Banni du serveur**", inline=False)
                embed2.add_field(name="🛡️ Historique", value=f"⚠️ Warns total : {data['total_warns']}\n🔇 Mutes : {data['mutes']} | 👢 Kicks : {data['kicks']} | 🔨 Bans : {data['bans']}", inline=False)
                await reaction.message.channel.send(embed=embed2)
            elif str(reaction.emoji) == "❌":
                await reaction.message.channel.send(embed=discord.Embed(title="❌ Action annulée", color=0x95a5a6))
            return

        if choice_type == "sanction_or_profile":
            if user.id != requester_id: return
            waiting_for_action_choice.pop(msg_id)
            if str(reaction.emoji) == "⚔️":
                if action_data.get("reason"):
                    await send_confirmation(reaction.message.channel, action_data, requester_id)
                else:
                    await reaction.message.channel.send(embed=discord.Embed(title="📝 Raison de la sanction", description="Quelle est la raison ?", color=0x3498db))
                    waiting_for_reason[requester_id] = action_data
            elif str(reaction.emoji) == "🔍":
                await show_profile(reaction.message.channel, member, reaction.message.guild)
            elif str(reaction.emoji) == "❌":
                await reaction.message.channel.send(embed=discord.Embed(title="❌ Action annulée", color=0x95a5a6))

        elif choice_type == "comment_mgmt":
            mod_member = reaction.message.guild.get_member(user.id)
            if not mod_member or not has_permission(mod_member): return
            waiting_for_action_choice.pop(msg_id)
            if str(reaction.emoji) == "➕":
                await reaction.message.channel.send(embed=discord.Embed(title="💬 Ajouter un commentaire", description=f"Écris ton commentaire pour **{member.display_name}** :", color=0x3498db))
                waiting_for_comment[user.id] = (member.id, "add", None)
            elif str(reaction.emoji) == "➖":
                db = load_db()
                data = get_member_data(db, member.id)
                comments = data.get("comments", [])
                if not comments:
                    await reaction.message.channel.send("Aucun commentaire à supprimer.")
                    return
                emojis_c = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
                embed2 = discord.Embed(title="🗑️ Supprimer un commentaire", color=0xe74c3c)
                for i, c in enumerate(comments[:5]):
                    embed2.add_field(name=f"{emojis_c[i]}", value=c, inline=False)
                cmsg = await reaction.message.channel.send(embed=embed2)
                for i in range(len(comments[:5])):
                    await cmsg.add_reaction(emojis_c[i])
                waiting_for_comment[user.id] = (member.id, "remove_pick", cmsg.id)
                waiting_for_action_choice[cmsg.id] = ("comment_remove_pick", member, None, user.id)

        elif choice_type == "comment_remove_pick":
            if user.id != requester_id: return
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
                    await reaction.message.channel.send(embed=discord.Embed(title="✅ Commentaire supprimé", color=0x2ecc71))
                    target = reaction.message.guild.get_member(member.id)
                    mod_m = reaction.message.guild.get_member(user.id)
                    await log_action(reaction.message.guild, "comment_remove", mod_m, target, extra={"Commentaire supprimé": removed})
        return

    if msg_id in waiting_for_member_choice:
        action_data, requester_id, candidates = waiting_for_member_choice[msg_id]
        if user.id != requester_id: return
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
        if user.id != requester_id: return
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

    if message.author.id in shadow_banned:
        sb = shadow_banned[message.author.id]
        sb["blocked"] = sb.get("blocked", 0) + 1
        try:
            await message.delete()
        except Exception:
            pass
        return

    await client.process_commands(message)

    channel_name = normalize_name(message.channel.name)
    content = message.content.strip()
    content_lower = content.lower()

    mid = str(message.author.id)
    today = datetime.now(timezone.utc).date().isoformat()
    if mid not in member_message_days:
        member_message_days[mid] = {}
    member_message_days[mid][today] = member_message_days[mid].get(today, 0) + 1

    if len(member_message_days) > 5000:
        member_message_days.clear()

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
        if content_lower in ["!boutique", "!spin"] or content_lower.startswith(("!acheter", "!équiper", "!cardspin")):
            boutique_ch = get_channel_by_name(message.guild, "boutique")
            if boutique_ch:
                await message.channel.send(f"❌ Cette commande est réservée à {boutique_ch.mention} !")
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
        spammed = await check_spam(message)
        if spammed:
            return
        await message.channel.send(embed=discord.Embed(title="❌ Permission refusée", description="Tu n'as pas la permission d'utiliser le bot de modération.", color=0xe74c3c))
        return

    if content_lower.startswith("!give"):
        await cmd_give(message, content[5:].strip())
        return

    if content_lower.startswith("!shadowban"):
        if not any(r.name in ["Modérateur", "Fondateur"] for r in message.author.roles):
            await message.channel.send("❌ Réservé aux modérateurs.")
            return
        if message.mentions:
            target = message.mentions[0]
            await shadow_ban_user(message.guild, message.author, target)
            await message.channel.send(f"🕵️ **{target.display_name}** est maintenant shadow-banni.")
        return

    if content_lower.startswith("!shadowunban"):
        if not any(r.name in ["Modérateur", "Fondateur"] for r in message.author.roles):
            await message.channel.send("❌ Réservé aux modérateurs.")
            return
        if message.mentions:
            target = message.mentions[0]
            await shadow_unban_user(message.guild, target.id)
            await message.channel.send(f"✅ **{target.display_name}** n'est plus shadow-banni.")
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
            await message.channel.send(embed=discord.Embed(title="✅ Commentaire ajouté", description=comment_text, color=0x2ecc71))
            await log_action(message.guild, "comment_add", message.author, target, extra={"Commentaire": message.content})
        return

    if message.author.id in waiting_for_reason:
        action_data = waiting_for_reason.pop(message.author.id)
        async with message.channel.typing():
            refined = await reformulate_reason(message.content)
        action_data["reason"] = refined
        await send_confirmation(message.channel, action_data, message.author.id)
        return

    if not await is_moderation_command(content):
        return

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
        await message.channel.send(embed=discord.Embed(title="❌ Erreur IA", description=f"```{e}```", color=0xe74c3c))
        return

    if action_data.get("action") == "none":
        target = action_data.get("target", "").strip()
        if target:
            exact, similar, is_id, is_banned = await find_member(message.guild, target, message.channel)
            all_candidates = exact + similar
            if all_candidates:
                action_data["action"] = "show_profile"
                action_data["resolved_member"] = all_candidates[0]
                if len(all_candidates) == 1:
                    await ask_action_choice(message.channel, all_candidates[0], action_data, message.author.id)
                else:
                    await handle_member_resolution(message.channel, action_data, message.author.id, exact, similar, is_id, is_banned)
        return

    if action_data.get("needs_clarification"):
        await message.channel.send(f"❓ {action_data.get('clarification_question')}")
        return

    exact, similar, is_id, is_banned = await find_member(message.guild, action_data.get("target", ""), message.channel)
    await handle_member_resolution(message.channel, action_data, message.author.id, exact, similar, is_id, is_banned)

async def setup_hook():
    for ext in ["cards", "trades", "voc", "tickets", "casino", "imposteur"]:
        try:
            await client.load_extension(ext)
            print(f"✅ Extension {ext} chargée")
        except Exception as e:
            print(f"❌ Erreur chargement {ext} : {e}")
    try:
        synced = await client.tree.sync()
        print(f"✅ {len(synced)} slash commands synchronisées")
    except Exception as e:
        print(f"❌ Erreur sync : {e}")

import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

def run_server():
    HTTPServer(("0.0.0.0", 8080), BaseHTTPRequestHandler).serve_forever()

threading.Thread(target=run_server, daemon=True).start()

client.setup_hook = setup_hook

client.run(DISCORD_TOKEN)
