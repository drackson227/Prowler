import discord
from discord.ext import commands
from discord import app_commands
import random
import asyncio
from datetime import datetime, timezone, timedelta
from db import load_db, save_db, get_member_data

# ============================================================
# CONSTANTES
# ============================================================

SALON_CASINO = "casino"

RARETE_POINTS = {
    "shlag": 1, "commun": 2, "rare": 3,
    "epique": 10, "hallal": 15, "legendaire": 25,
    "mythique": 50, "secret": 100
}

SUITS = ["♠", "♥", "♦", "♣"]
RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "V", "D", "R", "A"]

# ============================================================
# HELPERS
# ============================================================

def check_casino_channel(channel):
    name = channel.name.lower().replace("・", "").replace(" ", "")
    return "casino" in name

def nouvelle_carte():
    return f"{random.choice(RANKS)}{random.choice(SUITS)}"

def valeur_carte(c):
    r = c[:-1]
    if r in ["V", "D", "R"]: return 10
    if r == "A": return 11
    return int(r)

def valeur_main(main):
    total = 0
    aces = 0
    for c in main:
        v = valeur_carte(c)
        if c[:-1] == "A": aces += 1
        total += v
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total

def afficher_main(main, cacher_deuxieme=False):
    if cacher_deuxieme and len(main) >= 2:
        return f"{main[0]}  🂠"
    return "  ".join(main)

def carte_emoji(carte):
    suit = carte[-1]
    emojis = {"♠": "♠️", "♥": "♥️", "♦": "♦️", "♣": "♣️"}
    return f"`{carte[:-1]}{emojis.get(suit, suit)}`"

def main_emoji(main, cacher=False):
    if cacher and len(main) >= 2:
        return f"{carte_emoji(main[0])}  🂠"
    return "  ".join(carte_emoji(c) for c in main)

def get_role_name(winrate, parties, gains):
    if winrate >= 60 and parties >= 10:
        return "🃏 Card Shark"
    if parties >= 100:
        return "🎰 Pro Gambler"
    if gains >= 5000:
        return "💎 High Roller"
    return None

# ============================================================
# VUE BLACKJACK (boutons Hit/Stand/Double)
# ============================================================

