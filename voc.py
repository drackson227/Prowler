import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio

CATEGORY_NAME = "🎙️ Salons Vocaux"

class VocManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_vocs = {}  # {voc_id: {"owner": member_id, "guild_id": int, "text_channel_id": int}}
        self.check_empty_vocs.start()

    def get_owner_voc(self, member_id):
        for ch_id, data in self.active_vocs.items():
            if data["owner"] == member_id:
                return ch_id, data
        return None, None

    async def _delete_voc(self, guild, voc_id):
        data = self.active_vocs.pop(voc_id, None)
        voc = guild.get_channel(voc_id)
        if voc:
            try: await voc.delete()
            except: pass
        if data and data.get("text_channel_id"):
            text_ch = guild.get_channel(data["text_channel_id"])
            if text_ch:
                try: await text_ch.delete()
                except: pass

    async def _check_has_voc(self, ctx_or_channel, author):
        voc_id, _ = self.get_owner_voc(author.id)
        if not voc_id:
            await ctx_or_channel.send("❌ Tu n'as pas de salon vocal actif. Crée-en un avec `!createvoc NomDuSalon` ou `/createvoc`.")
            return False
        return True

    async def _create_voc_logic(self, guild, author, nom, reply_channel):
        voc_id, _ = self.get_owner_voc(author.id)
        if voc_id:
            ch = guild.get_channel(voc_id)
            await reply_channel.send(f"❌ Tu as déjà un salon actif : **{ch.name if ch else 'inconnu'}**. Supprime-le d'abord avec `!vocsuppr`.")
            return

        category = discord.utils.get(guild.categories, name=CATEGORY_NAME)
        if not category:
            category = await guild.create_category(CATEGORY_NAME)

        voc_name = f"🔊 {nom} — {author.display_name}"
        voc = await guild.create_voice_channel(
            name=voc_name, category=category,
            overwrites={
                guild.default_role: discord.PermissionOverwrite(connect=True, view_channel=True),
                author: discord.PermissionOverwrite(connect=True, view_channel=True, move_members=True, mute_members=True, deafen_members=True, manage_channels=True)
            }
        )

        text_name = f"🔧・cmds-{author.display_name.lower().replace(' ', '-')[:20]}"
        text_ch = await guild.create_text_channel(
            name=text_name, category=category,
            overwrites={
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                author: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True)
            }
        )

        self.active_vocs[voc.id] = {"owner": author.id, "guild_id": guild.id, "text_channel_id": text_ch.id}

        embed = discord.Embed(
            title=f"🎙️ Ton salon vocal — {voc_name}",
            description=(
                "Seul toi vois ce salon. Commandes disponibles :\n\n"
                "`!vockick @membre` / `/vockick` — Expulser\n"
                "`!vocmute @membre` / `/vocmute` — Muter\n"
                "`!vocunmute @membre` / `/vocunmute` — Démuter\n"
                "`!voclock` / `/voclock` — Fermer aux nouveaux\n"
                "`!vocunlock` / `/vocunlock` — Rouvrir\n"
                "`!vocrename NomDuSalon` / `/vocrename` — Renommer\n"
                "`!vocsuppr` / `/vocsuppr` — Supprimer"
            ),
            color=0x5865F2
        )
        embed.set_footer(text="Ce salon sera supprimé avec ton vocal.")
        await text_ch.send(author.mention, embed=embed)
        await reply_channel.send(f"✅ Salon **{voc_name}** créé ! Ton salon de commandes privé : {text_ch.mention}")

        await asyncio.sleep(30)
        voc_check = guild.get_channel(voc.id)
        if voc_check and len(voc_check.members) == 0:
            await self._delete_voc(guild, voc.id)
            try: await reply_channel.send(f"🗑️ **{voc_name}** supprimé — personne n'a rejoint en 30s.")
            except: pass

    # ── ! commandes ──────────────────────────────────────────
    @commands.command(name="createvoc")
    async def create_voc(self, ctx, *, nom: str = None):
        if not nom:
            return await ctx.send("❌ Usage : `!createvoc NomDuSalon`")
        await self._create_voc_logic(ctx.guild, ctx.author, nom, ctx.channel)

    @commands.command(name="vockick")
    async def voc_kick(self, ctx, member: discord.Member):
        if not await self._check_has_voc(ctx.channel, ctx.author): return
        voc_id, _ = self.get_owner_voc(ctx.author.id)
        voc = ctx.guild.get_channel(voc_id)
        if not voc or member not in voc.members:
            return await ctx.send("❌ Ce membre n'est pas dans ton salon.")
        await member.move_to(None)
        await ctx.send(f"👢 **{member.display_name}** expulsé.")

    @commands.command(name="vocmute")
    async def voc_mute(self, ctx, member: discord.Member):
        if not await self._check_has_voc(ctx.channel, ctx.author): return
        await member.edit(mute=True)
        await ctx.send(f"🔇 **{member.display_name}** muté.")

    @commands.command(name="vocunmute")
    async def voc_unmute(self, ctx, member: discord.Member):
        if not await self._check_has_voc(ctx.channel, ctx.author): return
        await member.edit(mute=False)
        await ctx.send(f"🔊 **{member.display_name}** démuté.")

    @commands.command(name="voclock")
    async def voc_lock(self, ctx):
        if not await self._check_has_voc(ctx.channel, ctx.author): return
        voc_id, _ = self.get_owner_voc(ctx.author.id)
        voc = ctx.guild.get_channel(voc_id)
        await voc.set_permissions(ctx.guild.default_role, connect=False)
        await ctx.send(f"🔒 **{voc.name}** verrouillé.")

    @commands.command(name="vocunlock")
    async def voc_unlock(self, ctx):
        if not await self._check_has_voc(ctx.channel, ctx.author): return
        voc_id, _ = self.get_owner_voc(ctx.author.id)
        voc = ctx.guild.get_channel(voc_id)
        await voc.set_permissions(ctx.guild.default_role, connect=True)
        await ctx.send(f"🔓 **{voc.name}** déverrouillé.")

    @commands.command(name="vocrename")
    async def voc_rename(self, ctx, *, nouveau_nom: str):
        if not await self._check_has_voc(ctx.channel, ctx.author): return
        voc_id, _ = self.get_owner_voc(ctx.author.id)
        voc = ctx.guild.get_channel(voc_id)
        await voc.edit(name=f"🔊 {nouveau_nom}")
        await ctx.send(f"✏️ Renommé en **{nouveau_nom}**.")

    @commands.command(name="vocsuppr")
    async def voc_suppr(self, ctx):
        voc_id, _ = self.get_owner_voc(ctx.author.id)
        if not voc_id: return await ctx.send("❌ Tu n'as pas de salon vocal actif.")
        voc = ctx.guild.get_channel(voc_id)
        name = voc.name if voc else "inconnu"
        await self._delete_voc(ctx.guild, voc_id)
        await ctx.send(f"🗑️ **{name}** supprimé.")

    # ── / commandes ──────────────────────────────────────────
    @app_commands.command(name="createvoc", description="Crée un salon vocal temporaire")
    @app_commands.describe(nom="Le nom de ton salon vocal")
    async def slash_createvoc(self, interaction: discord.Interaction, nom: str):
        await interaction.response.defer()
        await self._create_voc_logic(interaction.guild, interaction.user, nom, interaction.channel)

    @app_commands.command(name="vockick", description="Expulse un membre de ton salon vocal")
    @app_commands.describe(membre="Le membre à expulser")
    async def slash_vockick(self, interaction: discord.Interaction, membre: discord.Member):
        await interaction.response.defer()
        if not await self._check_has_voc(interaction.channel, interaction.user): return
        voc_id, _ = self.get_owner_voc(interaction.user.id)
        voc = interaction.guild.get_channel(voc_id)
        if not voc or membre not in voc.members:
            return await interaction.followup.send("❌ Ce membre n'est pas dans ton salon.")
        await membre.move_to(None)
        await interaction.followup.send(f"👢 **{membre.display_name}** expulsé.")

    @app_commands.command(name="vocmute", description="Mute un membre dans ton salon vocal")
    @app_commands.describe(membre="Le membre à muter")
    async def slash_vocmute(self, interaction: discord.Interaction, membre: discord.Member):
        await interaction.response.defer()
        if not await self._check_has_voc(interaction.channel, interaction.user): return
        await membre.edit(mute=True)
        await interaction.followup.send(f"🔇 **{membre.display_name}** muté.")

    @app_commands.command(name="vocunmute", description="Démute un membre dans ton salon vocal")
    @app_commands.describe(membre="Le membre à démuter")
    async def slash_vocunmute(self, interaction: discord.Interaction, membre: discord.Member):
        await interaction.response.defer()
        if not await self._check_has_voc(interaction.channel, interaction.user): return
        await membre.edit(mute=False)
        await interaction.followup.send(f"🔊 **{membre.display_name}** démuté.")

    @app_commands.command(name="voclock", description="Verrouille ton salon vocal")
    async def slash_voclock(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if not await self._check_has_voc(interaction.channel, interaction.user): return
        voc_id, _ = self.get_owner_voc(interaction.user.id)
        voc = interaction.guild.get_channel(voc_id)
        await voc.set_permissions(interaction.guild.default_role, connect=False)
        await interaction.followup.send(f"🔒 **{voc.name}** verrouillé.")

    @app_commands.command(name="vocunlock", description="Déverrouille ton salon vocal")
    async def slash_vocunlock(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if not await self._check_has_voc(interaction.channel, interaction.user): return
        voc_id, _ = self.get_owner_voc(interaction.user.id)
        voc = interaction.guild.get_channel(voc_id)
        await voc.set_permissions(interaction.guild.default_role, connect=True)
        await interaction.followup.send(f"🔓 **{voc.name}** déverrouillé.")

    @app_commands.command(name="vocrename", description="Renomme ton salon vocal")
    @app_commands.describe(nom="Le nouveau nom")
    async def slash_vocrename(self, interaction: discord.Interaction, nom: str):
        await interaction.response.defer()
        if not await self._check_has_voc(interaction.channel, interaction.user): return
        voc_id, _ = self.get_owner_voc(interaction.user.id)
        voc = interaction.guild.get_channel(voc_id)
        await voc.edit(name=f"🔊 {nom}")
        await interaction.followup.send(f"✏️ Renommé en **{nom}**.")

    @app_commands.command(name="vocsuppr", description="Supprime ton salon vocal")
    async def slash_vocsuppr(self, interaction: discord.Interaction):
        await interaction.response.defer()
        voc_id, _ = self.get_owner_voc(interaction.user.id)
        if not voc_id:
            return await interaction.followup.send("❌ Tu n'as pas de salon vocal actif.")
        voc = interaction.guild.get_channel(voc_id)
        name = voc.name if voc else "inconnu"
        await self._delete_voc(interaction.guild, voc_id)
        await interaction.followup.send(f"🗑️ **{name}** supprimé.")

    @tasks.loop(seconds=10)
    async def check_empty_vocs(self):
        now = asyncio.get_event_loop().time()
        for voc_id, data in list(self.active_vocs.items()):
            guild = self.bot.get_guild(data["guild_id"])
            if not guild: continue
            voc = guild.get_channel(voc_id)
            if not voc:
                self.active_vocs.pop(voc_id, None)
                continue
            if len(voc.members) == 0:
                if "_empty_since" not in data:
                    data["_empty_since"] = now
                elif now - data["_empty_since"] >= 30:
                    await self._delete_voc(guild, voc_id)
            else:
                data.pop("_empty_since", None)

    @check_empty_vocs.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(VocManager(bot))
