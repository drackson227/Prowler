# tickets.py — Partie 3 : Tickets + Anti-raid + Alertes activité

import discord
from discord.ext import commands
import asyncio
from datetime import datetime, timezone, timedelta

from config import (
    TICKET_CATEGORY, TICKET_CHANNEL, MOD_ROLES_FOR_TICKETS,
    RAID_JOIN_THRESHOLD, RAID_WINDOW,
    ACTIVITY_ALERT_THRESHOLD, ACTIVITY_ALERT_WINDOW
)
from db import load_db, save_db, get_member_data
from utils import get_channel_by_name, log_action

# ── Anti-raid tracker ────────────────────────────────────────
raid_join_times = []
raid_locked = False

# ── Activité tracker ─────────────────────────────────────────
activity_tracker = []
activity_alerted = False


class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ============================================================
    # SETUP TICKETS
    # ============================================================
    @commands.command(name="setup")
    async def setup(self, ctx, *, quoi: str = ""):
        """!setup tickets / !setup antiraid / !setup bienvenue"""
        if not any(r.name in MOD_ROLES_FOR_TICKETS for r in ctx.author.roles) and ctx.guild.owner_id != ctx.author.id:
            return await ctx.send("❌ Commande réservée aux modérateurs.")

        quoi = quoi.lower().strip()

        if quoi == "tickets":
            await self._setup_tickets(ctx)
        elif quoi == "antiraid":
            await ctx.send(embed=discord.Embed(
                title="🛡️ Anti-raid activé",
                description=(
                    f"Le bot surveillera les arrivées en masse.\n"
                    f"Si **{RAID_JOIN_THRESHOLD}+ membres** rejoignent en moins de **{RAID_WINDOW}s**, "
                    f"le serveur sera verrouillé automatiquement."
                ),
                color=0xe74c3c
            ))
        elif quoi == "bienvenue":
            general = get_channel_by_name(ctx.guild, "chat-général")
            if general:
                await ctx.send(embed=discord.Embed(
                    title="👋 Bienvenue configuré",
                    description=f"Les messages de bienvenue seront envoyés dans {general.mention}.",
                    color=0x2ecc71
                ))
            else:
                await ctx.send("❌ Salon `chat-général` introuvable.")
        else:
            embed = discord.Embed(title="⚙️ Commandes setup", color=0x3498db)
            embed.add_field(name="`!setup tickets`", value="Crée le salon et le système de tickets", inline=False)
            embed.add_field(name="`!setup antiraid`", value="Active la protection anti-raid", inline=False)
            embed.add_field(name="`!setup bienvenue`", value="Configure les messages de bienvenue", inline=False)
            await ctx.send(embed=embed)

    async def _setup_tickets(self, ctx):
        guild = ctx.guild

        # Cherche ou crée la catégorie
        category = discord.utils.get(guild.categories, name=TICKET_CATEGORY)
        if not category:
            try:
                category = await guild.create_category(
                    TICKET_CATEGORY,
                    reason="Setup tickets Prowler"
                )
            except discord.Forbidden:
                return await ctx.send("❌ Je n'ai pas la permission de créer des catégories.")

        # Cherche ou crée le salon tickets
        ticket_ch = discord.utils.get(guild.text_channels, name=TICKET_CHANNEL)
        if not ticket_ch:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=True, send_messages=False),
                guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True),
            }
            # Modos peuvent lire
            for role in guild.roles:
                if role.name in MOD_ROLES_FOR_TICKETS:
                    overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
            try:
                ticket_ch = await guild.create_text_channel(
                    TICKET_CHANNEL,
                    category=category,
                    overwrites=overwrites,
                    topic="Ouvre un ticket pour contester une sanction.",
                    reason="Setup tickets Prowler"
                )
            except discord.Forbidden:
                return await ctx.send("❌ Je n'ai pas la permission de créer des salons.")

        # Message d'accueil dans le salon tickets
        embed = discord.Embed(
            title="🎫 Système de tickets",
            description=(
                "Tu peux ouvrir un ticket pour :\n\n"
                "• Contester une sanction (mute, ban, kick)\n"
                "• Signaler un problème\n"
                "• Demander de l'aide à un modérateur\n\n"
                "Réagis avec 🎫 pour créer un ticket.\n"
                "⚠️ Les membres mutés peuvent toujours ouvrir un ticket."
            ),
            color=0x3498db
        )
        embed.set_footer(text="Prowler Bot — Système de tickets")
        msg = await ticket_ch.send(embed=embed)
        await msg.add_reaction("🎫")

        await ctx.send(embed=discord.Embed(
            title="✅ Tickets configurés !",
            description=f"Le salon {ticket_ch.mention} a été créé.\nLes membres peuvent y ouvrir un ticket même s'ils sont mutés.",
            color=0x2ecc71
        ))

    # ============================================================
    # LISTENER : réaction 🎫 → crée un ticket privé
    # ============================================================
    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        if user.bot:
            return
        if str(reaction.emoji) != "🎫":
            return

        channel_name = reaction.message.channel.name.lower().replace("・", "")
        if TICKET_CHANNEL not in channel_name:
            return

        guild = reaction.message.guild
        member = guild.get_member(user.id)
        if not member:
            return

        # Vérifie si un ticket est déjà ouvert pour ce membre
        existing = discord.utils.get(
            guild.text_channels,
            name=f"ticket-{member.name.lower().replace(' ', '-')}"
        )
        if existing:
            try:
                await member.send(f"⚠️ Tu as déjà un ticket ouvert : {existing.mention}")
            except:
                pass
            return

        # Trouve la catégorie tickets
        category = discord.utils.get(guild.categories, name=TICKET_CATEGORY)

        # Permissions : visible par le membre + modos, même s'il est muté
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True),
            member: discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True,  # peut écrire même muté
                read_message_history=True
            ),
        }
        for role in guild.roles:
            if role.name in MOD_ROLES_FOR_TICKETS:
                overwrites[role] = discord.PermissionOverwrite(
                    read_messages=True, send_messages=True, manage_channels=True
                )

        try:
            ticket_channel = await guild.create_text_channel(
                f"ticket-{member.name.lower().replace(' ', '-')}",
                category=category,
                overwrites=overwrites,
                topic=f"Ticket de {member.display_name}",
                reason=f"Ticket ouvert par {member.display_name}"
            )
        except discord.Forbidden:
            return

        # Message d'accueil dans le ticket
        embed = discord.Embed(
            title=f"🎫 Ticket de {member.display_name}",
            description=(
                f"Bonjour {member.mention} !\n\n"
                "Un modérateur va te répondre dès que possible.\n"
                "Explique ton problème ou ta contestation ici.\n\n"
                "🔒 Réagis avec 🔒 pour fermer ce ticket."
            ),
            color=0x3498db,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text=f"ID: {member.id}")
        msg = await ticket_channel.send(embed=embed)
        await msg.add_reaction("🔒")

        # Ping modos
        mod_roles_mentions = []
        for role in guild.roles:
            if role.name in MOD_ROLES_FOR_TICKETS:
                mod_roles_mentions.append(role.mention)
        if mod_roles_mentions:
            await ticket_channel.send(" ".join(mod_roles_mentions) + f" — nouveau ticket de {member.mention}")

        # Log
        await log_action(guild, "ticket_open", None, member)

        try:
            await member.send(f"✅ Ton ticket a été ouvert : {ticket_channel.mention}")
        except:
            pass

    # ============================================================
    # LISTENER : réaction 🔒 → ferme le ticket
    # ============================================================
    @commands.Cog.listener()
    async def on_reaction_add_close(self, reaction, user):
        # Note: on_reaction_add ne peut être déclaré qu'une fois par cog
        # Ce listener est géré dans on_reaction_add ci-dessus via la vérification emoji
        pass

    # ============================================================
    # ANTI-RAID : on_member_join
    # ============================================================
    @commands.Cog.listener()
    async def on_member_join(self, member):
        global raid_join_times, raid_locked
        guild = member.guild
        now = datetime.now(timezone.utc).timestamp()

        # Nettoie les vieux joins
        raid_join_times = [t for t in raid_join_times if now - t < RAID_WINDOW]
        raid_join_times.append(now)

        if len(raid_join_times) >= RAID_JOIN_THRESHOLD and not raid_locked:
            raid_locked = True
            await self._lock_server_raid(guild)

    async def _lock_server_raid(self, guild):
        """Verrouille le serveur et alerte les modos en cas de raid."""
        # Désactive les permissions d'envoi pour @everyone dans tous les salons publics
        locked_channels = []
        for channel in guild.text_channels:
            if channel.permissions_for(guild.default_role).send_messages:
                try:
                    await channel.set_permissions(
                        guild.default_role,
                        send_messages=False,
                        reason="Anti-raid automatique Prowler"
                    )
                    locked_channels.append(channel)
                except:
                    pass

        # Alerte dans le salon logs
        log_ch = get_channel_by_name(guild, "logs")
        mod_ch = get_channel_by_name(guild, "moderation")
        alert_ch = log_ch or mod_ch

        if alert_ch:
            # Ping tous les modos
            mod_mentions = []
            for role in guild.roles:
                if role.name in MOD_ROLES_FOR_TICKETS:
                    mod_mentions.append(role.mention)

            embed = discord.Embed(
                title="🚨 RAID DÉTECTÉ — Serveur verrouillé !",
                description=(
                    f"**{len(raid_join_times)}** membres ont rejoint en moins de **{RAID_WINDOW}s** !\n\n"
                    f"🔒 **{len(locked_channels)} salons verrouillés** automatiquement.\n\n"
                    "Pour déverrouiller : `!unlockserver`"
                ),
                color=0xe74c3c,
                timestamp=datetime.now(timezone.utc)
            )
            embed.set_footer(text="Anti-raid Prowler")
            ping_txt = " ".join(mod_mentions) if mod_mentions else ""
            await alert_ch.send(ping_txt, embed=embed)

    @commands.command(name="unlockserver")
    async def unlockserver(self, ctx):
        """Déverrouille le serveur après un raid."""
        global raid_locked, raid_join_times
        if not any(r.name in MOD_ROLES_FOR_TICKETS for r in ctx.author.roles) and ctx.guild.owner_id != ctx.author.id:
            return await ctx.send("❌ Commande réservée aux modérateurs.")

        guild = ctx.guild
        unlocked = []
        for channel in guild.text_channels:
            overwrite = channel.overwrites_for(guild.default_role)
            if overwrite.send_messages is False:
                try:
                    await channel.set_permissions(
                        guild.default_role,
                        send_messages=None,
                        reason=f"Déverrouillage serveur par {ctx.author.display_name}"
                    )
                    unlocked.append(channel)
                except:
                    pass

        raid_locked = False
        raid_join_times = []

        await ctx.send(embed=discord.Embed(
            title="🔓 Serveur déverrouillé",
            description=f"**{len(unlocked)} salons** ont été déverrouillés par {ctx.author.mention}.",
            color=0x2ecc71
        ))

    # ============================================================
    # ALERTES D'ACTIVITÉ
    # ============================================================
    @commands.Cog.listener()
    async def on_message(self, message):
        global activity_tracker, activity_alerted
        if message.author.bot:
            return

        now = datetime.now(timezone.utc).timestamp()
        activity_tracker = [t for t in activity_tracker if now - t < ACTIVITY_ALERT_WINDOW]
        activity_tracker.append(now)

        if len(activity_tracker) >= ACTIVITY_ALERT_THRESHOLD and not activity_alerted:
            activity_alerted = True
            asyncio.get_event_loop().call_later(ACTIVITY_ALERT_WINDOW, self._reset_activity_alert)

            mod_ch = get_channel_by_name(message.guild, "moderation")
            log_ch = get_channel_by_name(message.guild, "logs")
            alert_ch = mod_ch or log_ch
            if alert_ch:
                embed = discord.Embed(
                    title="📈 Pic d'activité détecté !",
                    description=(
                        f"**{len(activity_tracker)} messages** envoyés en moins de **{ACTIVITY_ALERT_WINDOW}s** !\n"
                        f"Salon actif : {message.channel.mention}"
                    ),
                    color=0xf39c12,
                    timestamp=datetime.now(timezone.utc)
                )
                embed.set_footer(text="Alerte activité Prowler")
                await alert_ch.send(embed=embed)

    def _reset_activity_alert(self):
        global activity_alerted
        activity_alerted = False

    # ============================================================
    # FERMETURE DE TICKET via réaction 🔒
    # ============================================================
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if payload.member and payload.member.bot:
            return
        if str(payload.emoji) == "🔒":
            guild = self.bot.get_guild(payload.guild_id)
            if not guild:
                return
            channel = guild.get_channel(payload.channel_id)
            if not channel:
                return
            # Vérifie que c'est bien un salon ticket
            if not channel.name.startswith("ticket-"):
                return
            member = guild.get_member(payload.user_id)
            if not member:
                return
            # Seuls les modos peuvent fermer
            if not any(r.name in MOD_ROLES_FOR_TICKETS for r in member.roles) and guild.owner_id != member.id:
                return

            embed = discord.Embed(
                title="🔒 Ticket fermé",
                description=f"Ticket fermé par {member.mention}.\nCe salon sera supprimé dans 5 secondes.",
                color=0xe74c3c,
                timestamp=datetime.now(timezone.utc)
            )
            await channel.send(embed=embed)

            # Log
            # Récupère le membre du ticket depuis le nom du salon
            ticket_owner_name = channel.name.replace("ticket-", "").replace("-", " ")
            ticket_owner = discord.utils.find(
                lambda m: m.name.lower().replace(" ", "-") == channel.name.replace("ticket-", ""),
                guild.members
            )
            await log_action(guild, "ticket_close", member, ticket_owner)

            await asyncio.sleep(5)
            try:
                await channel.delete(reason=f"Ticket fermé par {member.display_name}")
            except:
                pass


async def setup(bot):
    await bot.add_cog(Tickets(bot))
