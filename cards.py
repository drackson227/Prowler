import discord
from discord.ext import commands
import random
import asyncio
from db import load_db, save_db, get_member_data
from economy import spinning_actifs

RARETES = {
    "shlag":      {"emoji": "⚫", "couleur": 0x2C2C2C, "prob": 30.0, "label": "Shlag"},
    "commun":     {"emoji": "⚪", "couleur": 0xAAAAAA, "prob": 25.0, "label": "Commun"},
    "rare":       {"emoji": "🔵", "couleur": 0x3498DB, "prob": 18.0, "label": "Rare"},
    "epique":     {"emoji": "🟣", "couleur": 0x9B59B6, "prob": 12.0, "label": "Épique"},
    "hallal":     {"emoji": "🟢", "couleur": 0x2ECC71, "prob": 7.0,  "label": "Hallal"},
    "legendaire": {"emoji": "🟡", "couleur": 0xF1C40F, "prob": 4.5,  "label": "Légendaire"},
    "mythique":   {"emoji": "🔴", "couleur": 0xFF4500, "prob": 2.5,  "label": "Mythique"},
    "secret":     {"emoji": "🌈", "couleur": 0xFF1493, "prob": 1.0,  "label": "✨ Secret"},
}

CARTES = [
    {"id": "shlag_001", "nom": "Kebab Froid",          "rarete": "shlag",      "image_url": "https://i.imgur.com/placeholder1.png",  "description": "Un kebab oublié depuis 3 jours."},
    {"id": "shlag_002", "nom": "Wifi à 1 barre",       "rarete": "shlag",      "image_url": "https://i.imgur.com/placeholder2.png",  "description": "Chargement... chargement..."},
    {"id": "com_001",   "nom": "Pigeon de Paris",      "rarete": "commun",     "image_url": "https://i.imgur.com/placeholder3.png",  "description": "Il te regarde manger."},
    {"id": "com_002",   "nom": "Sac Tesco",            "rarete": "commun",     "image_url": "https://i.imgur.com/placeholder4.png",  "description": "Un classique indémodable."},
    {"id": "rare_001",  "nom": "Snapback Vintage",     "rarete": "rare",       "image_url": "https://i.imgur.com/placeholder5.png",  "description": "Portée une seule fois en soirée."},
    {"id": "rare_002",  "nom": "Boîte de Pandore",     "rarete": "rare",       "image_url": "https://i.imgur.com/placeholder6.png",  "description": "Mieux vaut ne pas l'ouvrir."},
    {"id": "epi_001",   "nom": "Danger de la Société", "rarete": "epique",     "image_url": "https://i.imgur.com/placeholder7.png",  "description": "Incontrôlable. Imprévisible."},
    {"id": "epi_002",   "nom": "eGirl Ascendante",     "rarete": "epique",     "image_url": "https://i.imgur.com/placeholder8.png",  "description": "Aesthetic maximal."},
    {"id": "hal_001",   "nom": "Bénédiction Hallal",   "rarete": "hallal",     "image_url": "https://i.imgur.com/placeholder9.png",  "description": "Certifiée, garantie, validée."},
    {"id": "hal_002",   "nom": "Mouton Sacré",         "rarete": "hallal",     "image_url": "https://i.imgur.com/placeholder10.png", "description": "Une présence apaisante."},
    {"id": "leg_001",   "nom": "Le Fondateur",         "rarete": "legendaire", "image_url": "https://i.imgur.com/placeholder11.png", "description": "Celui qui a tout lancé."},
    {"id": "leg_002",   "nom": "Carte LGBT Dorée",     "rarete": "legendaire", "image_url": "https://i.imgur.com/placeholder12.png", "description": "Rare et fière de l'être."},
    {"id": "myth_001",  "nom": "eBoy Ultime",          "rarete": "mythique",   "image_url": "https://i.imgur.com/placeholder13.png", "description": "Le niveau final de l'eBoy."},
    {"id": "myth_002",  "nom": "Glitch Matrix",        "rarete": "mythique",   "image_url": "https://i.imgur.com/placeholder14.png", "description": "Une erreur dans la simulation."},
    {"id": "sec_001",   "nom": "✨ Carte Inconnue",    "rarete": "secret",     "image_url": "https://i.imgur.com/placeholder15.png", "description": "Personne ne sait d'où elle vient."},
]

PRIX_SPIN = 100
SALON_BOUTIQUE = "boutique"

def tirer_carte():
    population = list(RARETES.keys())
    poids = [RARETES[r]["prob"] for r in population]
    rarete_tiree = random.choices(population, weights=poids, k=1)[0]
    cartes_dispo = [c for c in CARTES if c["rarete"] == rarete_tiree]
    return random.choice(cartes_dispo) if cartes_dispo else random.choice(CARTES)

