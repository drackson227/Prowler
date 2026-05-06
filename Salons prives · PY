# salons_prives.py — Salons textuels privés permanents

import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import json
import os
from datetime import datetime, timezone

from db import load_db, save_db, get_member_data

# ── Config ────────────────────────────────────────────────────────────────────
CATEGORY_NAME = "Salons Privés"
SALONS_FILE = "/data/salons_prives.json"

# ── Persistance ───────────────────────────────────────────────────────────────
def load_salons():
    if os.path.exists(SALONS_FILE):
        with open(SALONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}  # {str(owner_id): {"channel_id": int, "name": str, "public": bool, "invited": [int]}}

def save_salons(data):
    os.makedirs(os.path.dirname(SALONS_FILE), exist_ok=True)
    with open(SALONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ── Helpers ───────────────────────────────────────────────────────────────────
async def get_or_create_category(guild: discord.Guild) -> discord.CategoryChannel:
    cat = discord.utils.get(guild.categories, name=CATEGORY_NAME)
    if not cat:
        cat = await guild.create_category(
            CATEGORY_NAME,
            overwrites={guild.default_role: discord.PermissionOverwrite(read_messages=False)}
        )
    return cat

async def build_overwrites(guild, owner, invited_ids, public):
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(
            read_messages=public,
            send_messages=public
        ),
        guild.me: discord.PermissionOverwrite(
            read_messages=True, send_messages=True, manage_channels=True
        ),
        owner: discord.PermissionOverwrite(
            read_messages=True, send_messages=True, manage_messages=True
        )
    }
    for uid in invited_ids:
        member = guild.get_member(uid)
        if member:
            overwrites[member] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
    return overwrites


