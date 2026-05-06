import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import random
from datetime import datetime, timezone
from db import load_db, save_db, get_member_data
from utils import get_channel_by_name

SALON_CASINO = "casino"

RARETES_ORDRE = ["shlag", "commun", "rare", "epique", "hallal", "legendaire", "mythique", "secret"]
RARETES_POINTS = {"shlag": 1, "commun": 2, "rare": 3, "epique": 10, "hallal": 15,
                  "legendaire": 25, "mythique": 50, "secret": 100}
RARETES_EMOJI = {"secret": "🌈", "mythique": "🔴", "legendaire": "🟡",
                 "hallal": "🟢", "epique": "🟣", "rare": "🔵", "commun": "⚪", "shlag": "⚫"}
RARETES_PROBA = [("shlag", 31.5), ("commun", 26), ("rare", 18.5), ("epique", 13),
                 ("hallal", 7.5), ("legendaire", 2), ("mythique", 1), ("secret", 0.5)]

SUITS = ["♥", "♦", "♠", "♣"]
RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]

def check_casino(channel, guild):
    name = channel.name.lower().replace("・", "").replace("-", "")
    if "casino" not in name:
        casino_ch = get_channel_by_name(guild, "casino")
        mention = casino_ch.mention if casino_ch else "🎰・casino"
        return False, mention
    return True, None

def new_deck():
    deck = [f"{r}{s}" for r in RANKS for s in SUITS]
    random.shuffle(deck)
    return deck

def card_value(card):
    r = card[:-1]
    if r in ["J", "Q", "K"]: return 10
    if r == "A": return 11
    return int(r)

def hand_value(hand):
    total = sum(card_value(c) for c in hand)
    aces = sum(1 for c in hand if c[:-1] == "A")
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total

def format_hand(hand, hide_second=False):
    def display_card(card):
        rank = card[:-1]
        suit = card[-1]
        if rank in ["J", "Q", "K"]:
            return f"10{suit}"
        return card
    if hide_second and len(hand) >= 2:
        return f"{display_card(hand[0])} 🂠"
    return " ".join(display_card(c) for c in hand)

def tirage_gacha():
    roll = random.uniform(0, 100)
    cumul = 0
    for rarete, proba in RARETES_PROBA:
        cumul += proba
        if roll <= cumul:
            return rarete
    return "commun"

def get_casino_log(guild):
    return get_channel_by_name(guild, "casino-logs")

async def log_casino(guild, action, user, details, gain=0):
    ch = get_casino_log(guild)
    if not ch: return
    color = 0x2ECC71 if gain > 0 else (0xE74C3C if gain < 0 else 0xF1C40F)
    embed = discord.Embed(title=f"🎰 {action}", color=color,
                          timestamp=datetime.now(timezone.utc))
    embed.add_field(name="Joueur", value=user.mention, inline=True)
    embed.add_field(name="Gain/Perte", value=f"{'+' if gain > 0 else ''}{gain} 🪙", inline=True)
    embed.add_field(name="Détails", value=details, inline=False)
    await ch.send(embed=embed)

def get_or_init_bj(data):
    if "blackjack" not in data:
        data["blackjack"] = {"total_parties": 0, "wins": 0, "winrate": 0.0,
                              "pot_net": 0, "best_hand": 0, "hot_streak": 0, "current_streak": 0}
    return data["blackjack"]

def get_or_init_dice(data):
    if "dice" not in data:
        data["dice"] = {"total": 0, "wins": 0, "losses": 0}
    return data["dice"]

def get_or_init_duel(data):
    if "duel_stats" not in data:
        data["duel_stats"] = {"wins": 0, "losses": 0, "winrate": 0.0,
                               "pot_won": 0, "total_duels": 0, "best_win": ""}
    return data["duel_stats"]


