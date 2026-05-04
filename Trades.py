# trades.py — Système de trade avec interface interactive et fuzzy matching

import discord
from discord.ext import commands
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
    "hallal": "🟢", "epique": "🟣", "rare": "🔵",
    "commun": "⚪", "shlag": "⚫"
}
RARETES_COULEUR = {
    "secret": 0xFF1493, "mythique": 0xFF4500, "legendaire": 0xF1C40F,
    "hallal": 0x2ECC71, "epique": 0x9B59B6, "rare": 0x3498DB,
    "commun": 0xAAAAAA, "shlag": 0x2C2C2C
}

# ─── Fuzzy matching ───────────────────────────────────────────────

def similarite(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def top3_fuzzy(cartes: list, nom: str, seuil: float = 0.4):
    scores = []
    for i, c in enumerate(cartes):
        score = similarite(nom, c["nom"])
        if nom.lower() in c["nom"].lower():
            score = max(score, 0.75)
        if score >= seuil:
            scores.append((i, c, score))
    scores.sort(key=lambda x: x[2], reverse=True)
    return scores[:3]

def trouver_carte_exacte(cartes: list, nom: str):
    for i, c in enumerate(cartes):
        if c["nom"].lower() == nom.lower():
            return i, c
    return None, None

# ─── Interface de sélection interactive ──────────────────────────

async def interface_selection_cartes(bot, ctx_or_channel, author, membre_db: dict, titre: str, couleur: int = 0x5865F2):
    """
    Affiche un embed numéroté avec les cartes du membre.
    Retourne la carte sélectionnée ou None si annulé/timeout.
    Fonctionne avec ctx (commandes) ou channel (usage interne).
    """
    # Compatibilité ctx ou channel direct
    channel = ctx_or_channel.channel if hasattr(ctx_or_channel, 'channel') else ctx_or_channel

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
        footer = f"Page {page + 1}/{total_pages} • Réagis avec le numéro pour sélectionner • ❌ pour annuler"
        if fin < len(cartes_triees):
            footer += " • ▶ page suivante"
        if page > 0:
            footer += " • ◀ page précédente"
        embed.set_footer(text=footer)

        if msg is None:
            msg = await channel.send(embed=embed)
            for num in range(len(page_cartes)):
                await msg.add_reaction(NUMEROS[num])
            if page > 0:
                await msg.add_reaction("◀")
            if fin < len(cartes_triees):
                await msg.add_reaction("▶")
            await msg.add_reaction("❌")
        else:
            await msg.clear_reactions()
            await msg.edit(embed=embed)
            for num in range(len(page_cartes)):
                await msg.add_reaction(NUMEROS[num])
            if page > 0:
                await msg.add_reaction("◀")
            if fin < len(cartes_triees):
                await msg.add_reaction("▶")
            await msg.add_reaction("❌")

        return msg, page_cartes

    msg, page_cartes = await afficher_page()

    def check(reaction, user):
        return (
            user == author
            and reaction.message.id == msg.id
            and str(reaction.emoji) in NUMEROS[:len(page_cartes)] + ["◀", "▶", "❌"]
        )

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
            page += 1
            msg, page_cartes = await afficher_page(msg)
            continue

        if emoji == "◀":
            page -= 1
            msg, page_cartes = await afficher_page(msg)
            continue

        num = NUMEROS.index(emoji)
        idx_original, carte_choisie = page_cartes[num]

        rarete = carte_choisie["rarete"]
        embed_valid = discord.Embed(
            title=f"✅ Carte sélectionnée — {carte_choisie['nom']}",
            description=f"{RARETES_EMOJI.get(rarete, '❓')} **{rarete.capitalize()}**",
            color=RARETES_COULEUR.get(rarete, 0x5865F2)
        )
        embed_valid.set_image(url=carte_choisie.get("image_url", ""))
        embed_valid.set_footer(text="Cette carte a été ajoutée à ton offre de trade.")
        await channel.send(embed=embed_valid, delete_after=10)
        await msg.clear_reactions()
        return carte_choisie


async def interface_fuzzy(bot, channel, author, cartes: list, nom: str):
    resultats = top3_fuzzy(cartes, nom)

    if not resultats:
        await channel.send(f"❌ Aucune carte ressemblant à **{nom}** trouvée dans cet inventaire.")
        return None

    if resultats[0][2] >= 0.95:
        return resultats[0][1]

    lignes = []
    for num, (idx, carte, score) in enumerate(resultats):
        emoji_r = RARETES_EMOJI.get(carte["rarete"], "❓")
        lignes.append(f"{NUMEROS[num]} {emoji_r} **{carte['nom']}** — *{carte['rarete']}*")

    embed = discord.Embed(
        title="🔍 Carte introuvable — voulais-tu dire ?",
        description=(
            f"Je n'ai pas trouvé **\"{nom}\"** exactement.\n"
            f"Voici les cartes les plus proches :\n\n" + "\n".join(lignes)
        ),
        color=0xF1C40F
    )
    embed.set_footer(text="Réagis avec le numéro correspondant • ❌ pour annuler")
    msg = await channel.send(embed=embed)

    for num in range(len(resultats)):
        await msg.add_reaction(NUMEROS[num])
    await msg.add_reaction("❌")

    def check(reaction, user):
        return (
            user == author
            and reaction.message.id == msg.id
            and str(reaction.emoji) in NUMEROS[:len(resultats)] + ["❌"]
        )

    try:
        reaction, _ = await bot.wait_for("reaction_add", timeout=30.0, check=check)
    except asyncio.TimeoutError:
        await msg.edit(content="⌛ Sélection expirée.", embed=None)
        return None

    if str(reaction.emoji) == "❌":
        await msg.edit(content="❌ Annulé.", embed=None)
        return None

    num = NUMEROS.index(str(reaction.emoji))
    _, carte_choisie, _ = resultats[num]

    rarete = carte_choisie["rarete"]
    embed_valid = discord.Embed(
        title=f"✅ Carte sélectionnée — {carte_choisie['nom']}",
        color=RARETES_COULEUR.get(rarete, 0x5865F2)
    )
    embed_valid.set_image(url=carte_choisie.get("image_url", ""))
    await channel.send(embed=embed_valid, delete_after=10)
    await msg.clear_reactions()
    return carte_choisie


# ─── Construction d'une offre ─────────────────────────────────────

async def construire_offre(bot, channel, author, membre_db: dict, nom_membre: str):
    cartes_choisies = []
    coins_choisis = 0
    cartes_dispo = list(membre_db.get("cartes", []))

    while True:
        lignes_offre = [f"🃏 {c['nom']} *({c['rarete']})*" for c in cartes_choisies]
        if coins_choisis:
            lignes_offre.append(f"🪙 {coins_choisis} pièces")

        embed_menu = discord.Embed(
            title=f"🔄 Construction de l'offre — {nom_membre}",
            description=(
                "**Offre actuelle :**\n"
                + ("\n".join(lignes_offre) if lignes_offre else "*Rien pour l'instant*")
            ),
            color=0x5865F2
        )
        embed_menu.add_field(
            name="Actions",
            value="🃏 Ajouter une carte\n🪙 Ajouter des pièces\n✅ Valider l'offre\n❌ Annuler",
            inline=False
        )
        embed_menu.set_footer(text=f"Solde disponible : {membre_db.get('coins', 0)} pièces")
        msg_menu = await channel.send(embed=embed_menu)
        for emoji in ["🃏", "🪙", "✅", "❌"]:
            await msg_menu.add_reaction(emoji)

        def check_menu(reaction, user):
            return (
                user == author
                and reaction.message.id == msg_menu.id
                and str(reaction.emoji) in ["🃏", "🪙", "✅", "❌"]
            )

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
                await channel.send("❌ Ton offre est vide ! Ajoute au moins une carte ou des pièces.", delete_after=5)
                continue
            return cartes_choisies, coins_choisis

        if choix == "🪙":
            solde = membre_db.get("coins", 0) - coins_choisis
            if solde <= 0:
                await channel.send("❌ Tu n'as plus de pièces disponibles.", delete_after=5)
                continue

            await channel.send(
                f"🪙 Combien de pièces veux-tu ajouter ?\n"
                f"*(Solde restant disponible : **{solde} pièces**)*\nTape le montant ou `annuler`."
            )

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
            if not reponse.content.isdigit():
                await channel.send("❌ Montant invalide.", delete_after=5)
                continue

            montant = int(reponse.content)
            if montant <= 0:
                await channel.send("❌ Le montant doit être supérieur à 0.", delete_after=5)
                continue
            if montant > solde:
                await channel.send(f"❌ Tu n'as que **{solde} pièces** disponibles.", delete_after=5)
                continue

            coins_choisis += montant
            await channel.send(f"✅ **{montant} pièces** ajoutées à ton offre.", delete_after=5)
            continue

        if choix == "🃏":
            if not cartes_dispo:
                await channel.send("❌ Tu n'as plus de cartes disponibles.", delete_after=5)
                continue

            noms_deja_choisis = [c["nom"] for c in cartes_choisies]
            cartes_restantes_db = {
                "cartes": [c for c in cartes_dispo if c["nom"] not in noms_deja_choisis],
                "coins": membre_db.get("coins", 0)
            }

            carte = await interface_selection_cartes(
                bot, channel, author, cartes_restantes_db,
                titre="🃏 Choisis une carte à ajouter à ton offre",
                couleur=0x5865F2
            )
            if carte is None:
                continue
            cartes_choisies.append(carte)
            continue

    return cartes_choisies, coins_choisis


# ─── Embed de trade ───────────────────────────────────────────────

def build_embed_trade(auteur, cible, offre_cartes, offre_coins, demande_cartes, demande_coins, statut="en attente"):
    couleurs = {
        "en attente": 0xF1C40F,
        "accepté":    0x2ECC71,
        "refusé":     0xE74C3C,
        "expiré":     0x95A5A6
    }
    embed = discord.Embed(title="🔄 Proposition de Trade", color=couleurs.get(statut, 0x5865F2))

    offre_lines = [f"🃏 {c['nom']} *({c['rarete']})*" for c in offre_cartes]
    if offre_coins:
        offre_lines.append(f"🪙 {offre_coins} pièces")
    embed.add_field(
        name=f"📤 Offre de {auteur.display_name}",
        value="\n".join(offre_lines) if offre_lines else "*(rien)*",
        inline=True
    )

    demande_lines = [f"🃏 {c['nom']} *({c['rarete']})*" for c in demande_cartes]
    if demande_coins:
        demande_lines.append(f"🪙 {demande_coins} pièces")
    embed.add_field(
        name=f"📥 En échange de {cible.display_name}",
        value="\n".join(demande_lines) if demande_lines else "*(rien)*",
        inline=True
    )

    statut_texte = {
        "en attente": f"⏳ En attente des deux confirmations ({DUREE_TRADE}s)",
        "accepté":    "✅ Trade accepté et effectué !",
        "refusé":     "❌ Trade refusé",
        "expiré":     "⌛ Trade expiré — pas de réponse"
    }
    embed.add_field(name="Statut", value=statut_texte.get(statut, statut), inline=False)
    embed.set_footer(text="Réagis avec ✅ pour accepter ou ❌ pour refuser")
    return embed


# ─── Cog principal ────────────────────────────────────────────────

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

    @commands.command(name="trade")
    async def trade(self, ctx, cible: discord.Member, *, args: str = None):
        if not await self.verif_salon(ctx.channel, SALON_TRADES):
            return
        if cible.bot or cible == ctx.author:
            return await ctx.send("❌ Tu ne peux pas trader avec un bot ou toi-même.")
        if ctx.author.id in self.trades_actifs or cible.id in self.trades_actifs:
            return await ctx.send("❌ L'un de vous est déjà en cours de trade.")

        db = load_db()
        uid_auteur = str(ctx.author.id)
        uid_cible = str(cible.id)
        # Initialise les données si besoin (compatible avec get_member_data)
        get_member_data(db, ctx.author.id)
        get_member_data(db, cible.id)
        # Assure que le champ "cartes" existe
        db[uid_auteur].setdefault("cartes", [])
        db[uid_cible].setdefault("cartes", [])
        save_db(db)

        self.trades_actifs.add(ctx.author.id)
        self.trades_actifs.add(cible.id)

        try:
            # ── Cas 1 : commande texte avec args ──
            if args:
                args_lower = args.lower()
                if not args_lower.startswith("give ") or " contre " not in args_lower:
                    await ctx.send(
                        "❌ Format incorrect.\n"
                        "Usage : `!trade @membre give [carte/pièces] contre [carte/pièces]`\n"
                        "Ou simplement `!trade @membre` pour l'interface interactive."
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
                            if n.isdigit():
                                coins += int(n)
                        elif p:
                            noms.append(p)
                    return noms, coins

                noms_offre, coins_offre = parser(partie_offre_raw)
                noms_demande, coins_demande = parser(partie_demande_raw)

                offre_cartes_obj = []
                for nom in noms_offre:
                    idx, carte = trouver_carte_exacte(db[uid_auteur].get("cartes", []), nom)
                    if idx is None:
                        carte = await interface_fuzzy(
                            self.bot, ctx.channel, ctx.author,
                            db[uid_auteur].get("cartes", []), nom
                        )
                        if carte is None:
                            return
                    offre_cartes_obj.append(carte)

                demande_cartes_obj = []
                for nom in noms_demande:
                    idx, carte = trouver_carte_exacte(db[uid_cible].get("cartes", []), nom)
                    if idx is None:
                        carte = await interface_fuzzy(
                            self.bot, ctx.channel, cible,
                            db[uid_cible].get("cartes", []), nom
                        )
                        if carte is None:
                            return
                    demande_cartes_obj.append(carte)

                if coins_offre > db[uid_auteur].get("coins", 0):
                    await ctx.send(
                        f"❌ Tu n'as que **{db[uid_auteur].get('coins', 0)} pièces**,"
                        f" tu ne peux pas en offrir {coins_offre}."
                    )
                    return
                if coins_demande > db[uid_cible].get("coins", 0):
                    await ctx.send(
                        f"❌ **{cible.display_name}** n'a que **{db[uid_cible].get('coins', 0)} pièces**."
                    )
                    return

            # ── Cas 2 : interface interactive ──
            else:
                await ctx.send(
                    f"🔄 **Trade interactif lancé !**\n"
                    f"{ctx.author.mention}, construis ton offre pour {cible.mention}."
                )

                offre_cartes_obj, coins_offre = await construire_offre(
                    self.bot, ctx.channel, ctx.author,
                    db[uid_auteur], nom_membre=ctx.author.display_name
                )
                if offre_cartes_obj is None:
                    return

                await ctx.send(
                    f"✅ Offre de {ctx.author.mention} construite !\n"
                    f"Maintenant, {cible.mention}, qu'est-ce que tu proposes en échange ?"
                )

                demande_cartes_obj, coins_demande = await construire_offre(
                    self.bot, ctx.channel, cible,
                    db[uid_cible], nom_membre=cible.display_name
                )
                if demande_cartes_obj is None:
                    return

            # ── Proposition finale ──
            embed = build_embed_trade(
                ctx.author, cible,
                offre_cartes_obj, coins_offre,
                demande_cartes_obj, coins_demande
            )
            msg = await ctx.send(
                f"{ctx.author.mention} {cible.mention} — confirmez le trade !",
                embed=embed
            )
            await msg.add_reaction("✅")
            await msg.add_reaction("❌")

            reponses = {}

            def check(reaction, user):
                return (
                    user.id in (ctx.author.id, cible.id)
                    and str(reaction.emoji) in ("✅", "❌")
                    and reaction.message.id == msg.id
                    and user.id not in reponses
                )

            try:
                deadline = asyncio.get_event_loop().time() + DUREE_TRADE
                while True:
                    remaining = deadline - asyncio.get_event_loop().time()
                    if remaining <= 0:
                        raise asyncio.TimeoutError()
                    reaction, user = await self.bot.wait_for(
                        "reaction_add", timeout=remaining, check=check
                    )
                    reponses[user.id] = str(reaction.emoji)
                    if str(reaction.emoji) == "❌":
                        raise ValueError("refus")
                    if ctx.author.id in reponses and cible.id in reponses:
                        break

            except asyncio.TimeoutError:
                await msg.edit(embed=build_embed_trade(
                    ctx.author, cible, offre_cartes_obj, coins_offre,
                    demande_cartes_obj, coins_demande, "expiré"
                ))
                return

            except ValueError:
                refuseur = cible if reponses.get(cible.id) == "❌" else ctx.author
                embed_ref = build_embed_trade(
                    ctx.author, cible, offre_cartes_obj, coins_offre,
                    demande_cartes_obj, coins_demande, "refusé"
                )
                embed_ref.set_footer(text=f"Refusé par {refuseur.display_name}")
                await msg.edit(embed=embed_ref)
                return

            # ── Exécution du trade ──
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

            await msg.edit(embed=build_embed_trade(
                ctx.author, cible, offre_cartes_obj, coins_offre,
                demande_cartes_obj, coins_demande, "accepté"
            ))

        finally:
            self.trades_actifs.discard(ctx.author.id)
            self.trades_actifs.discard(cible.id)

    @commands.command(name="donner")
    async def donner(self, ctx, cible: discord.Member, montant: int, *, label: str = ""):
        if not await self.verif_salon(ctx.channel, SALON_TRADES):
            return
        if cible.bot or cible == ctx.author:
            return await ctx.send("❌ Destinataire invalide.")
        if montant <= 0:
            return await ctx.send("❌ Le montant doit être supérieur à 0 pièces.")

        db = load_db()
        uid_auteur = str(ctx.author.id)
        uid_cible = str(cible.id)
        get_member_data(db, ctx.author.id)
        get_member_data(db, cible.id)

        solde = db[uid_auteur].get("coins", 0)
        if solde < montant:
            return await ctx.send(
                f"❌ Tu n'as que **{solde} pièces**, tu ne peux pas en donner {montant}."
            )

        embed = discord.Embed(
            title="🪙 Confirmation de don",
            description=(
                f"Tu es sur le point de donner **{montant} pièces** à {cible.mention}.\n\n"
                f"💰 Solde actuel : **{solde} pièces**\n"
                f"💰 Solde après : **{solde - montant} pièces**"
            ),
            color=0xF1C40F
        )
        embed.set_footer(text="✅ Confirmer  •  ❌ Annuler  •  Expire dans 30s")
        msg = await ctx.send(embed=embed)
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")

        def check(reaction, user):
            return (
                user == ctx.author
                and str(reaction.emoji) in ("✅", "❌")
                and reaction.message.id == msg.id
            )

        try:
            reaction, _ = await self.bot.wait_for("reaction_add", timeout=30.0, check=check)
        except asyncio.TimeoutError:
            embed.color = 0x95A5A6
            embed.set_footer(text="⌛ Don annulé — pas de confirmation")
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
            description=(
                f"{ctx.author.mention} a donné **{montant} pièces** à {cible.mention} !\n\n"
                f"💰 Solde de {ctx.author.display_name} : **{db[uid_auteur]['coins']} pièces**\n"
                f"💰 Solde de {cible.display_name} : **{db[uid_cible]['coins']} pièces**"
            ),
            color=0x2ECC71
        ))

    @commands.command(name="tradecancel")
    async def tradecancel(self, ctx, member: discord.Member):
        if not await self.verif_salon(ctx.channel, SALON_MODERATION):
            return
        if not any(r.name in ["Modérateur", "Fondateur"] for r in ctx.author.roles):
            return await ctx.send("❌ Commande réservée aux modérateurs.")
        if member.id in self.trades_actifs:
            self.trades_actifs.discard(member.id)
            await ctx.send(f"✅ Trade de **{member.display_name}** débloqué.")
        else:
            await ctx.send(f"ℹ️ **{member.display_name}** n'est pas en cours de trade.")


async def setup(bot):
    await bot.add_cog(Trades(bot))