# ── Cog ───────────────────────────────────────────────────────────────────────
class SalonsPrives(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── !createsalon ─────────────────────────────────────────────────────────
    @commands.command(name="createsalon")
    async def createsalon(self, ctx, *, nom: str = None):
        """Crée un salon textuel privé. Usage: !createsalon NomDuSalon"""
        if nom is None:
            await ctx.send(embed=discord.Embed(
                title="❓ Usage",
                description="`!createsalon NomDuSalon` — crée ton salon textuel privé\n\nTu peux ensuite :\n`!invitesalon @membre` — inviter quelqu'un\n`!kicksalon @membre` — expulser quelqu'un\n`!togglesalon` — rendre public ou privé\n`!renamesalon NouveauNom` — renommer\n`!supprsalon` — supprimer le salon",
                color=0x3498db
            ))
            return

        salons = load_salons()
        uid = str(ctx.author.id)

        # Vérifier si le membre a déjà un salon
        if uid in salons:
            ch = ctx.guild.get_channel(salons[uid]["channel_id"])
            if ch:
                await ctx.send(embed=discord.Embed(
                    title="❌ Tu as déjà un salon privé",
                    description=f"Ton salon {ch.mention} existe déjà.\nUtilise `!supprsalon` pour le supprimer d'abord.",
                    color=0xe74c3c
                ))
                return
            else:
                # Le salon a été supprimé manuellement, on nettoie
                del salons[uid]
                save_salons(salons)

        # Limiter le nom
        nom_clean = nom[:32].strip()

        # Créer la catégorie si besoin
        cat = await get_or_create_category(ctx.guild)

        # Créer le salon
        overwrites = await build_overwrites(ctx.guild, ctx.author, [], False)
        try:
            channel = await ctx.guild.create_text_channel(
                name=nom_clean,
                category=cat,
                overwrites=overwrites,
                topic=f"Salon privé de {ctx.author.display_name}"
            )
        except discord.Forbidden:
            await ctx.send("❌ Je n'ai pas la permission de créer des salons.")
            return

        # Sauvegarder
        salons[uid] = {
            "channel_id": channel.id,
            "name": nom_clean,
            "public": False,
            "invited": []
        }
        save_salons(salons)

        embed = discord.Embed(
            title="🔒 Salon privé créé !",
            description=(
                f"Ton salon {channel.mention} a été créé !\n\n"
                f"**Commandes disponibles :**\n"
                f"`!invitesalon @membre` — inviter un membre\n"
                f"`!kicksalon @membre` — expulser un membre\n"
                f"`!togglesalon` — rendre public/privé\n"
                f"`!renamesalon NouveauNom` — renommer\n"
                f"`!supprsalon` — supprimer le salon\n\n"
                f"🔒 Statut actuel : **Privé**"
            ),
            color=0x2ecc71
        )
        embed.set_footer(text=f"Propriétaire : {ctx.author.display_name}")
        await ctx.send(embed=embed)

        # Message de bienvenue dans le salon
        welcome = discord.Embed(
            title=f"👋 Bienvenue dans #{nom_clean}",
            description=(
                f"Ce salon privé appartient à {ctx.author.mention}.\n\n"
                f"**Commandes du propriétaire :**\n"
                f"`!invitesalon @membre` — inviter\n"
                f"`!kicksalon @membre` — expulser\n"
                f"`!togglesalon` — public/privé\n"
                f"`!renamesalon Nom` — renommer\n"
                f"`!supprsalon` — supprimer"
            ),
            color=0x3498db
        )
        await channel.send(embed=welcome)

    # ── !invitesalon ─────────────────────────────────────────────────────────
    @commands.command(name="invitesalon")
    async def invitesalon(self, ctx, membre: discord.Member = None):
        """Invite un membre dans ton salon privé."""
        if membre is None:
            await ctx.send("❌ Usage : `!invitesalon @membre`")
            return

        salons = load_salons()
        uid = str(ctx.author.id)

        if uid not in salons:
            await ctx.send("❌ Tu n'as pas de salon privé. Crée-en un avec `!createsalon NomDuSalon`.")
            return

        channel = ctx.guild.get_channel(salons[uid]["channel_id"])
        if not channel:
            await ctx.send("❌ Ton salon n'existe plus. Recrée-le avec `!createsalon`.")
            del salons[uid]
            save_salons(salons)
            return

        if membre.id == ctx.author.id:
            await ctx.send("❌ Tu es déjà propriétaire du salon !")
            return

        if membre.id in salons[uid]["invited"]:
            await ctx.send(f"❌ **{membre.display_name}** est déjà invité.")
            return

        # Ajouter les permissions
        await channel.set_permissions(membre, read_messages=True, send_messages=True)
        salons[uid]["invited"].append(membre.id)
        save_salons(salons)

        embed = discord.Embed(
            title="✅ Membre invité",
            description=f"{membre.mention} peut maintenant accéder à {channel.mention} !",
            color=0x2ecc71
        )
        await ctx.send(embed=embed)

        # Notifier dans le salon
        try:
            await channel.send(f"👋 {membre.mention} a été invité par {ctx.author.mention} !")
        except Exception:
            pass

        # MP à l'invité
        try:
            await membre.send(
                f"🔓 **{ctx.author.display_name}** t'a invité dans son salon privé **#{salons[uid]['name']}** sur **{ctx.guild.name}** !\n"
                f"→ {channel.mention}"
            )
        except Exception:
            pass

    # ── !kicksalon ───────────────────────────────────────────────────────────
    @commands.command(name="kicksalon")
    async def kicksalon(self, ctx, membre: discord.Member = None):
        """Expulse un membre de ton salon privé."""
        if membre is None:
            await ctx.send("❌ Usage : `!kicksalon @membre`")
            return

        salons = load_salons()
        uid = str(ctx.author.id)

        if uid not in salons:
            await ctx.send("❌ Tu n'as pas de salon privé.")
            return

        channel = ctx.guild.get_channel(salons[uid]["channel_id"])
        if not channel:
            await ctx.send("❌ Ton salon n'existe plus.")
            del salons[uid]
            save_salons(salons)
            return

        if membre.id not in salons[uid]["invited"]:
            await ctx.send(f"❌ **{membre.display_name}** n'est pas dans ton salon.")
            return

        # Retirer les permissions
        await channel.set_permissions(membre, overwrite=None)
        salons[uid]["invited"].remove(membre.id)
        save_salons(salons)

        embed = discord.Embed(
            title="👢 Membre expulsé",
            description=f"**{membre.display_name}** a été retiré de {channel.mention}.",
            color=0xe74c3c
        )
        await ctx.send(embed=embed)

        try:
            await channel.send(f"👢 **{membre.display_name}** a été expulsé du salon par {ctx.author.mention}.")
        except Exception:
            pass

    # ── !togglesalon ─────────────────────────────────────────────────────────
    @commands.command(name="togglesalon")
    async def togglesalon(self, ctx):
        """Rend ton salon public ou privé."""
        salons = load_salons()
        uid = str(ctx.author.id)

        if uid not in salons:
            await ctx.send("❌ Tu n'as pas de salon privé.")
            return

        channel = ctx.guild.get_channel(salons[uid]["channel_id"])
        if not channel:
            await ctx.send("❌ Ton salon n'existe plus.")
            del salons[uid]
            save_salons(salons)
            return

        # Inverser le statut
        salons[uid]["public"] = not salons[uid]["public"]
        is_public = salons[uid]["public"]
        save_salons(salons)

        # Mettre à jour les permissions
        overwrites = await build_overwrites(
            ctx.guild, ctx.author,
            salons[uid]["invited"], is_public
        )
        await channel.edit(overwrites=overwrites)

        statut = "🌐 **Public**" if is_public else "🔒 **Privé**"
        embed = discord.Embed(
            title="🔄 Statut du salon modifié",
            description=f"{channel.mention} est maintenant {statut}",
            color=0x3498db
        )
        await ctx.send(embed=embed)
        await channel.send(f"🔄 Ce salon est maintenant {statut} (modifié par {ctx.author.mention})")

    # ── !renamesalon ─────────────────────────────────────────────────────────
    @commands.command(name="renamesalon")
    async def renamesalon(self, ctx, *, nouveau_nom: str = None):
        """Renomme ton salon privé."""
        if not nouveau_nom:
            await ctx.send("❌ Usage : `!renamesalon NouveauNom`")
            return

        salons = load_salons()
        uid = str(ctx.author.id)

        if uid not in salons:
            await ctx.send("❌ Tu n'as pas de salon privé.")
            return

        channel = ctx.guild.get_channel(salons[uid]["channel_id"])
        if not channel:
            await ctx.send("❌ Ton salon n'existe plus.")
            del salons[uid]
            save_salons(salons)
            return

        ancien_nom = salons[uid]["name"]
        nouveau_nom_clean = nouveau_nom[:32].strip()

        await channel.edit(name=nouveau_nom_clean)
        salons[uid]["name"] = nouveau_nom_clean
        save_salons(salons)

        embed = discord.Embed(
            title="✏️ Salon renommé",
            description=f"**#{ancien_nom}** → **#{nouveau_nom_clean}**",
            color=0x3498db
        )
        await ctx.send(embed=embed)

    # ── !supprsalon ──────────────────────────────────────────────────────────
    @commands.command(name="supprsalon")
    async def supprsalon(self, ctx):
        """Supprime ton salon privé."""
        salons = load_salons()
        uid = str(ctx.author.id)

        # Modos peuvent supprimer n'importe quel salon
        target_uid = uid
        if any(r.name in ["Modérateur", "Fondateur"] for r in ctx.author.roles):
            if ctx.message.mentions:
                target_uid = str(ctx.message.mentions[0].id)

        if target_uid not in salons:
            await ctx.send("❌ Aucun salon privé trouvé.")
            return

        channel = ctx.guild.get_channel(salons[target_uid]["channel_id"])

        # Confirmation
        embed = discord.Embed(
            title="⚠️ Confirmer la suppression",
            description=f"Es-tu sûr de vouloir supprimer **#{salons[target_uid]['name']}** ?\nCette action est **irréversible**.",
            color=0xe74c3c
        )
        embed.set_footer(text="✅ confirmer • ❌ annuler • Expire dans 15s")
        msg = await ctx.send(embed=embed)
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")

        def check(reaction, user):
            return user == ctx.author and str(reaction.emoji) in ("✅", "❌") and reaction.message.id == msg.id

        try:
            reaction, _ = await self.bot.wait_for("reaction_add", timeout=15.0, check=check)
        except asyncio.TimeoutError:
            await msg.edit(embed=discord.Embed(title="⌛ Suppression annulée", color=0x95a5a6))
            return

        if str(reaction.emoji) == "❌":
            await msg.edit(embed=discord.Embed(title="❌ Suppression annulée", color=0x95a5a6))
            return

        # Supprimer le salon Discord
        if channel:
            try:
                await channel.delete(reason=f"Supprimé par {ctx.author.display_name}")
            except Exception:
                pass

        del salons[target_uid]
        save_salons(salons)

        await ctx.send(embed=discord.Embed(
            title="🗑️ Salon supprimé",
            description=f"Le salon privé a été supprimé.",
            color=0x2ecc71
        ))

    # ── !infosalon ───────────────────────────────────────────────────────────
    @commands.command(name="infosalon")
    async def infosalon(self, ctx):
        """Affiche les infos de ton salon privé."""
        salons = load_salons()
        uid = str(ctx.author.id)

        if uid not in salons:
            await ctx.send("❌ Tu n'as pas de salon privé. Crée-en un avec `!createsalon NomDuSalon`.")
            return

        data = salons[uid]
        channel = ctx.guild.get_channel(data["channel_id"])
        if not channel:
            await ctx.send("❌ Ton salon n'existe plus.")
            del salons[uid]
            save_salons(salons)
            return

        invited_mentions = []
        for mid in data["invited"]:
            m = ctx.guild.get_member(mid)
            if m:
                invited_mentions.append(m.mention)

        statut = "🌐 Public" if data["public"] else "🔒 Privé"
        embed = discord.Embed(
            title=f"📋 Infos — #{data['name']}",
            color=0x3498db
        )
        embed.add_field(name="📍 Salon", value=channel.mention, inline=True)
        embed.add_field(name="🔑 Statut", value=statut, inline=True)
        embed.add_field(name="👥 Membres invités", value=", ".join(invited_mentions) if invited_mentions else "Aucun", inline=False)
        embed.add_field(
            name="⚙️ Commandes",
            value=(
                "`!invitesalon @m` • `!kicksalon @m`\n"
                "`!togglesalon` • `!renamesalon Nom` • `!supprsalon`"
            ),
            inline=False
        )
        embed.set_footer(text=f"Propriétaire : {ctx.author.display_name}")
        await ctx.send(embed=embed)

    # ── Nettoyage auto si salon supprimé manuellement ─────────────────────────
    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        salons = load_salons()
        to_remove = [uid for uid, data in salons.items() if data["channel_id"] == channel.id]
        for uid in to_remove:
            del salons[uid]
        if to_remove:
            save_salons(salons)

    # ── Slash commands ────────────────────────────────────────────────────────
    @app_commands.command(name="createsalon", description="Crée ton salon textuel privé permanent")
    @app_commands.describe(nom="Le nom de ton salon privé")
    async def slash_createsalon(self, interaction: discord.Interaction, nom: str):
        await interaction.response.defer()
        ctx = await self.bot.get_context(interaction.message) if hasattr(interaction, 'message') else None

        class FakeCtx:
            author = interaction.user
            guild = interaction.guild
            send = interaction.followup.send
            message = type('msg', (), {'mentions': []})()

        fake = FakeCtx()
        fake.send = interaction.followup.send

        salons = load_salons()
        uid = str(interaction.user.id)

        if uid in salons:
            ch = interaction.guild.get_channel(salons[uid]["channel_id"])
            if ch:
                await interaction.followup.send(f"❌ Tu as déjà un salon privé : {ch.mention}", ephemeral=True)
                return
            else:
                del salons[uid]
                save_salons(salons)

        nom_clean = nom[:32].strip()
        cat = await get_or_create_category(interaction.guild)
        overwrites = await build_overwrites(interaction.guild, interaction.user, [], False)

        try:
            channel = await interaction.guild.create_text_channel(
                name=nom_clean,
                category=cat,
                overwrites=overwrites,
                topic=f"Salon privé de {interaction.user.display_name}"
            )
        except discord.Forbidden:
            await interaction.followup.send("❌ Je n'ai pas la permission de créer des salons.", ephemeral=True)
            return

        salons[uid] = {"channel_id": channel.id, "name": nom_clean, "public": False, "invited": []}
        save_salons(salons)

        embed = discord.Embed(
            title="🔒 Salon privé créé !",
            description=(
                f"Ton salon {channel.mention} a été créé !\n\n"
                f"`!invitesalon @membre` — inviter\n"
                f"`!kicksalon @membre` — expulser\n"
                f"`!togglesalon` — public/privé\n"
                f"`!renamesalon NouveauNom` — renommer\n"
                f"`!supprsalon` — supprimer"
            ),
            color=0x2ecc71
        )
        await interaction.followup.send(embed=embed)

        welcome = discord.Embed(
            title=f"👋 Bienvenue dans #{nom_clean}",
            description=f"Ce salon privé appartient à {interaction.user.mention}.",
            color=0x3498db
        )
        await channel.send(embed=welcome)


async def setup(bot):
    await bot.add_cog(SalonsPrives(bot))
