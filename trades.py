import discord
from discord.ext import commands
from discord import app_commands
from difflib import SequenceMatcher
import asyncio
from db import load_db, save_db, get_member_data

SALON_TRADES = "trades"
SALON_MODERATION = "modération"
DUREE_TRADE = 30
DUREE_SELECTION = 60

NUMEROS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
RARETES_ORDRE = ["secret", "mythique", "legendaire", "hallal", "epique", "rare", "commun", "shlag"]
RARETES_EMOJI = {
    "secret": "🌈", "mythique": "🔴", "legendaire": "🟡",
    "hallal": "🟢", "epique": "🟣", "rare": "🔵", "commun": "⚪", "shlag": "⚫"
}
RARETES_COULEUR = {
    "secret": 0xFF1493, "mythique": 0xFF4500, "legendaire": 0xF1C40F,
    "hallal": 0x2ECC71, "epique": 0x9B59B6, "rare": 0x3498DB,
    "commun": 0xAAAAAA, "shlag": 0x2C2C2C
}

def similarite(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def top3_fuzzy(cartes, nom, seuil=0.4):
    scores = []
    for i, c in enumerate(cartes):
        score = similarite(nom, c["nom"])
        if nom.lower() in c["nom"].lower():
            score = max(score, 0.75)
        if score >= seuil:
            scores.append((i, c, score))
    scores.sort(key=lambda x: x[2], reverse=True)
    return scores[:3]

def trouver_carte_exacte(cartes, nom):
    for i, c in enumerate(cartes):
        if c["nom"].lower() == nom.lower():
            return i, c
    return None, None

async def interface_selection_cartes(bot, channel, author, membre_db, titre, couleur=0x5865F2):
    cartes = membre_db.get("cartes", [])
    if not cartes:
        await channel.send("❌ Tu n'as aucune carte dans ton inventaire.")
        return None

    cartes_triees = sorted(
        enumerate(cartes),
        key=lambda x: RARETES_ORDRE.index(x[1]["rarete"]) if x[1]["rarete"] in RARETES_ORDRE else 99
    )
    page = 0
    taille_page = 10

    async def afficher_page(msg=None):
        debut = page * taille_page
        fin = debut + taille_page
        page_cartes = cartes_triees[debut:fin]
        lignes = []
        for num, (idx_original, carte) in enumerate(page_cartes):
            emoji_r = RARETES_EMOJI.get(carte["rarete"], "❓")
            lignes.append(f"{NUMEROS[num]} {emoji_r} **{carte['nom']}** — *{carte['rarete']}*")
        total_pages = (len(cartes_triees) - 1) // taille_page + 1
        embed = discord.Embed(title=titre, description="\n".join(lignes), color=couleur)
        footer = f"Page {page + 1}/{total_pages} • Réagis avec le numéro • ❌ annuler"
        if fin < len(cartes_triees): footer += " • ▶ suite"
        if page > 0: footer += " • ◀ précédent"
        embed.set_footer(text=footer)
        reactions = [NUMEROS[n] for n in range(len(page_cartes))]
        if page > 0: reactions = ["◀"] + reactions
        if fin < len(cartes_triees): reactions.append("▶")
        reactions.append("❌")
        if msg is None:
            msg = await channel.send(embed=embed)
        else:
            await msg.clear_reactions()
            await msg.edit(embed=embed)
        for r in reactions:
            await msg.add_reaction(r)
        return msg, page_cartes

    msg, page_cartes = await afficher_page()

    def check(reaction, user):
        valid = NUMEROS[:len(page_cartes)] + ["◀", "▶", "❌"]
        return user == author and reaction.message.id == msg.id and str(reaction.emoji) in valid

    while True:
        try:
            reaction, _ = await bot.wait_for("reaction_add", timeout=DUREE_SELECTION, check=check)
        except asyncio.TimeoutError:
            await msg.edit(content="⌛ Sélection expirée.", embed=None)
            await msg.clear_reactions()
            return None

        emoji = str(reaction.emoji)
        if emoji == "❌":
            await msg.edit(content="❌ Sélection annulée.", embed=None)
            await msg.clear_reactions()
            return None
        if emoji == "▶":
            page += 1; msg, page_cartes = await afficher_page(msg); continue
        if emoji == "◀":
            page -= 1; msg, page_cartes = await afficher_page(msg); continue

        num = NUMEROS.index(emoji)
        _, carte_choisie = page_cartes[num]
        rarete = carte_choisie["rarete"]
        embed_valid = discord.Embed(
            title=f"✅ Carte sélectionnée — {carte_choisie['nom']}",
            description=f"{RARETES_EMOJI.get(rarete, '❓')} **{rarete.capitalize()}**",
            color=RARETES_COULEUR.get(rarete, 0x5865F2)
        )
        embed_valid.set_image(url=carte_choisie.get("image_url", ""))
        embed_valid.set_footer(text="Carte ajoutée à ton offre.")
        await channel.send(embed=embed_valid, delete_after=10)
        await msg.clear_reactions()
        return carte_choisie

async def interface_fuzzy(bot, channel, author, cartes, nom):
    resultats = top3_fuzzy(cartes, nom)
    if not resultats:
        await channel.send(f"❌ Aucune carte ressemblant à **{nom}** trouvée.")
        return None
    if resultats[0][2] >= 0.95:
        return resultats[0][1]

    lignes = [f"{NUMEROS[n]} {RARETES_EMOJI.get(c['rarete'], '❓')} **{c['nom']}** — *{c['rarete']}*"
              for n, (_, c, _) in enumerate(resultats)]
    embed = discord.Embed(
        title="🔍 Carte introuvable — voulais-tu dire ?",
        description=f"Je n'ai pas trouvé **\"{nom}\"**.\n\n" + "\n".join(lignes),
        color=0xF1C40F
    )
    embed.set_footer(text="Réagis avec le numéro • ❌ annuler")
    msg = await channel.send(embed=embed)
    for n in range(len(resultats)):
        await msg.add_reaction(NUMEROS[n])
    await msg.add_reaction("❌")

    def check(reaction, user):
        return user == author and reaction.message.id == msg.id and str(reaction.emoji) in NUMEROS[:len(resultats)] + ["❌"]

    try:
        reaction, _ = await bot.wait_for("reaction_add", timeout=30.0, check=check)
    except asyncio.TimeoutError:
        await msg.edit(content="⌛ Expiré.", embed=None)
        return None

    if str(reaction.emoji) == "❌":
        await msg.edit(content="❌ Annulé.", embed=None)
        return None

    num = NUMEROS.index(str(reaction.emoji))
    _, carte_choisie, _ = resultats[num]
    rarete = carte_choisie["rarete"]
    embed_valid = discord.Embed(
        title=f"✅ {carte_choisie['nom']}",
        color=RARETES_COULEUR.get(rarete, 0x5865F2)
    )
    embed_valid.set_image(url=carte_choisie.get("image_url", ""))
    await channel.send(embed=embed_valid, delete_after=10)
    await msg.clear_reactions()
    return carte_choisie

async def construire_offre(bot, channel, author, membre_db, nom_membre):
    cartes_choisies = []
    coins_choisis = 0

    while True:
        lignes_offre = [f"🃏 {c['nom']} *({c['rarete']})*" for c in cartes_choisies]
        if coins_choisis:
            lignes_offre.append(f"🪙 {coins_choisis} pièces")

        embed_menu = discord.Embed(
            title=f"🔄 Construction de l'offre — {nom_membre}",
            description="**Offre actuelle :**\n" + ("\n".join(lignes_offre) if lignes_offre else "*Rien*"),
            color=0x5865F2
        )
        embed_menu.add_field(name="Actions", value="🃏 Ajouter une carte\n🪙 Ajouter des pièces\n✅ Valider\n❌ Annuler", inline=False)
        embed_menu.set_footer(text=f"Solde disponible : {membre_db.get('coins', 0)} pièces")
        msg_menu = await channel.send(embed=embed_menu)
        for emoji in ["🃏", "🪙", "✅", "❌"]:
            await msg_menu.add_reaction(emoji)

        def check_menu(reaction, user):
            return user == author and reaction.message.id == msg_menu.id and str(reaction.emoji) in ["🃏", "🪙", "✅", "❌"]

        try:
            reaction, _ = await bot.wait_for("reaction_add", timeout=DUREE_SELECTION, check=check_menu)
        except asyncio.TimeoutError:
            await msg_menu.edit(content="⌛ Offre expirée.", embed=None)
            await msg_menu.clear_reactions()
            return None, None

        await msg_menu.clear_reactions()
        choix = str(reaction.emoji)

        if choix == "❌":
            await msg_menu.edit(content="❌ Trade annulé.", embed=None)
            return None, None

        if choix == "✅":
            if not cartes_choisies and coins_choisis == 0:
                await channel.send("❌ Offre vide !", delete_after=5)
                continue
            return cartes_choisies, coins_choisis

        if choix == "🪙":
            solde = membre_db.get("coins", 0) - coins_choisis
            if solde <= 0:
                await channel.send("❌ Plus de pièces disponibles.", delete_after=5)
                continue
            await channel.send(f"🪙 Combien de pièces ? *(Solde restant : **{solde} pièces**)*\nTape le montant ou `annuler`.")

            def check_msg(m):
                return m.author == author and m.channel == channel

            try:
                reponse = await bot.wait_for("message", timeout=30.0, check=check_msg)
            except asyncio.TimeoutError:
                await channel.send("⌛ Temps écoulé.", delete_after=5)
                continue

            await reponse.delete()
            if reponse.content.lower() == "annuler":
                continue
            if not reponse.content.isdigit() or int(reponse.content) <= 0:
                await channel.send("❌ Montant invalide.", delete_after=5)
                continue
            montant = int(reponse.content)
            if montant > solde:
                await channel.send(f"❌ Tu n'as que **{solde} pièces** disponibles.", delete_after=5)
                continue
            coins_choisis += montant
            await channel.send(f"✅ **{montant} pièces** ajoutées.", delete_after=5)
            continue

        if choix == "🃏":
            noms_deja_choisis = [c["nom"] for c in cartes_choisies]
            cartes_restantes = {"cartes": [c for c in membre_db.get("cartes", []) if c["nom"] not in noms_deja_choisis], "coins": membre_db.get("coins", 0)}
            if not cartes_restantes["cartes"]:
                await channel.send("❌ Plus de cartes disponibles.", delete_after=5)
                continue
            carte = await interface_selection_cartes(bot, channel, author, cartes_restantes, "🃏 Choisis une carte", couleur=0x5865F2)
            if carte:
                cartes_choisies.append(carte)

def build_embed_trade(auteur, cible, offre_cartes, offre_coins, demande_cartes, demande_coins, statut="en attente"):
    couleurs = {"en attente": 0xF1C40F, "accepté": 0x2ECC71, "refusé": 0xE74C3C, "expiré": 0x95A5A6}
    embed = discord.Embed(title="🔄 Proposition de Trade", color=couleurs.get(statut, 0x5865F2))
    offre_lines = [f"🃏 {c['nom']} *({c['rarete']})*" for c in offre_cartes]
    if offre_coins: offre_lines.append(f"🪙 {offre_coins} pièces")
    embed.add_field(name=f"📤 Offre de {auteur.display_name}", value="\n".join(offre_lines) if offre_lines else "*(rien)*", inline=True)
    demande_lines = [f"🃏 {c['nom']} *({c['rarete']})*" for c in demande_cartes]
    if demande_coins: demande_lines.append(f"🪙 {demande_coins} pièces")
    embed.add_field(name=f"📥 En échange de {cible.display_name}", value="\n".join(demande_lines) if demande_lines else "*(rien)*", inline=True)
    statut_texte = {
        "en attente": f"⏳ En attente des deux confirmations ({DUREE_TRADE}s)",
        "accepté": "✅ Trade accepté !", "refusé": "❌ Trade refusé", "expiré": "⌛ Trade expiré"
    }
    embed.add_field(name="Statut", value=statut_texte.get(statut, statut), inline=False)
    embed.set_footer(text="✅ accepter • ❌ refuser")
    return embed

async def _executer_trade(bot, channel, auteur, cible, offre_cartes_obj, coins_offre, demande_cartes_obj, coins_demande):
    embed = build_embed_trade(auteur, cible, offre_cartes_obj, coins_offre, demande_cartes_obj, coins_demande)
    msg = await channel.send(f"{auteur.mention} {cible.mention} — confirmez le trade !", embed=embed)
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")
    reponses = {}

    def check(reaction, user):
        return (user.id in (auteur.id, cible.id) and str(reaction.emoji) in ("✅", "❌")
                and reaction.message.id == msg.id and user.id not in reponses)

    try:
        deadline = asyncio.get_event_loop().time() + DUREE_TRADE
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0: raise asyncio.TimeoutError()
            reaction, user = await bot.wait_for("reaction_add", timeout=remaining, check=check)
            reponses[user.id] = str(reaction.emoji)
            if str(reaction.emoji) == "❌": raise ValueError("refus")
            if auteur.id in reponses and cible.id in reponses: break
    except asyncio.TimeoutError:
        await msg.edit(embed=build_embed_trade(auteur, cible, offre_cartes_obj, coins_offre, demande_cartes_obj, coins_demande, "expiré"))
        return False
    except ValueError:
        refuseur = cible if reponses.get(cible.id) == "❌" else auteur
        embed_ref = build_embed_trade(auteur, cible, offre_cartes_obj, coins_offre, demande_cartes_obj, coins_demande, "refusé")
        embed_ref.set_footer(text=f"Refusé par {refuseur.display_name}")
        await msg.edit(embed=embed_ref)
        return False

    # Exécution
    uid_auteur = str(auteur.id)
    uid_cible = str(cible.id)
    db = load_db()
    for carte in offre_cartes_obj:
        idx, _ = trouver_carte_exacte(db[uid_auteur].get("cartes", []), carte["nom"])
        if idx is not None:
            db[uid_auteur]["cartes"].pop(idx)
            db[uid_cible].setdefault("cartes", []).append(carte)
    for carte in demande_cartes_obj:
        idx, _ = trouver_carte_exacte(db[uid_cible].get("cartes", []), carte["nom"])
        if idx is not None:
            db[uid_cible]["cartes"].pop(idx)
            db[uid_auteur].setdefault("cartes", []).append(carte)
    db[uid_auteur]["coins"] = db[uid_auteur].get("coins", 0) - coins_offre + coins_demande
    db[uid_cible]["coins"] = db[uid_cible].get("coins", 0) - coins_demande + coins_offre
    save_db(db)
    await msg.edit(embed=build_embed_trade(auteur, cible, offre_cartes_obj, coins_offre, demande_cartes_obj, coins_demande, "accepté"))
    return True

class Trades(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.trades_actifs = set()

    async def verif_salon(self, channel, nom):
        if nom not in channel.name.lower():
            salon = discord.utils.get(channel.guild.text_channels, name__contains=nom)
            mention = salon.mention if salon else f"`🔄・{nom}`"
            await channel.send(f"❌ Cette commande n'est utilisable que dans {mention}.")
            return False
        return True

    async def _lancer_trade(self, bot, channel, auteur, cible, args=None):
        db = load_db()
        uid_auteur = str(auteur.id)
        uid_cible = str(cible.id)
        get_member_data(db, auteur.id)
        get_member_data(db, cible.id)
        save_db(db)
        db = load_db()

        self.trades_actifs.add(auteur.id)
        self.trades_actifs.add(cible.id)
        try:
            if args:
                args_lower = args.lower()
                if not args_lower.startswith("give ") or " contre " not in args_lower:
                    await channel.send(
                        "❌ Format : `trade @membre give [carte/pièces] contre [carte/pièces]`\n"
                        "Ou sans args pour l'interface interactive.\n\n"
                        "**Exemples :**\n"
                        "`!trade @Bob give Kebab Froid contre Pigeon de Paris`\n"
                        "`!trade @Bob give Kebab Froid et 100 pièces contre Glitch Matrix`\n"
                        "`!trade @Bob give 200 pièces contre Carte Inconnue`"
                    )
                    return

                idx_contre = args_lower.find(" contre ")
                partie_offre_raw = args[5:idx_contre].strip()
                partie_demande_raw = args[idx_contre + 8:].strip()

                def parser(texte):
                    coins = 0
                    noms = []
                    for p in texte.split(" et "):
                        p = p.strip()
                        p_lower = p.lower().replace(" ", "").rstrip("s")
                        if p_lower.endswith("pièce") or p_lower.endswith("piece"):
                            n = p.split()[0]
                            if n.isdigit(): coins += int(n)
                        elif p:
                            noms.append(p)
                    return noms, coins

                noms_offre, coins_offre = parser(partie_offre_raw)
                noms_demande, coins_demande = parser(partie_demande_raw)

                offre_cartes_obj = []
                for nom in noms_offre:
                    idx, carte = trouver_carte_exacte(db[uid_auteur].get("cartes", []), nom)
                    if idx is None:
                        carte = await interface_fuzzy(bot, channel, auteur, db[uid_auteur].get("cartes", []), nom)
                        if carte is None: return
                    offre_cartes_obj.append(carte)

                demande_cartes_obj = []
                for nom in noms_demande:
                    idx, carte = trouver_carte_exacte(db[uid_cible].get("cartes", []), nom)
                    if idx is None:
                        carte = await interface_fuzzy(bot, channel, cible, db[uid_cible].get("cartes", []), nom)
                        if carte is None: return
                    demande_cartes_obj.append(carte)

                if coins_offre > db[uid_auteur].get("coins", 0):
                    await channel.send(f"❌ Tu n'as que **{db[uid_auteur].get('coins', 0)} pièces**.")
                    return
                if coins_demande > db[uid_cible].get("coins", 0):
                    await channel.send(f"❌ **{cible.display_name}** n'a que **{db[uid_cible].get('coins', 0)} pièces**.")
                    return
            else:
                await channel.send(f"🔄 **Trade interactif !**\n{auteur.mention}, construis ton offre pour {cible.mention}.")
                offre_cartes_obj, coins_offre = await construire_offre(bot, channel, auteur, db[uid_auteur], auteur.display_name)
                if offre_cartes_obj is None: return
                await channel.send(f"✅ Offre de {auteur.mention} construite !\n{cible.mention}, qu'est-ce que tu proposes en échange ?")
                demande_cartes_obj, coins_demande = await construire_offre(bot, channel, cible, db[uid_cible], cible.display_name)
                if demande_cartes_obj is None: return

            await _executer_trade(bot, channel, auteur, cible, offre_cartes_obj, coins_offre, demande_cartes_obj, coins_demande)
        finally:
            self.trades_actifs.discard(auteur.id)
            self.trades_actifs.discard(cible.id)

    # ── ! commandes ──────────────────────────────────────────
    @commands.command(name="trade")
    async def trade(self, ctx, cible: discord.Member, *, args: str = None):
        if not await self.verif_salon(ctx.channel, SALON_TRADES): return
        if cible.bot or cible == ctx.author: return await ctx.send("❌ Destinataire invalide.")
        if ctx.author.id in self.trades_actifs or cible.id in self.trades_actifs:
            return await ctx.send("❌ L'un de vous est déjà en cours de trade.")
        await self._lancer_trade(self.bot, ctx.channel, ctx.author, cible, args)

    @commands.command(name="donner")
    async def donner(self, ctx, cible: discord.Member, montant: int):
        if not await self.verif_salon(ctx.channel, SALON_TRADES): return
        await _do_donner(self.bot, ctx.channel, ctx.author, cible, montant)

    @commands.command(name="tradecancel")
    async def tradecancel(self, ctx, member: discord.Member):
        if SALON_MODERATION not in ctx.channel.name.lower():
            return await ctx.send(f"❌ Commande réservée au salon modération.")
        if not any(r.name in ["Modérateur", "Fondateur"] for r in ctx.author.roles):
            return await ctx.send("❌ Réservé aux modérateurs.")
        if member.id in self.trades_actifs:
            self.trades_actifs.discard(member.id)
            await ctx.send(f"✅ Trade de **{member.display_name}** débloqué.")
        else:
            await ctx.send(f"ℹ️ **{member.display_name}** n'est pas en cours de trade.")

    # ── / commandes ──────────────────────────────────────────
    @app_commands.command(name="trade", description="Propose un trade interactif à un membre")
    @app_commands.describe(membre="Le membre avec qui trader")
    async def slash_trade(self, interaction: discord.Interaction, membre: discord.Member):
        await interaction.response.defer()
        if not await self.verif_salon(interaction.channel, SALON_TRADES): return
        if membre.bot or membre == interaction.user:
            return await interaction.followup.send("❌ Destinataire invalide.", ephemeral=True)
        if interaction.user.id in self.trades_actifs or membre.id in self.trades_actifs:
            return await interaction.followup.send("❌ L'un de vous est déjà en cours de trade.", ephemeral=True)
        await self._lancer_trade(self.bot, interaction.channel, interaction.user, membre, None)

    @app_commands.command(name="donner", description="Donne des pièces à un membre")
    @app_commands.describe(membre="Le membre à qui donner des pièces", montant="Nombre de pièces à donner")
    async def slash_donner(self, interaction: discord.Interaction, membre: discord.Member, montant: int):
        await interaction.response.defer()
        if not await self.verif_salon(interaction.channel, SALON_TRADES): return
        await _do_donner(self.bot, interaction.channel, interaction.user, membre, montant)

async def _do_donner(bot, channel, auteur, cible, montant):
    if cible.bot or cible == auteur:
        return await channel.send("❌ Destinataire invalide.")
    if montant <= 0:
        return await channel.send("❌ Le montant doit être supérieur à 0 pièces.")

    db = load_db()
    uid_auteur = str(auteur.id)
    uid_cible = str(cible.id)
    get_member_data(db, auteur.id)
    get_member_data(db, cible.id)
    solde = db[uid_auteur].get("coins", 0)

    if solde < montant:
        return await channel.send(f"❌ Tu n'as que **{solde} pièces**, tu ne peux pas en donner {montant} pièces.")

    embed = discord.Embed(
        title="🪙 Confirmation de don",
        description=f"Donner **{montant} pièces** à {cible.mention} ?\n\n💰 Solde actuel : **{solde} pièces**\n💰 Solde après : **{solde - montant} pièces**",
        color=0xF1C40F
    )
    embed.set_footer(text="✅ Confirmer  •  ❌ Annuler  •  Expire dans 30s")
    msg = await channel.send(embed=embed)
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")

    def check(reaction, user):
        return user == auteur and str(reaction.emoji) in ("✅", "❌") and reaction.message.id == msg.id

    try:
        reaction, _ = await bot.wait_for("reaction_add", timeout=30.0, check=check)
    except asyncio.TimeoutError:
        embed.color = 0x95A5A6
        embed.set_footer(text="⌛ Don annulé")
        await msg.edit(embed=embed)
        return

    if str(reaction.emoji) == "❌":
        embed.color = 0xE74C3C
        embed.set_footer(text="❌ Don annulé")
        await msg.edit(embed=embed)
        return

    db[uid_auteur]["coins"] = solde - montant
    db[uid_cible]["coins"] = db[uid_cible].get("coins", 0) + montant
    save_db(db)

    await msg.edit(embed=discord.Embed(
        title="🪙 Don effectué !",
        description=f"{auteur.mention} a donné **{montant} pièces** à {cible.mention} !\n\n💰 Solde de {auteur.display_name} : **{db[uid_auteur]['coins']} pièces**\n💰 Solde de {cible.display_name} : **{db[uid_cible]['coins']} pièces**",
        color=0x2ECC71
    ))

async def setup(bot):
    await bot.add_cog(Trades(bot))