class BlackjackView(discord.ui.View):
    def __init__(self, joueur_id, main_joueur, main_croupier, mise, db, data, table_nom):
        super().__init__(timeout=60)
        self.joueur_id = joueur_id
        self.main_joueur = main_joueur
        self.main_croupier = main_croupier
        self.mise = mise
        self.db = db
        self.data = data
        self.table_nom = table_nom
        self.termine = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.joueur_id:
            await interaction.response.send_message("❌ Ce n'est pas ta partie !", ephemeral=True)
            return False
        return True

    def build_embed(self, titre, couleur, extra_fields=None):
        score_j = valeur_main(self.main_joueur)
        score_c_cache = valeur_carte(self.main_croupier[0])
        embed = discord.Embed(title=f"🃏 Blackjack [{self.table_nom}] — {titre}", color=couleur)
        embed.add_field(
            name=f"🎴 Ta main ({score_j})",
            value=main_emoji(self.main_joueur),
            inline=False
        )
        embed.add_field(
            name=f"🤖 Croupier ({score_c_cache}+?)",
            value=main_emoji(self.main_croupier, cacher=True),
            inline=False
        )
        embed.add_field(name="💰 Mise", value=f"{self.mise} 🪙", inline=True)
        embed.add_field(name="🪙 Solde", value=f"{self.data['coins']} 🪙", inline=True)
        if extra_fields:
            for name, value in extra_fields:
                embed.add_field(name=name, value=value, inline=False)
        return embed

    def build_final_embed(self, titre, couleur, gain_txt):
        score_j = valeur_main(self.main_joueur)
        score_c = valeur_main(self.main_croupier)
        embed = discord.Embed(title=f"🃏 Blackjack [{self.table_nom}] — {titre}", color=couleur)
        embed.add_field(name=f"🎴 Ta main ({score_j})", value=main_emoji(self.main_joueur), inline=False)
        embed.add_field(name=f"🤖 Croupier ({score_c})", value=main_emoji(self.main_croupier), inline=False)
        embed.add_field(name="💰 Résultat", value=gain_txt, inline=True)
        embed.add_field(name="🪙 Solde", value=f"{self.data['coins']} 🪙", inline=True)
        return embed

    async def terminer(self, interaction):
        self.termine = True
        for child in self.children:
            child.disabled = True

        while valeur_main(self.main_croupier) < 17:
            self.main_croupier.append(nouvelle_carte())

        score_j = valeur_main(self.main_joueur)
        score_c = valeur_main(self.main_croupier)

        stats = self.data.setdefault("blackjack_stats", {})
        stats["games"] = stats.get("games", 0) + 1

        if score_j > 21:
            self.data["coins"] -= self.mise
            stats["losses"] = stats.get("losses", 0) + 1
            embed = self.build_final_embed("💥 Bust !", 0xe74c3c, f"-{self.mise} 🪙")
        elif score_c > 21 or score_j > score_c:
            self.data["coins"] += self.mise
            stats["wins"] = stats.get("wins", 0) + 1
            embed = self.build_final_embed("🏆 Gagné !", 0x2ecc71, f"+{self.mise} 🪙")
        elif score_j == score_c:
            embed = self.build_final_embed("🤝 Égalité !", 0x95a5a6, "Mise remboursée")
        else:
            self.data["coins"] -= self.mise
            stats["losses"] = stats.get("losses", 0) + 1
            embed = self.build_final_embed("❌ Perdu !", 0xe74c3c, f"-{self.mise} 🪙")

        save_db(self.db)
        await self._check_roles(interaction)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _check_roles(self, interaction):
        stats = self.data.get("blackjack_stats", {})
        games = stats.get("games", 0)
        wins = stats.get("wins", 0)
        winrate = round((wins / games) * 100, 1) if games > 0 else 0
        coins = self.data.get("coins", 0)
        role_name = get_role_name(winrate, games, coins)
        if role_name:
            guild = interaction.guild
            role = discord.utils.get(guild.roles, name=role_name)
            if not role:
                try:
                    role = await guild.create_role(
                        name=role_name,
                        color=discord.Color.gold(),
                        reason="Rôle casino créé automatiquement"
                    )
                except Exception:
                    return
            member = guild.get_member(self.joueur_id)
            if member and role not in member.roles:
                try:
                    await member.add_roles(role)
                    await interaction.channel.send(f"🎖️ {member.mention} a débloqué le rôle **{role_name}** !")
                except Exception:
                    pass

    @discord.ui.button(label="✅ Hit", style=discord.ButtonStyle.green)
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.termine:
            return
        self.main_joueur.append(nouvelle_carte())
        score = valeur_main(self.main_joueur)
        if score >= 21:
            await self.terminer(interaction)
        else:
            embed = self.build_embed("En jeu", 0x3498db)
            await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="❌ Stand", style=discord.ButtonStyle.red)
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.termine:
            return
        await self.terminer(interaction)

    @discord.ui.button(label="⚡ Double", style=discord.ButtonStyle.blurple)
    async def double(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.termine:
            return
        if self.data["coins"] < self.mise * 2:
            await interaction.response.send_message("❌ Pas assez de pièces pour doubler !", ephemeral=True)
            return
        self.mise *= 2
        self.main_joueur.append(nouvelle_carte())
        await self.terminer(interaction)

    async def on_timeout(self):
        self.termine = True
        for child in self.children:
            child.disabled = True

# ============================================================
# VUE GACHA DUEL (accepter/refuser)
# ============================================================

class DuelView(discord.ui.View):
    def __init__(self, challenger, adversaire, mise):
        super().__init__(timeout=30)
        self.challenger = challenger
        self.adversaire = adversaire
        self.mise = mise
        self.accepte = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.adversaire.id:
            await interaction.response.send_message("❌ Ce duel ne te concerne pas !", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✅ Accepter", style=discord.ButtonStyle.green)
    async def accepter(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.accepte = True
        self.stop()
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="❌ Refuser", style=discord.ButtonStyle.red)
    async def refuser(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.accepte = False
        self.stop()
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

# ============================================================
# COG CASINO
# ============================================================

class Casino(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ────────────────────────────────────────────────────────
    # /dice
    # ────────────────────────────────────────────────────────
    @app_commands.command(name="dice", description="🎲 Double ou rien ! (mise : 10–1000 🪙)")
    @app_commands.describe(mise="Montant à miser (10 à 1000 🪙)")
    async def dice(self, interaction: discord.Interaction, mise: int):
        await interaction.response.defer()

        if not check_casino_channel(interaction.channel):
            await interaction.followup.send("❌ Cette commande est réservée au salon 🎰・casino !")
            return

        if mise < 10 or mise > 1000:
            await interaction.followup.send("❌ La mise doit être entre **10** et **1000** 🪙 !")
            return

        db = load_db()
        data = get_member_data(db, interaction.user.id)

        if data["coins"] < mise:
            await interaction.followup.send(f"❌ Tu n'as que **{data['coins']} 🪙** !")
            return

        msg = await interaction.followup.send("🎲 Lancement des dés...")
        await asyncio.sleep(0.5)
        await msg.edit(content="🎲 Les dés roulent... ⏳")
        await asyncio.sleep(0.8)

        de1 = random.randint(1, 6)
        de2 = random.randint(1, 6)
        gagne = de1 > de2
        if de1 == de2:
            gagne = random.random() > 0.5

        des_faces = ["⚀", "⚁", "⚂", "⚃", "⚄", "⚅"]

        if gagne:
            data["coins"] += mise
            save_db(db)
            embed = discord.Embed(title="🎲 Dice — Gagné !", color=0x2ecc71)
            embed.add_field(name="🎯 Résultat", value=f"{des_faces[de1-1]} **{de1}**  vs  {des_faces[de2-1]} **{de2}** → ✅ Tu gagnes !", inline=False)
            embed.add_field(name="💰 Gain", value=f"+{mise} 🪙", inline=True)
        else:
            data["coins"] -= mise
            save_db(db)
            embed = discord.Embed(title="🎲 Dice — Perdu !", color=0xe74c3c)
            embed.add_field(name="🎯 Résultat", value=f"{des_faces[de1-1]} **{de1}**  vs  {des_faces[de2-1]} **{de2}** → ❌ Tu perds !", inline=False)
            embed.add_field(name="💸 Perte", value=f"-{mise} 🪙", inline=True)

        embed.add_field(name="🪙 Solde", value=f"{data['coins']} 🪙", inline=True)
        embed.set_footer(text=f"Joueur : {interaction.user.display_name}")
        await msg.edit(content=None, embed=embed)

    # ────────────────────────────────────────────────────────
    # BLACKJACK — fonction commune
    # ────────────────────────────────────────────────────────
    async def jouer_blackjack(self, interaction, mise, min_mise, max_mise, table_nom):
        if not check_casino_channel(interaction.channel):
            await interaction.followup.send("❌ Cette commande est réservée au salon 🎰・casino !")
            return

        if mise < min_mise or mise > max_mise:
            await interaction.followup.send(f"❌ Mise entre **{min_mise}** et **{max_mise}** 🪙 pour {table_nom} !")
            return

        db = load_db()
        data = get_member_data(db, interaction.user.id)

        if data["coins"] < mise:
            await interaction.followup.send(f"❌ Tu n'as que **{data['coins']} 🪙** !")
            return

        msg = await interaction.followup.send("🃏 Distribution des cartes...")
        await asyncio.sleep(0.6)
        await msg.edit(content="🃏 Mélange du sabot... 🔀")
        await asyncio.sleep(0.6)

        main_joueur = [nouvelle_carte(), nouvelle_carte()]
        main_croupier = [nouvelle_carte(), nouvelle_carte()]

        if valeur_main(main_joueur) == 21:
            gain = int(mise * 1.5)
            data["coins"] += gain
            stats = data.setdefault("blackjack_stats", {})
            stats["games"] = stats.get("games", 0) + 1
            stats["wins"] = stats.get("wins", 0) + 1
            save_db(db)
            embed = discord.Embed(title=f"🃏 Blackjack [{table_nom}] — 🌟 BLACKJACK NATUREL !", color=0xf1c40f)
            embed.add_field(name="🎴 Ta main", value=main_emoji(main_joueur), inline=False)
            embed.add_field(name="💰 Gain (x1.5)", value=f"+{gain} 🪙", inline=True)
            embed.add_field(name="🪙 Solde", value=f"{data['coins']} 🪙", inline=True)
            await msg.edit(content=None, embed=embed)
            return

        view = BlackjackView(interaction.user.id, main_joueur, main_croupier, mise, db, data, table_nom)
        embed = view.build_embed("En jeu — Ton tour !", 0x3498db)
        await msg.edit(content=None, embed=embed, view=view)

    # ────────────────────────────────────────────────────────
    # /blackjack-low
    # ────────────────────────────────────────────────────────
    @app_commands.command(name="blackjack-low", description="🃏 Table Low — Blackjack (10–100 🪙)")
    @app_commands.describe(mise="Montant à miser (10 à 100 🪙)")
    async def blackjack_low(self, interaction: discord.Interaction, mise: int):
        await interaction.response.defer()
        await self.jouer_blackjack(interaction, mise, 10, 100, "Table Low 🟢")

    # ────────────────────────────────────────────────────────
    # /blackjack-high
    # ────────────────────────────────────────────────────────
    @app_commands.command(name="blackjack-high", description="🃏 Table High — Blackjack (500–5000 🪙)")
    @app_commands.describe(mise="Montant à miser (500 à 5000 🪙)")
    async def blackjack_high(self, interaction: discord.Interaction, mise: int):
        await interaction.response.defer()
        await self.jouer_blackjack(interaction, mise, 500, 5000, "Table High 🟡")

    # ────────────────────────────────────────────────────────
    # /blackjack-vip
    # ────────────────────────────────────────────────────────
    @app_commands.command(name="blackjack-vip", description="🃏 Table VIP — Blackjack (10 000 🪙 minimum)")
    @app_commands.describe(mise="Montant à miser (10 000 🪙 minimum)")
    async def blackjack_vip(self, interaction: discord.Interaction, mise: int):
        await interaction.response.defer()
        await self.jouer_blackjack(interaction, mise, 10000, 9999999, "Table VIP 🔴")

    # ────────────────────────────────────────────────────────
    # /blackjack-stats
    # ────────────────────────────────────────────────────────
    @app_commands.command(name="blackjack-stats", description="📊 Tes statistiques de blackjack")
    async def blackjack_stats(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        db = load_db()
        data = get_member_data(db, interaction.user.id)
        stats = data.get("blackjack_stats", {})
        games = stats.get("games", 0)
        wins = stats.get("wins", 0)
        losses = stats.get("losses", 0)
        ratio = round((wins / games) * 100, 1) if games > 0 else 0

        saison = data.get("saison_blackjack", {})
        s_games = saison.get("games", 0)
        s_wins = saison.get("wins", 0)
        s_ratio = round((s_wins / s_games) * 100, 1) if s_games > 0 else 0

        embed = discord.Embed(
            title=f"📊 Stats Blackjack — {interaction.user.display_name}",
            color=0x3498db
        )
        embed.add_field(name="🎮 Total parties", value=str(games), inline=True)
        embed.add_field(name="✅ Victoires", value=str(wins), inline=True)
        embed.add_field(name="❌ Défaites", value=str(losses), inline=True)
        embed.add_field(name="📈 Winrate global", value=f"{ratio}%", inline=True)
        embed.add_field(name="🏆 Saison actuelle", value=f"{s_wins}V / {s_games} parties ({s_ratio}%)", inline=False)

        role_name = get_role_name(ratio, games, data.get("coins", 0))
        if role_name:
            embed.add_field(name="🎖️ Rôle débloqué", value=role_name, inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ────────────────────────────────────────────────────────
    # /gacha-duel
    # ────────────────────────────────────────────────────────
    @app_commands.command(name="gacha-duel", description="⚔️ Défie un membre en duel de cartes (25–500 🪙)")
    @app_commands.describe(
        adversaire="Le membre que tu veux défier",
        mise="Montant à miser (25 à 500 🪙)"
    )
    async def gacha_duel(self, interaction: discord.Interaction, adversaire: discord.Member, mise: int):
        await interaction.response.defer()

        if not check_casino_channel(interaction.channel):
            await interaction.followup.send("❌ Cette commande est réservée au salon 🎰・casino !")
            return

        if adversaire.bot:
            await interaction.followup.send("❌ Tu ne peux pas défier un bot !")
            return
        if adversaire.id == interaction.user.id:
            await interaction.followup.send("❌ Tu ne peux pas te défier toi-même !")
            return
        if mise < 25 or mise > 500:
            await interaction.followup.send("❌ La mise doit être entre **25** et **500** 🪙 !")
            return

        db = load_db()
        data_c = get_member_data(db, interaction.user.id)
        data_a = get_member_data(db, adversaire.id)

        if data_c["coins"] < mise:
            await interaction.followup.send(f"❌ Tu n'as que **{data_c['coins']} 🪙** !")
            return
        if data_a["coins"] < mise:
            await interaction.followup.send(f"❌ {adversaire.display_name} n'a que **{data_a['coins']} 🪙** !")
            return

        cartes_c = data_c.get("cartes", [])
        cartes_a = data_a.get("cartes", [])
        if not cartes_c:
            await interaction.followup.send("❌ Tu n'as aucune carte ! Fais `/cardspin` d'abord.")
            return
        if not cartes_a:
            await interaction.followup.send(f"❌ {adversaire.display_name} n'a aucune carte !")
            return

        embed_challenge = discord.Embed(
            title="⚔️ DUEL DE CARTES",
            description=f"{interaction.user.mention} défie {adversaire.mention} !\n💰 **Pot : {mise * 2} 🪙**",
            color=0xf1c40f
        )
        embed_challenge.add_field(name="Mise chacun", value=f"{mise} 🪙", inline=True)
        embed_challenge.set_footer(text="30 secondes pour accepter ou refuser !")

        view = DuelView(interaction.user, adversaire, mise)
        msg = await interaction.followup.send(embed=embed_challenge, view=view)

        await view.wait()

        if view.accepte is None or not view.accepte:
            embed_refuse = discord.Embed(
                title="⚔️ Duel refusé",
                description=f"{adversaire.display_name} a refusé le duel.",
                color=0xe74c3c
            )
            await msg.edit(embed=embed_refuse, view=view)
            return

        ORDRE_RARETE = ["shlag", "commun", "rare", "epique", "hallal", "legendaire", "mythique", "secret"]
        RARETE_INFO = {
            "shlag":      {"emoji": "⚫", "label": "Shlag",      "couleur": 0x2C2C2C},
            "commun":     {"emoji": "⚪", "label": "Commun",     "couleur": 0xAAAAAA},
            "rare":       {"emoji": "🔵", "label": "Rare",       "couleur": 0x3498DB},
            "epique":     {"emoji": "🟣", "label": "Épique",     "couleur": 0x9B59B6},
            "hallal":     {"emoji": "🟢", "label": "Hallal",     "couleur": 0x2ECC71},
            "legendaire": {"emoji": "🟡", "label": "Légendaire", "couleur": 0xF1C40F},
            "mythique":   {"emoji": "🔴", "label": "Mythique",   "couleur": 0xFF4500},
            "secret":     {"emoji": "🌈", "label": "✨ Secret",  "couleur": 0xFF1493},
        }

        await msg.edit(content="🎴 Tirage des cartes en cours...", embed=None, view=None)
        await asyncio.sleep(0.8)
        await msg.edit(content="🎴 Les cartes sont révélées...")
        await asyncio.sleep(0.8)

        carte_c = random.choice(cartes_c)
        carte_a = random.choice(cartes_a)

        puissance_c = ORDRE_RARETE.index(carte_c["rarete"]) if carte_c["rarete"] in ORDRE_RARETE else 0
        puissance_a = ORDRE_RARETE.index(carte_a["rarete"]) if carte_a["rarete"] in ORDRE_RARETE else 0

        if puissance_c == puissance_a:
            gagnant_id = random.choice([interaction.user.id, adversaire.id])
        else:
            gagnant_id = interaction.user.id if puissance_c > puissance_a else adversaire.id

        gagnant = interaction.user if gagnant_id == interaction.user.id else adversaire

        if gagnant_id == interaction.user.id:
            data_c["coins"] += mise
            data_a["coins"] -= mise
        else:
            data_a["coins"] += mise
            data_c["coins"] -= mise

        for d, result in [(data_c, gagnant_id == interaction.user.id), (data_a, gagnant_id == adversaire.id)]:
            ds = d.setdefault("duel_stats", {})
            ds["games"] = ds.get("games", 0) + 1
            if result:
                ds["wins"] = ds.get("wins", 0) + 1
                ds["pot_won"] = ds.get("pot_won", 0) + mise
            else:
                ds["losses"] = ds.get("losses", 0) + 1

        save_db(db)

        info_c = RARETE_INFO.get(carte_c["rarete"], {"emoji": "❓", "label": carte_c["rarete"], "couleur": 0x95a5a6})
        info_a = RARETE_INFO.get(carte_a["rarete"], {"emoji": "❓", "label": carte_a["rarete"], "couleur": 0x95a5a6})
        couleur_embed = RARETE_INFO.get(
            carte_c["rarete"] if gagnant_id == interaction.user.id else carte_a["rarete"],
            {}
        ).get("couleur", 0xf1c40f)

        egalite_txt = "\n*Égalité de rareté — victoire aléatoire !*" if puissance_c == puissance_a else ""

        embed_result = discord.Embed(title="⚔️ Résultat du Duel de Cartes !", color=couleur_embed)
        embed_result.add_field(
            name=f"🗡️ {interaction.user.display_name}",
            value=f"{info_c['emoji']} **{carte_c['nom']}**\n*{info_c['label']}*",
            inline=True
        )
        embed_result.add_field(name="⚔️", value="VS", inline=True)
        embed_result.add_field(
            name=f"🛡️ {adversaire.display_name}",
            value=f"{info_a['emoji']} **{carte_a['nom']}**\n*{info_a['label']}*",
            inline=True
        )
        embed_result.add_field(
            name="🏆 Gagnant",
            value=f"{gagnant.mention} remporte **{mise * 2} 🪙** !{egalite_txt}",
            inline=False
        )
        embed_result.set_footer(text=f"Mise : {mise} 🪙 chacun")
        await msg.edit(content=None, embed=embed_result)

    # ────────────────────────────────────────────────────────
    # /top-duel
    # ────────────────────────────────────────────────────────
    @app_commands.command(name="top-duel", description="🏆 Leaderboard des duels")
    async def top_duel(self, interaction: discord.Interaction):
        await interaction.response.defer()
        db = load_db()

        classement = []
        for mid, data in db.items():
            member = interaction.guild.get_member(int(mid))
            if not member:
                continue
            stats = data.get("duel_stats", {})
            wins = stats.get("wins", 0)
            games = stats.get("games", 0)
            if games > 0:
                winrate = round((wins / games) * 100, 1)
                classement.append((member.display_name, wins, stats.get("losses", 0), games, winrate))

        classement.sort(key=lambda x: (x[1], x[4]), reverse=True)
        top = classement[:10]

        if not top:
            await interaction.followup.send("❌ Aucun duel joué pour l'instant !")
            return

        medals = ["🥇", "🥈", "🥉"] + ["4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        lines = [
            f"{medals[i]} **{name}** — {wins}V / {losses}D ({games} parties) • {wr}%"
            for i, (name, wins, losses, games, wr) in enumerate(top)
        ]

        embed = discord.Embed(
            title="🏆 Top Duels — Saison Actuelle",
            description="\n".join(lines),
            color=0xf1c40f
        )
        embed.set_footer(text="Classé par victoires puis winrate")
        await interaction.followup.send(embed=embed)

    # ────────────────────────────────────────────────────────
    # /reset-saison (admin)
    # ────────────────────────────────────────────────────────
    @app_commands.command(name="reset-saison", description="🔄 Reset les stats de saison (admin)")
    @app_commands.default_permissions(administrator=True)
    async def reset_saison(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        db = load_db()
        count = 0
        for mid in db:
            if "saison_blackjack" in db[mid]:
                db[mid]["saison_blackjack"] = {}
                count += 1
            if "duel_stats" in db[mid]:
                db[mid]["duel_stats"] = {}
                count += 1
        save_db(db)

        embed = discord.Embed(
            title="🔄 Nouvelle Saison !",
            description=f"Stats remises à zéro pour {count} entrées.\nBonne chance à tous ! 🎰",
            color=0x2ecc71
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

        for channel in interaction.guild.text_channels:
            if "casino" in channel.name.lower():
                await channel.send(embed=discord.Embed(
                    title="🎰 NOUVELLE SAISON CASINO !",
                    description="Les stats de duel et de blackjack ont été remises à zéro.\nQue le meilleur gagne ! ⚔️🃏",
                    color=0xf1c40f
                ))
                break

    # ────────────────────────────────────────────────────────
    # /casino-help
    # ────────────────────────────────────────────────────────
    @app_commands.command(name="casino-help", description="📖 Guide complet des jeux du casino")
    async def casino_help(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎰 Guide du Casino — Gare du Nord",
            description="Tous les jeux disponibles dans le salon 🎰・casino",
            color=0xFFD700
        )
        embed.add_field(
            name="🃏 Blackjack — 3 Tables",
            value=(
                "`/blackjack-low [mise]` — Table Low (**10–100** 🪙)\n"
                "`/blackjack-high [mise]` — Table High (**500–5 000** 🪙)\n"
                "`/blackjack-vip [mise]` — Table VIP (**10 000+** 🪙)\n\n"
                "**But :** Atteindre 21 sans dépasser, battre le croupier.\n"
                "✅ **Hit** → Tirer une carte\n"
                "❌ **Stand** → Rester sur sa main\n"
                "⚡ **Double** → Doubler la mise + 1 carte\n"
                "🌟 **Blackjack naturel** (As + figure) = gain ×1.5\n"
                "🤖 Le croupier tire jusqu'à 17 minimum"
            ),
            inline=False
        )
        embed.add_field(
            name="🎲 Dice — Double ou Rien",
            value=(
                "`/dice [mise]` — Mise : **10–1 000** 🪙\n\n"
                "**But :** Obtenir un dé plus élevé que le croupier.\n"
                "🏆 Ton dé > croupier → **+mise** 🪙\n"
                "💀 Ton dé < croupier → **-mise** 🪙"
            ),
            inline=False
        )
        embed.add_field(
            name="⚔️ Duel de Cartes — PVP",
            value=(
                "`/gacha-duel [@membre] [mise]` — Mise : **25–500** 🪙\n\n"
                "**But :** Défier un autre membre. Chacun tire une carte aléatoire de sa collection.\n"
                "La carte de **rareté la plus haute** remporte le pot (**mise ×2**).\n"
                "Ordre : ⚫Shlag < ⚪Commun < 🔵Rare < 🟣Épique < 🟢Hallal < 🟡Légendaire < 🔴Mythique < 🌈Secret\n"
                "⚠️ En cas d'égalité de rareté → victoire aléatoire !\n"
                "Le défié a **30 secondes** pour accepter ou refuser."
            ),
            inline=False
        )
        embed.add_field(
            name="📊 Stats & Classements",
            value=(
                "`/blackjack-stats` — Tes stats de blackjack (privé)\n"
                "`/top-duel` — Classement des duels du serveur"
            ),
            inline=False
        )
        embed.add_field(
            name="🎖️ Rôles Débloquables (automatiques)",
            value=(
                "**🃏 Card Shark** — 60%+ winrate blackjack (10 parties min)\n"
                "**🎰 Pro Gambler** — 100 parties de blackjack\n"
                "**💎 High Roller** — 5 000+ 🪙 en solde\n"
                "*Les rôles sont créés et attribués automatiquement !*"
            ),
            inline=False
        )
        embed.set_footer(text="🍀 Bonne chance ! Joue dans #・casino uniquement.")
        await interaction.response.send_message(embed=embed)


# ============================================================
# SETUP
# ============================================================

async def setup(bot):
    await bot.add_cog(Casino(bot))
