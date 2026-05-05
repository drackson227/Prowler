"""
slash_commands.py — Slash commands (/) pour Prowler Bot
Enregistre toutes les commandes Discord Application Commands.
"""

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone

from db import load_db, save_db, get_member_data
from economy import (
    cmd_profil, cmd_boutique, cmd_classement,
    cmd_acheter, cmd_equiper, cmd_spin, cmd_daily, cmd_parrainer,
    get_level_from_xp, equip_role_discord
)
from shop import load_shop
from utils import get_channel_by_name, has_permission, log_action


# ============================================================
# HELPER — Faux message pour réutiliser les fonctions existantes
# ============================================================
class FakeMessage:
    """Simule un discord.Message pour réutiliser les fonctions existantes."""
    def __init__(self, interaction: discord.Interaction, content: str = ""):
        self.author = interaction.user
        self.guild = interaction.guild
        self.channel = interaction.channel
        self.content = content
        self.mentions = []


class FakeChannel:
    """Canal simulé qui envoie via followup d'une interaction."""
    def __init__(self, interaction: discord.Interaction):
        self.interaction = interaction
        self.name = interaction.channel.name
        self.guild = interaction.guild
        self._state = interaction.channel._state

    async def send(self, content=None, embed=None, **kwargs):
        if embed:
            await self.interaction.followup.send(embed=embed, ephemeral=False)
        elif content:
            await self.interaction.followup.send(content=content, ephemeral=False)

    async def _state(self):
        return self.interaction.channel._state


