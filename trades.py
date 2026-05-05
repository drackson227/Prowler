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

# Verrou anti-respin global (partagé avec economy.py via import)
spinning_lock = {}

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

async def interface_selection_cartes(bot, ctx_or_channel, author, membre_db: dict, titre: str, couleur: int = 0x5865F2):
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
        footer = f"Page {page + 1}/{total_pages} • Réagis avec le numéro • ❌ pour annuler"
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
        await channel.send(f"❌ Aucune carte ressemblant à **{nom}** trouvée.")
        return None
    if resultats[0][2] >= 0.95:
        return resultats[0][1]

    lignes = []
    for num, (idx, carte, score) in enumerate(resultats):
        emoji_r = RARETES_EMOJI.get(carte["rarete"], "❓")
        lignes.append(f"{NUMEROS[num]} {emoji_r} **{carte['nom']}** — *{carte['rarete']}*")

    embed = discord.Embed(
        title="🔍 Carte introuvable — voulais-tu dire ?",
        description=f"Je n'ai pas trouvé **\"{nom}\"** exactement.\n\n" + "\n".join(lignes),
        color=0xF1C40F
    )
    embed.set_footer(text="Réagis avec le numéro • ❌ pour annuler")
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

async def construire_offre(bot, channel, author, membre_db: dict, nom_membre: str):
    cartes_choisies = []
    coins_choisis = 0

    while True:
        lignes_offre = [f"🃏 {c['nom']} *({c['rarete']})*" for c in cartes_choisies]
        if coins_choisis:
            lignes_offre.append(f"🪙 {coins_choisis} pièces")

        embed_menu = discord.Embed(
            title=f"🔄 Construction de l'offre — {nom_membre}",
            description=(
                "**Offre actuelle :**\n"
                + ("\n".join(lignes_offre) if lignes_offre else "*Rien pour l'instant*")