class Casino(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.en_jeu = set()
        self.en_duel = set()

    # ══════════════════════════════════════════════════════════════════════════
    # RÈGLES
    # ══════════════════════════════════════════════════════════════════════════

    @app_commands.command(name="regles-blackjack", description="📖 Règles et guide du Blackjack")
    async def regles_blackjack(self, interaction: discord.Interaction):
        await interaction.response.send_message("📖 Chargement des règles...", ephemeral=True)
        embed = discord.Embed(title="🃏 Règles du Blackjack", color=0xF1C40F)
        embed.add_field(name="🎯 Objectif",
            value="Avoir une main dont la valeur est **plus proche de 21** que celle du croupier, **sans dépasser 21**.",
            inline=False)
        embed.add_field(name="🃏 Valeur des cartes",
            value="• **2 à 10** → valeur faciale\n• **J, Q, K** → **10**\n• **As (A)** → **11** (ou 1 si ça évite le bust)",
            inline=False)
        embed.add_field(name="⚡ Actions disponibles",
            value="• ✅ **HIT** — tirer une carte supplémentaire\n• ❌ **STAND** — rester avec ta main actuelle\n• ⚡ **DOUBLE** — doubler ta mise et tirer **une seule** carte",
            inline=False)
        embed.add_field(name="🏆 Résultats",
            value="• **Blackjack (21 dès le départ)** → gain x2.5 🏆\n• **Victoire normale** → gain x2 ✅\n• **Égalité (Push)** → mise remboursée 🤝\n• **Bust (>21)** → mise perdue 💥",
            inline=False)
        embed.add_field(name="🤖 Règle du croupier",
            value="Le croupier tire des cartes jusqu'à atteindre **17 ou plus**.", inline=False)
        embed.add_field(name="🎰 Tables disponibles",
            value=("`/blackjack-low [mise]` — 10 à 100 🪙 🟢\n"
                   "`/blackjack-high [mise]` — 500 à 5 000 🪙 🟡\n"
                   "`/blackjack-vip [mise]` — 10 000+ 🪙 🔴 *(Modos)*"),
            inline=False)
        embed.add_field(name="🏅 Rôles déblocables",
            value="• 🃏 **Card Shark** — 60%+ winrate sur 10+ parties\n• 🎰 **Pro Gambler** — 100 parties jouées\n• 💎 **High Roller** — +5 000 🪙 de profit net",
            inline=False)
        embed.set_footer(text="Prowler Bot • ?help dans #casino pour toutes les commandes")
        await interaction.edit_original_response(content=None, embed=embed)

    @app_commands.command(name="regles-dice", description="📖 Règles du jeu de dés")
    async def regles_dice(self, interaction: discord.Interaction):
        await interaction.response.send_message("📖 Chargement des règles...", ephemeral=True)
        embed = discord.Embed(title="🎲 Règles du Dice — Double ou Rien", color=0x3498DB)
        embed.add_field(name="🎯 Objectif",
            value="Lancer un dé et obtenir une valeur **plus haute** que celle du bot pour doubler ta mise.",
            inline=False)
        embed.add_field(name="⚙️ Comment jouer",
            value="1. `/dice [mise]` avec une mise entre **10 et 1 000 🪙**\n2. Toi et le bot lancez chacun un dé (1 à 6)\n3. Le plus haut score gagne !",
            inline=False)
        embed.add_field(name="🏆 Résultats",
            value="• **Ton dé > dé bot** → **+mise** 🪙 ✅\n• **Égalité** → mise conservée 🤝\n• **Ton dé < dé bot** → **-mise** 🪙 ❌",
            inline=False)
        embed.add_field(name="⏱️ Cooldown", value="10 secondes entre chaque partie.", inline=False)
        embed.set_footer(text="Prowler Bot • Simple, rapide, 50/50 !")
        await interaction.edit_original_response(content=None, embed=embed)

    @app_commands.command(name="regles-duel", description="📖 Règles du Gacha Duel")
    async def regles_duel(self, interaction: discord.Interaction):
        await interaction.response.send_message("📖 Chargement des règles...", ephemeral=True)
        embed = discord.Embed(title="⚔️ Règles du Gacha Duel", color=0x9B59B6)
        embed.add_field(name="🎯 Objectif",
            value="Défier un autre joueur — chacun tire une **rareté de carte** aléatoire. La meilleure rareté remporte le pot !",
            inline=False)
        embed.add_field(name="⚙️ Comment jouer",
            value="1. `/gacha-duel @adversaire [mise]` avec une mise entre **25 et 500 🪙**\n2. L'adversaire doit **accepter** dans les 30 secondes ✅\n3. Chacun tire une rareté aléatoire\n4. La plus haute rareté gagne le **pot total** !",
            inline=False)
        embed.add_field(name="🃏 Raretés (du plus fort au plus faible)",
            value="🌈 **Secret** — 100 pts\n🔴 **Mythique** — 50 pts\n🟡 **Légendaire** — 25 pts\n🟢 **Hallal** — 15 pts\n🟣 **Épique** — 10 pts\n🔵 **Rare** — 3 pts\n⚪ **Commun** — 2 pts\n⚫ **Shlag** — 1 pt",
            inline=False)
        embed.add_field(name="🎯 Probabilités",
            value="⚫ Shlag 31.5% • ⚪ Commun 26% • 🔵 Rare 18.5%\n🟣 Épique 13% • 🟢 Hallal 7.5% • 🟡 Légendaire 2%\n🔴 Mythique 1% • 🌈 Secret 0.5%",
            inline=False)
        embed.add_field(name="⚖️ Égalité",
            value="En cas d'égalité de rareté, un **dé** départage les deux joueurs.", inline=False)
        embed.set_footer(text="Prowler Bot • Que le meilleur gagne !")
        await interaction.edit_original_response(content=None, embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    # BLACKJACK — logique principale
    # ══════════════════════════════════════════════════════════════════════════
    async def _blackjack(self, channel, player, mise, table_name, mise_min, mise_max):
        ok, mention = check_casino(channel, player.guild)
        if not ok:
            await channel.send(f"{player.mention} ❌ Blackjack **uniquement** dans {mention} !")
            return
        if player.id in self.en_jeu:
            await channel.send(f"{player.mention} ❌ Tu as déjà une partie en cours !")
            return
        if not (mise_min <= mise <= mise_max):
            await channel.send(f"{player.mention} ❌ Mise invalide pour cette table. [{mise_min}–{mise_max} 🪙]")
            return
        db = load_db()
        data = get_member_data(db, player.id)
        if data["coins"] < mise:
            await channel.send(f"{player.mention} ❌ Solde insuffisant. Tu as **{data['coins']} 🪙**, il faut **{mise} 🪙**.")
            return

        self.en_jeu.add(player.id)
        data["coins"] -= mise
        save_db(db)

        try:
            deck = new_deck()
            main_joueur = [deck.pop(), deck.pop()]
            main_croupier = [deck.pop(), deck.pop()]

            embed = discord.Embed(title=f"🎰 BLACKJACK — {table_name}", color=0xF1C40F)
            embed.add_field(name="💰 Mise", value=f"{mise} 🪙", inline=True)
            embed.add_field(name="💳 Bankroll", value=f"{data['coins']} 🪙", inline=True)
            embed.set_footer(text="Deal en cours...")
            msg = await channel.send(f"{player.mention}", embed=embed)
            await asyncio.sleep(1)

            score_j = hand_value(main_joueur)
            embed.add_field(name="🃏 Tes cartes", value=f"{format_hand(main_joueur)} **[{score_j}]**", inline=False)
            embed.add_field(name="🤖 Croupier", value=f"{format_hand(main_croupier, hide_second=True)} **[??]**", inline=False)

            if score_j == 21:
                gain = int(mise * 2.5)
                db = load_db()
                data = get_member_data(db, player.id)
                bj = get_or_init_bj(data)
                bj["total_parties"] += 1
                bj["wins"] += 1
                bj["winrate"] = round(bj["wins"] / bj["total_parties"] * 100, 1)
                bj["pot_net"] += gain - mise
                bj["best_hand"] = 21
                bj["current_streak"] += 1
                bj["hot_streak"] = max(bj["hot_streak"], bj["current_streak"])
                data["coins"] += gain
                save_db(db)
                embed.color = 0xF1C40F
                embed.set_footer(text=f"🏆 BLACKJACK ! +{gain} 🪙 (5:2)")
                await msg.edit(content=f"{player.mention} 🎉 **BLACKJACK !**", embed=embed)
                await log_casino(player.guild, "Blackjack", player, f"BLACKJACK 21 — Table {table_name}", gain - mise)
                await self._check_bj_roles(player, data["blackjack"])
                return

            embed.set_footer(text="✅ HIT (carte) • ❌ STAND • ⚡ DOUBLE")
            await msg.edit(content=f"{player.mention}", embed=embed)
            await msg.add_reaction("✅")
            await msg.add_reaction("❌")
            db_check = load_db()
            data_check = get_member_data(db_check, player.id)
            if data_check["coins"] >= mise:
                await msg.add_reaction("⚡")

            doubled = False
            while True:
                def check(r, u):
                    return u == player and r.message.id == msg.id and str(r.emoji) in ["✅", "❌", "⚡"]
                try:
                    reaction, _ = await self.bot.wait_for("reaction_add", timeout=60, check=check)
                except asyncio.TimeoutError:
                    await msg.edit(content=f"{player.mention} ⌛ Partie expirée — mise perdue.", embed=None)
                    await log_casino(player.guild, "Blackjack", player, "Timeout — mise perdue", -mise)
                    return

                action = str(reaction.emoji)
                await msg.clear_reactions()

                if action == "⚡":
                    db2 = load_db()
                    data2 = get_member_data(db2, player.id)
                    if data2["coins"] >= mise:
                        data2["coins"] -= mise
                        save_db(db2)
                        mise *= 2
                        doubled = True
                    main_joueur.append(deck.pop())
                    action = "❌"

                if action == "✅":
                    main_joueur.append(deck.pop())
                    score_j = hand_value(main_joueur)
                    embed._fields = [f for f in embed._fields if "Tes cartes" not in f["name"]]
                    embed.add_field(name="🃏 Tes cartes", value=f"{format_hand(main_joueur)} **[{score_j}]**", inline=False)
                    if score_j > 21:
                        embed.color = 0xE74C3C
                        embed.set_footer(text=f"💥 BUST ! Perdu {mise} 🪙")
                        await msg.edit(content=f"{player.mention} 💥 **BUST !**", embed=embed)
                        await self._fin_bj(player, False, mise, score_j)
                        await log_casino(player.guild, "Blackjack", player, f"Bust [{score_j}]", -mise)
                        return
                    if score_j == 21:
                        action = "❌"
                    else:
                        embed.set_footer(text="✅ HIT • ❌ STAND" + (" • ⚡ DOUBLE" if not doubled else ""))
                        await msg.edit(embed=embed)
                        await msg.add_reaction("✅")
                        await msg.add_reaction("❌")
                        continue

                if action == "❌":
                    break

            score_j = hand_value(main_joueur)
            while hand_value(main_croupier) < 17:
                main_croupier.append(deck.pop())
            score_c = hand_value(main_croupier)

            embed._fields = [f for f in embed._fields if "Croupier" not in f["name"]]
            embed.add_field(name="🤖 Croupier", value=f"{format_hand(main_croupier)} **[{score_c}]**", inline=False)

            if score_c > 21 or score_j > score_c:
                gain = mise * 2
                embed.color = 0x2ECC71
                embed.set_footer(text=f"🏆 VICTOIRE ! +{mise} 🪙")
                await msg.edit(content=f"{player.mention} 🏆 **Victoire !**", embed=embed)
                await self._fin_bj(player, True, mise, score_j, gain)
                await log_casino(player.guild, "Blackjack", player, f"Win [{score_j}] vs [{score_c}]", mise)
            elif score_j == score_c:
                db3 = load_db()
                data3 = get_member_data(db3, player.id)
                data3["coins"] += mise
                save_db(db3)
                embed.color = 0x95A5A6
                embed.set_footer(text="🤝 ÉGALITÉ — Mise remboursée")
                await msg.edit(content=f"{player.mention} 🤝 **Égalité !**", embed=embed)
                await log_casino(player.guild, "Blackjack", player, f"Push [{score_j}]", 0)
            else:
                embed.color = 0xE74C3C
                embed.set_footer(text=f"❌ Perdu {mise} 🪙")
                await msg.edit(content=f"{player.mention} ❌ **Perdu !**", embed=embed)
                await self._fin_bj(player, False, mise, score_j)
                await log_casino(player.guild, "Blackjack", player, f"Loss [{score_j}] vs [{score_c}]", -mise)

        finally:
            self.en_jeu.discard(player.id)

    async def _fin_bj(self, player, win, mise, score, gain=0):
        db = load_db()
        data = get_member_data(db, player.id)
        bj = get_or_init_bj(data)
        bj["total_parties"] += 1
        if win:
            bj["wins"] += 1
            data["coins"] += gain
            bj["pot_net"] += gain - mise
            bj["current_streak"] += 1
            bj["hot_streak"] = max(bj["hot_streak"], bj["current_streak"])
        else:
            bj["pot_net"] -= mise
            bj["current_streak"] = 0
        bj["winrate"] = round(bj["wins"] / bj["total_parties"] * 100, 1)
        bj["best_hand"] = max(bj.get("best_hand", 0), score)
        save_db(db)
        await self._check_bj_roles(player, bj)

    async def _check_bj_roles(self, player, bj):
        guild = player.guild
        roles_a_donner = []
        if bj["winrate"] >= 60 and bj["total_parties"] >= 10:
            roles_a_donner.append("🃏 Card Shark")
        if bj["total_parties"] >= 100:
            roles_a_donner.append("🎰 Pro Gambler")
        if bj["pot_net"] >= 5000:
            roles_a_donner.append("💎 High Roller")
        for rname in roles_a_donner:
            role = discord.utils.get(guild.roles, name=rname)
            if role and role not in player.roles:
                try:
                    await player.add_roles(role)
                except:
                    pass

    # ══════════════════════════════════════════════════════════════════════════
    # TABLES BLACKJACK
    # ══════════════════════════════════════════════════════════════════════════

    @app_commands.command(name="blackjack-low", description="🟢 Blackjack Table Low (10–100 🪙) — Accessible à tous")
    @app_commands.describe(mise="Ta mise (10 à 100 pièces)")
    async def bj_low(self, interaction: discord.Interaction, mise: int):
        await interaction.response.send_message("🎰 Lancement de la partie...", ephemeral=True)
        await self._blackjack(interaction.channel, interaction.user, mise, "🟢 Low", 10, 100)

    @app_commands.command(name="blackjack-high", description="🟡 Blackjack Table High (500–5000 🪙)")
    @app_commands.describe(mise="Ta mise (500 à 5000 pièces)")
    async def bj_high(self, interaction: discord.Interaction, mise: int):
        await interaction.response.send_message("🎰 Vérification de ton accès...", ephemeral=True)
        db = load_db()
        data = get_member_data(db, interaction.user.id)
        bj = data.get("blackjack", {})
        total_parties = bj.get("total_parties", 0)
        winrate = bj.get("winrate", 0)
        if total_parties >= 10 and winrate < 50:
            await interaction.edit_original_response(
                content=(
                    f"❌ Table High réservée aux joueurs avec **50%+ winrate**.\n"
                    f"Ton winrate : **{winrate}%** sur **{total_parties}** parties.\n"
                    f"Continue sur la Table Low !"
                )
            )
            return
        await self._blackjack(interaction.channel, interaction.user, mise, "🟡 High", 500, 5000)

    @app_commands.command(name="blackjack-vip", description="🔴 Blackjack Table VIP (10 000+ 🪙) — Modos seulement")
    @app_commands.describe(mise="Ta mise (10 000+ pièces)")
    @app_commands.default_permissions(manage_roles=True)
    async def bj_vip(self, interaction: discord.Interaction, mise: int):
        await interaction.response.send_message("🎰 Lancement de la partie VIP...", ephemeral=True)
        await self._blackjack(interaction.channel, interaction.user, mise, "🔴 VIP", 10000, 999999)

    @app_commands.command(name="blackjack-stats", description="📊 Tes statistiques Blackjack")
    async def bj_stats(self, interaction: discord.Interaction):
        await interaction.response.send_message("📊 Chargement de tes stats...", ephemeral=True)
        db = load_db()
        data = get_member_data(db, interaction.user.id)
        bj = get_or_init_bj(data)
        embed = discord.Embed(title="🎰 Tes stats Blackjack", color=0xF1C40F)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.add_field(name="🎲 Parties", value=str(bj["total_parties"]), inline=True)
        embed.add_field(name="🏆 Victoires", value=str(bj["wins"]), inline=True)
        embed.add_field(name="📊 Winrate", value=f"{bj['winrate']}%", inline=True)
        embed.add_field(name="💰 Net", value=f"{bj['pot_net']:+} 🪙", inline=True)
        embed.add_field(name="🔥 Hot streak max", value=str(bj["hot_streak"]), inline=True)
        embed.add_field(name="🃏 Meilleure main", value=str(bj["best_hand"]), inline=True)
        total_parties = bj["total_parties"]
        winrate = bj["winrate"]
        if total_parties < 10:
            remaining = 10 - total_parties
            embed.add_field(
                name="🟡 Table High",
                value=f"Accessible — encore **{remaining}** partie(s) avant activation de la restriction.",
                inline=False
            )
        elif winrate >= 50:
            embed.add_field(name="🟡 Table High", value="✅ Accès débloqué !", inline=False)
        else:
            embed.add_field(name="🟡 Table High", value=f"❌ Bloqué — winrate trop bas ({winrate}% < 50%)", inline=False)
        await interaction.edit_original_response(content=None, embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    # DICE
    # ══════════════════════════════════════════════════════════════════════════
    dice_cooldowns = {}

    async def _dice(self, channel, player, mise):
        ok, mention = check_casino(channel, player.guild)
        if not ok:
            await channel.send(f"{player.mention} ❌ Dice **uniquement** dans {mention} !")
            return
        if not (10 <= mise <= 1000):
            await channel.send(f"{player.mention} ❌ Mise entre 10 et 1000 🪙.")
            return
        now = asyncio.get_event_loop().time()
        last = self.dice_cooldowns.get(player.id, 0)
        if now - last < 10:
            await channel.send(f"{player.mention} ⏳ Cooldown ! Attends {10 - int(now - last)}s.")
            return
        db = load_db()
        data = get_member_data(db, player.id)
        if data["coins"] < mise:
            await channel.send(f"{player.mention} ❌ Solde insuffisant ({data['coins']} 🪙).")
            return
        self.dice_cooldowns[player.id] = now
        de_joueur = random.randint(1, 6)
        de_bot = random.randint(1, 6)
        win = de_joueur > de_bot
        db = load_db()
        data = get_member_data(db, player.id)
        dice_s = get_or_init_dice(data)
        dice_s["total"] += 1
        if win:
            data["coins"] += mise
            dice_s["wins"] += 1
        elif de_joueur < de_bot:
            data["coins"] -= mise
            dice_s["losses"] += 1
        save_db(db)
        faces = ["⚀", "⚁", "⚂", "⚃", "⚄", "⚅"]
        embed = discord.Embed(
            title="🎲 DOUBLE OU RIEN",
            color=0x2ECC71 if win else (0xF1C40F if de_joueur == de_bot else 0xE74C3C)
        )
        embed.add_field(name="💰 Mise", value=f"{mise} 🪙", inline=True)
        embed.add_field(name="🎲 Toi", value=f"{faces[de_joueur-1]} **{de_joueur}**", inline=True)
        embed.add_field(name="🤖 Bot", value=f"{faces[de_bot-1]} **{de_bot}**", inline=True)
        if win:
            embed.set_footer(text=f"✅ VICTOIRE ! +{mise} 🪙 → Total : {data['coins']} 🪙")
        elif de_joueur == de_bot:
            embed.set_footer(text=f"🤝 Égalité — Mise conservée → Total : {data['coins']} 🪙")
        else:
            embed.set_footer(text=f"❌ Perdu {mise} 🪙 → Total : {data['coins']} 🪙")
        await channel.send(f"{player.mention}", embed=embed)
        await log_casino(player.guild, "Dice", player,
                         f"{de_joueur} vs {de_bot}", mise if win else (-mise if de_joueur < de_bot else 0))

    @app_commands.command(name="dice", description="🎲 Double ou rien ! Dé vs Bot (10–1000 🪙)")
    @app_commands.describe(mise="Ta mise (10 à 1000 pièces)")
    async def slash_dice(self, interaction: discord.Interaction, mise: int):
        await interaction.response.send_message("🎲 Lancement des dés...", ephemeral=True)
        await self._dice(interaction.channel, interaction.user, mise)

    @commands.command(name="dice")
    async def cmd_dice(self, ctx, mise: int):
        await self._dice(ctx.channel, ctx.author, mise)

    @app_commands.command(name="mystats", description="📊 Tes stats Dice")
    async def slash_mystats(self, interaction: discord.Interaction):
        await interaction.response.send_message("📊 Chargement...", ephemeral=True)
        db = load_db()
        data = get_member_data(db, interaction.user.id)
        d = get_or_init_dice(data)
        wr = round(d["wins"] / d["total"] * 100, 1) if d["total"] > 0 else 0
        embed = discord.Embed(title="🎲 Tes stats Dice", color=0x3498DB)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.add_field(name="🎲 Parties", value=str(d["total"]), inline=True)
        embed.add_field(name="✅ Victoires", value=str(d["wins"]), inline=True)
        embed.add_field(name="❌ Défaites", value=str(d["losses"]), inline=True)
        embed.add_field(name="📊 Winrate", value=f"{wr}%", inline=True)
        await interaction.edit_original_response(content=None, embed=embed)

    # ══════════════════════════════════════════════════════════════════════════
    # GACHA DUEL
    # ══════════════════════════════════════════════════════════════════════════
    @app_commands.command(name="gacha-duel", description="⚔️ Duel Gacha — Meilleure rareté gagne le pot !")
    @app_commands.describe(adversaire="Ton adversaire", mise="Mise (25–500 🪙)")
    async def slash_gacha_duel(self, interaction: discord.Interaction, adversaire: discord.Member, mise: int):
        await interaction.response.send_message("⚔️ Lancement du duel...", ephemeral=True)
        channel = interaction.channel
        joueur = interaction.user

        ok, mention = check_casino(channel, joueur.guild)
        if not ok:
            await interaction.edit_original_response(content=f"❌ Gacha Duel **uniquement** dans {mention} !")
            return
        if adversaire.bot or adversaire == joueur:
            await interaction.edit_original_response(content="❌ Adversaire invalide.")
            return
        if not (25 <= mise <= 500):
            await interaction.edit_original_response(content="❌ Mise entre 25 et 500 🪙.")
            return
        if joueur.id in self.en_duel or adversaire.id in self.en_duel:
            await interaction.edit_original_response(content="❌ L'un de vous est déjà en duel.")
            return

        db = load_db()
        dj = get_member_data(db, joueur.id)
        da = get_member_data(db, adversaire.id)
        if dj["coins"] < mise:
            await interaction.edit_original_response(content=f"❌ Solde insuffisant ({dj['coins']} 🪙).")
            return
        if da["coins"] < mise:
            await interaction.edit_original_response(content=f"❌ {adversaire.display_name} n'a que {da['coins']} 🪙.")
            return

        embed_ch = discord.Embed(
            title="🎰 CASINO DUEL",
            description=f"⚔️ {joueur.mention} défie {adversaire.mention} !\n💰 Mise : **{mise} 🪙** chacun\n🏆 **POT : {mise*2} 🪙**",
            color=0xF1C40F
        )
        embed_ch.set_footer(text="✅ Accepter • ❌ Refuser — 30s")
        msg = await channel.send(f"{adversaire.mention} — Tu es défié !", embed=embed_ch)
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")

        def check_conf(r, u):
            return u == adversaire and r.message.id == msg.id and str(r.emoji) in ["✅", "❌"]
        try:
            reaction, _ = await self.bot.wait_for("reaction_add", timeout=30, check=check_conf)
        except asyncio.TimeoutError:
            await msg.edit(content=f"{adversaire.mention} ⌛ Duel expiré.", embed=None)
            return
        if str(reaction.emoji) == "❌":
            await msg.edit(content=f"{adversaire.mention} ❌ Duel refusé.", embed=None)
            return

        self.en_duel.add(joueur.id)
        self.en_duel.add(adversaire.id)
        try:
            db = load_db()
            get_member_data(db, joueur.id)["coins"] -= mise
            get_member_data(db, adversaire.id)["coins"] -= mise
            save_db(db)

            frames = ["⏳ Chargement du duel...", "🟥▓▓ Spin en cours...",
                      "🟥🟥🟥 Dernières secondes...", "✨ Révélation !"]
            embed_anim = discord.Embed(title="⚔️ DUEL GACHA EN COURS", color=0x9B59B6)
            embed_anim.set_footer(text=frames[0])
            await msg.edit(content=f"{joueur.mention} {adversaire.mention}", embed=embed_anim)
            for frame in frames[1:]:
                await asyncio.sleep(1)
                embed_anim.set_footer(text=frame)
                await msg.edit(embed=embed_anim)

            rarete_j = tirage_gacha()
            rarete_a = tirage_gacha()
            pts_j = RARETES_POINTS[rarete_j]
            pts_a = RARETES_POINTS[rarete_a]

            if pts_j > pts_a:
                gagnant, perdant = joueur, adversaire
                rarete_g, rarete_p = rarete_j, rarete_a
            elif pts_a > pts_j:
                gagnant, perdant = adversaire, joueur
                rarete_g, rarete_p = rarete_a, rarete_j
            else:
                de_j, de_a = random.randint(1, 6), random.randint(1, 6)
                while de_j == de_a:
                    de_j, de_a = random.randint(1, 6), random.randint(1, 6)
                if de_j > de_a:
                    gagnant, perdant = joueur, adversaire
                    rarete_g, rarete_p = rarete_j, rarete_a
                else:
                    gagnant, perdant = adversaire, joueur
                    rarete_g, rarete_p = rarete_a, rarete_j

            pot = mise * 2
            db = load_db()
            get_member_data(db, gagnant.id)["coins"] += pot

            for uid, win in [(str(joueur.id), gagnant == joueur), (str(adversaire.id), gagnant == adversaire)]:
                ds = get_or_init_duel(db[uid])
                ds["total_duels"] += 1
                if win:
                    ds["wins"] += 1
                    ds["pot_won"] += pot
                else:
                    ds["losses"] += 1
                ds["winrate"] = round(ds["wins"] / ds["total_duels"] * 100, 1)
            save_db(db)

            pts_affiche_j = pts_j if gagnant == joueur else pts_a
            pts_affiche_p = pts_a if gagnant == joueur else pts_j

            embed_res = discord.Embed(
                title=f"🏆 {gagnant.display_name} GAGNE !",
                color=0x2ECC71
            )
            embed_res.add_field(
                name=f"🥇 {gagnant.display_name}",
                value=f"{RARETES_EMOJI[rarete_g]} **{rarete_g.upper()}** ({pts_affiche_j} pts)",
                inline=True
            )
            embed_res.add_field(
                name=f"💀 {perdant.display_name}",
                value=f"{RARETES_EMOJI[rarete_p]} **{rarete_p.upper()}** ({pts_affiche_p} pts)",
                inline=True
            )
            embed_res.set_footer(text=f"💰 +{pot} 🪙 pour {gagnant.display_name}")
            await msg.edit(content=f"{gagnant.mention} 🎉 **Victoire !** {perdant.mention}", embed=embed_res)
            await log_casino(joueur.guild, "Gacha Duel", gagnant,
                             f"vs {perdant.display_name} — {rarete_g} > {rarete_p}", pot - mise)
            await self._check_duel_roles(gagnant, db[str(gagnant.id)]["duel_stats"])

        finally:
            self.en_duel.discard(joueur.id)
            self.en_duel.discard(adversaire.id)

    async def _check_duel_roles(self, player, ds):
        guild = player.guild
        roles_map = [
            ("total_duels", 50, "Dueliste 🗡️"),
            ("winrate", 60, "Champion 👑"),
            ("pot_won", 5000, "Whale 🐋")
        ]
        for key, seuil, rname in roles_map:
            if ds.get(key, 0) >= seuil:
                role = discord.utils.get(guild.roles, name=rname)
                if role and role not in player.roles:
                    try:
                        await player.add_roles(role)
                    except:
                        pass

    @app_commands.command(name="top-duel", description="🏆 Leaderboard des duels Gacha")
    async def slash_top_duel(self, interaction: discord.Interaction):
        await interaction.response.send_message("🏆 Chargement du leaderboard...", ephemeral=True)
        db = load_db()
        data_list = []
        for mid, data in db.items():
            m = interaction.guild.get_member(int(mid))
            if m and "duel_stats" in data:
                ds = data["duel_stats"]
                if ds["total_duels"] >= 5:
                    data_list.append((m.display_name, ds["winrate"], ds["total_duels"], ds["pot_won"]))
        data_list.sort(key=lambda x: x[1], reverse=True)
        medals = ["🥇", "🥈", "🥉"] + [f"{i}." for i in range(4, 11)]
        lines = [
            f"{medals[i]} **{n}** — {wr}% ({t} duels) • {p} 🪙 gagnés"
            for i, (n, wr, t, p) in enumerate(data_list[:10])
        ]
        embed = discord.Embed(
            title="⚔️ Top Duels Gacha",
            description="\n".join(lines) if lines else "Aucun duel (5 minimum requis).",
            color=0x9B59B6
        )
        await interaction.edit_original_response(content=None, embed=embed)


async def setup(bot):
    await bot.add_cog(Casino(bot))