# ============================================================
# COG SLASH COMMANDS
# ============================================================
class SlashCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ──────────────────────────────────────────────────────────
    # /profil
    # ──────────────────────────────────────────────────────────
    @app_commands.command(name="profil", description="Affiche ton profil : niveau, XP, pièces, rôle équipé")
    @app_commands.describe(membre="Le membre dont tu veux voir le profil (optionnel)")
    async def slash_profil(self, interaction: discord.Interaction, membre: discord.Member = None):
        await interaction.response.defer()
        target = membre or interaction.user
        db = load_db()
        data = get_member_data(db, target.id)
        level, current_xp, needed_xp = get_level_from_xp(data["xp"])
        progress = int((current_xp / needed_xp) * 10) if needed_xp > 0 else 0
        progress_bar = "█" * progress + "░" * (10 - progress)
        embed = discord.Embed(
            title=f"👤 Profil — {target.display_name}",
            color=0x3498db
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="⭐ Niveau", value=str(level), inline=True)
        embed.add_field(name="✨ XP", value=f"{current_xp}/{needed_xp}", inline=True)
        embed.add_field(name="🪙 Pièces", value=str(data["coins"]), inline=True)
        embed.add_field(name="📊 Progression", value=f"`{progress_bar}`", inline=False)
        embed.add_field(name="🔥 Streak daily", value=f"{data['daily_streak']} jours", inline=True)
        equipped = data.get("equipped", [])
        embed.add_field(name="👗 Rôle équipé", value=", ".join(equipped) if equipped else "Aucun", inline=True)
        await interaction.followup.send(embed=embed)

    # ──────────────────────────────────────────────────────────
    # /inventaire
    # ──────────────────────────────────────────────────────────
    @app_commands.command(name="inventaire", description="Affiche ton inventaire et permet d'équiper/déséquiper un rôle")
    async def slash_inventaire(self, interaction: discord.Interaction):
        await interaction.response.defer()
        db = load_db()
        data = get_member_data(db, interaction.user.id)
        inventory = data.get("inventory", [])
        equipped = data.get("equipped", [])

        embed = discord.Embed(
            title=f"🎒 Inventaire — {interaction.user.display_name}",
            color=0x9b59b6
        )

        if not inventory:
            embed.description = "Tu n'as aucun article dans ton inventaire."
            await interaction.followup.send(embed=embed)
            return

        NUMBER_EMOJIS = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
        display_items = inventory[:10]
        lines = []
        for i, item in enumerate(display_items):
            is_equipped = item["name"] in equipped
            equipped_tag = " ✅" if is_equipped else ""
            expire_tag = f" — expire le {item['expires']}" if item.get("expires") else ""
            lines.append(f"{NUMBER_EMOJIS[i]} **{item['name']}**{equipped_tag}{expire_tag}")

        embed.description = "\n".join(lines)
        embed.set_footer(text="Réagis avec le numéro pour équiper/déséquiper • ✅ = rôle actuellement équipé")
        inv_msg = await interaction.followup.send(embed=embed, wait=True)

        for i in range(len(display_items)):
            try:
                await inv_msg.add_reaction(NUMBER_EMOJIS[i])
            except:
                break

        def check(reaction, user):
            return (
                user.id == interaction.user.id
                and reaction.message.id == inv_msg.id
                and str(reaction.emoji) in NUMBER_EMOJIS[:len(display_items)]
            )

        try:
            reaction, user = await self.bot.wait_for("reaction_add", timeout=30.0, check=check)
            idx = NUMBER_EMOJIS.index(str(reaction.emoji))
            item = display_items[idx]

            db = load_db()
            data = get_member_data(db, interaction.user.id)
            equipped = data.get("equipped", [])

            if item["name"] in equipped:
                role = discord.utils.get(interaction.guild.roles, name=item["name"])
                if role and role in interaction.user.roles:
                    try:
                        await interaction.user.remove_roles(role)
                    except:
                        pass
                data["equipped"] = []
                save_db(db)
                await interaction.channel.send(embed=discord.Embed(
                    title="👗 Rôle retiré !",
                    description=f"Tu ne portes plus **{item['name']}**.",
                    color=0xe74c3c
                ))
            else:
                success, result = await equip_role_discord(interaction.guild, interaction.user, item, db, data)
                save_db(db)
                if success:
                    await interaction.channel.send(embed=discord.Embed(
                        title="👗 Rôle équipé !",
                        description=f"Tu portes maintenant **{result}** !",
                        color=0x2ecc71
                    ))
                    await log_action(interaction.guild, "shop_equip", None, interaction.user, extra={"Rôle équipé": result})
                else:
                    await interaction.channel.send(f"❌ {result}")
        except Exception:
            try:
                await inv_msg.clear_reactions()
            except:
                pass

    # ──────────────────────────────────────────────────────────
    # /boutique
    # ──────────────────────────────────────────────────────────
    @app_commands.command(name="boutique", description="Affiche la boutique standard, rotative et le gacha")
    async def slash_boutique(self, interaction: discord.Interaction):
        await interaction.response.defer()
        fake_msg = FakeMessage(interaction)
        fake_msg.channel = FakeChannel(interaction)
        await cmd_boutique(fake_msg)

    # ──────────────────────────────────────────────────────────
    # /acheter
    # ──────────────────────────────────────────────────────────
    @app_commands.command(name="acheter", description="Achète un article de la boutique")
    @app_commands.describe(article="Le nom de l'article à acheter")
    async def slash_acheter(self, interaction: discord.Interaction, article: str):
        await interaction.response.defer()
        fake_msg = FakeMessage(interaction, f"!acheter {article}")
        fake_msg.channel = FakeChannel(interaction)
        await cmd_acheter(fake_msg, article)

    # ──────────────────────────────────────────────────────────
    # /équiper
    # ──────────────────────────────────────────────────────────
    @app_commands.command(name="equiper", description="Équipe un rôle cosmétique de ton inventaire")
    @app_commands.describe(role="Le nom du rôle à équiper")
    async def slash_equiper(self, interaction: discord.Interaction, role: str):
        await interaction.response.defer()
        fake_msg = FakeMessage(interaction, f"!équiper {role}")
        fake_msg.channel = FakeChannel(interaction)
        await cmd_equiper(fake_msg, role)

    # ──────────────────────────────────────────────────────────
    # /spin (rolespin)
    # ──────────────────────────────────────────────────────────
    @app_commands.command(name="rolespin", description="Lance le gacha de rôles (coûte 50 🪙)")
    async def slash_rolespin(self, interaction: discord.Interaction):
        await interaction.response.defer()
        fake_msg = FakeMessage(interaction)
        fake_msg.channel = FakeChannel(interaction)
        await cmd_spin(fake_msg)

    # ──────────────────────────────────────────────────────────
    # /classement
    # ──────────────────────────────────────────────────────────
    @app_commands.command(name="classement", description="Affiche le top 10 des membres les plus actifs")
    async def slash_classement(self, interaction: discord.Interaction):
        await interaction.response.defer()
        fake_msg = FakeMessage(interaction)
        fake_msg.channel = FakeChannel(interaction)
        await cmd_classement(fake_msg)

    # ──────────────────────────────────────────────────────────
    # /daily
    # ──────────────────────────────────────────────────────────
    @app_commands.command(name="daily", description="Récupère ta récompense quotidienne")
    async def slash_daily(self, interaction: discord.Interaction):
        await interaction.response.defer()
        fake_msg = FakeMessage(interaction)
        fake_msg.channel = FakeChannel(interaction)
        await cmd_daily(fake_msg)

    # ──────────────────────────────────────────────────────────
    # /parrainer
    # ──────────────────────────────────────────────────────────
    @app_commands.command(name="parrainer", description="Parraine un ami et recevez chacun 100 🪙")
    @app_commands.describe(membre="Le membre que tu veux parrainer")
    async def slash_parrainer(self, interaction: discord.Interaction, membre: discord.Member):
        await interaction.response.defer()
        fake_msg = FakeMessage(interaction)
        fake_msg.channel = FakeChannel(interaction)
        fake_msg.mentions = [membre]
        await cmd_parrainer(fake_msg, "")

    # ──────────────────────────────────────────────────────────
    # /cardspin
    # ──────────────────────────────────────────────────────────
    @app_commands.command(name="cardspin", description="Lance le spin de cartes (coûte 100 🪙)")
    async def slash_cardspin(self, interaction: discord.Interaction):
        await interaction.response.defer()
        # Réutilise la logique de cards.py via le cog
        cog = self.bot.cogs.get("Cards")
        if cog:
            ctx = await self.bot.get_context(await interaction.original_response())
            ctx.author = interaction.user
            ctx.channel = interaction.channel
            ctx.guild = interaction.guild
            await cog.cardspin(ctx)
        else:
            await interaction.followup.send("❌ Module Cards non chargé.", ephemeral=True)

    # ──────────────────────────────────────────────────────────
    # /collection
    # ──────────────────────────────────────────────────────────
    @app_commands.command(name="collection", description="Affiche ta collection de cartes")
    @app_commands.describe(membre="Le membre dont tu veux voir la collection (optionnel)")
    async def slash_collection(self, interaction: discord.Interaction, membre: discord.Member = None):
        await interaction.response.defer()
        target = membre or interaction.user
        db = load_db()
        uid = str(target.id)
        cartes = db.get(uid, {}).get("cartes", [])

        RARETES_ORDRE = ["secret","mythique","legendaire","hallal","epique","rare","commun","shlag"]
        RARETES_EMOJI = {
            "secret":"🌈","mythique":"🔴","legendaire":"🟡",
            "hallal":"🟢","epique":"🟣","rare":"🔵","commun":"⚪","shlag":"⚫"
        }

        if not cartes:
            await interaction.followup.send(f"📭 **{target.display_name}** n'a aucune carte.")
            return

        cartes_triees = sorted(
            cartes,
            key=lambda c: RARETES_ORDRE.index(c["rarete"]) if c["rarete"] in RARETES_ORDRE else 99
        )
        lignes = []
        rarete_actuelle = None
        for c in cartes_triees:
            r = c["rarete"]
            if r != rarete_actuelle:
                emoji_r = RARETES_EMOJI.get(r, "❓")
                lignes.append(f"\n{emoji_r} **{r.capitalize()}**")
                rarete_actuelle = r
            lignes.append(f" └ {c['nom']}")

        total = len(cartes)
        uniques = len({c["id"] for c in cartes})
        embed = discord.Embed(
            title=f"🃏 Collection de {target.display_name}",
            description="\n".join(lignes[:40]),
            color=0x5865F2
        )
        embed.set_footer(text=f"{total} cartes au total • {uniques} cartes uniques")
        embed.set_thumbnail(url=target.display_avatar.url)
        await interaction.followup.send(embed=embed)

    # ──────────────────────────────────────────────────────────
    # /cartesinfo
    # ──────────────────────────────────────────────────────────
    @app_commands.command(name="cartesinfo", description="Affiche les probabilités des raretés du gacha cartes")
    async def slash_cartesinfo(self, interaction: discord.Interaction):
        await interaction.response.defer()
        from cards import RARETES, PRIX_SPIN
        lignes = []
        for key, info in RARETES.items():
            barre = "█" * int(info["prob"] / 2)
            lignes.append(f"{info['emoji']} **{info['label']}** — `{info['prob']}%` {barre}")
        embed = discord.Embed(
            title="🎴 Probabilités des raretés",
            description="\n".join(lignes),
            color=0x5865F2
        )
        embed.set_footer(text=f"Prix d'un spin : {PRIX_SPIN} pièces • /cardspin dans 🛍️・boutique")
        await interaction.followup.send(embed=embed)

    # ──────────────────────────────────────────────────────────
    # /solde
    # ──────────────────────────────────────────────────────────
    @app_commands.command(name="solde", description="Vérifie rapidement ton solde de pièces")
    async def slash_solde(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        db = load_db()
        data = get_member_data(db, interaction.user.id)
        await interaction.followup.send(
            f"🪙 Tu as **{data['coins']} pièces** et **{data['xp']} XP**.",
            ephemeral=True
        )

    # ──────────────────────────────────────────────────────────
    # /notif
    # ──────────────────────────────────────────────────────────
    @app_commands.command(name="notif", description="Active ou désactive les notifications de level up en MP")
    @app_commands.describe(etat="on pour activer, off pour désactiver")
    @app_commands.choices(etat=[
        app_commands.Choice(name="Activer", value="on"),
        app_commands.Choice(name="Désactiver", value="off"),
    ])
    async def slash_notif(self, interaction: discord.Interaction, etat: str):
        await interaction.response.defer(ephemeral=True)
        db = load_db()
        data = get_member_data(db, interaction.user.id)
        data["levelup_notif"] = (etat == "on")
        save_db(db)
        status = "✅ activées" if etat == "on" else "❌ désactivées"
        await interaction.followup.send(
            f"Notifications de level up **{status}**.",
            ephemeral=True
        )

    # ──────────────────────────────────────────────────────────
    # /help
    # ──────────────────────────────────────────────────────────
    @app_commands.command(name="help", description="Affiche l'aide des commandes disponibles dans ce salon")
    async def slash_help(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        import unicodedata

        def normalize_name(s):
            s = s.lower().replace("・", "")
            return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode("ascii")

        channel_name = normalize_name(interaction.channel.name)
        embed = discord.Embed(color=0x3498db, timestamp=datetime.now(timezone.utc))

        if "jeux" in channel_name:
            embed.title = "📖 Commandes — 🎮・jeux"
            embed.description = (
                "**Profil & Stats**\n"
                "`/profil` — voir ton niveau, pièces, rôles équipés\n"
                "`/inventaire` — voir tous tes rôles achetés\n"
                "`/classement` — top des membres les plus actifs\n"
                "`/solde` — vérifier ton solde rapidement\n\n"
                "**Social**\n"
                "`/parrainer @pseudo` — parrainer un ami\n"
                "`/collection` — voir ta collection de cartes\n\n"
                "**Préférences**\n"
                "`/notif on/off` — activer/désactiver les notifications level up\n\n"
                "💡 Boutique → 🛍️・boutique\n"
                "🎁 Récompense quotidienne → 🎁・daily"
            )
        elif "boutique" in channel_name:
            embed.title = "📖 Commandes — 🛍️・boutique"
            embed.description = (
                "**Boutique & Gacha**\n"
                "`/boutique` — voir la boutique standard et rotative\n"
                "`/acheter [nom]` — acheter un article\n"
                "`/equiper [nom]` — équiper un rôle cosmétique\n"
                "`/rolespin` — gacha rôles (50 🪙)\n"
                "`/cardspin` — gacha cartes (100 🪙)\n"
                "`/cartesinfo` — probabilités des raretés cartes\n\n"
                "💡 La boutique rotative se renouvelle toutes les **3h**"
            )
        elif "daily" in channel_name:
            embed.title = "📖 Commandes — 🎁・daily"
            embed.description = (
                "`/daily` — récupère ta récompense quotidienne\n\n"
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
                "• `profil de @pseudo`\n\n"
                "**Commandes slash :**\n"
                "`/give` — donner des pièces/rôles/cartes (Fondateur)"
            )
        elif "trades" in channel_name:
            embed.title = "📖 Commandes — 🔄・trades"
            embed.description = (
                "`/trade @membre` — lancer un trade interactif\n"
                "`/donner @membre [montant]` — donner des pièces\n"
                "`/collection` — voir ta collection de cartes"
            )
        else:
            embed.title = "📖 Aide — Prowler Bot"
            embed.description = (
                "**Salons disponibles :**\n\n"
                "🎮・jeux — `/profil`, `/classement`, `/inventaire`\n"
                "🛍️・boutique — `/boutique`, `/rolespin`, `/cardspin`\n"
                "🎁・daily — `/daily`\n"
                "🔄・trades — `/trade`, `/donner`\n\n"
                "Tape `/help` dans ces salons pour les commandes détaillées."
            )

        embed.set_footer(text="Prowler Bot")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ──────────────────────────────────────────────────────────
    # /trade (wrapper slash)
    # ──────────────────────────────────────────────────────────
    @app_commands.command(name="trade", description="Propose un trade de cartes/pièces à un membre")
    @app_commands.describe(membre="Le membre avec qui tu veux trader")
    async def slash_trade(self, interaction: discord.Interaction, membre: discord.Member):
        await interaction.response.defer()
        cog = self.bot.cogs.get("Trades")
        if not cog:
            await interaction.followup.send("❌ Module Trades non chargé.", ephemeral=True)
            return
        # Simule un ctx pour le cog
        await interaction.followup.send(
            f"🔄 Lance `!trade @{membre.display_name}` dans 🔄・trades pour le trade interactif.\n"
            f"*(Les slash commands pour les trades arrivent bientôt — le système de réactions est incompatible pour l'instant)*",
            ephemeral=True
        )

    # ──────────────────────────────────────────────────────────
    # /donner (pièces)
    # ──────────────────────────────────────────────────────────
    @app_commands.command(name="donner", description="Donne des pièces à un membre")
    @app_commands.describe(membre="Le membre à qui donner des pièces", montant="Le nombre de pièces à donner")
    async def slash_donner(self, interaction: discord.Interaction, membre: discord.Member, montant: int):
        await interaction.response.defer()
        if membre.bot or membre == interaction.user:
            await interaction.followup.send("❌ Destinataire invalide.", ephemeral=True)
            return
        if montant <= 0:
            await interaction.followup.send("❌ Le montant doit être supérieur à 0 pièces.", ephemeral=True)
            return

        db = load_db()
        data_author = get_member_data(db, interaction.user.id)
        data_target = get_member_data(db, membre.id)
        solde = data_author.get("coins", 0)

        if solde < montant:
            await interaction.followup.send(
                f"❌ Tu n'as que **{solde} pièces**, tu ne peux pas en donner {montant}.",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="🪙 Confirmation de don",
            description=(
                f"Tu es sur le point de donner **{montant} pièces** à {membre.mention}.\n\n"
                f"💰 Solde actuel : **{solde} pièces**\n"
                f"💰 Solde après : **{solde - montant} pièces**"
            ),
            color=0xF1C40F
        )
        embed.set_footer(text="✅ Confirmer  •  ❌ Annuler  •  Expire dans 30s")
        msg = await interaction.followup.send(embed=embed, wait=True)
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")

        def check(reaction, user):
            return user == interaction.user and str(reaction.emoji) in ("✅","❌") and reaction.message.id == msg.id

        try:
            reaction, _ = await self.bot.wait_for("reaction_add", timeout=30.0, check=check)
        except:
            embed.color = 0x95A5A6
            embed.set_footer(text="⌛ Don annulé — pas de confirmation")
            await msg.edit(embed=embed)
            return

        if str(reaction.emoji) == "❌":
            embed.color = 0xE74C3C
            embed.set_footer(text="❌ Don annulé")
            await msg.edit(embed=embed)
            return

        data_author["coins"] = solde - montant
        data_target["coins"] = data_target.get("coins", 0) + montant
        save_db(db)

        await msg.edit(embed=discord.Embed(
            title="🪙 Don effectué !",
            description=(
                f"{interaction.user.mention} a donné **{montant} pièces** à {membre.mention} !\n\n"
                f"💰 Solde de {interaction.user.display_name} : **{data_author['coins']} pièces**\n"
                f"💰 Solde de {membre.display_name} : **{data_target['coins']} pièces**"
            ),
            color=0x2ECC71
        ))


async def setup(bot: commands.Bot):
    await bot.add_cog(SlashCommands(bot))
