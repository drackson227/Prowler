# voc.py — Salons vocaux temporaires avec salon texte privé

import discord
from discord.ext import commands, tasks
import asyncio

CATEGORY_NAME = "🎙️ Salons Vocaux"


class VocManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # {voc_id: {"owner": member_id, "guild_id": guild_id, "text_channel_id": int}}
        self.active_vocs = {}
        self.check_empty_vocs.start()

    # ─── Helper : trouver la voc active d'un owner ───────────────
    def get_owner_voc(self, member_id):
        for ch_id, data in self.active_vocs.items():
            if data["owner"] == member_id:
                return ch_id, data
        return None, None

    # ─── Suppression propre (voc + salon texte) ──────────────────
    async def _delete_voc(self, guild, voc_id):
        data = self.active_vocs.pop(voc_id, None)
        voc = guild.get_channel(voc_id)
        if voc:
            try:
                await voc.delete()
            except:
                pass
        if data and data.get("text_channel_id"):
            text_ch = guild.get_channel(data["text_channel_id"])
            if text_ch:
                try:
                    await text_ch.delete()
                except:
                    pass

    # ─── Guard : doit avoir une voc active ───────────────────────
    async def _check_has_voc(self, ctx):
        voc_id, _ = self.get_owner_voc(ctx.author.id)
        if not voc_id:
            await ctx.send("❌ Tu n'as pas de salon vocal actif. Crée-en un avec `!createvoc NomDuSalon`.")
            return False
        return True

    # ─── !createvoc ──────────────────────────────────────────────
    @commands.command(name="createvoc")
    async def create_voc(self, ctx, *, nom: str = None):
        """Crée un salon vocal temporaire avec un salon de commandes privé."""
        if not nom:
            return await ctx.send("❌ Usage : `!createvoc NomDuSalon`")

        # Vérif : déjà une voc active
        voc_id, _ = self.get_owner_voc(ctx.author.id)
        if voc_id:
            ch = ctx.guild.get_channel(voc_id)
            return await ctx.send(
                f"❌ Tu as déjà un salon actif : **{ch.name if ch else 'inconnu'}**. "
                f"Supprime-le d'abord avec `!vocsuppr`."
            )

        # Catégorie
        category = discord.utils.get(ctx.guild.categories, name=CATEGORY_NAME)
        if not category:
            category = await ctx.guild.create_category(CATEGORY_NAME)

        # Salon vocal
        voc_name = f"🔊 {nom} — {ctx.author.display_name}"
        voc = await ctx.guild.create_voice_channel(
            name=voc_name,
            category=category,
            overwrites={
                ctx.guild.default_role: discord.PermissionOverwrite(connect=True, view_channel=True),
                ctx.author: discord.PermissionOverwrite(
                    connect=True, view_channel=True,
                    move_members=True, mute_members=True,
                    deafen_members=True, manage_channels=True
                )
            }
        )

        # Salon texte PRIVÉ (visible uniquement par l'owner)
        text_name = f"🔧・cmds-{ctx.author.display_name.lower().replace(' ', '-')}"
        text_ch = await ctx.guild.create_text_channel(
            name=text_name,
            category=category,
            overwrites={
                ctx.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                ctx.author: discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True
                ),
                ctx.guild.me: discord.PermissionOverwrite(
                    view_channel=True, send_messages=True
                )
            }
        )

        self.active_vocs[voc.id] = {
            "owner": ctx.author.id,
            "guild_id": ctx.guild.id,
            "text_channel_id": text_ch.id
        }

        # Help dans le salon privé
        embed = discord.Embed(
            title=f"🎙️ Ton salon vocal — {voc_name}",
            description=(
                "Seul toi vois ce salon. Tu peux utiliser ces commandes **depuis n'importe quel salon** du serveur.\n\n"
                "**Commandes disponibles :**\n"
                "`!vockick @membre` — Expulser un membre\n"
                "`!vocmute @membre` — Muter un membre\n"
                "`!vocunmute @membre` — Démuter un membre\n"
                "`!voclock` — Fermer le salon aux nouveaux\n"
                "`!vocunlock` — Rouvrir le salon\n"
                "`!vocrename NouveauNom` — Renommer le salon\n"
                "`!vocsuppr` — Supprimer le salon\n\n"
                "💡 Tape `?help` ici pour revoir ces commandes."
            ),
            color=0x5865F2
        )
        embed.set_footer(text="Ce salon sera supprimé automatiquement avec ton vocal.")
        await text_ch.send(ctx.author.mention, embed=embed)

        await ctx.send(
            f"✅ Salon **{voc_name}** créé ! "
            f"Gère-le avec `!vockick`, `!vocmute`, etc. depuis n'importe quel salon. "
            f"Ton salon privé : {text_ch.mention}"
        )

        # Suppression si personne ne rejoint en 30s
        await asyncio.sleep(30)
        voc_check = ctx.guild.get_channel(voc.id)
        if voc_check and len(voc_check.members) == 0:
            await self._delete_voc(ctx.guild, voc.id)
            try:
                await ctx.send(f"🗑️ **{voc_name}** supprimé — personne n'a rejoint en 30s.")
            except:
                pass

    # ─── Commandes de gestion (utilisables depuis n'importe quel salon) ──
    @commands.command(name="vockick")
    async def voc_kick(self, ctx, member: discord.Member):
        if not await self._check_has_voc(ctx):
            return
        voc_id, _ = self.get_owner_voc(ctx.author.id)
        voc = ctx.guild.get_channel(voc_id)
        if not voc or member not in voc.members:
            return await ctx.send("❌ Ce membre n'est pas dans ton salon.")
        await member.move_to(None)
        await ctx.send(f"👢 **{member.display_name}** expulsé du salon.")

    @commands.command(name="vocmute")
    async def voc_mute(self, ctx, member: discord.Member):
        if not await self._check_has_voc(ctx):
            return
        await member.edit(mute=True)
        await ctx.send(f"🔇 **{member.display_name}** muté.")

    @commands.command(name="vocunmute")
    async def voc_unmute(self, ctx, member: discord.Member):
        if not await self._check_has_voc(ctx):
            return
        await member.edit(mute=False)
        await ctx.send(f"🔊 **{member.display_name}** démuté.")

    @commands.command(name="voclock")
    async def voc_lock(self, ctx):
        if not await self._check_has_voc(ctx):
            return
        voc_id, _ = self.get_owner_voc(ctx.author.id)
        voc = ctx.guild.get_channel(voc_id)
        await voc.set_permissions(ctx.guild.default_role, connect=False)
        await ctx.send(f"🔒 **{voc.name}** verrouillé — plus personne ne peut rejoindre.")

    @commands.command(name="vocunlock")
    async def voc_unlock(self, ctx):
        if not await self._check_has_voc(ctx):
            return
        voc_id, _ = self.get_owner_voc(ctx.author.id)
        voc = ctx.guild.get_channel(voc_id)
        await voc.set_permissions(ctx.guild.default_role, connect=True)
        await ctx.send(f"🔓 **{voc.name}** déverrouillé.")

    @commands.command(name="vocrename")
    async def voc_rename(self, ctx, *, nouveau_nom: str):
        if not await self._check_has_voc(ctx):
            return
        voc_id, _ = self.get_owner_voc(ctx.author.id)
        voc = ctx.guild.get_channel(voc_id)
        await voc.edit(name=f"🔊 {nouveau_nom}")
        await ctx.send(f"✏️ Salon renommé en **{nouveau_nom}**.")

    @commands.command(name="vocsuppr")
    async def voc_suppr(self, ctx):
        voc_id, _ = self.get_owner_voc(ctx.author.id)
        if not voc_id:
            return await ctx.send("❌ Tu n'as pas de salon vocal actif.")
        voc = ctx.guild.get_channel(voc_id)
        name = voc.name if voc else "inconnu"
        await self._delete_voc(ctx.guild, voc_id)
        await ctx.send(f"🗑️ **{name}** supprimé.")

    # ─── Surveillance salons vides ────────────────────────────────
    @tasks.loop(seconds=10)
    async def check_empty_vocs(self):
        now = asyncio.get_event_loop().time()
        for voc_id, data in list(self.active_vocs.items()):
            guild = self.bot.get_guild(data["guild_id"])
            if not guild:
                continue
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
