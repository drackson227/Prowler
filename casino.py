import discord
from discord.ext import commands
from discord import app_commands
import random
from datetime import datetime, timezone
from db import load_db, save_db, get_member_data

# ============================================================
# BLACKJACK HELPER
# ============================================================

def nouvelle_carte():
    valeurs = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "V", "D", "R", "A"]
    return random.choice(valeurs)

def valeur_main(main):
    total = 0
    as_count = 0
    for carte in main:
        if carte in ["V", "D", "R"]:
            total += 10
        elif carte == "A":
            total += 11
            as_count += 1
        else:
            total += int(carte)
    while total > 21 and as_count:
        total -= 10
        as_count -= 1
    return total

def afficher_main(main):
    return " | ".join(main)


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

        if mise < 10 or mise > 1000:
            await interaction.followup.send("❌ La mise doit être entre **10** et **1000** 🪙 !")
            return

        db = load_db()
        data = get_member_data(db, interaction.user.id)

        if data["coins"] < mise:
            await interaction.followup.send(f"❌ Tu n'as que **{data['coins']} 🪙**, pas assez pour miser {mise} 🪙 !")
            return

        gagne = random.random() > 0.5
        de1 = random.randint(1, 6)
        de2 = random.randint(1, 6)

        if gagne:
            data["coins"] += mise
            save_db(db)
            embed = discord.Embed(title="🎲 Dice — Gagné !", color=0x2ecc71)
            embed.add_field(name="🎯 Résultat", value=f"🎲 {de1}  vs  🎲 {de2} → **Tu gagnes !**", inline=False)
            embed.add_field(name="💰 Gain", value=f"+{mise} 🪙", inline=True)
            embed.add_field(name="🪙 Solde", value=f"{data['coins']} 🪙", inline=True)
        else:
            data["coins"] -= mise
            save_db(db)
            embed = discord.Embed(title="🎲 Dice — Perdu !", color=0xe74c3c)
            embed.add_field(name="🎯 Résultat", value=f"🎲 {de1}  vs  🎲 {de2} → **Tu perds !**", inline=False)
            embed.add_field(name="💸 Perte", value=f"-{mise} 🪙", inline=True)
            embed.add_field(name="🪙 Solde", value=f"{data['coins']} 🪙", inline=True)

        embed.set_footer(text=f"Joueur : {interaction.user.display_name}")
        await interaction.followup.send(embed=embed)

    # ────────────────────────────────────────────────────────
    # BLACKJACK (fonction commune)
    # ────────────────────────────────────────────────────────
    async def jouer_blackjack(self, interaction: discord.Interaction, mise: int, min_mise: int, max_mise: int, table_nom: str):
        if mise < min_mise or mise > max_mise:
            await interaction.followup.send(f"❌ Mise entre **{min_mise}** et **{max_mise}** 🪙 pour la {table_nom} !")
            return

        db = load_db()
        data = get_member_data(db, interaction.user.id)

        if data["coins"] < mise:
            await interaction.followup.send(f"❌ Tu n'as que **{data['coins']} 🪙**, pas assez !")
            return

        # Distribution des cartes
        main_joueur = [nouvelle_carte(), nouvelle_carte()]
        main_croupier = [nouvelle_carte(), nouvelle_carte()]

        score_joueur = valeur_main(main_joueur)
        score_croupier = valeur_main(main_croupier)

        # Blackjack naturel ?
        if score_joueur == 21:
            gain = int(mise * 1.5)
            data["coins"] += gain
            data.setdefault("blackjack_stats", {})
            data["blackjack_stats"]["wins"] = data["blackjack_stats"].get("wins", 0) + 1
            data["blackjack_stats"]["games"] = data["blackjack_stats"].get("games", 0) + 1
            save_db(db)
            embed = discord.Embed(title=f"🃏 Blackjack [{table_nom}] — BLACKJACK NATUREL !", color=0xf1c40f)
            embed.add_field(name="🃏 Ta main", value=f"{afficher_main(main_joueur)} = **{score_joueur}**", inline=False)
            embed.add_field(name="💰 Gain", value=f"+{gain} 🪙 (x1.5)", inline=True)
            embed.add_field(name="🪙 Solde", value=f"{data['coins']} 🪙", inline=True)
            await interaction.followup.send(embed=embed)
            return

        # Le croupier tire jusqu'à 17
        while valeur_main(main_croupier) < 17:
            main_croupier.append(nouvelle_carte())

        score_croupier = valeur_main(main_croupier)

        # Tirage du joueur (simple : on tire une carte supplémentaire si < 17)
        if score_joueur < 17:
            main_joueur.append(nouvelle_carte())
            score_joueur = valeur_main(main_joueur)

        data.setdefault("blackjack_stats", {})
        data["blackjack_stats"]["games"] = data["blackjack_stats"].get("games", 0) + 1

        # Déterminer le résultat
        if score_joueur > 21:
            # Joueur bust
            data["coins"] -= mise
            data["blackjack_stats"]["losses"] = data["blackjack_stats"].get("losses", 0) + 1
            save_db(db)
            embed = discord.Embed(title=f"🃏 Blackjack [{table_nom}] — Bust !", color=0xe74c3c)
            embed.add_field(name="🃏 Ta main", value=f"{afficher_main(main_joueur)} = **{score_joueur}** (Bust !)", inline=False)
            embed.add_field(name="🎰 Croupier", value=f"{afficher_main(main_croupier)} = **{score_croupier}**", inline=False)
            embed.add_field(name="💸 Perte", value=f"-{mise} 🪙", inline=True)
            embed.add_field(name="🪙 Solde", value=f"{data['coins']} 🪙", inline=True)
        elif score_croupier > 21 or score_joueur > score_croupier:
            # Joueur gagne
            data["coins"] += mise
            data["blackjack_stats"]["wins"] = data["blackjack_stats"].get("wins", 0) + 1
            save_db(db)
            embed = discord.Embed(title=f"🃏 Blackjack [{table_nom}] — Gagné !", color=0x2ecc71)
            embed.add_field(name="🃏 Ta main", value=f"{afficher_main(main_joueur)} = **{score_joueur}**", inline=False)
            embed.add_field(name="🎰 Croupier", value=f"{afficher_main(main_croupier)} = **{score_croupier}**", inline=False)
            embed.add_field(name="💰 Gain", value=f"+{mise} 🪙", inline=True)
            embed.add_field(name="🪙 Solde", value=f"{data['coins']} 🪙", inline=True)
        elif score_joueur == score_croupier:
            # Égalité
            save_db(db)
            embed = discord.Embed(title=f"🃏 Blackjack [{table_nom}] — Égalité !", color=0x95a5a6)
            embed.add_field(name="🃏 Ta main", value=f"{afficher_main(main_joueur)} = **{score_joueur}**", inline=False)
            embed.add_field(name="🎰 Croupier", value=f"{afficher_main(main_croupier)} = **{score_croupier}**", inline=False)
            embed.add_field(name="💰 Mise remboursée", value=f"{mise} 🪙", inline=True)
            embed.add_field(name="🪙 Solde", value=f"{data['coins']} 🪙", inline=True)
        else:
            # Croupier gagne
            data["coins"] -= mise
            data["blackjack_stats"]["losses"] = data["blackjack_stats"].get("losses", 0) + 1
            save_db(db)
            embed = discord.Embed(title=f"🃏 Blackjack [{table_nom}] — Perdu !", color=0xe74c3c)
            embed.add_field(name="🃏 Ta main", value=f"{afficher_main(main_joueur)} = **{score_joueur}**", inline=False)
            embed.add_field(name="🎰 Croupier", value=f"{afficher_main(main_croupier)} = **{score_croupier}**", inline=False)
            embed.add_field(name="💸 Perte", value=f"-{mise} 🪙", inline=True)
            embed.add_field(name="🪙 Solde", value=f"{data['coins']} 🪙", inline=True)

        embed.set_footer(text=f"Joueur : {interaction.user.display_name}")
        await interaction.followup.send(embed=embed)

    # ────────────────────────────────────────────────────────
    # /blackjack-low
    # ────────────────────────────────────────────────────────
    @app_commands.command(name="blackjack-low", description="🃏 Table Low — Blackjack (10–100 🪙)")
    @app_commands.describe(mise="Montant à miser (10 à 100 🪙)")
    async def blackjack_low(self, interaction: discord.Interaction, mise: int):
        await interaction.response.defer()
        await self.jouer_blackjack(interaction, mise, 10, 100, "Table Low")

    # ────────────────────────────────────────────────────────
    # /blackjack-high
    # ────────────────────────────────────────────────────────
    @app_commands.command(name="blackjack-high", description="🃏 Table High — Blackjack (500–5000 🪙)")
    @app_commands.describe(mise="Montant à miser (500 à 5000 🪙)")
    async def blackjack_high(self, interaction: discord.Interaction, mise: int):
        await interaction.response.defer()
        await self.jouer_blackjack(interaction, mise, 500, 5000, "Table High")

    # ────────────────────────────────────────────────────────
    # /blackjack-vip
    # ────────────────────────────────────────────────────────
    @app_commands.command(name="blackjack-vip", description="🃏 Table VIP — Blackjack (10 000 🪙 minimum)")
    @app_commands.describe(mise="Montant à miser (10 000 🪙 minimum)")
    async def blackjack_vip(self, interaction: discord.Interaction, mise: int):
        await interaction.response.defer()
        await self.jouer_blackjack(interaction, mise, 10000, 9999999, "Table VIP")

    # ────────────────────────────────────────────────────────
    # /blackjack-stats
    # ────────────────────────────────────────────────────────
    @app_commands.command(name="blackjack-stats", description="📊 Tes statistiques de blackjack")
    async def blackjack_stats(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        db = load_db()
        data = get_member_data(db, interaction.user.id)
        stats = data.get("blackjack_stats", {})
        games  = stats.get("games", 0)
        wins   = stats.get("wins", 0)
        losses = stats.get("losses", 0)
        ratio  = round((wins / games) * 100, 1) if games > 0 else 0

        embed = discord.Embed(title=f"📊 Stats Blackjack — {interaction.user.display_name}", color=0x3498db)
        embed.add_field(name="🎮 Parties",  value=str(games),  inline=True)
        embed.add_field(name="✅ Victoires", value=str(wins),   inline=True)
        embed.add_field(name="❌ Défaites",  value=str(losses), inline=True)
        embed.add_field(name="📈 Ratio",    value=f"{ratio}%", inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ────────────────────────────────────────────────────────
    # /gacha-duel
    # ────────────────────────────────────────────────────────
    @app_commands.command(name="gacha-duel", description="⚔️ Défie un membre en duel (25–500 🪙)")
    @app_commands.describe(
        adversaire="Le membre que tu veux défier",
        mise="Montant à miser (25 à 500 🪙)"
    )
    async def gacha_duel(self, interaction: discord.Interaction, adversaire: discord.Member, mise: int):
        await interaction.response.defer()

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
        data_challenger = get_member_data(db, interaction.user.id)
        data_adversaire  = get_member_data(db, adversaire.id)

        if data_challenger["coins"] < mise:
            await interaction.followup.send(f"❌ Tu n'as que **{data_challenger['coins']} 🪙**, pas assez !")
            return

        if data_adversaire["coins"] < mise:
            await interaction.followup.send(f"❌ {adversaire.display_name} n'a que **{data_adversaire['coins']} 🪙**, pas assez !")
            return

        # Tirage
        score_challenger = random.randint(1, 100)
        score_adversaire  = random.randint(1, 100)

        # Relancer en cas d'égalité
        while score_challenger == score_adversaire:
            score_challenger = random.randint(1, 100)
            score_adversaire  = random.randint(1, 100)

        if score_challenger > score_adversaire:
            gagnant = interaction.user
            perdant = adversaire
            data_challenger["coins"] += mise
            data_adversaire["coins"]  -= mise
            # Stats duel
            data_challenger.setdefault("duel_stats", {})
            data_challenger["duel_stats"]["wins"]  = data_challenger["duel_stats"].get("wins", 0) + 1
            data_challenger["duel_stats"]["games"] = data_challenger["duel_stats"].get("games", 0) + 1
            data_adversaire.setdefault("duel_stats", {})
            data_adversaire["duel_stats"]["losses"] = data_adversaire["duel_stats"].get("losses", 0) + 1
            data_adversaire["duel_stats"]["games"]  = data_adversaire["duel_stats"].get("games", 0) + 1
        else:
            gagnant = adversaire
            perdant = interaction.user
            data_adversaire["coins"]  += mise
            data_challenger["coins"] -= mise
            data_adversaire.setdefault("duel_stats", {})
            data_adversaire["duel_stats"]["wins"]  = data_adversaire["duel_stats"].get("wins", 0) + 1
            data_adversaire["duel_stats"]["games"] = data_adversaire["duel_stats"].get("games", 0) + 1
            data_challenger.setdefault("duel_stats", {})
            data_challenger["duel_stats"]["losses"] = data_challenger["duel_stats"].get("losses", 0) + 1
            data_challenger["duel_stats"]["games"]  = data_challenger["duel_stats"].get("games", 0) + 1

        save_db(db)

        embed = discord.Embed(title="⚔️ Gacha Duel — Résultat !", color=0xf1c40f)
        embed.add_field(
            name=f"🗡️ {interaction.user.display_name}",
            value=f"Score : **{score_challenger}**",
            inline=True
        )
        embed.add_field(
            name=f"🛡️ {adversaire.display_name}",
            value=f"Score : **{score_adversaire}**",
            inline=True
        )
        embed.add_field(
            name="🏆 Gagnant",
            value=f"{gagnant.mention} remporte **{mise} 🪙** !",
            inline=False
        )
        embed.set_footer(text=f"Mise : {mise} 🪙")
        await interaction.followup.send(embed=embed)

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
            wins  = stats.get("wins", 0)
            if wins > 0:
                classement.append((member.display_name, wins, stats.get("losses", 0), stats.get("games", 0)))

        classement.sort(key=lambda x: x[1], reverse=True)
        top = classement[:10]

        if not top:
            await interaction.followup.send("❌ Aucun duel joué pour l'instant !")
            return

        medals = ["🥇", "🥈", "🥉"] + ["4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        lines = [
            f"{medals[i]} **{name}** — {wins}V / {losses}D ({games} parties)"
            for i, (name, wins, losses, games) in enumerate(top)
        ]

        embed = discord.Embed(
            title="🏆 Top Duels",
            description="\n".join(lines),
            color=0xf1c40f
        )
        await interaction.followup.send(embed=embed)


# ============================================================
# OBLIGATOIRE — sans ça le bot ne charge pas le cog
# ============================================================
async def setup(bot):
    await bot.add_cog(Casino(bot))