def build_frame_animation(frame_num: int) -> str:
    rarete_keys = list(RARETES.keys())
    slots = []
    for _ in range(3):
        r = random.choice(rarete_keys)
        info = RARETES[r]
        slots.append(f"{info['emoji']} **{info['label']}**")
    indicateur = ["⬛⬛⬛", "🟥⬛⬛", "🟥🟥⬛", "🟥🟥🟥", "✅✅✅"][min(frame_num, 4)]
    return f"╔══════════════════╗\n║  {slots[0]}\n║  {slots[1]}\n║  {slots[2]}\n╚══════════════════╝\n{indicateur}"


class Cards(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="cardspin")
    async def cardspin(self, ctx):
        channel_name = ctx.channel.name.lower().replace("・", "")
        if SALON_BOUTIQUE not in channel_name:
            boutique = next(
                (ch for ch in ctx.guild.text_channels if SALON_BOUTIQUE in ch.name.lower().replace("・", "")),
                None
            )
            mention = boutique.mention if boutique else "`🛍️・boutique`"
            return await ctx.send(f"❌ La commande `!cardspin` n'est utilisable que dans {mention}.")

        # Anti-respin
        if ctx.author.id in spinning_actifs:
            return await ctx.send("⏳ Ton spin est encore en cours, attends la fin !")
        spinning_actifs.add(ctx.author.id)

        try:
            db = load_db()
            membre = get_member_data(db, ctx.author.id)
            if membre.get("coins", 0) < PRIX_SPIN:
                return await ctx.send(
                    f"❌ Il te faut **{PRIX_SPIN} pièces** pour spinner. "
                    f"Tu en as **{membre.get('coins', 0)}**."
                )

            membre["coins"] -= PRIX_SPIN
            save_db(db)

            embed_anim = discord.Embed(
                title="🎴 Card Spin — En cours...",
                description=build_frame_animation(0),
                color=0x5865F2
            )
            embed_anim.set_footer(text=f"Coût : {PRIX_SPIN} pièces • Solde restant : {membre['coins']} pièces")
            msg = await ctx.send(embed=embed_anim)

            for i in range(1, 5):
                await asyncio.sleep(0.8)
                embed_anim.description = build_frame_animation(i)
                await msg.edit(embed=embed_anim)

            carte = tirer_carte()
            rarete_info = RARETES[carte["rarete"]]

            db = load_db()
            membre = get_member_data(db, ctx.author.id)
            if "cartes" not in membre:
                membre["cartes"] = []

            doublon = any(c["id"] == carte["id"] for c in membre["cartes"])
            membre["cartes"].append({
                "id": carte["id"],
                "nom": carte["nom"],
                "rarete": carte["rarete"]
            })
            save_db(db)

            embed_result = discord.Embed(
                title=f"{rarete_info['emoji']} {carte['nom']}",
                description=carte["description"],
                color=rarete_info["couleur"]
            )
            embed_result.add_field(name="Rareté", value=f"{rarete_info['emoji']} **{rarete_info['label']}**", inline=True)
            embed_result.add_field(name="Probabilité", value=f"`{rarete_info['prob']}%`", inline=True)
            if doublon:
                embed_result.add_field(name="⚠️ Doublon", value="Tu possèdes déjà cette carte !", inline=False)
            embed_result.set_image(url=carte["image_url"])
            embed_result.set_footer(
                text=f"{ctx.author.display_name} • Solde : {membre['coins']} pièces",
                icon_url=ctx.author.display_avatar.url
            )
            await asyncio.sleep(0.5)
            await msg.edit(embed=embed_result)
        finally:
            spinning_actifs.discard(ctx.author.id)

    @commands.command(name="collection")
    async def collection(self, ctx, member: discord.Member = None):
        target = member or ctx.author
        db = load_db()
        uid = str(target.id)
        cartes = db.get(uid, {}).get("cartes", [])
        if not cartes:
            return await ctx.send(f"📭 **{target.display_name}** n'a aucune carte pour l'instant.")
        ordre = list(RARETES.keys())
        cartes_triees = sorted(
            cartes,
            key=lambda c: ordre.index(c["rarete"]) if c["rarete"] in ordre else 99,
            reverse=True
        )
        lignes = []
        rarete_actuelle = None
        for c in cartes_triees:
            r = c["rarete"]
            if r != rarete_actuelle:
                info = RARETES.get(r, {"emoji": "❓", "label": r})
                lignes.append(f"\n{info['emoji']} **{info['label']}**")
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
        await ctx.send(embed=embed)

    @commands.command(name="cartesinfo")
    async def cartesinfo(self, ctx):
        lignes = []
        for key, info in RARETES.items():
            barre = "█" * int(info["prob"] / 2)
            lignes.append(f"{info['emoji']} **{info['label']}** — `{info['prob']}%` {barre}")
        embed = discord.Embed(
            title="🎴 Probabilités des raretés",
            description="\n".join(lignes),
            color=0x5865F2
        )
        embed.set_footer(text=f"Prix d'un spin : {PRIX_SPIN} pièces • !cardspin dans 🛍️・boutique")
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Cards(bot))
